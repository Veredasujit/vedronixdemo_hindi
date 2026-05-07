# Voice Agent (Pipecat + Twilio Media Streams)

Voice AI agent built with Pipecat for telephony-style realtime conversations.

The primary implementation is in `smartflo_agent.py`, with Deepgram STT, OpenAI LLM,
ElevenLabs TTS, tool-calling for appointment creation, transcript/audio capture,
and latency/metrics observers.

## What This Repository Contains

- `smartflo_agent.py`: Main production bot (Twilio-compatible websocket flow + tool calling).
- `tool_calling.py`: Tool schema and API call function (`create_data`).
- `MetricsLogObserver.py`: Local wrapper for Pipecat metrics observer.
- `UserBotLatencyLogObserver.py`: Local wrapper for Pipecat latency observer.
- `prompt.txt`: System prompt consumed by `smartflo_agent.py` and `agent.py`.
- `recording/`: Saved transcript JSON files and generated recordings/logs.

## Prerequisites

- Python 3.10+
- A virtual environment (this repo commonly uses `.venv`)
- API keys:
  - `OPENAI_API_KEY`
  - `DEEPGRAM_API_KEY`
  - `ELEVENLABS_API_KEY`
  - `ELEVENLABS_VOICE_ID`
- Optional clinic auth keys for appointment API integration:
  - `CLINIC_001_API_KEY`
  - `CLINIC_001_API_SECRET`

## Setup

### 1. Create/Activate Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Dependencies

Using `pyproject.toml` (recommended):

```bash
pip install -e .
```

Or direct install from requirements:

```bash
pip install -r requirements.txt
```

### 3. Configure Environment

Create `.env` in project root:

```env
OPENAI_API_KEY=sk-...
DEEPGRAM_API_KEY=...
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
ELEVENLABS_MODEL=eleven_multilingual_v2
SMARTFLO_AUTHORIZATION=...
SMARTFLO_API_TOKEN=..

# Optional for appointment API flow in tool_calling.py
DEFAULT_CLINIC_ID=clinic_001
CLINIC_001_API_KEY=...
CLINIC_001_API_SECRET=...
```

## Running

### Main Bot (`smartflo_agent.py`)

```bash
source .venv/bin/activate
export CLINIC_001_API_KEY=...
export CLINIC_001_API_SECRET=
python smartflo_agent.py  --transport twilio
```

#ngrok to expose port
in other terminal run ngrok http 7860

Note: Each script uses Pipecat's runner entrypoint (`from pipecat.runner.run import main`).
Pass runner/transport options as needed for your deployment/runtime.

## Runtime Behavior

- Parses telephony websocket metadata via Pipecat `parse_telephony_websocket`.
- Uses `TwilioFrameSerializer` at 8kHz settings for telephony audio.
- Saves merged recordings and transcript JSON into `recording/`.
- Emits metrics and latency logs (for the variants that enable observers).

## Tool Calling (Appointment Creation)

`smartflo_agent.py` registers the `create_data` function from `tool_calling.py`.
When sufficient patient details are collected, the bot can call the external API.

Current expected fields:

- name
- symptom
- days
- preferred_time (`morning` or `evening`)

## Quick Troubleshooting

- No audio reply:
  - Check `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`.
- Model errors:
  - Verify `OPENAI_API_KEY` and model availability (`gpt-4o-mini`).
- Function call failing:
  - Confirm clinic keys and target API endpoint reachability.
- No logs/transcripts:
  - Ensure write permission to `recording/` directory.
