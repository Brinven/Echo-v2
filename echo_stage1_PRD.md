# Echo Stage 1 — Conversational Voice Loop
## Product Requirements Document

---

## Context & Stage 0 Results

Stage 0 established baseline latency on this hardware:

| Component | Avg | Min | Max |
|---|---|---|---|
| STT (faster-whisper or openai-whisper) | 0.31s | 0.16s | 0.43s |
| LLM (Gemma-4-E4B via LM Studio) | 2.38s | 2.26s | 2.47s |
| TTS (Kokoro) | 2.16s | 2.14s | 2.17s |
| **Total (sequential)** | **4.88s** | **4.76s** | **5.00s** |

**Key finding:** LLM and TTS are stacked sequentially. With streaming,
TTS can begin on the first complete sentence while LLM is still generating.
Target: **first audio within 1.5s of speech end.**

**TTS anomaly:** Kokoro latency is suspiciously flat (2.14–2.17s) regardless
of response length. This suggests per-call re-initialization rather than a
warm model. **Investigate and fix this before implementing streaming** —
it may be the single biggest win.

---

## Hardware & Runtime (unchanged from Stage 0)

| Item | Spec |
|---|---|
| OS | Windows 11 |
| CPU | AMD Ryzen 9900x |
| RAM | 64GB DDR5 |
| GPU | NVIDIA RTX 5080 (16GB VRAM) |
| LLM | LM Studio, localhost:1234, Gemma-4-E4B-Uncensored |
| STT | Auto-detected from Stage 0 (faster-whisper preferred) |
| TTS | Kokoro (kokoro-onnx preferred) |

---

## Primary Goal

Transform the Stage 0 single-shot tester into a continuous, natural
voice conversation loop that feels responsive — specifically, it must
not feel like a smart speaker (Alexa-class). The bar is:
- First word of audio within ~1.5s of the user finishing speaking
- No awkward dead air while waiting for a full LLM response
- Clean handling of the mic-while-speaking problem

---

## Priority Order for Implementation

Claude Code must tackle these in sequence, not in parallel:

### Priority 1 — Fix TTS Warm-Start (Before Anything Else)

Investigate why Kokoro latency is flat at ~2.16s regardless of response length.

Expected behavior: a 10-word response should be significantly faster than
a 50-word response. Flat latency means initialization overhead is dominating.

Fix: initialize Kokoro once at application startup and keep the model
in memory for the lifetime of the session. Every call after the first
should only pay synthesis cost, not load cost.

Verify the fix by running 3 test syntheses after startup and confirming
latency drops and varies with text length.

### Priority 2 — LLM Streaming with Sentence-Boundary TTS

Replace the full-response LLM call with streaming. Feed completed
sentences to TTS as they arrive, not after the full response.

**Sentence boundary detection rules:**
- Split on: `.` `!` `?` followed by whitespace or end of stream
- Minimum chunk size before sending to TTS: 8 words
  (prevents TTS being called with fragments like "The Romans")
- Maximum buffer wait: 3 seconds
  (if no sentence boundary found in 3s, flush the buffer anyway)
- Final chunk: send whatever remains when the stream ends,
  even if it doesn't end with punctuation

**Audio queue:**
- TTS chunks feed into an ordered audio queue
- Audio player consumes the queue sequentially
- LLM streaming, TTS synthesis, and audio playback run as
  concurrent tasks (asyncio or threading — choose based on
  what the Kokoro API supports cleanly)

**Timing target:**
- t0: user stops speaking
- t_first_audio: ≤ 1.5s after t0
- Log t_first_audio separately in JSONL — this is the new primary metric

### Priority 3 — Continuous Conversation Loop with State Machine

Replace the single-shot PTT test with a continuous loop governed
by this state machine:

```
STATES:
  LISTENING  → VAD detects speech onset      → RECORDING
  LISTENING  → SPACE held (PTT)              → RECORDING
  RECORDING  → VAD detects silence (>700ms)  → PROCESSING
  RECORDING  → SPACE released (PTT)          → PROCESSING
  PROCESSING → pipeline complete             → SPEAKING
  SPEAKING   → TTS audio queue empty         → LISTENING
  SPEAKING   → M key pressed                 → MUTED (audio stops)
  MUTED      → M key pressed again           → LISTENING
  ANY STATE  → Q key                         → SHUTDOWN (graceful)
```

**Critical:** mic input (VAD and PTT) must be DISABLED during SPEAKING state.
This prevents Echo from hearing its own TTS output and triggering a response
to itself. The user cannot interrupt in Stage 1 — that is acceptable.
(Barge-in / output mute is a Stage 2 feature.)

---

## VAD Implementation

Use `webrtcvad` (Google's WebRTC VAD) — it is lightweight, fast, and
well-tested on Windows.

```
pip install webrtcvad
```

**VAD configuration:**
- Aggressiveness: mode 2 (0=least aggressive, 3=most)
  Mode 2 balances sensitivity without too many false positives indoors
- Frame size: 30ms (webrtcvad requirement)
- Sample rate: 16000Hz (matches Whisper — no resampling needed)
- Speech onset: 3 consecutive voiced frames → transition to RECORDING
- Silence detection: 700ms of silence after speech → transition to PROCESSING
  (700ms chosen to allow natural pauses mid-sentence without cutting off)

**PTT override:**
PTT (SPACE key) forces RECORDING regardless of VAD state.
SPACE release forces PROCESSING regardless of VAD state.
PTT takes priority over VAD at all times.

**VAD failure mode:**
If `webrtcvad` is not installed or fails to initialize, fall back to
PTT-only mode automatically. Print a clear warning:
"VAD unavailable — running PTT-only mode. Install webrtcvad for hands-free operation."
Do not crash.

---

## Mute Feature (Option A — Input Mute)

- Toggle key: **M**
- In MUTED state: VAD is disabled, PTT is disabled, microphone is not read
- Echo does NOT stop mid-sentence if muted while SPEAKING —
  current audio completes, then system enters MUTED (does not return to LISTENING)
- Visual indicator in console: `[MUTED]` shown in status line
- Mute persists until M is pressed again
- Mute state is NOT saved between sessions (always starts unmuted)

---

## System Prompt (Stage 1)

Slightly expanded from Stage 0 but still minimal. Personality is Stage 5.

```
You are Echo, a helpful voice assistant. You are running locally on the
user's PC. Keep responses conversational and concise — you are speaking
aloud, not writing. Avoid lists, bullet points, and markdown formatting.
Prefer 2-4 sentences unless the user explicitly asks for more detail.
```

**Do not expand this prompt.** Personality, memory, and persona are
added in later stages. Complexity in the system prompt caused problems
in previous Echo attempts.

---

## Console UI

Replace the Stage 0 report with a live status display:

```
╔══════════════════════════════════════════╗
║  ECHO — Stage 1                          ║
╠══════════════════════════════════════════╣
║  Status:  LISTENING                      ║
║  VAD:     active                         ║
║  Mute:    off                            ║
╠══════════════════════════════════════════╣
║  Last:    STT 0.18s │ first audio 0.89s  ║
╚══════════════════════════════════════════╝
  [SPACE: PTT]  [M: mute]  [Q: quit]
```

Status line updates in-place (no scrolling) using ANSI escape codes or
the `curses` library — whichever works more reliably on Windows.

Transcript and response are printed below the status box as a scrolling
conversation log so the user can see what Echo heard and said.

---

## Logging (extends Stage 0 JSONL format)

Add these fields to the existing stage0_log.jsonl format:
(same file, same structure — Stage 0 and Stage 1 runs are comparable)

```json
{
  "stage": 1,
  "timestamp": "ISO8601",
  "model": "model-name",
  "stt_backend": "faster-whisper|openai-whisper",
  "tts_backend": "kokoro-onnx|kokoro",
  "vad_mode": "webrtcvad|ptt-only",
  "input_duration_s": 0.0,
  "stt_latency_s": 0.0,
  "llm_first_token_s": 0.0,
  "llm_full_response_s": 0.0,
  "tts_first_chunk_s": 0.0,
  "first_audio_s": 0.0,
  "total_latency_s": 0.0,
  "passed_budget": true,
  "transcript": "",
  "response_full": ""
}
```

`first_audio_s` is the new primary metric (replaces `total_latency_s` as
the headline number).

---

## File Structure

Extend Stage 0 structure — do not delete Stage 0 files:

```
echo_stage0/          ← rename to echo/ or keep as-is
├── main.py           ← REPLACE with Stage 1 conversation loop
├── stt.py            ← extend (interface unchanged)
├── llm.py            ← extend with streaming support
├── tts.py            ← fix warm-start, add chunk synthesis method
├── vad.py            ← NEW: webrtcvad wrapper with PTT fallback
├── audio_queue.py    ← NEW: ordered audio playback queue
├── state.py          ← NEW: conversation state machine
├── timer.py          ← unchanged
├── logger.py         ← extend with new fields
├── requirements.txt  ← add webrtcvad
├── README.md         ← update with Stage 1 instructions
└── logs/
    └── stage0_log.jsonl  ← same file, Stage 1 adds records with stage:1
```

---

## Success Criteria

Stage 1 is complete when:

1. **TTS warm-start fixed**: third+ synthesis call is measurably faster
   than first call, and latency varies with text length
2. **First audio ≤ 1.5s**: at least 7 out of 10 runs achieve first audio
   within 1.5s of speech end (measured and logged)
3. **VAD works indoors**: 10 consecutive utterances trigger correctly
   without false positives from room noise (or PTT fallback is active
   with clear explanation of why VAD failed)
4. **No self-triggering**: Echo never responds to its own TTS output
5. **Mute works**: M key reliably pauses and resumes input
6. **Graceful shutdown**: Q key completes current response, writes
   session summary to log, exits cleanly
7. **Conversation feels natural**: subjective test — have a 5-turn
   conversation and it should not feel like talking to Alexa

---

## Notes for Claude Code

- Reuse Stage 0 module interfaces exactly. If you need to change an
  interface, document why in README.md under "Stage 1 Changes."
- The TTS warm-start fix is Priority 1. Do not implement streaming
  until you have confirmed Kokoro stays warm between calls.
- webrtcvad requires 16-bit signed integer audio frames. Ensure the
  audio capture pipeline matches this format before passing to VAD.
- On Windows, keyboard input handling for non-blocking SPACE/M/Q
  detection while VAD is running requires care. Use `keyboard` library
  or `pynput` — do not use `input()` which blocks. Test this specifically.
- The "Alexa bar" is real: if there is more than ~1.5s of silence after
  the user stops speaking before any audio starts, it will feel broken
  to non-technical users. First audio latency is the metric that matters.
