"""
Audio pipeline — handles:
- mulaw 8kHz <-> PCM 16-bit conversion (Twilio's format)
- Deepgram streaming STT with auto-reconnect on dropped connections
- Event-driven turn detection via Deepgram UtteranceEnd
"""

import asyncio
try:
    import audioop
except ImportError:
    import audioop_lts as audioop  # Python 3.13+ backport
import os
import warnings
from typing import Callable

from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
)

warnings.filterwarnings("ignore", category=DeprecationWarning)

# Deepgram client singleton
_deepgram_client: DeepgramClient | None = None


def get_deepgram_client() -> DeepgramClient:
    global _deepgram_client
    if _deepgram_client is None:
        _deepgram_client = DeepgramClient(
            api_key=os.environ["DEEPGRAM_API_KEY"],
            config=DeepgramClientOptions(verbose=False),
        )
    return _deepgram_client


def mulaw_to_pcm(mulaw_bytes: bytes) -> bytes:
    """Convert 8-bit mulaw (Twilio format) to 16-bit PCM."""
    return audioop.ulaw2lin(mulaw_bytes, 2)


def pcm_to_mulaw(pcm_bytes: bytes) -> bytes:
    """Convert 16-bit PCM to 8-bit mulaw (for sending back to Twilio)."""
    return audioop.lin2ulaw(pcm_bytes, 2)


async def mp3_to_mulaw_8khz(mp3_bytes: bytes) -> bytes:
    """
    Convert MP3 audio to mulaw 8kHz for Twilio.
    Uses ffmpeg via subprocess.
    """
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(mp3_bytes)
        mp3_path = f.name

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", mp3_path,
                "-ar", "8000",
                "-ac", "1",
                "-f", "mulaw",
                "-acodec", "pcm_mulaw",
                "pipe:1",
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg error: {result.stderr.decode()}")
        return result.stdout
    finally:
        os.unlink(mp3_path)


class TurnDetector:
    """
    Event-driven turn detection using Deepgram's UtteranceEnd event.
    No polling, no silence timers — Deepgram's VAD fires the signal.
    """

    def __init__(self):
        self._accumulated: list[str] = []
        self._utterance_end_event = asyncio.Event()

    def add_transcript(self, text: str):
        self._accumulated.append(text)

    def signal_utterance_end(self):
        self._utterance_end_event.set()

    def get_full_text(self) -> str:
        return " ".join(self._accumulated).strip()

    def clear(self):
        self._accumulated = []
        self._utterance_end_event.clear()

    async def wait_for_turn_end(self, timeout: float = 20.0) -> str:
        """
        Block until Deepgram signals end-of-utterance.

        Falls back after `timeout` seconds so a dropped Deepgram connection
        never hangs the call indefinitely — returns whatever was accumulated.
        """
        try:
            await asyncio.wait_for(self._utterance_end_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            accumulated = self.get_full_text()
            if accumulated:
                print("  [TurnDetector] UtteranceEnd timeout — using partial transcript")
            else:
                print("  [TurnDetector] UtteranceEnd timeout — no speech detected")
        text = self.get_full_text()
        self.clear()
        return text


class DeepgramStreamingSTT:
    """
    Manages a live Deepgram connection for streaming speech-to-text.

    Handles dropped connections gracefully:
    - Silently stops sending on disconnect (no log spam)
    - Auto-reconnects before the next agent turn via ensure_connected()
    """

    def __init__(
        self,
        on_transcript: Callable[[str], None],
        on_utterance_end: Callable[[], None],
    ):
        self.on_transcript = on_transcript
        self.on_utterance_end = on_utterance_end
        self.connection = None
        self._is_connected = False

    def _make_options(self) -> LiveOptions:
        return LiveOptions(
            model="nova-2",
            language="en-US",
            encoding="mulaw",
            sample_rate=8000,
            channels=1,
            punctuate=True,
            interim_results=True,
            utterance_end_ms="1000",
            vad_events=True,
            endpointing=300,
        )

    async def connect(self):
        dg = get_deepgram_client()
        self.connection = dg.listen.asynclive.v("1")

        async def on_message(self_inner, result, **kwargs):
            try:
                sentence = result.channel.alternatives[0].transcript
                if sentence.strip() and result.is_final:
                    self.on_transcript(sentence.strip())
            except Exception:
                pass

        async def _on_utterance_end(self_inner, utterance_end, **kwargs):
            self.on_utterance_end()

        async def on_error(self_inner, error, **kwargs):
            # Only print once — prevents the flood of send() failure messages
            if self._is_connected:
                print("  [Deepgram] Connection lost — will reconnect on next turn")
                self._is_connected = False

        async def on_close(self_inner, close, **kwargs):
            self._is_connected = False

        self.connection.on(LiveTranscriptionEvents.Transcript, on_message)
        self.connection.on(LiveTranscriptionEvents.UtteranceEnd, _on_utterance_end)
        self.connection.on(LiveTranscriptionEvents.Error, on_error)
        self.connection.on(LiveTranscriptionEvents.Close, on_close)

        await self.connection.start(self._make_options())
        self._is_connected = True

        # Keepalive: Deepgram drops idle connections after ~10s without data.
        # Ping every 8s to keep the connection alive during agent speech pauses.
        asyncio.create_task(self._keepalive())

    async def _keepalive(self):
        """Ping Deepgram every 8s to prevent idle connection timeout."""
        while self._is_connected:
            await asyncio.sleep(8)
            if self._is_connected and self.connection:
                try:
                    await self.connection.keep_alive()
                except Exception:
                    self._is_connected = False
                    break

    async def ensure_connected(self):
        """Reconnect if the connection dropped. Call before each listening window."""
        if not self._is_connected:
            print("  [Deepgram] Reconnecting...")
            try:
                if self.connection:
                    await self.connection.finish()
            except Exception:
                pass
            await self.connect()

    async def send_audio(self, mulaw_bytes: bytes):
        """Send raw mulaw audio. Silent no-op if disconnected."""
        if not self._is_connected or not self.connection:
            return
        try:
            await self.connection.send(mulaw_bytes)
        except Exception:
            # Suppress further spam — on_error already printed the notice
            self._is_connected = False

    async def close(self):
        self._is_connected = False
        if self.connection:
            try:
                await self.connection.finish()
            except Exception:
                pass