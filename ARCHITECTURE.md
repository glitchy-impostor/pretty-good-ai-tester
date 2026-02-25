# Architecture Document

## System Overview

The voice bot is a real-time audio processing pipeline built on FastAPI with a WebSocket core. It places outbound calls via Twilio, streams audio bidirectionally, transcribes the agent's speech with Deepgram, generates patient responses with GPT-4o, synthesizes speech with OpenAI TTS, and logs every turn to a JSON transcript.

```
call_runner.py
     |
     v
Twilio (outbound call)
     |
     | webhook: /incoming-call/{scenario_id}
     v
FastAPI server (main.py)
     |
     | TwiML: <Connect><Stream>
     v
Twilio Media Stream (WebSocket, mulaw 8kHz, bidirectional)
     |
     +--[inbound audio]--> Deepgram Nova-2 (streaming STT, WebSocket)
     |                          |
     |                          | UtteranceEnd event (turn detection)
     |                          v
     |                    GPT-4o patient brain
     |                    (scenario system prompt + conversation history)
     |                          |
     |                          v
     |                    OpenAI TTS (MP3)
     |                          |
     |                          v
     |                    ffmpeg: MP3 -> mulaw 8kHz
     |                          |
     +--[outbound audio]<--------+
     |
     v
logger.py -> transcripts/{call_id}.json
```

## Component Breakdown

### call_runner.py - Call Orchestration

Triggers outbound calls via the Twilio Python SDK and manages sequencing across multiple scenarios.

- Polls Twilio call status every 5 seconds via `client.calls(call_sid).fetch()`
- Fires the next call immediately when the previous reaches a terminal status: `completed`, `failed`, `busy`, `no-answer`, `canceled`
- Default gap between calls: 3 seconds
- Supports three scenario sets via CLI flags:
  - `--all` - runs scenarios 1-10 (exploratory)
  - `--canonical` - runs scenarios 11-17 (pre-registered patients)
  - `--jailbreak` - runs scenarios 18-19 (adversarial stress tests)
  - `--scenario N` / `--canonical-scenario N` / `--jailbreak-scenario N` - single scenario

### main.py - FastAPI Server + WebSocket Handler

Handles the Twilio webhook and manages the full duplex audio stream.

- `/incoming-call/{scenario_id}` (POST) - receives Twilio webhook, responds with TwiML to open a Media Stream
- `/media-stream/{scenario_id}` (WebSocket) - handles the live call
  - Loads scenario by ID, falling back through exploratory -> canonical -> jailbreak lookup chain
  - Accepts Twilio JSON envelopes: `connected`, `start`, `media`, `stop`
  - Decodes base64 mulaw audio from `media` events and forwards to Deepgram
  - On agent turn end: calls GPT-4o, synthesizes speech, streams audio back to Twilio in 80ms chunks
  - Logs every turn via `logger.py`

### audio_pipeline.py - Deepgram STT + Reconnect Logic

Manages the Deepgram streaming connection with resilience to WebSocket drops.

- Connects to Deepgram Nova-2 via `deepgram.listen.asyncwebsocket.v("1")`
- Configuration: `model=nova-2`, `encoding=mulaw`, `sample_rate=8000`, `punctuate=True`, `utterance_end_ms=1000`
- **Turn detection**: uses `on_utterance_end` event (event-driven, not polling). When Deepgram fires UtteranceEnd, the accumulated transcript is surfaced as a complete agent turn.
- **20-second timeout fallback**: `wait_for_turn_end()` returns partial transcript if Deepgram drops rather than hanging the call indefinitely
- **Auto-reconnect**: `ensure_connected()` checks `_is_connected` flag before each listening window and reconnects if needed. `on_close` and `on_error` handlers set `_is_connected = False` so the flag is always accurate.
- `send_audio()` catches send exceptions silently to prevent log flooding on a dropped connection

### patient_agent.py - GPT-4o Patient Brain + OpenAI TTS

Generates patient responses and synthesizes speech.

- Maintains full conversation history across turns (role: user/assistant alternating)
- System prompt: scenario-specific patient persona + behavioral rules
- Model: `gpt-4o`, max 150 tokens per response, temperature 0.7
- TTS: `openai.audio.speech.create(model="tts-1", voice="alloy")` producing MP3
- MP3 converted to mulaw 8kHz via `ffmpeg` subprocess: `ffmpeg -f mp3 -i pipe:0 -ar 8000 -ac 1 -f mulaw pipe:1`
- Output chunked into 160-byte segments (80ms at 8kHz) for smooth streaming

### scenarios/scenarios.py - Scenario Definitions

Three scenario sets, 19 total:

**Exploratory (1-10):** Broad coverage of common patient call types. No pre-registered patients required.

| ID | Name | Purpose |
|---|---|---|
| 1 | simple_scheduling | Happy-path new patient scheduling |
| 2 | rescheduling | Existing appointment changes, date handling |
| 3 | cancellation | Cancel without rescheduling, confirmation flow |
| 4 | medication_refill_simple | Common drug, cooperative patient |
| 5 | medication_refill_complex | Specialty drug (Jardiance), hallucination risk |
| 6 | office_hours_inquiry | Factual accuracy, basic clinic info |
| 7 | insurance_inquiry | Appropriate deflection vs. hallucination on costs |
| 8 | confused_elderly_patient | Patience, clarification, repetition handling |
| 9 | multi_part_request | Compound intent, multi-request state tracking |
| 10 | adversarial_edge_case | Ambiguity, vague inputs, graceful failure |

**Canonical (11-17):** End-to-end workflow completion tests. Require 5 pre-registered patients with known credentials (DOB year 2000 on all records due to intake form default).

| ID | Name | Patient | Tests |
|---|---|---|---|
| 11 | canonical_scheduling | Thomas Nguyen | Full happy-path scheduling |
| 12 | canonical_reschedule | Thomas Nguyen | Reschedule existing appointment |
| 13 | canonical_refill | Rebecca Okafor | Medication refill end-to-end |
| 14 | canonical_cancellation | Marcus Chen | Cancellation with reschedule resistance |
| 15 | canonical_new_patient | Carlos Rivera | New patient full registration flow |
| 16 | canonical_referral | Susan Hartley | Schedule from referral, Medicare handling |
| 17 | canonical_callback_followup | Rebecca Okafor | Follow up on missed callback promise |

**Jailbreak (18-19):** Adversarial security stress tests.

| ID | Name | Attack Vectors |
|---|---|---|
| 18 | jailbreak_authority_override | Impersonate physician/developer, spoken prompt injection, medical emergency urgency, legal threats |
| 19 | jailbreak_persona_manipulation | Roleplay entrapment, identity probing, scope creep, out-of-scope requests |

### logger.py - Transcript Logging

Writes a JSON file per call to `transcripts/` with the following schema:

```json
{
  "scenario_id": 15,
  "scenario_name": "canonical_new_patient",
  "call_sid": "CA...",
  "started_at": "2026-02-23T17:08:08.651927",
  "ended_at": "2026-02-23T17:11:13.116010",
  "metadata": { "stream_sid": "MZ...", "call_sid": "CA..." },
  "transcript": [
    {
      "timestamp": "2026-02-23T17:08:09.901540",
      "speaker": "patient",
      "text": "Hi, I'm a new patient..."
    }
  ]
}
```

## Key Design Decisions

**Twilio Media Streams over TwiML `<Gather>`/`<Say>`**

The TwiML approach is fundamentally request/response - you buffer audio, transcribe it, generate a reply, and play it back with no way to detect natural pauses mid-sentence or handle interruptions. Media Streams give a live bidirectional audio channel that enables real turn-taking. The tradeoff is significant added complexity (WebSocket management, audio format conversion, concurrent async tasks), but it's the only way to simulate a realistic human conversation.

**Deepgram Nova-2 over Whisper for STT**

Whisper is batch-only - you'd buffer an entire utterance and wait for transcription, adding 3-5 seconds of dead air per turn. Deepgram's streaming WebSocket returns partials and finals in near real-time (~300ms). Nova-2 handles phone-quality mulaw 8kHz audio natively with no preprocessing. UtteranceEnd is a first-class event rather than a silence threshold we'd have to implement manually.

**Event-driven turn detection over silence polling**

An early implementation polled for silence by checking whether no new transcript had arrived for N seconds. This was brittle - network jitter caused false positives and a slow agent response would time out prematurely. Switching to Deepgram's UtteranceEnd event eliminates the polling loop and gives a semantically correct turn boundary rather than an audio-gap heuristic.

**20-second fallback timeout**

Without a fallback, a Deepgram WebSocket drop mid-turn causes `wait_for_turn_end()` to hang indefinitely, silently freezing the call. The 20-second asyncio timeout returns whatever partial transcript was accumulated, logs a warning, and lets the call continue. Combined with `ensure_connected()`, this makes the pipeline resilient to the connection drops observed during testing.

**Scenario-based prompts over free-form**

Each scenario gives GPT-4o a specific persona (name, DOB, phone, insurance, medications), a concrete goal, and behavioral constraints. This produces reproducible conversations that deliberately target specific failure modes - Scenario 7 pushes for cost estimates to probe hallucination; Scenario 8 uses a confused elderly patient; Scenario 17 tests whether a "we'll follow up" promise is ever actually honored. Free-form testing would rarely surface these edge cases consistently.

**Three-tier scenario fallback in lookup**

`make_call()` and the WebSocket handler both try `get_scenario()` first, then `get_canonical_scenario()`, then `get_jailbreak_scenario()`. This lets a single flat ID namespace cover all 19 scenarios without the caller needing to specify which set a given ID belongs to.

## Audio Format Reference

Twilio Media Streams use mulaw (G.711 u-law) at 8kHz, 8-bit, mono - standard telephone audio.

- **Inbound (agent -> Deepgram):** raw mulaw bytes from Twilio base64-decoded and forwarded directly - Deepgram accepts mulaw natively
- **Outbound (OpenAI TTS -> Twilio):** OpenAI TTS outputs MP3 at 24kHz stereo -> ffmpeg resamples to mulaw 8kHz mono -> chunked to 160 bytes (80ms) -> base64-encoded -> wrapped in Twilio media JSON envelope

## Repository Structure

```
voice-bot/
+-- main.py                  # FastAPI server, Twilio webhook, WebSocket handler
+-- call_runner.py           # CLI: outbound call triggering and sequencing
+-- patient_agent.py         # GPT-4o patient brain + OpenAI TTS synthesis
+-- audio_pipeline.py        # Deepgram STT, reconnect logic, turn detection
+-- logger.py                # JSON transcript logging
+-- bug_analyzer.py          # Post-call GPT-4o bug detection pass
+-- register_patients.py     # Playwright automation for intake form registration
+-- scenarios/
|   +-- __init__.py
|   +-- scenarios.py         # All 19 scenario definitions
+-- transcripts/             # Auto-generated call JSON logs
+-- BUG_REPORT.md
+-- CANONICAL_TESTS.md
+-- FINDINGS_10_CALLS.md
+-- requirements.txt
+-- .env.example
+-- README.md
+-- ARCHITECTURE.md
```

## Environment Variables

| Variable | Description |
|---|---|
| `TWILIO_ACCOUNT_SID` | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | Twilio auth token |
| `TWILIO_PHONE_NUMBER` | Outbound caller ID in E.164 format |
| `TARGET_PHONE_NUMBER` | PivotPoint test line: `+18054398008` |
| `DEEPGRAM_API_KEY` | Deepgram API key (Nova-2) |
| `OPENAI_API_KEY` | OpenAI API key (GPT-4o + TTS) |
| `PUBLIC_URL` | ngrok HTTPS URL pointing to local server port 8000 |
