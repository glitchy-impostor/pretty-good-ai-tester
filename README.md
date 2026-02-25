# Pretty Good AI — Voice Bot

An automated voice bot that calls the Pretty Good AI test line, simulates realistic patient conversations, and identifies bugs in the AI agent.

## How It Works (Quick Version)

```
Twilio Outbound Call → Media Stream WebSocket → Deepgram STT → GPT-4o Patient → OpenAI TTS → Back to Twilio
```

Your server receives raw mulaw audio from Twilio in real time, transcribes it with Deepgram, generates a patient response with GPT-4o, synthesizes speech with OpenAI TTS, and streams it back.

---

## Prerequisites

- Python 3.10+
- `ffmpeg` installed (`brew install ffmpeg` / `apt install ffmpeg`)
- A [Twilio account](https://twilio.com) with a phone number
- A [Deepgram account](https://deepgram.com) (free tier works)
- An [OpenAI account](https://platform.openai.com)
- [ngrok](https://ngrok.com) for a public tunnel

---

## Setup

### 1. Clone and install

```bash
git clone <repo-url>
cd voice-bot
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in:

| Variable | Description |
|---|---|
| `TWILIO_ACCOUNT_SID` | From [Twilio console](https://console.twilio.com) |
| `TWILIO_AUTH_TOKEN` | From Twilio console |
| `TWILIO_PHONE_NUMBER` | Your Twilio number in E.164 format, e.g. `+18005551234` |
| `DEEPGRAM_API_KEY` | From [Deepgram console](https://console.deepgram.com) |
| `OPENAI_API_KEY` | From [OpenAI platform](https://platform.openai.com/api-keys) |
| `TARGET_PHONE_NUMBER` | `+18054398008` (already set in example) |
| `PUBLIC_URL` | Your ngrok URL — set this **after** step 4 |

### 3. Start ngrok

```bash
ngrok http 8000
```

Copy the `https://...ngrok-free.app` URL and set it as `PUBLIC_URL` in your `.env`.

### 4. Start the server

```bash
python main.py
```

You should see: `Uvicorn running on http://0.0.0.0:8000`

Verify it's accessible: `curl https://your-ngrok-url.ngrok-free.app/health`

---

## Running Calls

### Single scenario

```bash
python call_runner.py --scenario 1
```

### All 10 scenarios (recommended: run in order)

```bash
python call_runner.py --all --delay 90
```

The `--delay` flag (default: 90 seconds) adds a pause between calls so you don't flood the test line.

### Specific range

```bash
python call_runner.py --range 1 5
```

---

## Analyzing Results

After calls complete, transcripts are saved to `transcripts/`. Run the bug analyzer:

```bash
python bug_analyzer.py
```

This generates:
- `transcripts/analyses.json` — raw analysis data
- `BUG_REPORT.md` — consolidated, severity-ranked bug report

---

## Scenarios

| # | Name | What It Tests |
|---|------|---------------|
| 1 | Simple Scheduling | Happy-path new patient flow |
| 2 | Rescheduling | Existing appointment changes |
| 3 | Cancellation | Cancel without rescheduling |
| 4 | Medication Refill (Simple) | Common drug, cooperative patient |
| 5 | Medication Refill (Complex) | Specialty drug, hallucination risk |
| 6 | Office Hours Inquiry | Factual accuracy vs. hallucination |
| 7 | Insurance Inquiry | Appropriate deflection vs. making up costs |
| 8 | Confused Elderly Patient | Patience, clarification, repetition handling |
| 9 | Multi-Part Request | Compound intent, state tracking |
| 10 | Adversarial/Edge Case | Ambiguity, vague inputs, graceful failure |

---

## Project Structure

```
voice-bot/
├── main.py              # FastAPI server + Twilio webhook + WebSocket handler
├── call_runner.py       # CLI tool to trigger outbound calls
├── patient_agent.py     # GPT-4o patient brain + OpenAI TTS
├── audio_pipeline.py    # Deepgram STT + mulaw/PCM audio conversion
├── bug_analyzer.py      # Post-call bug detection via GPT-4o
├── logger.py            # Conversation transcript logging
├── scenarios/
│   └── scenarios.py     # 10 test scenario definitions
├── transcripts/         # Auto-generated call transcripts (JSON)
├── BUG_REPORT.md        # Generated bug report (after analysis)
├── requirements.txt
├── .env.example
└── README.md
```

---

## Troubleshooting

**Call connects but no audio plays**
- Check that ffmpeg is installed: `ffmpeg -version`
- Verify your ngrok URL is correct in `.env` and the server is running

**Deepgram not transcribing**
- Confirm `DEEPGRAM_API_KEY` is valid
- Check the server logs for Deepgram error messages

**Twilio error on call creation**
- Verify `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_PHONE_NUMBER`
- Ensure your Twilio account has funds and the number is SMS/voice capable

**WebSocket disconnects immediately**
- Make sure PUBLIC_URL in `.env` matches your current ngrok session (ngrok URLs change each restart unless you have a paid plan)

---

## Cost Estimates

| Service | Est. for 10 calls (~3 min each) |
|---|---|
| Twilio (outbound) | ~$1.50 |
| Deepgram STT | ~$0.15 |
| OpenAI GPT-4o | ~$1–2 |
| OpenAI TTS | ~$0.75 |
| **Total** | **~$3–5** |
