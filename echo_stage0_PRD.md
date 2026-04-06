# Echo Stage 0 — Voice Pipeline Latency Tester
## Product Requirements Document

---

## Purpose

Stage 0 is a **diagnostic instrument**, not a prototype. Its sole job is to measure
the end-to-end latency of the voice pipeline on this specific hardware so that
architectural decisions for Stages 1–5 are grounded in real numbers, not assumptions.

Nothing built here is throwaway — the STT, LLM, and TTS wrappers written here
become the foundation that Stage 1 builds on.

---

## Hardware & Runtime Context

| Item | Spec |
|---|---|
| OS | Windows 11 |
| CPU | AMD Ryzen 9900x |
| RAM | 64GB DDR5 |
| Primary GPU | NVIDIA RTX 5080 (16GB VRAM) |
| LLM Server | LM Studio, localhost:1234, OpenAI-compatible API |
| Target roundtrip budget | < 3 seconds (finish speaking → first word of audio out) |

---

## Auto-Detection Requirement

Claude Code built the STT and TTS components previously — the exact packages
installed are not confirmed. **The tester must auto-detect which implementations
are available** rather than hardcoding a specific library.

### STT — detect in this priority order:
1. `faster-whisper` (preferred — fastest on CUDA)
2. `openai-whisper`
3. If neither found: raise a clear error with install instructions for both,
   recommending `faster-whisper`

### TTS — detect in this priority order:
1. `kokoro-onnx` (ONNX runtime variant)
2. `kokoro` (HuggingFace variant)
3. If neither found: raise a clear error with install instructions

### LLM:
- Always use LM Studio's OpenAI-compatible endpoint: `http://localhost:1234/v1`
- Use the `openai` Python package
- On startup, call `/v1/models` and list available models — prompt user to select
  one if more than one is loaded, or auto-select if only one is active

---

## Functional Requirements

### Input
- **Push-to-talk via SPACE bar**
- Press and hold SPACE → recording begins
- Release SPACE → recording stops, pipeline executes
- Recording captured via `sounddevice` or `pyaudio` (auto-detect which is available)
- Sample rate: 16kHz mono (Whisper's native format — avoids resampling overhead)

### Pipeline Execution (sequential, synchronous for Stage 0)
After SPACE release, run in order:

```
1. STT   — transcribe captured audio → text
2. LLM   — send text to LM Studio → receive full response text
3. TTS   — synthesize response text → audio
4. PLAY  — play audio output
```

**Every transition is timestamped.** See Timing Requirements below.

### LLM Call
- System prompt: `"You are a helpful assistant. Keep responses to 1-3 sentences."`
- This is intentionally short — we are testing latency, not quality
- Request full completion (non-streaming) for Stage 0
  - Streaming will be added in Stage 1; we need the full-response baseline first
- Timeout: 30 seconds (fail loudly if exceeded)

### Output — Console Report
After audio playback completes, print a report in this exact format:

```
─────────────────────────────────────────
 ECHO STAGE 0 — LATENCY REPORT
─────────────────────────────────────────
 Input audio duration:   X.XXs
 
 STT latency:            X.XXs
   └─ Transcript: "[what you said]"
 
 LLM latency (full):     X.XXs
   └─ Response: "[first 80 chars of response...]"
 
 TTS latency:            X.XXs
 
 TOTAL (speech-to-audio): X.XXs   ← PRIMARY METRIC
 
 Budget:                 3.00s
 Status:                 ✅ PASS / ❌ FAIL
─────────────────────────────────────────
```

### Session Log
- Each run appends one JSON record to `./logs/stage0_log.jsonl`
- Record structure:
```json
{
  "timestamp": "ISO8601",
  "model": "model-name-from-lm-studio",
  "stt_backend": "faster-whisper|openai-whisper",
  "tts_backend": "kokoro-onnx|kokoro",
  "input_duration_s": 0.0,
  "stt_latency_s": 0.0,
  "llm_latency_s": 0.0,
  "tts_latency_s": 0.0,
  "total_latency_s": 0.0,
  "passed_budget": true,
  "transcript": "",
  "response_preview": ""
}
```
- Log directory created automatically if it doesn't exist
- Purpose: accumulate runs across models and settings for comparison

### Session Summary
When user presses `Q` to quit, print a summary across all runs in the current session:

```
─────────────────────────────────────────
 SESSION SUMMARY  (N runs)
─────────────────────────────────────────
 STT avg:    X.XXs   (min: X.XX  max: X.XX)
 LLM avg:    X.XXs   (min: X.XX  max: X.XX)
 TTS avg:    X.XXs   (min: X.XX  max: X.XX)
 TOTAL avg:  X.XXs   (min: X.XX  max: X.XX)
 Pass rate:  X/N runs under 3s budget
─────────────────────────────────────────
```

---

## Timing Requirements (Critical)

Timestamps must be captured at these exact moments:

| Timestamp | Event |
|---|---|
| `t0` | SPACE released (recording ends) |
| `t1` | STT call begins |
| `t2` | STT returns transcript |
| `t3` | LLM call begins |
| `t4` | LLM returns full response |
| `t5` | TTS call begins |
| `t6` | TTS returns audio |
| `t7` | Audio playback begins |

**Primary metric = t7 - t0** (from end of speaking to first word heard)

Note: t1 should equal t0 in practice. Any gap between them indicates
overhead in the pipeline handoff and should be visible in logs.

---

## Non-Requirements (explicitly out of scope)

- No memory system
- No persistent conversation
- No personality or system prompt beyond the minimal latency-test prompt
- No streaming LLM responses (deferred to Stage 1)
- No VAD (voice activity detection)
- No GUI
- No second GPU / 4060 integration
- No wake word

---

## Error Handling

| Error | Behavior |
|---|---|
| LM Studio not running | Clear message: "LM Studio not detected at localhost:1234. Please start LM Studio and load a model." |
| No model loaded in LM Studio | Clear message listing what was found at /v1/models |
| STT package missing | Clear message with install command |
| TTS package missing | Clear message with install command |
| Audio device not found | List available audio devices, ask user to select |
| LLM timeout (>30s) | Log the timeout, print FAIL, continue to next run |

---

## File Structure

```
echo_stage0/
├── main.py              # Entry point, PTT loop, report printer
├── stt.py               # STT wrapper with auto-detection
├── llm.py               # LLM wrapper (LM Studio / OpenAI-compatible)
├── tts.py               # TTS wrapper with auto-detection
├── timer.py             # Timestamp capture and latency calculation
├── logger.py            # JSONL session logger
├── requirements.txt     # All dependencies
├── README.md            # Setup and run instructions
└── logs/                # Auto-created, gitignored
    └── stage0_log.jsonl
```

---

## Success Criteria

Stage 0 is complete when:
1. A full PTT → STT → LLM → TTS → audio cycle runs without errors
2. The latency report prints correctly after each run
3. At least 5 consecutive runs complete and log to JSONL without crashing
4. The total latency is measured and reported accurately
   (accuracy matters more than the number itself — a 4s result that's
   correctly measured is more useful than an unchecked 2s result)

---

## Notes for Claude Code

- Write clean, modular code. The wrappers in stt.py, llm.py, and tts.py
  will be imported directly by Stage 1. Design their interfaces accordingly.
- Prefer explicit error messages over silent fallbacks.
- The JSONL log format must not change between Stage 0 and Stage 1 — 
  we want to compare runs across both stages.
- Do not optimize for latency yet. Measure first. Optimize in Stage 1.
- If you discover the STT or TTS packages behave differently than expected,
  document it in README.md under a "Findings" section.
