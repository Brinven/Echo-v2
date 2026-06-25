# CLAUDE.md — Echo Project Architectural Context (Updated: Stage 1)

This file exists to give Claude Code the decisions already made about the Echo
project so that code written does not conflict with established architecture.
Do not override these decisions without explicit user instruction.

---

## Git Workflow

Solo repo — **commit and push directly to `main`**. No feature branches, no PR
flow (confirmed by Michael 2026-06-24). GitHub is backup redundancy, not review.
Don't branch before pushing to the default branch on this repo.

---

## What Echo Is

Echo is a local-first AI voice companion. It runs entirely on the user's Windows PC.
It is privacy-first: no cloud APIs, no data leaving the machine.

This is a learning project. Clean, readable, modular code is preferred over
clever or optimized code. When in doubt, be explicit.

---

## Current Build Stage

**Stage 0 — Latency Measurement Only**

Nothing in Stage 0 should be built as if it is the final companion. It is a
diagnostic instrument. The goal is accurate measurement, not a good product.

The build order is:

| Stage | Focus |
|---|---|
| 0 | STT → LLM → TTS pipeline latency test |
| 1 | Conversational core (wiring Stage 0 into a working voice loop) |
| 2 | Session management (sign-off, conversation logging) |
| 3 | Memory write (OpenMemory, "remember that" + end-of-session summary) |
| 4 | Memory read (inject relevant memories into system prompt at session start) |
| 5 | Personality layer (stable persona via system prompt) |

Do not build Stage 1+ features into Stage 0. Do not build Stage 2+ features into Stage 1. etc.

---

## Hardware

| Item | Spec |
|---|---|
| OS | Windows 11 |
| CPU | AMD Ryzen 9900x |
| RAM | 64GB DDR5 |
| GPU | NVIDIA RTX 5080 (16GB VRAM) — primary and only GPU for PoC |
| Storage | 2TB+ M.2 free space |

A second GPU (RTX 4060, 8GB) is available but NOT used in the PoC.
It is deferred to post-PoC for running STT/TTS/embeddings separately.
Do not architect for dual-GPU now.

---

## LLM Stack

- **Runtime**: LM Studio, localhost:1234, OpenAI-compatible API (`/v1/`)
- **Package**: `openai` Python package pointed at local endpoint
- **Model selection**: auto-detect from `/v1/models` at startup
- **Preferred models**: Gemma 4B (fast, ~80 tok/s on this hardware) or similar small-medium local model
- **No cloud LLM**: do not add fallback to OpenAI, Anthropic, or any external API
- **Fine-tuning**: considered as a future option, not in scope yet

---

## STT Stack

- Auto-detect between `faster-whisper` (preferred) and `openai-whisper`
- CUDA-accelerated on RTX 5080
- Sample rate: 16kHz mono (Whisper native — no resampling)
- Input: PTT (push-to-talk) via SPACE key for PoC
- VAD (voice activity detection): deferred to Stage 1, with PTT as permanent fallback

---

## TTS Stack

- Kokoro (either `kokoro-onnx` or HuggingFace `kokoro` — auto-detect)
- This was chosen based on direct testing: 82M model, excellent quality, fast on CPU
  (may also run on GPU — test and use whichever is faster)
- No other TTS engines should be added without explicit instruction

---

## Memory System

- **Selected library**: OpenMemory by CaviraOSS
  (https://github.com/CaviraOSS/OpenMemory)
- **Rationale**: local-first, SQLite, Ollama embedding support, MCP endpoint,
  memory decay built in, multi-sector cognitive model
- **NOT in scope for Stage 0**. Do not install or reference it yet.
- Memory architecture decisions already made:

### Memory Write Strategy (Stage 3)
Two paths, both independent:
1. **Explicit**: user says "remember that" → immediate write to OpenMemory, no delay
2. **End-of-session**: user says sign-off phrase → LLM summary pass → writes
   everything else to OpenMemory

"Remember that" items are written immediately and NOT re-processed at sign-off.
The summary pass skips already-stored items.

### Memory Read Strategy (Stage 4)
- At session start, query OpenMemory for relevant context
- Inject retrieved memories into system prompt before first LLM call
- Retrieval is semantic — not a full dump of all memories

### Memory Types
OpenMemory's multi-sector model maps to Echo's needs:
- **Episodic** (high decay): time-bound events (visits, appointments)
- **Semantic** (low decay): persistent facts about people, preferences, relationships
Do not conflate these. Store them with appropriate sector tags.

---

## Personality / Persona

- Name: Echo
- Defined via system prompt (not fine-tuning) for the PoC
- Stage 5 work — do not add personality prompting to Stage 0 or 1 beyond
  the minimal latency-test system prompt
- The problem with previous Echo attempts was an overcomplicated system prompt
  that confused the model. Keep it simple and test incrementally.

---

## Session Management (Stage 2)

- Sessions have an explicit start and end
- Sign-off is a spoken/typed phrase that triggers end-of-session processing
- Heartbeat auto-detection (check every 30 min for unsaved sessions) is
  a future feature — NOT in the PoC
- Conversation logs saved as JSONL per session

---

## What Was Explicitly Rejected

These were considered and rejected — do not reintroduce them:

| Idea | Why rejected |
|---|---|
| Cloud LLM (OpenAI, Anthropic, etc.) | Privacy requirement, local-only |
| MemOS (MemTensor) | Requires external API keys (BaiLian), enterprise overkill |
| SimpleMem (aiming-lab) | Recursive Consolidation not implemented, research-only |
| A-mem (agiresearch) | Research implementation, not production-ready |
| opencode-agent-memory | Designed for OpenCode CLI tool, wrong domain |
| Autonomous email sending | Agentic with real-world write access, out of PoC scope |
| Calendar integration | Out of PoC scope |
| Dual-GPU orchestration | Post-PoC only |
| VAD in Stage 0 | PTT is simpler and sufficient for latency testing |
| Streaming LLM in Stage 0 | Need full-response baseline first; streaming is Stage 1 |

---

## Latency Budget

- Target: **< 3 seconds** total roundtrip (end of speaking → first word of audio)
- This was chosen by the user as "acceptable" for a home companion
- Stage 0 exists to verify this is achievable on this hardware
- If Stage 0 shows consistent > 3s, the model size or STT model must be
  reconsidered before proceeding to Stage 1

---

## Code Standards

- Python 3.10+
- Modular: STT, LLM, TTS are separate modules with clean interfaces
- These modules will be imported by later stages — design interfaces to be stable
- Explicit errors over silent fallbacks
- JSONL for all logging (consistent format across all stages)
- No frameworks (no LangChain, no LlamaIndex) in Stage 0 — raw API calls only
- Windows-compatible paths (use `pathlib.Path`, not hardcoded `/` separators)

---

## Stage 2 Additions

**Sign-off phrase:** "Echo, that's all for now"
- "Echo" must appear in transcript
- Handle both "that's" and "thats" variants
- Partial matches acceptable

**Session files:** `./sessions/session_YYYY-MM-DD_HH-MM-SS.json`
**Summary files:** `./sessions/summary_YYYY-MM-DD_HH-MM-SS.json`

Summary JSON schema is the CONTRACT with Stage 3 — do not change field names:
topics_discussed, facts_about_user, facts_general, action_items,
explicitly_remembered, conversation_mood, summary_text

**user_name** stored in config.json — used in goodbye and summary.
Default: "Michael"

**Q key in Stage 2:** requires double-press mid-conversation to prevent accidental loss.

---

## Stage 3 Additions

**Memory system:** OpenMemory by CaviraOSS, localhost:8080
Use http://127.0.0.1:8080 not localhost (Windows DNS penalty).
User ID: "echo_michael" (configurable in config.json)

**Path A — "remember that":** immediate write during conversation.
Tags: ["explicit", "user_requested"]
Extraction call: max_tokens=50, temperature=0, single sentence output.

**Path B — session summary writer:** runs at sign-off after summary LLM pass.
Write: facts_about_user, action_items, facts_general[source=web_search]
Skip: facts_general[source=model_knowledge], topics_discussed, mood, summary_text
Never re-write explicitly_remembered items — already stored via Path A.

**facts_general schema (updated Stage 3):**
{"fact": "string", "source": "model_knowledge|web_search"}
Backward compat: flat strings treated as model_knowledge (skip).

**memory.py** is a thin client wrapper only — no business logic.
Business logic for what to write lives in session.py (Path B)
and the conversation loop (Path A).

---

## ⚠ Memory Backend: Ib-Lite (Stage 5 Part 1, 2026-06-24 — replaced Hindsight)

Echo's runtime memory is now **Ib-Lite**, a self-contained local SQLite store in
`echo_stage0/ib_lite/`. It replaced the external Hindsight HTTP backend entirely —
no memory server, no cloud, no curator model. Everything runs behind the local
Gemma 4 12B QAT.

- `memory.py`, `memory_reader.py`, `smoke_memory.py`, `openmemory.db*` are archived
  in `echo_stage0/archived-hindsight-2026-06-24/`. Do not re-import them.
- The pipeline imports only `from ib_lite import IbLite`. Five typed tables
  (Core, Policy, Preference, Fact, Episodic) live in `echo_stage0/echo.db`
  (created on first run).
- Reads: Core+Policy injected every turn; Fact+Episodic hybrid-retrieved per turn
  (FTS5 BM25 + sqlite-vec cosine + recency), ~13ms/turn. Writes: a background
  significance-gate thread fires after each turn (single-flight) — off the hot path.
- sqlite-vec ships via the `sqlite-vec` pip package (0.1.9), loaded with
  `sqlite_vec.load(conn)` — NOT a hand-placed `vec0.dll`.

### Critical runtime gotcha — gate model thinking must be disabled

The significance gate calls the SAME loaded model. Gemma 4 12B QAT in LM Studio is a
**thinking model**: by default it spends the whole token budget in `reasoning_content`
and returns an EMPTY `content` (finish_reason=length) — the gate gets nothing to parse.
Fix (already in `ib_lite/significance.py`): pass **`reasoning_effort="none"`** on the gate
completion. This is the ONLY knob that works for this template — `reasoning_effort="low"`
and `chat_template_kwargs.enable_thinking=false` do NOT disable it. With it: clean JSON in
~1s. If the gate is ever pointed at a different thinking model, re-verify reasoning is off.

### FTS5 stays in sync via UPSERT, not REPLACE

Fact writes use explicit `INSERT ... ON CONFLICT(entity,attribute) DO UPDATE` (NOT
`INSERT OR REPLACE`). REPLACE is delete+insert and would orphan external-content FTS5 rows
(the AFTER DELETE trigger only fires under recursive_triggers, which must stay OFF or the
`fact_touch` trigger loops). DO UPDATE keeps the rowid and fires the AFTER UPDATE trigger
that re-syncs `fact_fts` correctly.

### Retrieval threshold is empirical

`MIN_SCORE=0.4` in `ib_lite/retrieval.py`. BM25 is unbounded (and flipped `* -1`), so the
weighted score (0.5·BM25 + 0.3·cosine + 0.2·recency) is NOT normalized — 0.4 is a tuned
floor, not a probability. Raise it if irrelevant facts surface; lower it if relevant ones
get dropped. Weights and `TOP_K=5` are tunable constants in the same file. The embedder
(all-MiniLM-L6-v2) is CPU-only by design — never move it to GPU; that VRAM is the 12B's.

`confidence` weights the **rank** (final `score = base × confidence`) and gates via
`MIN_CONFIDENCE=0.15`, but the `MIN_SCORE` floor is checked against the *un-weighted* base —
so a normal fact (default confidence 0.85) keeps its recall, while a fact dialed below 0.15
(via the CLI) is suppressed without being deleted.

### Curation & correction tools (Nice-to-Haves, built 2026-06-25)

- **`echo_stage0/ib_lite_cli.py`** — inspect/curate memory from the terminal:
  `python ib_lite_cli.py list | facts | search "<q>" | core <k> "<v>" | policy ... |
  pref ... | confidence <fact_id> <0-1> | rm <table> <id|key>`. Use it to see what the
  gate saved, seed Core/Policy, or down-rank/delete bad facts.
- **"Echo, forget that"** — `is_forget()` in `session.py` + `IbLite.forget_last_fact()`
  deletes the most recent fact written this session (tracked as `_last_fact`, set on the
  gate thread under `_gate_lock`) and Echo confirms aloud. Handled in `main.py` before the
  normal turn; the forget turn is never itself gated.
- **Deferred to Stage 5 Part 2:** mood_signal → tone at session start (belongs with the
  personality layer). The full confidence-*decay* job is also deferred — recency already
  ages facts at read time; revisit only if stale facts become a real problem.

---

## ⚠ Personality Layer (Stage 5 Part 2, 2026-06-24)

Echo's character lives in **`echo_stage0/persona.py`** — `PERSONA_BLOCK`, `SNARK_CONTEXTS`,
`ANTI_DRIFT_ANCHOR`, `build_persona_block(snark)`, and `build_system_prompt(exchange_count,
snark, core_block, memory_block)`. Identity is here and ONLY here.

- **System prompt assembly is now in `main.py`, not `ib_lite`.** Per turn it builds:
  `persona block → core_block (ib.build_context_block) → memory_block (ib.read_memory) →
  anti-drift anchor`. `IbLite.system_prompt_for_turn()` still exists but is **retired from
  the hot path** — do not reintroduce it as the assembler or the two orderings will diverge.
  Persona is built even when Ib-Lite is unavailable (empty core/memory), so the old generic
  `DEFAULT_SYSTEM_PROMPT` fallback is bypassed.
- **Identity is single-sourced.** The old `persona` row in `core_memory` was removed from the
  seed AND a one-time migration in `db.py` (guarded by `PRAGMA user_version`, v1) deletes it
  from existing `echo.db` files. Core memory now holds DATA about Michael (`user_profile`,
  `relationship`), never identity. Don't re-add a `persona` core row — it would duplicate the block.

### Snark level (0–10)
- `echo_stage0/daily_state.py` rolls a random level once per calendar day, persisted to
  `echo_daily_state.json` (gitignored runtime state), default **5** on missing/corrupt.
- **Effective snark is recomputed per turn:** `10 if session.max_snark else session.daily_snark`.
  Do NOT cache the effective level at session start — Max Snark Mode changes it mid-session.
- **Maximum Snark Mode** (locks 10 for the session): voice "Echo, maximum snark mode"
  (`is_max_snark()` in `session.py`, handled in `main.py` like the forget path — not gated, does
  not advance the exchange counter) OR the **S** key toggle. Resets on next launch (per-process).

### Anti-drift anchor — counter semantics (off-by-one is the trap)
- `session.exchange_count` counts full user→Echo **exchanges** (1/round-trip). This is DISTINCT
  from `session.turn_count`, which is speaker-turns (2/exchange) and is used only for logging.
- `increment_exchange()` fires once per **real** exchange, AFTER the too-short/no-speech/
  sign-off/forget/max-snark guards (those `return` first and must not advance it). The anchor is
  injected when `exchange_count % 8 == 0` (exchanges 8, 16, 24…). First real exchange reads 1, so
  no anchor; the eighth reads 8. Per-process, resets at session end.

### CoT isolation + sampler (llm.py)
- The character pass now passes **`reasoning_effort="none"`** (Gemma 4 12B QAT is a thinking
  model — same gotcha as the gate). Verified live: non-empty content, **TTFT ~0.11s** (no silent
  reasoning preamble), and even arithmetic stayed correct (17×23=391). An empty-content guard
  logs loudly if reasoning ever sneaks back on.
- Sampler baseline in **`echo_sampler.json`** (temp 0.72 / top_p 0.90 / top_k 40 / repeat_penalty
  1.08 / max_tokens 300), loaded once in `LLMClient.__init__`, fail-soft to built-in defaults.
  `temperature`/`top_p`/`max_tokens` are direct kwargs; **`top_k`/`repeat_penalty` go via
  `extra_body`** (LM Studio passthrough — verified against LM Studio's documented payload params;
  the spelling is `repeat_penalty`, not `repetition_penalty`). The significance gate keeps its own
  `temperature=0.1` / `max_tokens=150` — untouched.

### Test harnesses
- `test_personality.py` (M8): offline asserts (snark scaling, anchor timing, never-trim-persona) +
  live 10-prompt banned-phrase sweep + Mike-deflection + reasoning A/B.
- `test_hold_20turn.py` (M9): offline anchor-at-8/16 + live 20-turn hold, logs the transcript to
  `sessions/hold_test_*.json` for manual review.
- Both call `LLMClient()` which uses the **interactive model picker** when LM Studio has many
  models loaded. To run non-interactively, pin the model (construct the client and set `_model`).
  Banned phrases (PRD §10): "Certainly", "Absolutely!", "Great question", "As an AI",
  "I don't have access", "I remember that", "last time we spoke", "Is there anything else",
  "fascinating".

---

## Memory (Hindsight bank routing)

**Hindsight bank:** `echo`
**Tags:** `echo`, `voice-companion`

Set before invoking CC on this project: `$env:HINDSIGHT_BANK_ID="echo"`

This is distinct from Echo's *runtime* memory above (now **Ib-Lite** — local SQLite,
no Hindsight at runtime). This section is for
**Claude Code sessions working on Echo** — the CC hindsight-memory plugin routes a
session's auto-retains by the `HINDSIGHT_BANK_ID` env var, which falls back to
`axly-infra` if unset. Set it to `echo` so Echo development notes land in the
`echo` bank, not the shared infra bank. Cross-cutting infra (pm2, ports, OS
gotchas) belongs in `axly-infra`; personal/relational content goes to Ib.

---

## Stage 4 Additions

**Memory reads:** memory_reader.py — read-only counterpart to memory.py.
Never writes to OpenMemory. All writes stay in memory.py.

**Retrieval:**
- Session start: k=10 broad context query before first utterance
- Per turn: k=3 semantic query against transcript, min_score=0.6
- Cap: 15 memories max in system prompt at any time
- Target: < 100ms per turn retrieval

**System prompt is now dynamic.** llm.py must accept system prompt
as parameter — not hardcoded. Memory block appended at runtime.

**The subtlety rule is functional, not stylistic:**
Echo never says "I remember" or "last time we spoke".
It simply knows. Instruction in system prompt:
"Use this knowledge naturally — the way a close friend would,
without announcing that you remember it."

**Memory block omitted entirely if OpenMemory has no memories.**
Do not inject empty block.

**New JSONL fields:** memory_retrieval_ms, memories_injected, turn_memories_added
