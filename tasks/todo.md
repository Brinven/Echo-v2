# Echo Stage 4 — Memory Reads Build Tracker

## Completed
- [x] P1: Refactor llm.py — dynamic system prompt (parameter, not hardcoded)
- [x] P2: Create memory_reader.py — session-start + per-turn retrieval
- [x] P3: Update config.json — add memory_read settings
- [x] P4: Update main.py — wire retrieval into startup + conversation loop
- [x] P5: Update logger.py — no changes needed (kwargs pass-through handles new fields)
- [x] P6: Verify — syntax OK, imports OK, unit tests pass

## Next
- [ ] Live test — run session with memories present
- [ ] Verify latency: first audio ≤ 1.0s avg, memory retrieval < 100ms
- [ ] Test A: cross-session recall (subtle)
- [ ] Test B: preference recall
- [ ] Test C: empty memory — fresh install behavior
- [ ] Test D: latency within budget
