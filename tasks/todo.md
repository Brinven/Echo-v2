# Echo Stage 1 -- Build Tracker

## Completed
- [x] P1: TTS warm-start fix -- added warm-up call at init, confirmed latency varies with text length (0.09s-0.26s after warm-up vs 2.4s cold)
- [x] P2: LLM streaming with sentence-boundary TTS -- stream_sentences() generator, sentence boundary detection (min 8 words), AudioQueue for threaded playback
- [x] P3: State machine and conversation loop -- LISTENING/RECORDING/PROCESSING/SPEAKING/MUTED/SHUTDOWN states, VAD (webrtcvad) + PTT fallback, mute toggle
- [x] Updated logger with Stage 1 fields (first_audio_s, llm_first_token_s, etc.)
- [x] Created vad.py, state.py, audio_queue.py
- [x] Updated llm.py with streaming + conversation history
- [x] Console UI with ANSI status box
- [x] All imports and syntax verified

## Next
- [ ] Live test -- run full conversation loop
- [ ] Verify first-audio < 1.5s target
- [ ] Test VAD speech detection (10 consecutive utterances)
- [ ] Test mute toggle
- [ ] Test graceful Q shutdown
- [ ] Update README.md with Stage 1 instructions
