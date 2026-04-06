# Echo Stage 0 — Voice Pipeline Latency Tester

Measures end-to-end latency of the STT → LLM → TTS voice pipeline
on local hardware. This is a diagnostic tool, not a product.

## Prerequisites

1. **Python 3.10+**
2. **LM Studio** running at `localhost:1234` with a model loaded
   (e.g., Gemma 4B for fastest results)
3. **Kokoro-FastAPI** running at `localhost:8880`
   (start with `start-kokoro.bat` in the Kokoro-FastAPI directory)
4. A working **microphone**

## Install

```bash
cd echo_stage0
pip install -r requirements.txt
```

`faster-whisper` is the preferred STT engine. If it fails to install,
`openai-whisper` works as a fallback:

```bash
pip install openai-whisper
```

## Run

```bash
python main.py
```

Or use the launcher from the project root:

```bash
start-echo.bat
```

## Controls

| Key | Action |
|---|---|
| Hold SPACE | Record audio |
| Release SPACE | Process pipeline (STT → LLM → TTS → playback) |
| Q | Quit and show session summary |

## What It Measures

- **STT latency**: Time to transcribe your speech
- **LLM latency**: Time for the model to generate a response
- **TTS latency**: Time to synthesize audio from the response
- **Total latency**: End of speaking → start of audio playback (primary metric)

Target budget: **< 3 seconds** total roundtrip.

## Logs

Each run appends a JSON record to `logs/stage0_log.jsonl`.
Use these logs to compare performance across models and settings.

## Findings

_(This section will be populated after testing)_
