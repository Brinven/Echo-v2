# Ib-Lite Build — tasks/todo.md (Stage 5, Part 1)

**Replaces** Echo's external Hindsight HTTP memory with a self-contained SQLite memory
(Core/Policy/Preference/Fact/Episodic + FTS5 + sqlite-vec + 12B significance gate).
Voice pipeline untouched; first-audio must stay ≤ 1.3s.

Plan: `C:\Users\zwolf\.claude\plans\binary-hugging-toast.md`
Supersedes the prior Stage 4 tracker (Stage 4 complete).

## Decisions locked
- Significance gate: per-turn, background thread, single-flight (never blocks first-audio).
- Scope: MVP milestones 1–8 only (no CLI inspector / decay / mood-tone).
- Package at `echo_stage0/ib_lite/`; DB at `echo_stage0/echo.db`.
- sqlite-vec via pip package (sqlite-vec 0.1.9), not hand-placed vec0.dll.
- Old Hindsight files archived (not hard-deleted); gate subsumes Path A "remember that".
- Do NOT alter personal Core/Policy seeds without explicit say-so.

## Checklist
- [x] 0. Install `sqlite-vec` (0.1.9); confirm load + `vec_distance_cosine` on Windows.
- [x] 1. M1 Schema + Core inject: `db.py`, `embedder.py`, copy schema, `start_session`, `build_context_block`.
- [x] 2. M2 Preference r/w: live gate emits preference; surfaces in context block.
- [x] 3. M3 Fact write: gate → entity/attribute/value → validate (+1 retry, else log) → `fact_memory` + embed.
- [x] 4. M4 Fact read: `retrieval.fact_search` hybrid (BM25+cosine+recency) → per-turn injection.
- [x] 5. M5 Episodic write: `end_session` maps summary → `episodic_memory` (before `ended_at`).
- [x] 6. M6 Episodic read: `retrieval.episodic_search` joins Fact results in per-turn block.
- [x] 7. M7 Pipeline integration: `main.py` swap; archived Hindsight files; no OpenMemory/Hindsight imports remain.
- [x] 8. M8 Smoke + latency: `smoke_ib_lite.py` green (incl. live gate); per-turn read ~13ms (≤100ms).
- [x] 9. Updated `requirements.txt` (pinned sqlite-vec + sentence-transformers); CLAUDE.md notes updated.

## Review

**Status: COMPLETE.** All 8 milestones built and verified against the real Gemma 4 12B QAT.

What shipped:
- New self-contained package `echo_stage0/ib_lite/` (db, embedder, schema, significance,
  retrieval, ib_lite facade + schema.sql). DB at `echo_stage0/echo.db` (created on first run).
- `main.py` rewired: Core+Policy injected at start, per-turn Fact/Episodic retrieval,
  background significance gate write, episodic write at sign-off. `llm.py` untouched (already
  took `system_prompt=`). Voice pipeline untouched.
- Old Hindsight files archived to `echo_stage0/archived-hindsight-2026-06-24/`.

Verified:
- Offline: Core/Policy inject, FTS-sync-on-UPSERT, hybrid retrieval, episodic write-before-end.
- Live (Gemma 4 12B QAT): gate saves fact/preference, rejects smalltalk, 0.6–1.3s per call.
- Latency: per-turn read avg 12.6ms (budget 100ms); gate is off the hot path.

**Key bug fixed during build** — the gate model is a *thinking* model: it spent the whole
token budget in `reasoning_content` and returned empty `content` (finish_reason=length).
Fix: `reasoning_effort="none"` on the gate call (the only knob that worked for this Gemma
template — `low` / `enable_thinking=false` did not). See `significance.py` comment.

**Correctness improvement over PRD stub** — used explicit `ON CONFLICT(entity,attribute)
DO UPDATE` instead of `INSERT OR REPLACE`, so the external-content FTS5 stays in sync via the
AFTER UPDATE trigger (REPLACE = delete+insert would orphan FTS rows; recursive_triggers must
stay OFF or `fact_touch` loops).

Deferred (PRD Nice-to-Haves, not built): CLI inspector, confidence decay, mood-driven tone.
Next: Stage 5 Part 2 (personality) and Part 3 (web search).
