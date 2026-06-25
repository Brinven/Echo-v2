# PRD: Ib-Lite — Echo Memory Subsystem (Stage 5, Part 1)
**Project:** Echo / Ib-Lite
**Author:** Michael (Axly's Customs)
**Date:** 2026-06-24
**Status:** Ready for build

---

## 1. Overview

Echo is a local-first AI voice companion for a 2000 Jeep Wrangler TJ. Stages 0–4
delivered a complete voice pipeline (STT → LLM → TTS) with sub-1.3s first-audio
latency. The memory layer in those stages used OpenMemory/Hindsight, an external
HTTP memory server requiring a 31B cloud model for curation. That backend is being
**replaced entirely** by Ib-Lite.

**Ib-Lite** is a self-contained companion memory system that runs fully behind the
Gemma 4 12B QAT inference model — no external servers, no cloud calls, no curator
model. It is a single SQLite file with five typed memory tables, FTS5 keyword search,
and sqlite-vec semantic search. The 12B model manages its own memory via tool calls
and structured prompts.

This PRD covers the Ib-Lite implementation only. Personality design and web search
(Stage 5 Parts 2 and 3) follow once Ib-Lite is validated.

---

## 2. Goals

### Must-Have

- Replace `memory_reader.py` and all OpenMemory/Hindsight calls with Ib-Lite
- Five typed memory tables: Core, Preference, Fact, Episodic, Policy
- Core and Policy memory injected into system prompt at every session start
- Significance gate: 12B model decides what is worth saving after each turn
- Schema-aware write path: entity → attribute → value extraction for Fact memory
- Python-side validation gate with one retry on schema failure
- Hybrid retrieval for Fact and Episodic: FTS5 (BM25) + sqlite-vec (cosine) + recency decay
- Preference memory: keyed lookup, no retrieval needed
- Episodic memory: session-end summary written to table, retrieved per-turn
- End-of-session flow: Episodic write triggers on sign-off, before `ended_at` is set
- No external dependencies beyond SQLite, sqlite-vec, and all-MiniLM-L6-v2
- Smoke test: Core injection → write one Fact → retrieve it → session end → Episodic write

### Nice-to-Have

- CLI tool to inspect and manually edit memory tables (`python ib_lite_cli.py`)
- Confidence decay on Fact memories over time (reduce confidence on old, unconfirmed facts)
- `mood_signal` used to adjust Echo's tone at session start based on recent Episodic entries
- Manual memory correction: "Echo, forget that" and "Echo, remember that differently"

### Non-Goals

- Personality design or character voice (Stage 5 Part 2)
- Web search tool integration (Stage 5 Part 3)
- Audio/vision input pipeline changes (separate track)
- Migration of old OpenMemory data — start fresh
- Graph relationships between memories (possible in a future iteration)
- Multi-user support

---

## 3. Critical Accuracy

> **The voice pipeline is not touched.** Stages 0–4 are complete and working with
> sub-1.3s first-audio latency. Ib-Lite replaces ONLY the memory layer:
> `memory_reader.py`, the OpenMemory/Hindsight HTTP calls, and the Stage 3
> write paths. Do not modify `vad.py`, `stt.py`, `tts.py`, `kokoro.py`,
> or the core streaming/latency logic in `llm.py` (except to update the
> system prompt assembly to use Ib-Lite output). If it involves audio or
> latency, leave it alone.

> **Use 127.0.0.1, never localhost** for all local HTTP calls (LM Studio).
> DNS resolution of localhost adds ~2s per request on Windows. This is a
> known existing gotcha and must not be reintroduced.

> **Embedding generation is CPU-side.** all-MiniLM-L6-v2 runs on CPU, not
> GPU. It does not compete with the 12B for VRAM. Never move embeddings to GPU.

---

## 4. Data Models

See `ib_lite_schema.sql` for full schema with triggers, FTS5 virtual tables,
seed data, and hybrid retrieval reference query.

### Memory Type Summary

| Type       | Table              | Retrieval         | Written by            | Read at               |
|------------|--------------------|-------------------|-----------------------|-----------------------|
| Core       | `core_memory`      | Always injected   | Manual seed + model   | Session start         |
| Policy     | `policy_memory`    | Always injected   | Manual seed + model   | Session start         |
| Preference | `preference_memory`| Keyed lookup      | Significance gate     | Per-turn (on match)   |
| Fact       | `fact_memory`      | Hybrid (FTS5+vec) | Significance gate     | Per-turn              |
| Episodic   | `episodic_memory`  | Hybrid (FTS5+vec) | Session end           | Per-turn              |

### Write Path (Fact and Preference)

```
Turn ends
  → Significance Gate prompt (12B)
      → { save: false }  → discard
      → { save: true, type: "fact"|"preference"|"policy", ...fields }
          → Validation Gate (Python schema check)
              → PASS → INSERT OR REPLACE into appropriate table + embed
              → FAIL → retry once with correction prompt
                  → PASS → insert
                  → FAIL → log and discard
```

### Significance Gate Output Schema

```json
{
  "save": true,
  "type": "fact",
  "entity": "Michael",
  "attribute": "current_project",
  "value": "Echo — in-vehicle AI companion in the Jeep"
}
```

```json
{
  "save": true,
  "type": "preference",
  "key": "music_mood",
  "value": "likes driving music when on the highway"
}
```

```json
{
  "save": false
}
```

### Hybrid Retrieval Score

```
score = (0.5 × BM25) + (0.3 × cosine_similarity) + (0.2 × recency_decay)

recency_decay = 1.0 / (1.0 + days_since_update)   [Fact]
recency_decay = 1.0 / (1.0 + days_since_created)  [Episodic, stronger decay]
```

Weights are tunable constants in `ib_lite.py`. Start with 0.5/0.3/0.2.

---

## 5. MVP Milestones

| # | Milestone                       | Deliverable                                                                   | Done When                                                    |
|---|---------------------------------|-------------------------------------------------------------------------------|--------------------------------------------------------------|
| 1 | Schema + Core inject            | `ib_lite.py` init, schema creation, Core + Policy load into prompt block      | System prompt includes Core and Policy content at boot       |
| 2 | Preference read/write           | Significance gate → Preference write; keyed lookup on read                    | "Call me Michael" saves and surfaces in next session         |
| 3 | Fact write                      | Significance gate + schema extraction + validation gate → `fact_memory`       | Structured fact saves with entity/attribute/value            |
| 4 | Fact read                       | Hybrid retrieval (FTS5 + sqlite-vec + recency) → context injection            | Relevant facts surface per-turn, irrelevant ones don't       |
| 5 | Episodic write                  | Session-end summary → `episodic_memory` (replaces Stage 3 Path B)             | Each session produces an Episodic row                        |
| 6 | Episodic read                   | Per-turn Episodic retrieval joins Fact results in context block                | "Last time we talked about X" surfaces naturally             |
| 7 | Pipeline integration            | Remove `memory_reader.py`, remove OpenMemory imports, update `llm.py` prompt  | Echo runs end-to-end with no OpenMemory/Hindsight dependency |
| 8 | Smoke test + latency check      | Full session smoke test, confirm first-audio latency unchanged                | ≤1.3s first-audio maintained, smoke test green               |

---

## 6. Risks

| Risk                                        | Likelihood | Mitigation                                                                                   |
|---------------------------------------------|------------|----------------------------------------------------------------------------------------------|
| sqlite-vec DLL not available for Windows     | Medium     | Download pre-built `vec0.dll` from sqlite-vec releases; load via `conn.load_extension()`    |
| Significance gate produces malformed JSON   | Medium     | Validation gate catches and retries; hard JSON schema in prompt; fall back to discard        |
| Schema extraction imprecise (62% baseline)  | High       | Validation gate is the fix; log all failures for tuning; retry improves accuracy             |
| Embedding latency adds to turn time         | Low        | CPU-side MiniLM is fast (~10-50ms); generate async, don't block response                    |
| FTS5 trigger misses UPDATE edge case        | Low        | Test UPDATE specifically; triggers cover INSERT/UPDATE/DELETE in schema                      |
| Hybrid retrieval surfaces irrelevant memory | Low-Medium | Tune weights; set minimum score threshold (0.4 default); cap results at k=5                 |
| Personality drift during gate prompts       | Medium     | Gate is a separate system prompt call, not part of Echo's personality generation pass        |
