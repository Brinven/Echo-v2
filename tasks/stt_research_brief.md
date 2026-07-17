# Maat research brief — best local STT for Echo (2026-07)

## Topic / question
What is the best locally-run speech-to-text model available RIGHT NOW (mid-2026) to replace
Whisper `base` in a real-time voice assistant on Windows, judged on accuracy (especially
proper nouns), latency, VRAM footprint, and Windows/Python integration pain?

## The system it's going into (hard constraints)
- Windows 11, Python 3.11 virtualenv. **torch in this venv is deliberately CPU-only and
  must stay that way** — today's STT (faster-whisper) does CUDA through CTranslate2, which
  is torch-independent. A candidate must either (a) run CUDA without CUDA-torch
  (CTranslate2 / ONNX Runtime / its own runtime), or (b) run as a SEPARATE local server
  process in its own environment (the app already uses this pattern for TTS — a
  Kokoro-FastAPI server on a local port). Candidates that force CUDA torch into the app
  venv are disqualified unless the server route works.
- GPU: RTX 5080 16GB, SHARED with a 12B LLM (~8GB resident) and often an image-gen app.
  Realistic STT VRAM budget: **≤ ~2–3GB**, less is better. CPU-only candidates are
  interesting if they hit the latency bar.
- Input: one utterance at a time (push-to-talk / VAD-segmented), 16 kHz mono, typically
  2–10s. **Streaming/partial results NOT required.** Latency bar: ≤ ~0.5s per short
  utterance on this hardware (current Whisper base: 0.19s — there is headroom to spend).
- English only is fine. 100% local/offline after a one-time model download — no cloud APIs.
- Audio comes from two mic characters: a desk USB mic and phone recordings (AAC/Opus
  decoded to 16k). Robustness across both matters.

## What's actually broken today (the reason for the search)
Whisper `base` misses proper nouns and casual fast speech — e.g. "we ate at a place called
Sushi Hayo" transcribed as "we read a place called Sushi Hayo". The fix wanted is a model
with materially better word error rate, not prompt tricks.

## Evaluate at minimum (plus anything newer)
- Whisper family current state: large-v3, large-v3-turbo, distil-large-v3 (and any v4/2026
  successors), via faster-whisper/CTranslate2.
- NVIDIA Parakeet / Canary family (NeMo) — note the Windows + dependency story honestly.
- Kyutai STT, Moonshine, SenseVoice, Voxtral (Mistral), Qwen audio/omni line, Granite
  Speech — and any 2025–2026 releases that beat these.
- Check the Hugging Face Open ASR leaderboard's current top entries for models that meet
  the constraints above.

## Scoring dimensions (in order)
1. English WER on real conversational speech (leaderboard + independent reports), with
   special attention to proper nouns / named entities.
2. Latency for a 5s utterance on a modern consumer GPU (or CPU where relevant).
3. VRAM/RAM footprint.
4. Windows integration pain: pip-installable? CTranslate2/ONNX build? Or research-grade
   Linux-first tooling? Server-mode option?
5. Bonus features: custom-vocabulary / hotword boosting (e.g. household names), word-level
   confidence, punctuation quality, hallucination behavior on silence/noise.

## Deliverable
A ranked shortlist (top 3–5) with: model, WER evidence, expected latency + VRAM on an RTX
5080, exact integration route on Windows (venv-safe vs separate-server), and a single
clear recommendation with runner-up. Flag anything where the Windows story is unproven.
