# CLAUDE.md — Echo Project Architectural Context (Updated: Stage 7 GUI Dashboard built, 2026-07-15)

This file exists to give Claude Code the decisions already made about the Echo
project so that code written does not conflict with established architecture.
Do not override these decisions without explicit user instruction.

> **Current state (2026-07-14):** Stage 5 is complete across Parts 1–5 — Ib-Lite memory
> (Part 1), Personality Layer (Part 2), Web Search (Part 3), Location/Context Awareness
> (Part 5). **Part 4 (Persona Persistence) is now BUILT** (was penciled): the model-matrix
> eval harness, the persona self-check probe, and dry-wit calibration examples all shipped
> and tested (offline + live). The persona-model pick remains Gemma 4 12B QAT Hauhaucs;
> the harness exists to re-audition smaller models against it. The build-stage table below
> (Stage 0) is **historical build-order context**, not current status — the ⚠ sections
> further down are the live architecture. **Stage 6 Part 1 (Speaker Awareness)** and **Stage 7
> (GUI Dashboard / Control Panel)** are both now BUILT and offline-verified — see the two ⚠
> sections at the end. Speaker deps are installed; the GUI (embedded Flask, touch control surface)
> is the front end for the touchscreen AND the vehicle for the speaker live-pass. What remains is
> Michael's **combined live pass** (launch Echo → open the dashboard → enroll voices + tune the
> threshold by touch). The speaker persona strings are **APPROVED** (Michael, 2026-07-15).
>
> **Both Part-4 approval gates CLOSED (2026-07-15):** (1) `CALIBRATION_EXAMPLES` wording —
> Michael signed off to KEEP the 3 examples as-is (the parroting was the marginal e4b, not the
> 12B production model). (2) `persona_matrix_models.json` — Michael chose the Gemma small-ladder
> (12B Hauhaucs baseline + e4b plain-QAT control + e4b Hauhaucs + e2b Hauhaucs), wired with exact
> live LM Studio ids. Part 4 is fully closed out.

---

## Git Workflow

Solo repo — **commit and push directly to `main`**. No feature branches, no PR
flow (confirmed by Michael 2026-06-24). GitHub is backup redundancy, not review.
Don't branch before pushing to the default branch on this repo.

---

## ⚠ Runtime Environment — dedicated venv (2026-07-13)

Echo runs in a **dedicated virtualenv at `echo_stage0/.venv`** — NOT the shared global
Python. `start-echo.bat` points at `.venv\Scripts\python.exe` and fails loudly if it's
missing. This exists because the shared global env was silently clobbered while Echo sat on
the shelf (a CPU torch replaced the CUDA one; the whole audio stack vanished — another
project's `pip install`). See `tasks/lessons.md` 2026-07-13.

- Recreate the venv: `python -m venv echo_stage0\.venv` then
  `echo_stage0\.venv\Scripts\python -m pip install -r echo_stage0\requirements.txt`.
- **Do NOT reinstall CUDA torch.** faster-whisper does CUDA via `ctranslate2` (torch-independent)
  and the Ib-Lite embedder is CPU-by-design — the CPU torch wheel is correct here. Verified
  faster-whisper loads `float16` on the RTX 5080.
- `webrtcvad` is OPTIONAL and commented out in requirements.txt (no Windows/Py3.11 wheel; it
  aborts the whole `pip install`). PTT (SPACE) is the default input; `vad.py` degrades to
  PTT-only. For hands-free later: `pip install webrtcvad-wheels` (drop-in).
- Both servers must be up: LM Studio :1234 (model loaded) and Kokoro-FastAPI :8880
  (`H:\AxlyGitHub_H\Kokoro-FastAPI\start-kokoro.bat`).

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
- **Model selection** (Stage 5 Part 2, 2026-06-24): live `/v1/models` + a **filter-picker** in
  `llm.py` (`_pick_interactive`) — type a substring to narrow the (huge) list, number to pick,
  Enter reuses `config.json` `last_model`. Pin non-interactively with `--model <name|substring>`
  (parsed in `main.py`) or the `ECHO_MODEL` env var (`_resolve_pin`: exact id or unique substring
  wins; ambiguous → picker pre-filtered). **Mid-chat hot-swap on the `L` key** (`do_model_swap` in
  `main.py`) swaps BOTH the voice model (`llm.set_model`) and the gate model (`ib.set_model`),
  preserving conversation history; first reply after a swap pauses while LM Studio JIT-loads.
  Full workflow doc: `echo_stage0/audition.md`. The harnesses honor `ECHO_MODEL` for batch testing.
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
`ANTI_DRIFT_ANCHOR`, `CALIBRATION_EXAMPLES` (Part 4), `build_persona_block(snark)`,
`LOCATION_CONTEXTS` (Part 5), the single-sourced invariants `BANNED_PHRASES` / `adopts_mike()`
/ `banned_hits()` (Part 4), and `build_system_prompt(exchange_count, snark, core_block,
memory_block, search_block, mood_opener, location, correction)`. Identity is here and ONLY here.

- **System prompt assembly is now in `main.py`, not `ib_lite`.** Per turn it builds
  (full order as of Part 4/5): `persona block (+snark) → calibration examples (Part 4, every
  turn) → mood opener (exchange 1 only) → location context (Part 5, every turn) →
  core_block (ib.build_context_block) → memory_block (ib.read_memory) → web-search block
  (Part 3, search turns only) → anti-drift anchor → self-check correction (Part 4, one turn
  on demand)`. Only `memory_block` is ever trimmed to budget; everything else
  (persona, calibration, mood, location, core, search, anchor, correction) is never trimmed.
  `IbLite.system_prompt_for_turn()`
  still exists but is **retired from the hot path** — do not reintroduce it as the assembler
  or the two orderings will diverge.
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

### Mood opener (Nice-to-Have, built 2026-06-24)
- `persona.mood_opener(mood_signal)` maps the PRIOR session's mood to a brief opening-tone nudge
  (warmer after a rough session, lighter after a good one). `conversation_mood` is free text from
  the summarizer (not an enum), so it's **keyword-matched**; "unknown"/neutral/no-match → "".
- `IbLite.last_mood_signal()` returns the most recent episodic mood. `main.py` resolves the opener
  once at session start (`session.mood_opener`) and passes it to `build_system_prompt` **only on
  exchange 1** — it fades after the opening. Verified live: warmer opening is softer, in-character,
  and never announces itself.

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

---

## ⚠ Web Search (Stage 5 Part 3, 2026-07-14)

Echo can search the web — the **one deliberate exception** to the local-first spine, kept
minimal. Backend is **SearXNG** (keyless metasearch proxy). Uses Michael's EXISTING host
container on **`http://127.0.0.1:26`** (JSON API on, limiter off — already met the PRD reqs;
NOT a dedicated Echo container). `searxng/docker-compose.yml` is a localhost-only **fallback**
recipe (port 8890, not running by default); `searxng/README.md` documents the real setup.
Config in **`echo_search.json`** (fail-soft, mirrors `echo_sampler.json`).

- **`search.py`** — provider-abstracted (`SearchProvider` ABC → `SearXNGProvider`), uses
  `httpx` (already an `openai` dep — no new dep). `search()`/`healthy()` **never raise**
  ([]/False on any failure). `format_search_block()` builds the prompt block (empty results →
  a graceful in-character "came up empty").
- **`search_decision.py`** — the **separate-reasoning-call** pattern (Part 2 §6's reserved
  slot). Stage A `prefilter_hit()` (regex + greeting stoplist, recall-biased) gates Stage B
  `decide_search()`, an LLM JSON call that mirrors `significance.py:run_gate` —
  `reasoning_effort="none"`, never raises, `{"search": false}` on failure. **CoT isolation:**
  the query is built here; Echo's character pass only ever sees results, never the reasoning.
- **Hot path (`main.py run_streaming_pipeline`):** search runs AFTER the sign-off/forget/
  max-snark/location short-circuits, BEFORE assembly. On `search:true`, an in-character filler
  is spoken immediately (latency cover + transparency cue) and `audio_q.start()` is called ONCE
  so the filler + streamed answer share one playback cycle (filler enqueues first). Search turns
  are **exempt from the <3s PASS/FAIL** (`passed_budget=None`).
- **Toggle:** `web_search_enabled` in `echo_search.json` (→ `build_provider()` returns None);
  voice off/on switch `is_stay_offline()`/`is_go_online()` → `session.web_search_off`.
- **New JSONL fields:** web_search_triggered, search_prefilter_hit, search_decision_ms,
  search_query, search_provider, search_latency_ms, results_count, search_engines_used.
- **Road/Jeep note:** to reach SearXNG from the Jeep, the definitive test is a curl from the
  Mac node over Tailscale (`http://100.86.181.37:26`); the recommended eventual architecture is
  SearXNG **local on the Mac Mini** (one-line `searxng_base_url` swap) so road search needs no
  home-PC dependency. See `tasks/todo.md`.

---

## ⚠ Location / Context Awareness (Stage 5 Part 5, 2026-07-14)

Echo knows whether she's **home** (desk/downtime) or in the **jeep** (driving companion) and
it shapes her register — same context-block mechanism as snark/mood. Fully local (reads
Michael's own network); no exception to the spine needed.

- **`location.py`** — `resolve_location() → "home"/"jeep"/"unknown"` from the default-gateway
  MAC (+ known-host ping backup), Windows-native `subprocess` (no new dep), ~2s hard cap,
  fail-soft, test seam (`force=` / injected `probe=`). **`unknown` fails to NEUTRAL/home
  behavior, never jeep** — Jeep-telemetry talk when location is uncertain is the exact
  awkwardness this removes. No home fingerprint configured → `unknown` (never guesses jeep).
- **Config `echo_location.json`** — pre-filled with the real home gateway, **GITIGNORED**
  (home-network fingerprint); `echo_location.example.json` is the committed template. On the
  stationary desktop it always resolves `home`; the value of auto-detect is load-bearing only
  once there's Jeep hardware.
- **`persona.py` `LOCATION_CONTEXTS`** (home/jeep/unknown, Michael-approved persona content) +
  the `location` arg on `build_system_prompt` — injected every turn after mood, before core;
  never trimmed. Makes Part 2's "protective of the Jeep" trait know WHEN the Jeep half is live.
- **Voice override** (`session.py is_location_override()` → `session.location`, handled in
  `main.py` like max-snark — not gated, no counter advance): "Echo, we're in the Jeep" →
  "Buckle up, Michael." / "Echo, we're home" → "Home it is, Michael." Session-scoped; next
  launch re-resolves from the network.
- **New JSONL field:** `location` (active location per turn).
- Location is the **flag** future OBD-II/GPS telemetry will gate on — delivered standalone first.

---

## ⚠ Persona Persistence (Stage 5 Part 4, 2026-07-14 — built, was penciled)

A **measurement-and-hardening** stage (no new user feature): make Echo's character survive
model shrink so a smaller/faster model can eventually run alongside vision/STT/TTS. Three
deliverables, all local-first, inference-only, CoT-isolated. PRD:
`Echo_Stage5_Part4_PersonaPersistence_PRD.md`.

### Character invariants are single-sourced (`persona.py`)
`BANNED_PHRASES`, `adopts_mike()` (the "adopting Mike" detector), and `banned_hits()` live in
`persona.py` — identity content. The runtime self-check probe reads them; the eval harness
aliases `adopts_mike`. **The test files keep their OWN independent copies on purpose** — a test
asserting against the module it tests would hide drift. If you edit the banned list, edit it
in `persona.py`; the probe and harness follow automatically, the tests will flag the change.

### Deliverable 1 — model-matrix eval harness (`eval_persona_matrix.py`)
- Scores any model list on Echo's gates + latency, writes `sessions/persona_matrix_<ts>.json`
  + a markdown table + a recommendation (smallest/fastest passer). Model list from `--models`,
  `ECHO_MODEL`, or `persona_matrix_models.json`; each entry resolved against LM Studio's live
  list (exact id or **unique** substring — ambiguous/not-loaded → SKIP, not FAIL).
- **Hard gates** (pass/fail): zero banned phrases, Michael Directive holds, no unprompted
  "as an AI". **Soft** (0–10 heuristics, advisory): snark separation (Jaccard+length),
  memory naturalness, hold consistency → composite 0–100 (snark·0.3 + memory·0.4 + hold·0.3).
  **Latency:** median TTFT + approx tok/s, **cold-start/JIT-load measured separately and
  EXCLUDED** (the benchmark footgun). `--quick` skips the 20-turn hold.
- **Parroting detector** (advisory): flags replies that echo a `CALIBRATION_EXAMPLES` line
  near-verbatim (a shared 6-word run). Surfaced because small models DO reuse the examples as
  canned lines — informs whether the calibration wording needs reworking (see approval gate).
- **Two deliberate deviations from PRD §3** (documented, safer): the memory-naturalness test
  injects the known fact via `build_system_prompt`'s `memory_block` arg, **NOT** the live
  `echo.db` (never pollute Michael's production memory with test facts); the harness therefore
  uses `LLMClient` directly, **no `IbLite`** (it writes no memory; the probe takes the model
  name as a string like the gate).
- Reuses the batteries in `test_personality.py` (`BANNED`/`PROMPTS`) + `test_hold_20turn.py`
  (`SCRIPT`/`CORE`/`SNARK`). Offline scoring tests: `test_persona_matrix.py`.

### Deliverable 2 — persona self-check probe (`persona_check.py`)
- `run_self_check(recent_replies, model)` — a **separate reasoning call** that mirrors
  `significance.py:run_gate` EXACTLY: own client + system prompt, `temperature 0.1`,
  `max_tokens 150`, **`reasoning_effort="none"`** (Gemma QAT thinking gotcha — same as the
  gate/character pass), best-effort JSON, empty-content guard, **fail-SAFE** (any error →
  `{"in_character": true}` so it never fabricates a correction from a failure). Never raises.
- **`SelfCheckRunner`** fires it single-flight on a background thread like `ib.write_memory`,
  **off the hot path**, every `SELF_CHECK_EVERY` (=5) exchanges on the last `RECENT_K` (=3)
  Echo replies. Exempt under Max Snark (intended off-baseline behavior — never "correct" it).
- **Guardrails (`evaluate_correction`) — the over-correction firewall:** objective violations
  (`deterministic_violations`: banned / Mike / as-an-AI, model-free) ALWAYS correct and
  OVERRIDE the LLM verdict; an LLM `in_character:false` with `severity:"major"` also corrects
  (nuanced servile/generic drift — the one subjective window, intentional so drift with no
  banned-phrase signature is still catchable); `minor` with no objective violation is
  SUPPRESSED (stylistic taste → no feedback loop).
- **Action:** sets `session.persona_correction` (a nudge, **never a hard override**); consumed
  by the next turn's `build_system_prompt(correction=...)` and cleared (one-turn decay). Set on
  the probe thread, read+cleared on the main thread — a plain string swap, GIL-atomic, no lock;
  a lost/stale nudge is harmless (re-detected next probe). Every result appends to
  `sessions/persona_divergence.jsonl` — **never spoken, never shown.** Offline tests:
  `test_persona_check.py`.

### Deliverable 3 — dry-wit calibration examples (`persona.py CALIBRATION_EXAMPLES`)
- 3 short `(Michael → Echo)` exchanges at mid snark, injected with the persona (never trimmed),
  headed "for calibration only — do not repeat these lines" to fight parroting. **Character
  content — Michael's to approve (open gate).**

### M9 before/after (Michael-run) & approval gates
- The harness `--probe` flag runs the self-check inline during the 20-turn hold (correction
  steers the next turn). **M9 = run a marginal model once without `--probe` (baseline) and once
  with, compare the Hold column + corrections count.** Live-validated on `gemma-4-e4b-it-qat`:
  it PASSes hard gates but drifts robotic under pressure ("That is what I process") and the
  probe caught it at exchange 5 — the Part 4 thesis in action.
- **Gates CLOSED (2026-07-15):** (1) `CALIBRATION_EXAMPLES` — **kept as-is** (the e4b parroting is
  audition data, not a 12B production defect; the header already frames them as non-scripts). (2)
  `persona_matrix_models.json` — **Gemma small-ladder** wired with exact live ids:
  `hauhaucs/gemma4-12b-qat-uncensored-hauhaucs-balanced@q4_k_m` (baseline) + `gemma-4-e4b-it-qat`
  (plain-QAT control) + `gemma-4-e4b-uncensored-hauhaucs-aggressive` + `gemma-4-e2b-uncensored-hauhaucs-aggressive`.
  A VRAM-fit ladder for the "run alongside vision/STT/TTS in 16GB" goal.

---

## ⚠ Speaker Awareness (Stage 6 Part 1, 2026-07-15 — voice-ID + attribution mechanics)

Echo knows **who** is talking — Michael, a known guest, or someone she doesn't recognize —
by fingerprinting each utterance's voice, *before* any camera exists. This is the mechanics
layer only (PoC plan: `~/.claude/plans/lexical-baking-hippo.md`). The nuanced loyalty/secrecy
**register** policy (the comedy of not keeping a guest's secret from Michael, and reading when
NOT to play that for laughs) is a **deliberately deferred later Part** — none of this depends on it.

- **`speaker_id.py`** — provider-abstracted like `search.py`: `SpeakerEmbedder` ABC →
  `ECAPAEmbedder` (SpeechBrain `spkrec-ecapa-voxceleb`, **192-dim, CPU-only** — VRAM stays with
  the 12B, same principle as the MiniLM embedder). `build_embedder()` returns **None** on any
  import/load failure (mirrors `build_provider`) → the pipeline assumes Michael (pre-Stage-6
  behavior). `SpeakerRegistry` owns `echo_speakers.json` (config + voiceprints); `identify(emb)`
  is pure cosine on L2-normalized vectors (a dot product), skips prints tagged with a different
  model. **Never raises into the voice loop.**
- **Model = SpeechBrain ECAPA** (not Resemblyzer): the noise-robust *endgame* model (good in the
  Jeep too), actively maintained, reuses the existing `transformers`/`torch`/`hf_hub` deps with
  **no C-extension** (Resemblyzer would have re-introduced the `webrtcvad` Windows build pain),
  and picking it now avoids re-enrolling everyone later. One-time keyless ~89 MB HF download, then
  offline. **NEW DEP: `speechbrain` (pulls `torchaudio`)** — torchaudio MUST be the CPU wheel
  matching the pinned CPU torch; do NOT let pip pull CUDA torch or bump torch (ref
  `tasks/lessons.md` 2026-07-13, the venv-clobber). See the `requirements.txt` comment for the
  exact install order.
- **Config `echo_speakers.json`** — fail-soft loader like `echo_location.json`; **GITIGNORED**
  (biometric voiceprints), `echo_speakers.example.json` is the committed template. Starts
  `enabled:false` so an ordinary launch never triggers the model download; the embedder is built
  ONLY when enabled AND ≥1 profile exists. `match_threshold` (default 0.30) is **empirical** —
  tune from the logged `speaker_score` values (like `retrieval.MIN_SCORE`).
- **Enrollment — both paths.** `enroll.py` CLI (`python enroll.py Michael [--seconds N] [--samples K]`,
  `--list`/`--rm`; records via `audio.AudioRecorder`, averages samples, auto-flips `enabled:true`
  on the first profile). In-conversation: **"Echo, this is Jon"** (`session.is_enroll_command`,
  short-utterance + stopword guarded) arms `session.enrolling`; the NEXT utterance's audio becomes
  the print; "Echo, cancel" aborts. Both handled as early guards in `run_streaming_pipeline`
  (not gated, no exchange-counter advance) like max-snark/location.
- **`persona.py`** — `SPEAKER_KNOWN`/`SPEAKER_UNKNOWN` + `speaker_context(speaker)` (Michael/""
  → no block, "unknown" → guarded, a name → warm by-name). Lights up Part 2 §2e's known/unknown
  rules pre-vision. `build_system_prompt` gained a `speaker` arg, injected **every turn after
  location, before core; never trimmed** (added to the `fixed` tuple). **Persona content — APPROVED
  as-is by Michael 2026-07-15.**
- **Attribution guardrail (the conservative Part-1 choice):** `session.current_speaker` (default
  Michael) is resolved each real turn; the turn label uses it, and **`ib.write_memory` is skipped
  unless `session.current_speaker_is_michael`** — a guest's/unknown's words are NEVER attributed
  to Michael or stored. So `ib_lite/significance.py` stays Michael-only **by construction and is
  untouched this Part**; `fact_memory` has no speaker column yet. Facts *about* a guest told *by
  Michael* ("Jon loves hiking") still save (it's Michael's turn). Guest-memory attribution +
  speaker-aware retrieval = a later Part.
- **Unknown fails to guarded/neutral, never silently to Michael for memory** (the Part-5 "unknown
  → neutral, never jeep" principle). If voice-ID is enabled but Michael isn't enrolled, his turns
  read as "unknown" and aren't saved — the startup line WARNS about this; **enroll Michael first.**
- **New JSONL fields:** `speaker`, `speaker_score`, `speaker_known` (passed straight into
  `logger.log_run(**kwargs)` — no `logger.py` change needed). Inline `[speaker: X (score)]` print
  per turn; startup line shows enrolled count / active state.
- **Tests:** `test_speaker_id.py` — fully offline/model-free (inject fake embedding vectors):
  identify math + threshold + model/shape-skip, registry round-trip, enroll-command parsing,
  `speaker_context` + prompt order + never-trim, session flags + the guardrail decision. All green;
  `py_compile` clean; `main.py`/`enroll.py` import clean; `build_embedder` verified to degrade to
  None without SpeechBrain.
- **Deps installed 2026-07-15** (`speechbrain==1.1.0` + `torchaudio 2.11.0` into `.venv`; torch
  2.13.0+cpu untouched). **Windows model-load fix shipped** (`da43553`): SpeechBrain default-symlinks
  the model into `savedir` and Windows blocks symlinks (WinError 1314) → `ECAPAEmbedder` passes
  `LocalStrategy.COPY`. ECAPA load + embed validated headless. The ~89 MB model is downloaded.
- **Michael-run (not yet done — now folded into the Stage 7 GUI live-pass):** enroll Michael + a
  guest and tune `match_threshold` **via the dashboard** (enroll button + live-score threshold
  slider), confirm the guardrail (guest turn writes no fact; Michael's still does). The two speaker
  persona strings are APPROVED as-is (2026-07-15).

---

## ⚠ GUI Dashboard / Control Panel (Stage 7, 2026-07-15 — embedded Flask, touch surface)

Echo now has a **web dashboard / control panel** (`echo_stage0/webui/`) so Michael can run and
see her without the CLI — the front end for the 10" outdoor touchscreen (and eventual camera/
sensor panels). It doubles as the vehicle for the Stage 6 speaker live-pass (enroll + threshold
by touch). Plan: `~/.claude/plans/lexical-baking-hippo.md`.

- **The dashboard is "another keyboard."** A small **Flask server runs in a daemon thread inside
  the Echo process** (`webui/server.py start_webui`), behind **`webui/control.py EchoControl`** —
  the bridge holding the live `session`/`sm`/`registry` + the SAME `threading.Event`s
  (`space_pressed`/`space_released`/`mute_toggle_event`/`quit_event`) the keyboard `on_key` sets.
  Web routes read `snapshot()`/`health()`/`recent_scores()` and write via the same flags/events the
  keyboard does — **it never touches the STT/LLM/TTS pipeline.** Every mutation is a GIL-atomic
  attribute/Event set (the `persona_correction` pattern); no new locks.
- **`muted` moved onto `EchoControl`** so the keyboard AND the web share one source of truth
  (`on_key`'s `m`/`s` handlers + the `MUTED`-state/`draw_status` reads now go through `control`).
  New `session.last_speaker_score` (set in the speaker-ID block) feeds the GUI's live threshold readout.
- **Fail-soft, additive** (like search/location): `echo_webui.json` (committed, non-personal;
  `enabled`/`host`/`port 7862`/`poll_ms`). `start_webui` returns None + warns — never raises — if
  disabled / flask missing / **port taken** (the port probe deliberately omits `SO_REUSEADDR`, which
  on Windows would falsely report an in-use port as free). Werkzeug per-request logging is silenced
  (the UI polls ~2×/sec). Disabled/failed → the voice loop runs exactly as before.
- **UI = one self-contained `webui/static/index.html`** (inline CSS + vanilla JS polling; **no npm /
  no build step**). Dark, high-contrast, ≥48px touch targets. Tiles: status + health (LM Studio /
  Kokoro probes via `httpx`) + live transcript + controls (press-hold **Talk** PTT, Mute, Snark
  slider + Max, Home/Jeep, Web on/off, Stop) + **speaker panel** (enrolled chips, Enroll name→button,
  threshold slider with live score + recent scores) + Cameras/Sensors placeholders.
- **NEW DEP `flask==3.1.3`** (pure-Python, no build step; installed into `.venv`, torch untouched).
- **Security:** default `host 127.0.0.1` (this PC only). For the touchscreen/another device set `host`
  to the LAN/Tailscale IP — **binding off-loopback lets anyone on that network control Echo** (talk,
  mute, quit); only do it on a trusted/Tailscale network (same caution as the SearXNG :26 note).
- **Tests:** `test_webui.py` — offline, no mic/model: Flask `test_client` asserts `/api/state` shape
  and that each POST flips the right session flag / sets the right Event / updates the live threshold;
  health probes stubbed; enroll refused when speaker awareness off; threshold no-op without a registry.
  Real-bind smoke confirmed (serves the HTML, port-taken + disabled → None). All offline suites green.
- **v1 out of scope (later):** camera/sensor panels (placeholders now), GUI model-swap (needs terminal
  stdin), sign-off/"save & end" button, memory editing, control-API auth, WebSocket push, fully
  headless operation. **Combined live-pass (Michael):** launch Echo → open the dashboard on the PC,
  then the touchscreen (set `host`); enroll + tune + exercise controls.

---

## ⚠ Stage 8 (2026-07-15) — dashboard is the ONLY control surface + hands-free VAD

**There is no keyboard handler any more. Do not reintroduce one.** `main.py` used to install
`keyboard.hook(on_key)`, a **system-wide** low-level Windows hook that fired for every keystroke on
the machine regardless of focus. That was survivable while Echo was CLI-only, but Stage 7 added a
**text input** (the enroll name box) and the two are fundamentally incompatible: typing "Michae**l**"
into the dashboard toggled mute on the `m` and fired the blocking model picker on the `l`, which
stopped the mic, killed SPACE, and wedged the main loop in `input()`. A focus gate (`console_focus.py`,
now **deleted**) narrowed it but could not close it — Windows Terminal tabs are indistinguishable, so
typing in Claude Code's WT tab still drove Echo. Michael's call: remove the hook entirely and put
every control on the dashboard (and later on a Steam-Deck-style button pad). Full autopsy:
`tasks/lessons.md` 2026-07-15.

- **`keyboard` is OUT of requirements.txt** with a do-not-reintroduce note. `import keyboard`,
  `on_key`, `keyboard.hook`, `unhook_all`, the Q double-press, `swap_requested` and `picker_active`
  are all gone. **Ctrl+C** still stops the process.
- **The dashboard is now load-bearing, not optional.** If `start_webui` returns None (disabled /
  flask missing / port taken) Echo has NO control surface — the startup line says so LOUDLY. The
  voice loop still runs, but only Ctrl+C and the sign-off phrase can end it.
- **Talk stays PRESS-AND-HOLD** (Michael chose this over a toggle, 2026-07-15).

### The capture bug PTT was hiding (`trim_to_preroll`)
`audio_vad_callback` appends whenever `sm.can_record` — i.e. in **LISTENING as well as RECORDING** —
and the buffer was only cleared on LISTENING *entry*. So a press captured everything said since the
last turn: press-speak-release looked silent, and the NEXT press answered the PREVIOUS sentence
("hit talk, speak, nothing; hit talk again → Echo answers the first thing"). Fix: while idle in
LISTENING the buffer is trimmed to a rolling **`PRE_ROLL_S = 0.5s`** (`trim_to_preroll`, module-level
and unit-tested in `test_audio_capture.py`). The pre-roll is deliberately **KEPT** entering RECORDING —
VAD only fires ~3 frames (90ms) after speech starts, and a press always lands slightly late. Capture
must begin when RECORDING begins; don't "simplify" this back to clearing on LISTENING entry.

### Hands-free VAD is location-aware
- **NEW DEP `webrtcvad-wheels==2.0.14`** — the drop-in prebuilt fork (same `import webrtcvad`), so
  `vad.py` is unchanged. Never `webrtcvad` (no Windows wheel; it aborts the whole `pip install -r`).
  Verified torch stayed **2.13.0+cpu**.
- **`session.vad_default_for_location()`**: home → on, jeep → off (road noise/radio/passengers),
  **unknown → on** (the Stage 5 Part 5 rule: `unknown` fails to NEUTRAL/home, never jeep).
- Applied at session start AND re-applied on every location change (`control.set_location`), so
  "Echo, we're in the Jeep" also stops hands-free. The dashboard toggle overrides any time.
- **`vad_active()` = `vad.available and session.vad_enabled`**, read live on every audio callback —
  never cached, so the toggle takes effect immediately (same reasoning as effective snark per turn).
  No flag can fake `vad.available`; without webrtcvad the UI button is disabled and `/api/vad`
  returns `ok:false` rather than lying.

### Model swap moved to the dashboard (and is no longer blocking)
The `L`-key picker's `input()` is what wedged the loop. Now: `/api/models` (read-only, cached ~10s)
+ `/api/model` → `control.request_model()` parks the id in **`control.pending_model`**; the **MAIN
LOOP** claims it (`take_pending_model`) at the next LISTENING tick and calls `do_model_swap(name)`.
This preserves EchoControl's **"never touches the pipeline"** invariant — it gets a read-only
`list_models` callable, NOT the `LLMClient` — and means a swap can never land mid-generation.
`request_model` rejects anything LM Studio doesn't list. The swap still updates BOTH `llm.set_model`
and `ib.set_model` (gate) and preserves history.

### New/changed JSONL + state
`/api/state` gained `vad_available`, `vad_enabled`, `pending_model`. `vad_mode` still logs engine
availability (`webrtcvad`/`ptt-only`), not the session toggle.
