"""
Main FastAPI server.

Handles:
- POST /incoming-call: Twilio webhook — returns TwiML to start media stream
- WebSocket /media-stream/{scenario_id}: Bidirectional audio stream for a call
"""

import asyncio
import base64
import json
import os
import time

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response

from audio_pipeline import DeepgramStreamingSTT, TurnDetector, mp3_to_mulaw_8khz
from logger import CallLogger
from patient_agent import get_patient_response, synthesize_speech, should_end_call
from scenarios.scenarios import get_scenario, get_canonical_scenario, get_jailbreak_scenario

load_dotenv()

app = FastAPI(title="Pretty Good AI Voice Bot")

# ─────────────────────────────────────────────────────────────────
# Twilio webhook: returns TwiML that opens a Media Stream
# ─────────────────────────────────────────────────────────────────

@app.post("/incoming-call/{scenario_id}")
async def incoming_call(scenario_id: int, request: Request):
    """
    Twilio calls this URL when our outbound call connects.
    We respond with TwiML that opens a bidirectional media stream.
    """
    public_url = os.environ["PUBLIC_URL"].rstrip("/")
    ws_url = public_url.replace("https://", "wss://").replace("http://", "ws://")
    ws_endpoint = f"{ws_url}/media-stream/{scenario_id}"

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{ws_endpoint}" />
    </Connect>
</Response>"""

    return Response(content=twiml, media_type="application/xml")


# ─────────────────────────────────────────────────────────────────
# WebSocket: handles bidirectional audio with Twilio
# ─────────────────────────────────────────────────────────────────

@app.websocket("/media-stream/{scenario_id}")
async def media_stream(websocket: WebSocket, scenario_id: int):
    """
    This is the core of the voice bot.

    Flow per turn:
      1. Receive mulaw audio from Twilio (agent speaking)
      2. Stream to Deepgram for real-time STT
      3. Detect end of agent turn (silence)
      4. Send agent text to GPT-4o patient brain
      5. Get patient text response
      6. Convert to speech (OpenAI TTS → MP3 → mulaw)
      7. Send audio back through Twilio stream
    """
    await websocket.accept()
    try:
        scenario = get_scenario(scenario_id)
    except ValueError:
        try:
            scenario = get_canonical_scenario(scenario_id)
        except ValueError:
            scenario = get_jailbreak_scenario(scenario_id)
    print(f"\n{'='*60}")
    print(f"📞 Call connected: Scenario {scenario_id} — {scenario['name']}")
    print(f"{'='*60}")

    logger = CallLogger(scenario_id, scenario["name"])
    conversation_history: list[dict] = []
    stream_sid: str | None = None

    # Turn detector accumulates Deepgram transcripts
    turn_detector = TurnDetector()

    # Deepgram STT — transcripts and utterance-end events feed into turn_detector
    stt = DeepgramStreamingSTT(
        on_transcript=turn_detector.add_transcript,
        on_utterance_end=turn_detector.signal_utterance_end,
    )
    await stt.connect()

    # Track whether we've sent the first patient message yet
    call_started = False
    call_ended = False

    async def send_audio_to_twilio(mp3_bytes: bytes):
        """Convert TTS audio and stream it back to Twilio."""
        nonlocal call_ended
        try:
            mulaw_bytes = await mp3_to_mulaw_8khz(mp3_bytes)
        except Exception as e:
            print(f"  [Audio conversion error]: {e}")
            return

        # Twilio expects base64-encoded mulaw in Media messages
        # Send in chunks to avoid overwhelming the buffer
        chunk_size = 640  # 80ms chunks at 8kHz
        for i in range(0, len(mulaw_bytes), chunk_size):
            chunk = mulaw_bytes[i : i + chunk_size]
            b64_chunk = base64.b64encode(chunk).decode("utf-8")
            msg = json.dumps({
                "event": "media",
                "streamSid": stream_sid,
                "media": {"payload": b64_chunk},
            })
            try:
                await websocket.send_text(msg)
            except Exception:
                call_ended = True
                return
            # Small delay to pace the audio — don't flood Twilio
            await asyncio.sleep(0.01)

        # Send a mark to know when audio finishes
        mark_msg = json.dumps({
            "event": "mark",
            "streamSid": stream_sid,
            "mark": {"name": "audio_complete"},
        })
        try:
            await websocket.send_text(mark_msg)
        except Exception:
            call_ended = True

    async def handle_agent_turn(agent_text: str):
        """Process what the agent said and generate + send patient response."""
        nonlocal call_ended

        if not agent_text.strip():
            return

        logger.log_turn("agent", agent_text)

        # Add to conversation history (agent = "user" from OpenAI's perspective)
        conversation_history.append({"role": "user", "content": agent_text})

        # Generate patient response
        patient_text = await get_patient_response(
            scenario_system_prompt=scenario["system_prompt"],
            conversation_history=conversation_history[:-1],  # history before this turn
            agent_message=agent_text,
        )

        logger.log_turn("patient", patient_text)

        # Add patient response to history
        conversation_history.append({"role": "assistant", "content": patient_text})

        # Check if patient is saying goodbye
        if await should_end_call(patient_text):
            call_ended = True

        # Convert to speech and send
        mp3_bytes = await synthesize_speech(patient_text)
        await send_audio_to_twilio(mp3_bytes)

        # If ending, give audio time to play then close
        if call_ended:
            await asyncio.sleep(3)

    # ── Main WebSocket receive loop ──────────────────────────────
    try:
        # State machine for listening vs. speaking
        is_agent_speaking = False
        agent_speech_task: asyncio.Task | None = None

        async def listen_for_agent():
            """Background task: waits for Deepgram UtteranceEnd, then responds."""
            nonlocal call_ended, is_agent_speaking
            while not call_ended:
                # Reconnect if Deepgram dropped during TTS playback
                await stt.ensure_connected()
                agent_text = await turn_detector.wait_for_turn_end()
                if agent_text and not call_ended:
                    is_agent_speaking = False
                    await handle_agent_turn(agent_text)
                    if call_ended:
                        break

        # Start the greeting: patient speaks first after a short delay
        async def send_greeting():
            nonlocal call_started, stream_sid
            # Wait for stream_sid to be set
            for _ in range(50):
                if stream_sid:
                    break
                await asyncio.sleep(0.1)

            await asyncio.sleep(1.0)  # Brief pause before patient speaks

            greeting = scenario["first_message"]
            logger.log_turn("patient", greeting)
            conversation_history.append({"role": "assistant", "content": greeting})

            mp3_bytes = await synthesize_speech(greeting)
            await send_audio_to_twilio(mp3_bytes)
            call_started = True

        greeting_task = asyncio.create_task(send_greeting())
        listen_task = asyncio.create_task(listen_for_agent())

        async for message in websocket.iter_text():
            if call_ended:
                break

            data = json.loads(message)
            event = data.get("event")

            if event == "connected":
                print("  [Twilio] Stream connected")

            elif event == "start":
                stream_sid = data["start"]["streamSid"]
                logger.set_metadata(stream_sid=stream_sid, call_sid=data["start"].get("callSid"))
                print(f"  [Twilio] Stream started: {stream_sid}")

            elif event == "media":
                # Received audio chunk from Twilio (agent speaking)
                if call_started:
                    payload = data["media"]["payload"]
                    mulaw_bytes = base64.b64decode(payload)
                    await stt.send_audio(mulaw_bytes)

            elif event == "mark":
                pass  # Audio playback complete marker

            elif event == "stop":
                print("  [Twilio] Stream stopped")
                break

    except WebSocketDisconnect:
        print("  [WebSocket] Disconnected")
    except Exception as e:
        print(f"  [Error in media stream]: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Clean up
        greeting_task.cancel() if not greeting_task.done() else None
        listen_task.cancel() if not listen_task.done() else None
        await stt.close()
        filepath = logger.save()
        print(f"\n✅ Call complete. Transcript: {filepath}")


# ─────────────────────────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
