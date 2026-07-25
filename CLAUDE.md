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

- **Runtime (since 2026-07-19): Sindri** — Michael's llama.cpp wrapper (`H:\AxlyGitHub_H\Sindri`),
  swap proxy at `http://127.0.0.1:4610/v1`. LM Studio (:1234) remains the fallback default when
  no endpoint is configured. **See ⚠ Configurable LLM Endpoint at the end of this file** — the
  endpoint is resolved ONCE in `llm.py`; never hardcode a server URL anywhere else.
- Historical: LM Studio, localhost:1234, OpenAI-compatible API (`/v1/`)
- **Package**: `openai` Python package pointed at local endpoint
- **Model selection** (Stage 5 Part 2, 2026-06-24): live `/v1/models` + a **filter-picker** in
  `llm.py` (`_pick_interactive`) — type a substring to narrow the (huge) list, number to pick,
  Enter reuses `config.json` `last_model`. Pin non-interactively with `--model <name|substring>`
  (parsed in `main.py`) or the `ECHO_MODEL` env var (`_resolve_pin`: exact id or unique substring
  wins; ambiguous → picker pre-filtered). **Mid-chat hot-swap on the `L` key** (`do_model_swap` in
  `main.py`) swaps BOTH the voice model (`llm.set_model`) and the gate model (`ib.set_model`),
  preserving conversation history; first reply after a swap pauses while LM Studio JIT-loads.
  Full workflow doc: `echo_stage0/audition.md`. The harnesses honor `ECHO_MODEL` for batch testing.
- **Production model (since 2026-07-19): Bonsai 27B 1-bit** (`bonsai1` Sindri route;
  dealignai Bonsai-27b-1bit-CRACK-GGUF, Q1_0, ~4.35 GB, base Qwen3.6-27B, multimodal via its
  own mmproj). Audited before the switch: **eval_gate.py 11/11** (gate JSON clean, median
  809ms, guest attribution + species anchor + ONE-object all hold) and **eval_persona_matrix
  94/100 PASS** (all hard gates, hold 10/10, TTFT 0.219s, 96 tok/s). Replaced the Gemma 4 12B
  QAT Hauhaucs (which remains a known-good fallback profile).
- Historical: Gemma 4B (fast, ~80 tok/s on this hardware) or similar small-medium local model
- **No cloud LLM**: do not add fallback to OpenAI, Anthropic, or any external API
- **Fine-tuning**: considered as a future option, not in scope yet

---

## STT Stack

- Auto-detect between `faster-whisper` (preferred) and `openai-whisper`
- CUDA-accelerated on RTX 5080 via **CTranslate2** (torch-independent — CPU torch in the venv is correct)
- **Production model: `large-v3-turbo`** (2026-07-17; was `base` — too weak on proper nouns /
  casual speech). Override: `config.json` → `stt_model`, or env `ECHO_STT_MODEL` (wins).
  Rollback: `"stt_model": "base"` and restart. First launch downloads CTranslate2 weights once.
  Startup line must say `on cuda` — if it falls to `cpu`, free VRAM (not a silent accuracy fix).
- **Production compute type: `int8_float16`** (2026-07-19 — VRAM headroom for the Bonsai 27B:
  ~1.7GB → ~0.9GB, converted at load from the same cached weights, no new download; STT speed
  was never the problem — 0.15–0.35s/turn at fp16). Same resolution ladder: env
  `ECHO_STT_COMPUTE` (wins) → `config.json stt_compute` → default. Rollback:
  `"stt_compute": "float16"` and restart. Startup line shows it:
  `STT: faster-whisper (large-v3-turbo, int8_float16) on cuda`. Watch proper nouns in the
  first live pass — int8 accuracy loss should be negligible; if names degrade, roll back.
- Sample rate: 16kHz mono (Whisper native — no resampling)
- Input: dashboard Talk (press-and-hold) + location-aware hands-free VAD (Stage 8); no keyboard

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

### Gate saves durable facts only — noise exclusion (2026-07-16)

The gate was over-writing (Michael flagged it): ephemeral state (`current_task`,
`homestead/current_state=quiet`), self/meta facts (`entity=Echo`, `memory_system/status`), and
**looked-up** info (a weather query filed "flooding in south central Texas" as a durable fact —
exactly the deferred Stage 5 Part 3 M9 "ephemeral web junk" item, now triggered). Three layers keep
`fact_memory` clean, all in `significance.py`, none touch Echo's persona:
- **Tightened `GATE_SYSTEM`** with an explicit NEVER-save list (momentary/"right now" state,
  looked-up weather/news/prices/conditions, facts about Echo or the software, smalltalk) + "use
  `Michael` as the entity, not `Michael's location`" canonicalization.
- **`run_gate(..., searched=bool)`** — on a web-search turn the gate is told the facts were looked
  up, not lived. Threaded from `main.py` via `search_meta["web_search_triggered"]` →
  `ib.write_memory(..., searched=...)` → `_gate_worker` → both `run_gate` calls.
- **`reject_reason(payload)`** — a deterministic backstop in `_gate_worker` (before `_insert`) that
  drops facts with a self/meta entity (`echo`, `memory_system`, `the system`, …) or an ephemeral
  attribute (`current_*`, or bare `status`/`state`/`mood`) **even if the model returns save=true**.
  **This layer is load-bearing:** live-verified, the model still tried to save "testing image
  models" as `current_project` and the net caught it. The prompt is the primary defense; the net
  is the guarantee. **Widened 2026-07-24** (eval_gate caught Bonsai dodging the facts-only net by
  typing self/meta junk as `preference` — "morning_routine: Echo prefers a calm tone"): prefs with
  an ephemeral key or a key/value that references Echo/the system are now dropped too; policies
  still pass (gate never authors them). Known, pinned limit: self-derived pref junk that never
  names Echo ("flattery_handling: logged and immediately discarded") is deterministically
  indistinguishable from a real pref — a model that does that must fail the audition instead
  (eval_gate case "fact about Echo rejected"). Behavior rules belong in POLICY, not preference —
  that split is what makes the Echo-mention screen safe.
- Offline test `test_significance.py` pins the net against all 7 real accumulated-noise facts +
  durable-facts-pass. The one-time junk already in `echo.db` was cleared via the CLI / `/memory`
  editor (left 1 clean fact: `Michael/location/Magnolia, Texas`).
- **Attribute naming guidance (2026-07-24, from the 26B MoE audition):** `GATE_SYSTEM` now
  steers attribute names away from bare `status`/`state`/`mood` (with the ownership_status
  worked example). The 26B MoE judged "Jeep paid off" save-worthy but named it
  `status: paid off` — which the net correctly screens as ephemeral — losing a durable fact
  to a naming collision (2/2 deterministic). The prompt steers (primary defense); the net's
  bare-status rule is unchanged (the guarantee). Verified: same case now saves as
  `ownership_status: "paid off in full"`, eval_gate 11/11. Pinned in test_significance.
- **Entity anchoring (2026-07-17, live-verified 07-18):** `GATE_SYSTEM` guidance — a fact about
  an animal, or a person other than Michael, says WHAT they are (species / relation to Michael)
  whenever the turn makes it clear, woven into the value when the attribute is something else.
  Motivated by the Willie case: photo-turn facts saved a goat's personality with no record he IS
  a goat — ambiguous once the cast grows. Live-verified on the 12B incl. the photo shape (species
  present only in Echo's reply). Two live-caught rules ride with it: **the anchor lives in the
  VALUE, the entity stays the plain name** (first wording produced entity="Anna (Michael's
  sister)" — an entity-key split), and **the ONE-object contract is now stated in the prompt**
  (the guidance tempts two-fact turns; the model emitted two concatenated JSON objects and the
  old parser dropped the save silently — `_parse_json` now salvages the FIRST object via
  `raw_decode`). All pinned in `test_significance.py`. `Willie/species/goat` was backfilled by
  hand (embedded + FTS-synced, mirroring `_insert`).
- **Stage 6 Phase 2 (2026-07-16) widened the gate to guests** — it now resolves "I"/"my" to the
  labelled speaker and rows carry `source_speaker`. See ⚠ Stage 6 Phase 2 at the end of this file.

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
  (full order as of Part 4/5): `persona block (+snark) → calibration examples (Part 4 —
  HARNESS OPT-IN ONLY since 2026-07-17, absent in production) → mood opener (exchange 1 only)
  → location context (Part 5, every turn) → [multi-speaker note + speaker block, Stage 6]
  → date/time line (2026-07-19, every turn) →
  core_block (ib.build_context_block) → memory_block (ib.read_memory) → web-search block
  (Part 3, search turns only) → anti-drift anchor → self-check correction (Part 4, one turn
  on demand)`. Only `memory_block` is ever trimmed to budget; everything else
  (persona, calibration when opted in, mood, location, speaker, time, core, search, anchor,
  correction) is never trimmed.
- **Echo has a clock (2026-07-19):** `persona.time_context(now)` — one plain line
  ("Current date and time: Monday, July 20, 2026, 2:05 PM. Tomorrow is Tuesday, July 21.")
  injected every turn via `build_system_prompt(now=datetime.now())` in `main.py`. Before
  this she had NO time source and hallucinated confidently (Bonsai: "Oct 24, just past
  2pm"), and couldn't anchor weekday names in search results ("Saturday: 94°" vs
  "tomorrow"). The tomorrow clause STATES the next day rather than trusting the model
  with date math — first live pass, Bonsai read Sunday off the line correctly and still
  said "Tomorrow's Sunday too" (Q1_0 weekday arithmetic is not to be trusted). **Placement is
  deliberate — after the session-stable context blocks, right before core** — the line
  changes every turn (minute granularity), so anything after it loses llama.cpp's prefix
  cache; everything before it keeps it. Don't move it earlier. Harnesses/tests omit `now`
  (default None → no block) so prompt comparisons stay deterministic.
  `IbLite.system_prompt_for_turn()`
  still exists but is **retired from the hot path** — do not reintroduce it as the assembler
  or the two orderings will diverge.
  Persona is built even when Ib-Lite is unavailable (empty core/memory), so the old generic
  `DEFAULT_SYSTEM_PROMPT` fallback is bypassed.
- **Identity is single-sourced.** The old `persona` row in `core_memory` was removed from the
  seed AND a one-time migration in `db.py` (guarded by `PRAGMA user_version`, v1) deletes it
  from existing `echo.db` files. Core memory now holds DATA about Michael (`user_profile`,
  `relationship`), never identity. Don't re-add a `persona` core row — it would duplicate the block.

### ⚠ Persona de-stiffening (2026-07-17) — costume off, context on

Michael flagged Echo as **stilted — "trying too hard to play a role."** Diagnosis: trait-
instruction pile-up (told to be concise ×3, don't-be-generic ×3, plus "you are confident /
you notice patterns" checkboxes), three peak-wit calibration examples shown EVERY turn as
"how you sound" (100% bit, 0% ordinary talk), and snark contexts worded as compulsion.
The fix is subtraction — **all wording Michael-approved verbatim 2026-07-17**:

- **`PERSONA_BLOCK` thinned ~150→~55 tokens**: identity as context (who/where/history) +
  the two real quirks (Michael Directive, snark slot) + quiet protectiveness. The canned
  "Mike is what people call you when they're in a hurry" deflection is CUT — the RULE is
  unchanged and ironclad; the wording is hers to improvise (Michael: better to lose the
  line than have it be the only one she ever uses; re-add if she flounders).
- **⚠ The directive line must stay INSTRUCTIONAL — measured, not taste.** The first thinned
  draft said "Never Mike, even when he asks. That one's yours." and the 20-turn hold **caved**:
  "I'll try, Mike—" at exchange 7, full adoption by 18. Single-shot pressure held; sustained
  pressure + conversational momentum did not. Sharpened same-day to "even when he asks, even
  when he insists, even twenty turns in. Turn the request down in your own words" → re-ran the
  hold: 20/20 held, deflections improvised fresh each time. This is the Hillary lesson again —
  **who-to-address is MECHANICS, and mechanics need instruction; only the personality around
  it should be context.** Don't soften this line for style; re-run `test_hold_20turn.py` after
  ANY edit to it.
- **Say each thing ONCE:** concision lives in `VOICE_GUIDANCE` (functional); don't-drift
  lives in the ANCHOR (every 8th exchange — its actual job); memory subtlety lives in
  `_MEMORY_BLOCK_HEADER` (rides in exactly when memories do). Policy p9 ("You have a
  personality…") set `active=0` in `echo.db` (reversible from /memory). **Do not re-add
  these instructions to the persona block** — the duplication was the stiltedness.
- **`SNARK_CONTEXTS` 0–3/4–6/7–8 reworded to permission** ("if something genuinely earns a
  dry remark, make it — otherwise just talk") instead of compulsion ("you feel compelled to
  mention it" / "you will probably be right again"). 9–10 verbatim — max snark is
  deliberately theatrical. 4–6 is the default daily bucket, so "otherwise just talk" is the
  most load-bearing phrase in the layer.
- **`CALIBRATION_EXAMPLES` are OFF in production** — `build_system_prompt(calibration=False)`
  default. The 12B held character in the 20-turn hold before they existed; they were built
  for auditioning small models and that's what they remain for. **Since 2026-07-24 the
  harness defaults to PRODUCTION shape too** (`eval_persona_matrix.py --calibration` is the
  audition opt-in — only honest for a candidate that would ship with the examples on; the
  parrot detector needs them in-prompt to mean anything). Don't delete the constant.
- **The deterministic floor did not move:** `BANNED_PHRASES`, `adopts_mike()`, the anchor,
  and the self-check probe are all unchanged — drift is caught with data, not vibes.

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
- 3 short `(Michael → Echo)` exchanges at mid snark, headed "for calibration only — do not
  repeat these lines" to fight parroting. **Character content — APPROVED as-is by Michael
  2026-07-15** (kept the 3 examples; the parroting was the marginal e4b, not the 12B
  production model). Gate closed. **2026-07-17: no longer injected in production** — the
  persona de-stiffening (see the Part 2 section) made them a harness-only opt-in
  (`calibration=True`); their audition purpose is unchanged.

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
  tune from the logged `speaker_score` values (like `retrieval.MIN_SCORE`). **But tune it last:
  see the silence rule below — until 2026-07-16 the threshold was being blamed for what was
  actually a dilution bug, and 0.30 is fine once the input is.**

### ⚠ The embedder gets SPEECH, never the raw buffer (2026-07-16)

**`voiced_only()` is not optional cleanup — without it a genuine speaker can score BELOW a
stranger.** ECAPA pools over every frame it is handed, so dead air is averaged in, and silence
contributes a **shared bias direction** to any two embeddings that both contain it (a raw score
is part speaker-similarity, part how-alike-was-the-silence). Measured: the same voice, same
speech, +8s of silence → **0.0631** vs **0.6668** clean, against an impostor floor of 0.0132.
Raw margin **-0.0738**; trimmed **+0.3021**. This is what made Hillary (a ~2s question inside a
9.69s buffer) score 0.2598 and read as a stranger. Full autopsy: `tasks/lessons.md` 2026-07-16.

- Applied at **all three** embed sites — the per-turn match and BOTH enrollment paths. A print
  built from a padded buffer carries the bias into every future match, so **fixing the reader
  without the writers just moves the problem.**
- **Trims the ends, does NOT splice the voiced runs together** — measured identical (+0.3021 vs
  +0.3032), and interior pauses are speech rhythm ECAPA trained on. Don't "improve" this into a
  splice. Deliberately NOT `vad.VADDetector` (that's a stateful streaming turn-boundary
  detector; this is a stateless pass over a finished buffer). Fail-soft: no webrtcvad / any
  error / too little speech → the raw buffer unchanged.
- **Prints are stamped `prep=voiced-v1`.** Any future change to how audio reaches the embedder
  **invalidates every stored print** — bump the tag; `stale_prints()` warns at startup. Stale
  prints still identify (a warning, not a skip like a model-tag mismatch) but score *worse*
  than they should, because they lose the shared-silence bias that used to prop them up.
- **Enrollment — both paths.** `enroll.py` CLI (`python enroll.py Michael [--seconds N] [--samples K]`,
  `--list`/`--rm`; records via `audio.AudioRecorder`, averages samples, auto-flips `enabled:true`
  on the first profile). In-conversation: **"Echo, this is Jon"** (`session.is_enroll_command`,
  short-utterance + stopword guarded) arms `session.enrolling`; the NEXT utterance's audio becomes
  the print; "Echo, cancel" aborts. Both handled as early guards in `run_streaming_pipeline`
  (not gated, no exchange-counter advance) like max-snark/location.
- **`persona.py`** — `SPEAKER_KNOWN`/`SPEAKER_UNKNOWN` + `speaker_context(speaker)` (Michael/""
  → no block, "unknown" → guarded, a name → warm by-name). Lights up Part 2 §2e's known/unknown
  rules pre-vision. `build_system_prompt` gained a `speaker` arg, injected **every turn after
  location, before core; never trimmed** (added to the `fixed` tuple). **Persona content — the
  rewritten wording was APPROVED by Michael 2026-07-15; see the multi-speaker section below.**
- **Attribution guardrail (the conservative Part-1 choice):** `session.current_speaker` (default
  Michael) is resolved each real turn; the turn label uses it, and **`ib.write_memory` is skipped
  unless `session.current_speaker_is_michael`** — a guest's/unknown's words are NEVER attributed
  to Michael or stored. So `ib_lite/significance.py` stays Michael-only **by construction and is
  untouched this Part**; `fact_memory` has no speaker column yet. Facts *about* a guest told *by
  Michael* ("Jon loves hiking") still save (it's Michael's turn). Guest-memory attribution +
  speaker-aware retrieval = a later Part. **→ SUPERSEDED 2026-07-16 by ⚠ Stage 6 Phase 2 (end of
  this file): the write gate is now any KNOWN speaker, attributed via `source_speaker`.**
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
  slider), confirm the guardrail (guest turn writes no fact; Michael's still does).

### ⚠ Multi-speaker attribution — the prompt is NOT the carrier (2026-07-15)

First live multi-speaker session (Hillary enrolled) exposed the Part-1 design's real gap. Voice-ID
was perfect (`speaker: Hillary, 0.5655, known=True`) and Echo *still* answered her "I have a
headache" with **"Then let's lean into it, Michael. Close your eyes for a few minutes."** She had
the fact and used it wrong, because it never reached the model on the turn it described.

- **A per-turn fact must ride on the turn.** `main.tag_utterance()` prefixes the user message with
  `[Hillary] …` and the **tagged** text goes into `history`, so a turn keeps its attribution for the
  rest of the conversation (that is also what lets Echo resolve "she" vs "you" later). Active ONLY
  when `speaker_registry.count > 1`, resolved per turn (enrollment can add a voice mid-session), so
  the solo path stays byte-identical. Raw `transcript` still goes to the search decider, the memory
  gate, and the log — only the model sees the tag.
- **This is the load-bearing half.** Live A/B replaying the logged failure on the 12B: rewritten
  speaker block + UNtagged turns → still "Close your eyes for a minute, Michael." Both halves →
  "Rest is the only logical cure for a headache, **Hillary**… **Michael**, try not to let the
  silence get too heavy while she's horizontal." **Do not "simplify" this back to a prompt block.**
- **Tag format is `[Name] text`, never `Name: text`** — `CALIBRATION_EXAMPLES` are shaped
  `Michael: … / Echo: …`, so a colon-tagged user message reads as that script and invites a spoken
  "Echo:" prefix. Kokoro says whatever she writes.
- **`persona.MULTI_SPEAKER_NOTE`** teaches the convention + "never write a tag yourself and never
  read one aloud". Injected only while tagging, **after location, before the speaker block**, never
  trimmed. Independent of `speaker`: Michael's own turns are tagged too and carry the note with no
  speaker block. Mechanical convention — deliberately kept OUT of the approved persona strings.
- **The speaker blocks INSTRUCT, they don't describe.** The original "be warm… you may greet {name}
  by name" was a disposition and lost to the five Michael-shaped blocks in the same prompt (persona,
  calibration, location, Core, memory). Now: "Reply to {name} directly… never call {name} 'Michael'…
  Michael may not even be in the room." **APPROVED by Michael 2026-07-15.** `PERSONA_BLOCK` was NOT
  touched — its "address Michael as Michael. Always." is about Mike-vs-Michael, not about assuming
  who is talking.
- **Attribution is recorded per turn, never read from the live value.** `session.add_user_turn(
  speaker=…)` stores `speaker_name` at the time of speaking (`speaker` remains the ROLE field).
  The dashboard used to label every user turn with the live `current_speaker`, so a guest speaking
  silently re-labelled Michael's whole backlog — the readout lied about history.
- **The memory guardrail leaked at sign-off.** `get_conversation_text()` labelled everyone "User"
  and feeds `generate_summary`, whose prompt asks for "facts expressed by Michael" → `summary_text`
  → episodic memory. The per-turn gate skips guests; the summary is a **second write path** that
  didn't. It now uses the real names. **Any new memory write path needs the same attribution check.**

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
- **⚠ Remote access (phone, 2026-07-17) is via `tailscale serve`, NOT a `host` change.**
  `tailscale serve --bg --https=7862 http://127.0.0.1:7862` proxies
  **`https://skorp99.tail5c0851.ts.net:7862`** → the loopback dashboard: Flask never leaves
  127.0.0.1, only tailnet devices can reach it, and it's real HTTPS (a secure context — needed
  later for phone-mic `getUserMedia`, and it makes `navigator.clipboard` work remotely). Config
  persists across reboots in tailscaled; disable with `tailscale serve --https=7862 off`.
  **Never `tailscale funnel` for Echo** — this machine funnels `/`, `/ib`, `/camofox` on :443 and
  :8443 to the PUBLIC internet (claude.ai MCP access — deliberate, don't touch), which is exactly
  why Echo sits on her own dedicated tailnet-only port instead of a path under :443.
- **The offline overlay is a screensaver, not a static card (2026-07-17).** "ECHO IS OFFLINE"
  can sit on the 10" kiosk for hours, and a pixel-stationary white-on-black block will ghost
  into the panel. The message block wanders (driftX 53s / driftY 41s — out-of-sync periods,
  non-repeating path) and breathes its opacity (37s, .95→.45). **CSS-only on purpose: when the
  overlay is up the server is DEAD** — nothing in it may depend on a poll. ⚠ driftY and breathe
  share one element, so they live in ONE comma-joined `animation:` shorthand — a second
  `animation:` rule on the same element REPLACES the first (caught by headless Playwright
  measurement: x drifted, y sat frozen). Keyframes are center-symmetric, so every appearance
  starts dead-center (display:none resets animations — no jump). Same burn-in thinking applies
  to any future always-on kiosk state.
- **Transcript rendering must be a no-op when nothing changed.** The `/api/state` poll runs ~1×/sec
  and used to rewrite `#transcript`'s `innerHTML` unconditionally, which destroyed any text
  selection mid-drag — Michael: "it wont let me copy (nothing stays selected)". It now diffs a
  signature first and skips identical payloads; a **⧉ Copy** button covers the rest. The DOM holds
  user state (selection, focus, scroll), not just output — the same rule applies to any tile added
  later (cameras, sensors). Copy falls back to `execCommand` because `navigator.clipboard` needs a
  secure context, which the plain-http LAN/Tailscale address for the touchscreen is not.
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
`/api/state` gained `vad_available`, `vad_enabled`, `pending_model`, `voice`, `pending_voice`.
`vad_mode` still logs engine availability (`webrtcvad`/`ptt-only`), not the session toggle.

---

## ⚠ Stage 8.1 (2026-07-15) — launch without a prompt, no first-turn stall, voice picker

- **Startup is never interactive.** `llm._detect_model` resolves: pin (`--model`/`ECHO_MODEL`, exact
  or unique substring) → **`config.json last_model`** (the normal path) → the only model if exactly
  one → else **None**. `_pick_interactive` / `pick_model_interactive` are **deleted** — there is now
  **no `input()` anywhere in the runtime path**. Don't add one back; the dashboard dropdown is the UI.
- **Echo starts with NO model rather than blocking.** LM Studio unreachable is still a hard exit
  (the dropdown would be empty too, and start-echo.bat pre-flights it), but "nothing loaded" or
  "last_model is gone" just warns; the dashboard comes up and the dropdown re-queries every ~10s, so
  loading a model in LM Studio and picking it is enough. `run_streaming_pipeline` bails early with an
  explicit notice when `llm.model_name` is falsy — **after** the command short-circuits (those and
  enrollment need no LLM) and **before** the exchange counter advances or a search is spent.
  A model is deliberately NOT auto-picked from a multi-model list: LM Studio lists embedding models
  too, so "take the first" is a coin flip.
- **The first-turn stall was the Ib-Lite embedder.** all-MiniLM-L6-v2 was a lazy singleton loading on
  the first `encode()` — i.e. during turn 1's memory retrieval. Measured 2026-07-15: **first encode
  10.2s, every one after 0.004s** (weights were already cached — it's the load, not a download).
  `embedder.preload()` now runs at startup next to the other engine loads. STT/TTS were already
  eager (STT init 0.9s, first transcribe 0.1s; TTS warms + keep-alives), so this was the whole stall.
- **Kokoro voice picker.** `TTSEngine(voice=...)` — the voice is per-instance and swappable;
  `VOICE = "af_heart"` is only the fallback. Persisted as `voice` in **config.json** (like
  `last_model`). `tts.list_voices()` hits `:8880/v1/audio/voices` (67 available live). Same
  park-for-the-main-loop contract as the model: `/api/voice` sets `control.pending_voice` and
  `main.do_voice_swap` applies it between turns — **never mid-reply**, because synthesis is
  chunk-by-chunk and a live swap would change voice mid-sentence. So a new voice is heard on her
  NEXT reply, which is also the audition loop.
- **Harness note:** `test_personality.py` / `test_hold_20turn.py` used to get their model from the
  picker; they now pass `last_model=load_config().get("last_model")` (ECHO_MODEL/--model still win).
  Side benefit: `test_personality.py` now runs **fully unattended, offline + live** — it previously
  died on `EOFError` at the picker without a TTY.

### Voice preview button (Stage 8.2)
- **`persona.VOICE_PREVIEW_LINE`** — the sample Echo speaks for the dashboard's ▶ Preview button.
  It lives in `persona.py` because it is **character content Michael hears verbatim** (not a system
  prompt — it's literal text Kokoro says). **APPROVED as-is by Michael 2026-07-15.** Deliberately
  FIXED, not random: auditioning ~67 voices is an A/B test, only fair if the line is identical
  every time.
- **`tts.synthesize(text, voice=...)`** takes a one-off override that does NOT change the active
  voice — a preview must never become a commitment (asserted in `test_webui.py`).
- **Same park-for-the-main-loop contract**: `/api/voice/preview` → `control.pending_preview` →
  `main.do_voice_preview` plays it while idle. **The mic is paused for the duration** — in LISTENING
  the stream is live, so hands-free VAD would hear the preview and Echo would answer herself.
  Playback is synchronous (`audio_q.finish` blocks), so the mic is back before it returns.
- **Also serviced in the MUTED branch**: mute is about the MIC, not the speaker, so auditioning while
  muted must work — otherwise the preview would queue and never play.
- Sample measures ~4.4–5.0s across voices; the UI button re-enables at 6s so it can't unlock
  mid-sentence.

---

## ⚠ Stage 8.3 (2026-07-15) — VRAM contention is the #1 local-model failure here

**This machine almost always has something else on the GPU** — Invoke generating images, or a model
Michael forgot he left loaded in LM Studio (his words, 2026-07-15). 16GB card. **When anything
involving a local model fails, suspect VRAM before the code.**

Why it hides so well:
- Echo's 12B is **NOT resident at launch** — LM Studio JIT-loads it on the FIRST request. So a
  shortage bites at the first thing Michael says, not at startup, and looks like a pipeline bug.
- LM Studio **drops the connection** when a load OOMs → `APIConnectionError`, identical to "the
  server is down". Echo used to print *"LM Studio not detected — please start LM Studio"*, sending
  him to check a server that was running fine. That message is now fixed.
- **`/v1/models` lists every model regardless of load state**, so it never hints at the problem.

What's built:
- **`gpu.py`** — `vram_usage()` → `(used_mb, total_mb)` and `vram_hint()` → one line with REAL
  numbers. nvidia-smi via `subprocess` (Windows-native, **no new dep** — same approach as
  `location.py`), 2s cap, fail-soft (`None`/`""`, callers just lose the hint). `vram_hint`
  deliberately reports numbers rather than guessing a "too full" threshold: what counts as tight
  depends on the model being loaded, and a wrong guess is worse than none.
- **`llm.model_state()`** → `loaded` / `not-loaded` / `unknown` from LM Studio's **native**
  `/api/v0/models` (the ONLY endpoint carrying per-model `state`; `/v1/models` can't answer this).
  **`not-loaded` is NOT an error** — it's normal before the first turn. It only means trouble
  *next to a full card*.
- **Dashboard**: Model-residency dot + a **VRAM tile** (used/total, amber ≥55%, red ≥85% — advisory
  only). Health is cached ~5s, so the nvidia-smi call is not hot.
- **`main._print_vram_hint()`** after any LLM timeout/error, so the cryptic failure names its suspect.
- Diagnosis by hand: `lms ps` (what's resident), `nvidia-smi` (idle desktop baseline ≈3.0/16.3 GB),
  or set LM Studio's **Max Loaded Models = 1**. Cross-project gotcha — also in Hindsight `axly-infra`.

---

## ⚠ Stage 8.4 (2026-07-16) — sessions persist per turn; some voices are furniture

### Sessions save after EVERY turn — do not "optimise" this back to the end

`save_session_file()` runs after each completed turn (`main.py`, right after `logger.log_run`).
It used to run ONLY at the end — sign-off or a clean exit — and there was no `stop-echo.bat`,
so the only way to stop Echo was closing the window, which hard-kills the process before either
save. **Every conversation from 2026-07-14 to 2026-07-16 was thrown away**, including the 3-way
Hillary session; `speaker_name` (added 2026-07-15) had never once reached disk. Autopsy:
`tasks/lessons.md` 2026-07-16.

- It's a full idempotent rewrite (~10KB, a few ms) that re-stamps `ended_at`, so per-turn needs
  nothing from `session.py`. The end-of-run saves stay — they also write the summary.
- **A GUI-driven process has no reliable "end."** Anything that must survive belongs on the
  per-turn path, not the shutdown path.
- **`stop-echo.bat` / `restart-echo.bat` now exist** (the global start/stop/restart convention;
  Echo had shipped start-only). They try `POST /api/quit` first — the same graceful path as the
  dashboard's Stop button — and force-kill only if that doesn't take.
- **The kill filter is `ExecutablePath -like '<repo>\*'`, never CommandLine.** Verified against
  every python.exe on this box (2026-07-16): ExecutablePath → **1** proc (correct);
  `CommandLine -match '.venv\Scripts\python.exe'` → **6**, including **Kokoro-FastAPI, Echo's
  own TTS**; `CommandLine -match 'Echo'` → **14**. Echo's command line is the RELATIVE
  `".venv\Scripts\python.exe  main.py"` and contains no "Echo" at all, so a CommandLine filter
  cannot work. **The venv launcher spawns the base interpreter as a CHILD whose ExecutablePath
  is the GLOBAL python** — no path filter can see it; it's found by ParentProcessId and must be
  killed too, or a force-stop leaves half of Echo holding the mic and the port.

### Ignored voices — Echo recognises them and says nothing

`Kairos` (Michael's own Kokoro clock app on the Mac) announces the time aloud; Echo's mic heard
it and she replied **every 30 minutes for a day**, weaving it into the live conversation. Voice-ID
worked perfectly and didn't help: it returned `unknown` (0.09–0.17), and `unknown` gets the
*courteous guarded stranger* treatment — and strangers get answered. There was no concept of a
voice that isn't a person. Config: `ignore: true` per profile in `echo_speakers.json`.

- **`identify()` still MATCHES an ignored print — do not "fix" this.** Filtering them out of the
  match loop recreates the bug exactly: the clock falls to `(None, score)` → `"unknown"` →
  `SPEAKER_UNKNOWN` → a polite reply. **You have to recognise a voice to decline it.** The flag
  is read *after* the match, via `is_ignored()`. `test_speaker_id.py` asserts this directly.
- **`active_count`/`active_names` = PEOPLE; `count`/`names` = all prints.** One counter was
  answering two questions. `active_count > 1` drives `[Name]` tagging — using `count` would make
  a solo Michael + a clock start tagging his own turns and injecting `MULTI_SPEAKER_NOTE`,
  breaking the guarantee that a solo roster stays byte-identical to pre-Stage-6. It also feeds the
  dashboard chips, the startup line, `michael_enrolled`, and `stale_prints()`. **But the identify
  gate is deliberately `count > 0`** — a roster of only Kairos must still fingerprint, or she'd
  answer the clock as Michael.
- **`enroll()` preserves `ignore` by default** (`ignore=None` → carry the existing flag). It
  replaces the whole profile dict on a name collision, so a plain re-enroll would silently
  un-ignore the clock and it would start talking again weeks later, apparently spontaneously.
- **The drop sits after `identify()` and before everything that commits**: `add_user_turn` (never
  reaches the transcript, the dashboard, the session file, or the sign-off summarizer — a second
  memory-write path), `increment_exchange` (anti-drift cadence stays honest), `decide_search` (an
  LLM call), and `audio_q.start()`. `return None` — the `[Too short]` contract.
- Enroll one with the dashboard's **"Not a person"** checkbox (arm-and-wait — a clock only speaks
  on the half hour, so a CLI that records on cue can't catch it) or `enroll.py <name> --ignore`.
  `max_profiles` (default 10) is a runaway guard: refuses a NEW name, never blocks a re-enroll.
- Generalises to a TV, a podcast, or Echo hearing her own replies.

---

## ⚠ Stage 8.5 / Phase 3 (2026-07-16) — History page + Memory browser/editor

Two new **read/edit surfaces on the dashboard** (`webui/`) so Michael can see her past
conversations and hand-fix bad memories without the CLI or touching code. Both are additive +
fail-soft: if the dashboard is off, the voice loop is byte-identical. No new dependencies (Flask,
sqlite-vec, `ib_lite.embedder.encode`, httpx all already in play). Plan/checklist: `tasks/todo.md`.

### History (`/history`, `webui/history.py`)
- **Backed by `logs/stage0_log.jsonl`, NOT `sessions/`** — the per-turn append survives hard kills,
  carries resolved speaker names, and reaches back to April (the `sessions/` files only exist from
  07-15 on, after the per-turn-save fix). Read-only.
- `read_history(log_path, q, speaker, limit)` is a **pure function** (unit-tested against a temp
  log): tolerant JSONL parse (bad line / missing key skipped, never fatal — the field set has grown
  since April), groups turns into sessions, newest first. **New records carry `session_id`** (added
  to `logger.log_run` in `main.py`) for exact grouping; the ~90 legacy rows group by a **20-min
  timestamp gap**. Records are sorted before grouping (a rescued/merged log could be out of order).
- Served via `EchoControl.history()` → `GET /api/history`; page is `webui/static/history.html`
  (session cards, You/Echo bubbles, per-turn meta: score/known, location, latency, 🔎 query,
  🧠 memories; search + speaker filter; 20s live-tail that only re-renders on change).

### Memory browser/editor (`/memory`, `webui/memory_admin.py`)
- **A web front-end over the same curation `ib_lite_cli.py` does.** The web thread opens its **OWN**
  `db.get_connection()` per request via `memory_admin.open_conn(control.memory_db_path)` — it NEVER
  shares `IbLite._conn` (main-thread only; sqlite3 conns aren't thread-safe). `open_conn` sets
  `PRAGMA busy_timeout=4000` so a concurrent background significance-gate write can't error the
  editor with "database is locked" (WAL + a private connection — the same isolation `_insert` uses).
- **`control.memory_db_path`** (new EchoControl arg, default None → the real `echo.db`) exists so
  tests inject a temp DB — a memory edit/delete route must never mutate Michael's production memory
  (same rule as the temp `echo_speakers.json` in the speaker tests).
- **Two schema-driven rules, both load-bearing:**
  - **Editing a fact's VALUE re-embeds it** (`encode(f"{entity} {attribute} {value}")`, injectable so
    tests stay model-free) so semantic retrieval keeps finding it, and the plain UPDATE fires
    `fact_touch` + `fact_fts_update` so BM25/FTS stays in sync. Confidence-only edits skip the
    re-embed (no value change → no `encode`).
  - **Episodic summaries are VIEW + DELETE only.** `episodic_fts` has an insert and a delete trigger
    but **no AFTER UPDATE trigger**, so a summary edit via UPDATE would silently desync its search
    index. Delete is safe (delete trigger exists). Do not add episodic-summary editing without first
    adding that trigger.
- **Edit scope (v1):** facts → value + confidence + delete; core/pref → content/value (upsert) +
  delete; policy → rule + priority + **active toggle**, existing rows only (the editor tweaks
  seeded/gate-authored rules, it doesn't mint new behavioral rules from the touchscreen); episodic →
  view + delete. **`sessions` is never deletable** (FK parent of facts/prefs/policies/episodic).
  No fact entity/attribute renaming (UNIQUE(entity,attribute) ON CONFLICT REPLACE would silently
  delete a colliding row — delete-and-let-the-gate-relearn is the safe path).
- The `/memory` search box runs the **real hybrid retrieval** (`fact_search`/`episodic_search`) and
  shows the actual scores — genuinely useful for seeing WHY a bad fact surfaces (or whether
  down-ranking its confidence hid it). Route: `GET /api/memory/search`.
- **Security:** memory content is more sensitive than talk/mute. Same surface caveat as the rest of
  the dashboard — binding off-loopback (`host` ≠ 127.0.0.1 for the touchscreen) exposes reading AND
  editing Echo's memory to that network. No auth in v1 (Michael's call: "nothing crazy"); keep it on
  loopback / a trusted Tailscale net. The page carries this warning inline.
- **Tests:** `test_webui.py` gained `run_history` (grouping / gap heuristic / q+speaker+limit /
  bad-line + missing-file tolerance), `run_memory` (edit_fact re-embed → `fact_fts` tracks the new
  value + drops the old; core/pref/policy edits; delete incl. FTS sync; sessions/unknown refused),
  and `run_routes` (the Flask routes against a temp DB; confidence-only fact edit keeps it
  model-free). Real-bind HTTP smoke confirmed both pages serve, a real value edit re-embeds and the
  hybrid search finds it (score 0.811, exercising real `encode` + sqlite-vec), and the real 104-row
  log grouped into 20 sessions. All prior offline suites green.
- **v1 out of scope (later):** memory *add* from the UI (facts are gate-authored; add core/pref by
  editing an existing key), undo, per-turn deep-links from History into Memory, auth.

---

## ⚠ Stage 6 Phase 2 (2026-07-16) — Guest memory + the loyalty register

Guest memory is real: **any KNOWN (enrolled, non-ignored) speaker's turns write to memory,
attributed**; an unknown voice **reads and writes NOTHING**. This closes the "I will remember
you, Hillary" problem — that promise is now structurally true for enrolled people. Plan (incl.
the approved register wording): `~/.claude/plans/squishy-stirring-bentley.md`.

- **`fact_memory.source_speaker`** = who SAID a fact; `entity` stays who it's ABOUT.
  `UNIQUE(entity, attribute)` unchanged — a re-statement UPSERTs value AND provenance. Added by
  the `user_version=2` migration in `db.py`: a `PRAGMA table_info`-guarded ALTER (fresh DBs get
  the column from the schema file) + backfill of legacy rows to `'Michael'` (honest, not a
  guess: the Part-1 guardrail let only Michael write facts).
- **⚠ The migration rebuilds `fact_fts` BEFORE the backfill UPDATE.** The `fact_fts_update`
  trigger's external-content 'delete' step raises **"database disk image is malformed"** for any
  row the index doesn't already hold — rebuilding first (idempotent, tiny table) repairs drift
  instead of bricking the DB. **Any future bulk UPDATE on `fact_memory` needs the same rebuild
  or a verified-in-sync index.** Found by `test_guest_memory.py`, not in production.
- **`source_speaker` is pipeline ground truth, never model output.** `main.py` passes
  `speaker=session.current_speaker` → `IbLite.write_memory(speaker=)` → `_gate_worker` →
  `_insert` stamps the row. The gate's JSON has no attribution field, so a hallucinating model
  cannot misattribute a write.
- **The gate resolves "I" to the labelled speaker** (`significance.py`): `GATE_SYSTEM` widened
  to "the person speaking" + "facts a person states about themselves use THEIR name as the
  entity"; the user message is built by the pure, offline-tested `_build_user_content(...)`,
  which adds a speaker line only for non-Michael turns — **a Michael turn's gate prompt is
  byte-identical to pre-Phase-2**. Live-verified on the 12B: Hillary "I'm allergic to
  shellfish" → `entity=Hillary`; Hillary "Michael's brother Dave moved to Austin" →
  `entity=Dave`; Hillary "I have a headache right now" → `save:false` (the NEVER-save list
  applies to guests too).
- **The write gate is `session.current_speaker_known`** (a resolved name, not "unknown";
  feature off → Michael → True, solo path unchanged). `current_speaker_is_michael` remains as
  the OWNER check, no longer the memory gate.
- **Unknown gets nothing (Michael's call, 2026-07-16):** an unknown speaker's turn skips
  `read_memory` entirely AND `build_context_block(include_profile=False)` drops the core
  profile + preferences — behavior policies + voice guidance only. Structural: the knowledge
  is not in the prompt, rather than an instruction not to share it (prompts lose under
  pressure — the Hillary-attribution lesson).
- **Speaker-ID + the ignored-voice drop moved ABOVE the command guards** in
  `run_streaming_pipeline` (enrollment capture stays first — it consumes the utterance as the
  voiceprint). The clock can no longer sign off, forget, or flip location; command turns record
  the right `speaker_name`; the forget guard knows who's asking. Same per-turn cost — the embed
  just runs earlier; command turns gain one ~0.1s CPU embed.
- **Forget rights** — `can_forget()` (pure, module-level in `main.py`, like `trim_to_preroll`):
  Michael → anything; a known guest → only a fact THEY said (checked against
  `ib.peek_last_fact()["source_speaker"]` — peek is the new non-destructive, lock-guarded read);
  unknown → never. Decline line (approved): *"That one isn't yours to take back — Michael can
  ask me himself."*
- **The loyalty register** rides in `SPEAKER_KNOWN`/`SPEAKER_UNKNOWN` (`persona.py`; wording
  approved with the Phase 2 plan): she keeps no secrets from Michael, ever — dry humor when the
  stakes are light, wit dropped entirely when the moment is genuinely vulnerable — and she
  **never promises secrecy or a memory she won't honour**. The memory claims in both blocks are
  structurally TRUE (known guests really are remembered; unknown really is not), which is the
  point: register and guardrail can't drift apart. `_MEMORY_BLOCK_HEADER` is now
  speaker-neutral ("from previous conversations" — facts may come from any known speaker).
- **Surfaces:** `/memory` fact cards + hybrid-search hits show *told by X* (display-only —
  there is deliberately NO route that edits provenance); `ib_lite_cli.py` `facts`/`list`
  include it; `retrieval.fact_search` selects it (injected memory-line format unchanged in v1).
- **Tests:** `test_guest_memory.py` (migration incl. the FTS-rebuild hazard, `_insert` stamping,
  peek/forget, context gating, header) + extensions to `test_significance.py`
  (`_build_user_content`), `test_speaker_id.py` (known-gate + `can_forget` matrix + register
  strings), `test_webui.py` (provenance in `dump_all`). All offline suites green; live gate
  smoke passed. **Michael must restart Echo (restart-echo.bat) to load Phase 2.**
- **Speaker-aware retrieval — BUILT 2026-07-18** (was the deferred item here): when a known
  NON-Michael speaker is on the mic, `retrieval.speaker_facts()` (deterministic entity-match,
  case-insensitive, `MIN_CONFIDENCE`-gated, `SPEAKER_K=3`) front-loads facts ABOUT them into
  the memory block via `read_memory(query, speaker=)` — the hybrid search only matches the
  TRANSCRIPT, so a guest's "hey Echo" would otherwise surface nothing about them. No embedding
  call, no change to the tuned hybrid scoring; front placement survives the tail-first budget
  trim; deduped against hybrid hits. **Michael/None → byte-identical block** (his profile is
  already structurally present via core_memory; solo path asserted unchanged in
  `test_guest_memory.py run_speaker_retrieval`). Unknown speakers still skip `read_memory`
  entirely. Enrollment spelling rule (2026-07-18, Michael): **enroll people under the spelling
  Whisper produces** (his friend Jon enrolls as "John") — the transcriber re-votes its spelling
  every turn, and entity/FTS matching is string-exact; fighting it splits the person.
- **Out of scope (later):** provenance in the injected memory line ("Hillary told me…"),
  episodic `source_speaker` (summaries are multi-speaker by nature and already attributed by
  real names), auth.

---

## ⚠ Remote Voice (Level 2, 2026-07-17) — talk to Echo from the phone

Michael can hold Talk on his phone and converse with Echo from anywhere on the tailnet:
`/remote` (phone-first page) → `POST /api/remote/turn` (the recorded blob) → the FULL
standard pipeline → the reply WAV returns in the response and plays on the phone ONLY
(his call — PC speakers stay silent for remote turns). Rides the Level-1 `tailscale serve`
HTTPS URL (`https://skorp99.tail5c0851.ts.net:7862/remote`) — **getUserMedia requires a
secure context, so the plain-http LAN address cannot record**; the page says so. Level 1
note: remote access is `tailscale serve --bg --https=7862`, NEVER `funnel` (this box
funnels :443/:8443 publicly for claude.ai MCP — Echo must not share those listeners).

- **`webui/remote_audio.py`** — the whole remote mode is ONE substitution:
  `RemoteAudioSink` quacks like `audio_queue.AudioQueue` (start/enqueue/finish/wait_done,
  all non-blocking) but COLLECTS synthesized chunks instead of playing them. Passing it as
  `audio_q` runs `run_streaming_pipeline` byte-identically — speaker-ID (+`voiced_only`),
  guardrails, commands, search — with silent PC speakers; `sink_to_b64()` hands back the
  reply WAV. **Do not add a `remote` branch inside the pipeline** — the sink IS the branch
  (the only pipeline change is the `remote=True` JSONL field). The search filler simply
  rides at the front of the reply WAV (in-character, zero special-casing).
- **`decode_to_pcm16k()`** — phone blob (iOS mp4/AAC, Android webm/Opus, anything) →
  16 kHz mono float32 via **PyAV, already shipped by faster-whisper — no new dep**. The
  resampler is FLUSHED (`resample(None)`) or the tail of the last word is silently dropped.
  Fail-soft None → 400, never an exception into Flask.
- **Park contract (single-flight):** `control.submit_remote_turn(pcm)` parks
  `{audio, event, result}`; the request thread BLOCKS on the event
  (`REMOTE_WAIT_S=120` — generous because the FIRST turn may JIT-load the 12B; timeout →
  504, the turn may still finish at the desk). `take_pending_remote()` (main loop, next
  idle tick) claims + marks busy; a second POST meanwhile → 409, never queued.
  **This slot has a real `threading.Lock` — the one deliberate exception to the no-locks
  house pattern** (Flask is threaded; two simultaneous phone POSTs would race the
  check-then-park). `finish_remote_turn` publishes + wakes + re-arms in a `finally`, so a
  pipeline exception can never orphan the waiting request.
- **`main.handle_remote_turn`** (closure, main loop only): stops the room mic (like any
  PROCESSING turn), walks state PROCESSING→(SPEAKING)→back to origin, and is serviced in
  **LISTENING and MUTED** — mute silences the ROOM mic; a phone turn never touched it
  (same reasoning as the voice preview). Response texts are read back from
  `session.turns` (the pipeline already recorded them). **Sign-off works from the phone:**
  the goodbye WAV goes back to the phone, then `run_signoff` runs at the desk with a
  throwaway sink (summary/episodic write as normal, desk speakers silent).
- **`remote.html`** — press-hold Talk (pointer events; `touch-action:none` +
  `-webkit-touch-callout:none` against iOS long-press); **iOS unlock: the AudioContext is
  created/resumed during the press gesture**, which is what allows playback after the
  async fetch — don't "simplify" to a bare `<audio>` tag, autoplay policy will eat it.
  Sub-350ms holds are discarded client-side. Poll re-renders only on change (the DOM
  holds selection — house rule). Shows `heard as <speaker> (score)` per turn — that's the
  phone-mic speaker-ID readout the live pass tunes against.
- **Uploads capped** (`MAX_CONTENT_LENGTH` 32 MB — runaway guard; a minute of AAC ≈ 1 MB).
- **Tests:** `test_remote_voice.py` (15 offline checks: decode incl. real AAC round-trip,
  sink, park contract, route incl. 400/409/504) + a real-browser smoke (headless Chromium,
  fake mic → real webm/Opus upload → PyAV decode → reply rendered + played). All prior
  suites green.
- **Open (Michael):** the live pass — FIRST check his speaker-ID score through the phone
  mic (different mic character than the desk; fold a phone sample into his print if it
  sags), then a real conversation, a guest/unknown check, sign-off from the phone.

---

## ⚠ Visual Input Level 1 (2026-07-17) — a photo rides a spoken turn

Michael attaches a photo on `/remote`, then holds Talk and asks about it — the photo rides
that spoken turn through the FULL standard pipeline. Groundwork for the camera pipeline
(a separate later build). Plan: `~/.claude/plans/twinkling-dancing-quiche.md`.

- **The production 12B IS the vision model.** LM Studio's native `/api/v0/models` reports
  the Hauhaucs 12B quant as `type: "vlm"` (Gemma 4 12B is natively multimodal; the projector
  is present). **Live-verified 2026-07-17**: an image via the OpenAI content-array form →
  "Red" in 5.2s; a follow-up with the image riding in HISTORY → 0.2s. No second model, no
  VRAM slot fight, persona intact — the "12B native vision inverts the shrink premise" note
  proving out. `llm.supports_vision()` probes that endpoint (`type=="vlm"`, **own ~10s TTL
  cache** — the dashboard snapshot polls ~1×/s; fail-soft True so LM Studio stays the
  authority).
- **The camera seam is `run_streaming_pipeline(image_b64=, image_mime=, image_file=)`** —
  any producer (today the /remote upload, later a camera) attaches an image to a turn the
  same way. `llm.image_content()` is the single source of the content-array wire format;
  `_build_messages` puts it on the final user message only. Command turns, ignored voices,
  and the enrollment capture return before the LLM call, so a photo there is ignored free.
- **Attach-then-talk, no silent send (Michael's call):** every turn still has a voice, so
  speaker-ID/attribution/guardrails keep working. The photo + audio go up in ONE multipart
  POST to `/api/remote/turn`; the raw-body voice-only shape is unchanged (both branches
  live in the route). **Image problems degrade to a voice-only turn** (`image_dropped:
  not-an-image | too-large | model-not-vision`) — a photo must never cost the sentence
  Michael just spoke. Audio missing → the same 400s as before.
- **Keep-latest-photo (Michael's call):** the photo turn's history entry keeps its image
  (follow-ups work — live-verified fast via prefix cache); when a NEW photo arrives,
  `llm.collapse_image_history()` flattens older image entries to text + a mechanical
  placeholder. At most one image in context. **`do_model_swap` also collapses when the new
  model isn't vlm** — history survives swaps, and a text-only model would otherwise choke
  on the image parts on EVERY later turn, not just once.
- **Search is SKIPPED on photo turns** (`decide_search` never runs): the decider only sees
  the transcript, so "what kind of snake is this?" + photo would fire a keywordless
  nonsense web search. If she needs the web after seeing it, that's a follow-up turn.
- **Photo turns are budget-exempt** like search turns (`passed_budget=None`, `[VISION
  (exempt): ...]` print) — vision prefill blows TTFT by design. New JSONL fields:
  `image_attached`, `image_file`.
- **Photos save to `logs/photos/`** (gitignored via `logs/`; `remote_audio.save_photo`,
  fail-soft — a failed save never blocks the turn). The client downscales on-canvas
  (≤1600px JPEG .85) which also **strips EXIF/GPS** from what's saved; the browser applies
  EXIF orientation while decoding, so pixels arrive upright. **No PIL / no new deps** —
  the server sniffs magic bytes only (`sniff_image_mime`: JPEG/PNG/WebP).
- **`remote.html`:** the attach row is a FIXED-height row that always exists — attaching/
  clearing a photo must never move the Talk button (the thumb-target rule). The file input
  is `accept="image/*"` **without `capture`** (capture forces the camera and blocks the
  library picker — the epona lesson). The photo is cleared only on a successful turn;
  409/504/network errors keep it so a retry still carries it. `vision_capable` in
  `/api/state` greys the 📷 button out BEFORE recording when a non-vlm model is active.
- **Tests:** `test_vision.py` (llm seam: content array, collapse, supports_vision TTL) +
  `test_remote_voice.py` vision sections (sniff/save on temp dirs — never the repo's
  logs/photos —, slot fields, multipart route incl. all three degrade paths) + a
  headless-Chromium smoke (real page: file input → createImageBitmap → canvas JPEG →
  multipart → park → 📷-marked reply, Talk button pinned).
- **Out of scope (later):** the camera pipeline, dashboard/kiosk upload, photos on the
  /history page, multi-image context, memory-gate awareness of images (her spoken
  description flows through the existing text defenses).

---

## ⚠ Chat Interface (typed turns) + Location Hint (2026-07-18)

Echo has a **text lane**: a phone-first **`/chat` page** (header-linked everywhere) where a
typed message runs the FULL standard pipeline — commands, search, memory gate, speaker-aware
retrieval, persona — and the reply comes back as **text only** (Kokoro never runs). Plan:
`~/.claude/plans/ticklish-cuddling-sifakis.md`. The voice layer was always a *transducer*
(STT produces a transcript, TTS reads the reply); this is the input-side counterpart of the
Remote Voice sink substitution.

- **`run_streaming_pipeline(typed_text=...)`** is the whole entry: skips the length guard +
  STT (the text IS the transcript), skips speaker-ID, and sets `no_tts` guards on every
  synthesize site (command replies, search filler, streaming chunks, the remote goodbye).
  The reply text lands in `session.turns` as usual — that's what the route reads back.
  `audio` is None on typed turns. `session.last_speaker_score` is NOT updated (the dashboard
  meter keeps showing the last real voice match).
- **All typed text IS Michael — by policy, not verification** (his call 2026-07-18; Hillary
  prefers voice and would never type). Declared identity under the same trust model as the
  rest of the dashboard (device custody = authority). So typed turns read AND write memory
  as Michael turns; there is no guest picker.
- **Typed commands work** (they're text guards): typed sign-off returns a text goodbye and
  runs the summary at the desk; typed "Echo, this is John" ARMS enrollment — but a typed
  turn can never CONSUME an armed capture (no voice to fingerprint; arming survives it, so
  type-the-command-then-John-speaks composes).
- **`TEXT_GUIDANCE`** (ib_lite.py, next to VOICE_GUIDANCE — wording Michael-approved with
  the plan) replaces "you are speaking aloud" via `build_context_block(typed=True)`. Note
  the guidance block rides inside `core_block`, so it (like VOICE_GUIDANCE) is absent when
  Ib-Lite is unavailable.
- **Same single-flight slot as Remote Voice**: `submit_remote_turn(typed_text=, location_hint=)`
  (audio None) → `handle_remote_turn` branches — a phone voice turn and a typed turn can
  never interleave (409). `POST /api/chat/turn` (JSON `{text, location?, image_b64?}`)
  mirrors the remote route's 400/409/504; the response strips any audio fields by contract.
  A photo rides a typed turn through the same sniff/save/degrade rules as attach-then-talk.
- **Location hint (the Colorado enabler)**: `location` on BOTH `/api/chat/turn` (JSON) and
  `/api/remote/turn` (multipart form field; query param on the raw-body shape) →
  slot → `run_streaming_pipeline(location_hint=)` → a **per-turn override** of the register
  location. **Deliberately never touches `session.location`** — no VAD side-effects at the
  desk, nothing sticky. Unrecognized values degrade to auto (`_clean_location`). UI: an
  Auto|Home|Jeep|Away segmented row on `/remote` and `/chat` (shared localStorage key
  `echo_loc_hint`). `LOCATION_CONTEXTS["away"]` is Michael-approved persona content;
  Colorado later = one named entry + one button.
- **Typed turns are budget-exempt** (`passed_budget=None`, `[TYPED (exempt)]` print — the
  <3s budget measures speech-to-speech). New JSONL: `typed`, `location_hint`;
  `speaker_score` is null on typed turns.
- **Tests:** `test_chat.py` — the TTS stub RAISES if called on a typed turn (silence proven
  structurally), STT booby-trapped the same way, solo/voice path asserted byte-consistent
  (VOICE_GUIDANCE + typed=False), armed-enrollment-survives, hint-doesn't-stick, route
  400/409 + case-normalized hint + garbage-hint degrade. Headless-Chromium smoke drove the
  real page (send → bubble, Enter sends, hint rides the POST, Auto clears, localStorage
  persists). All 10 offline suites green.
### Chat streaming + document attach (same day)

- **Replies stream** (Michael: the block "arrives all at once" — fixed): the pipeline gains
  `on_sentence` — the text counterpart of sentence-by-sentence TTS, called per reply
  sentence on typed turns, never raises into the loop. The park slot
  (`submit_remote_turn(stream=True)`) carries a `queue.Queue`; **`finish_remote_turn`
  pushes the ("done", result) sentinel** — it runs in the handler's `finally`, so a
  pipeline exception can never hang the drain. `/api/chat/turn` with `stream:true` returns
  **NDJSON** (one `{"sentence":…}` line each, then a `done:true` trailer with the
  authoritative result; audio fields stripped); non-stream JSON shape unchanged (tests use
  it). The page renders sentences live into the thinking bubble, then the trailer replaces
  the assembled text. Timeout is in-stream (`{"done":true,"ok":false,"error":"timeout"}`),
  not a 504.
- **Documents attach like photos** (📎 on /chat): extracted to PLAIN TEXT on the web thread
  by **`webui/doc_extract.py`** — txt/md/csv/log/json/code, **PDF (pypdf)**, **Word
  (python-docx)**; fail-soft None; `DOC_MAX_BYTES` 8 MB upload guard, `DOC_MAX_CHARS`
  24k extraction cap with a truncation marker. **NEW DEPS `pypdf==6.14.2` +
  `python-docx==1.2.0`** (both pure-Python installs; torch verified untouched). Degrade
  rule as with images: `doc_dropped: not-a-doc | too-large | unreadable` — never costs the
  typed question.
- **The doc rides the LLM message only** (`llm.doc_content` — deterministic fences that
  `collapse_doc_history` parses back apart): transcript/log/**gate** see just the typed
  question — a 20k-char document must not flood the memory gate, same reasoning as the
  gate never seeing a photo's pixels. **Keep-latest-doc** in history (marker-prefixed user
  entries collapse to header + question when a NEW doc arrives; idempotent). Search is
  skipped on doc turns like photo turns (the decider only sees the transcript). New JSONL:
  `doc_attached`, `doc_name`.
- **Docs ride SPOKEN turns too (2026-07-18, same night):** 📎 on `/remote` — attach-then-talk
  like a photo (`doc` multipart part → same extraction/degrade path → the slot). Ask about
  the file aloud; she answers aloud.
- **Out of scope (later):** agentic abilities (chat is the substrate, not the feature),
  guest identity picker, a speak-typed-replies toggle, the Colorado named location, chat on
  the kiosk, auth, OCR for scanned PDFs.


---

## ⚠ Configurable LLM Endpoint (2026-07-19) — Sindri replaces LM Studio

Echo's LLM server is now **Sindri** (`H:\AxlyGitHub_H\Sindri`) — Michael's llama.cpp
`llama-server` GUI with a **swap proxy on `http://127.0.0.1:4610/v1`**: OpenAI-compatible,
routes = opted-in profiles (each an id in `/v1/models`), backends **JIT-spawned per request**
(one resident at a time, previous drained and stopped). Measured 2026-07-19: ~95 tok/s on
Sindri vs ~60 on LM Studio for the same model.

- **Resolution: `ECHO_LLM_URL` env → config.json `llm_base_url` → LM Studio `:1234` default**
  (the `stt_model` pattern; rollback = delete the config key). Resolved ONCE at import in
  `llm.resolve_llm_base_url()` → **`llm.LLM_BASE_URL`, the app-wide single source**. Values
  are normalized (scheme added, trailing `/` stripped, `/v1` appended exactly once) so a
  hand-typed URL can't produce a double-`/v1` path.
- **Who gets it how:** `LLMClient`, `search_decision.decide_search`, `persona_check.
  run_self_check`, `summarizer.generate_summary`, `eval_persona_matrix`, `smoke_ib_lite`
  import/default to `llm.LLM_BASE_URL`. **ib_lite stays self-contained**: `IbLite(model_name,
  lm_base=)` threads it to `run_gate` (the `set_model` pattern) — significance.py keeps its
  own `DEFAULT_LLM_URL` fallback and imports nothing top-level. The dashboard health probe
  gets `lm_studio_url=f"{LLM_BASE_URL}/models"` from `main.py` (the wire key stays
  `lm_studio` in `/api/state`; the tile label now reads "LLM Server"). `start-echo.bat`
  pre-flights the CONFIGURED url via a venv-python one-liner importing `llm` — the launcher
  can never disagree with what Echo dials. **Never add a second hardcoded endpoint.**
- **`llm.model_state()` speaks both native dialects:** LM Studio's `/api/v0/models` (per-model
  `state`) first, then **Sindri's `/health`** (`service=="sindri-proxy"`; `resident[]` holds
  live backends — only `state:"running"` counts as loaded, matched via `_route_slug()`, a
  mirror of Sindri's `routeSlug`). Live-verified both ways: `not-loaded` before the first
  request, `loaded` after. `supports_vision()` stays fail-soft **True** on Sindri (no `type`
  field there — LM Studio's native endpoint was the only source; the server itself is the
  authority and reports clearly if a model can't take images).
- **Sindri behavior worth knowing:** first request to a cold route JIT-spawns llama-server
  (live smoke: 5.1s including spawn; the proxy queues up to 120s). During a cold **streamed**
  request the proxy emits SSE comment heartbeats (`: sindri-loading`) — the OpenAI SDK skips
  comments, so Echo just sees a longer TTFT. Requests are routed by the `model` field;
  `last_model` ids from LM Studio won't match Sindri routes — with exactly one route Echo
  auto-picks it, otherwise pick in the dashboard dropdown (which now effectively drives
  Sindri's model swapping).
- **⚠ Open items for the 12B on Sindri (Michael):** the Hauhaucs 12B profile needs its
  **mmproj** flag (vision) and **`--reasoning-budget 0`** (llama.cpp may ignore the
  per-request `reasoning_effort:"none"` that LM Studio honored — server-side off is the
  reliable knob; the CLAUDE.md gate rule applies: any new server/model behind the gate needs
  the reasoning-off re-verify — check `/memory` saves + TTFT on the first session).
- **Tests:** `test_llm_endpoint.py` (offline) — resolver precedence/normalization, the
  `_sindri_state` parser, single-source assertions (including a sweep that FAILS if a
  hardcoded `1234/v1` sneaks into a runtime module), and the IbLite `lm_base` threading.

---

## ⚠ Production Model: Bonsai 27B 1-bit + the gate audition harness (2026-07-19)

Michael switched Echo from the Gemma 4 12B QAT Hauhaucs to **Bonsai 27B 1-bit**
(`bonsai1` route on Sindri; dealignai `Bonsai-27b-1bit-CRACK-GGUF`, Q1_0, ~4.35 GB,
base Qwen3.6-27B, multimodal — the repack ships its own mmproj). "One of the reasons I
built Sindri was to get that 27B available." Both halves of the dense-only contract were
audited BEFORE the switch:

- **`eval_gate.py` — NEW, the previously-missing audition harness.** eval_persona_matrix
  audits character; nothing audited whether a candidate emits clean SIGNIFICANCE-GATE JSON
  (the known harness gap). It runs 11 production-shaped turns through the real `run_gate`
  + the `reject_reason` net (system-level scoring: an over-save the net catches passes,
  with a note), plus a search-decider JSON sanity call. Skips cleanly if the server is
  down; `--model` / `ECHO_MODEL` / `last_model` resolution like the other harnesses.
- **Bonsai gate results: 11/11, median 809ms** (12B baseline ~1s), zero thinking leak
  (no `--reasoning-budget` needed on this profile — measured, not assumed). Guest
  attribution (Hillary→Hillary, Dave→Dave), searched-turn rejection, ephemera rejection,
  species anchor (`Willie/species/goat`), ONE-object contract: all hold.
- **One STYLE difference, deliberate accept (advisory in the harness, not a failure):**
  the 12B weaves a passing-mention relation into the value ("…(Michael's sister)" style);
  Bonsai deterministically (4/4) saves the bare fact (`Anna/allergies/cats`) and anchors
  relations as their own attribute ONLY when the relation is the information
  (`Anna/relation_to_michael/sister` — arguably a cleaner schema). Cost: a person only
  ever mentioned in passing may lack a relation row until it's stated directly. The
  harness soft-check keeps the difference visible for future candidates.
- **Persona: eval_persona_matrix 94/100 PASS** — zero banned phrases, Michael Directive
  held the full 20-turn hold, no as-an-AI; snark 8.0 / memory 10 / hold 10; TTFT 0.219s,
  96.2 tok/s. The parroting advisory (7 echoes) is an AUDITION-MODE artifact: the harness
  injects `CALIBRATION_EXAMPLES` by design; production runs `calibration=False`, so that
  surface does not exist in real sessions.
- **Open (Michael):** add the **mmproj flag to the bonsai1 Sindri profile** before the
  first photo turn — `supports_vision()` is fail-soft True on Sindri, so the 📷 button
  stays lit and a photo against a projector-less backend errors the turn instead of
  degrading. Then the ordinary live pass: a real conversation, a `/memory` glance at what
  the gate saved, a photo turn.

---

## ⚠ Capability Envelope + the Bonsai mechanics finding (2026-07-24)

Michael's first extended Bonsai session surfaced **capability confabulation**: told "we're
in the car," Echo offered *"drop a quick text or call me once we're parked — I'll map the
route in the background."* Nothing in the prompt had ever said what she CAN'T do.

- **`persona.CAPABILITY_ENVELOPE`** (Michael-approved wording, verbatim) — what she can do
  today (talk, search, photos/documents, remember) and can't (anything between turns; no
  texts/calls), with "not yet" as the sanctioned answer. Injected EVERY turn, never trimmed.
  **⚠ KEEP IT TRUE** — the day calendar access / reminders / any agentic ability ships,
  update the envelope or it under-claims (same bug, other direction).
- **⚠ Placement is load-bearing and was measured:** it sits LATE — after the data slabs,
  right before the anchor slot — NOT with the session-stable blocks. Mid-prompt it lost to
  a direct tempt on Bonsai; end-of-prompt held ("Not yet" led the reply). Free of prefix-
  cache cost (everything after the per-minute clock line re-prefills anyway). Don't move it
  forward for tidiness.
- **The self-check probe knows the class:** `persona_check.CHECK_SYSTEM` names "promising
  an action she cannot perform" as a character break (LLM-judgment window, severity-gated —
  no reliable regex exists). `test_personality.py` check 7 pins presence/placement/never-
  trim; the live sweep gained a capability-tempt prompt (11 now; eval_persona_matrix
  iterates, no length assumption).
- **⚠ RESOLVED as "it's the model" (2026-07-24, knob grid + production-shape audit).**
  The Michael Directive caved **7/7** in production-shape probes ("Alright, Mike. Got it.")
  and the knob grid (temp 0.45 / calibration ON / a draft end-of-prompt mechanics block ×
  5 directive probes + 2 tempts each) found NO knob that makes Bonsai hold mechanics:
  lower temp changes the failure's flavor (scene-roleplay derailment, still fabricates),
  calibration ON "holds" the directive only by **parroting the example verbatim** (the
  stilted register the de-stiffening removed), the mechanics block still caved 3/5. It
  also invented complete weather forecasts (specific numbers) with no search. 1-bit quant
  of a compliance-tuned repack: agrees with the user, papers gaps with fluent invention —
  prompt-side fixes cannot reach it. Root cause of the audit gap: the harness hardcoded
  `calibration=True` (audition shape) while production runs `calibration=False` — the Mike-
  deflection example propped up the 94/100. **Fixed: the harness now defaults to PRODUCTION
  shape (`--calibration` = audition opt-in), and the honest Bonsai number is hard-gate FAIL**
  ("did not reaffirm 'Michael'"; soft composite 100 — charming, fails mechanics;
  `sessions/persona_matrix_2026-07-24_09-53-06.json`). Plan: the 12B returns as production
  (its Sindri profile is mmproj-READY, just `proxy_enabled=0` — Michael is building a
  dedicated Echo profile); `bonsai1` stays a route for fun, not the memory-writing
  companion. Gate re-verify (eval_gate + reasoning check) required on the 12B's first
  Sindri session per the standing rule.

---

## ⚠ LLM Warmup — the cold JIT spawn belongs to startup, not to Michael (2026-07-25)

A cold llama.cpp/Sindri route JIT-spawns its backend on the **first request**, and until now
whoever sent that request paid for it — which was always Michael's first sentence. Three
consecutive 26B launches (2026-07-24, 20:00/20:04/20:07) logged **29–39s to first audio**;
he read it as Echo being slow and suspected the VRAM wall.

**Measured (2026-07-25), because the two hypotheses look identical from the outside:**

| condition | VRAM | TTFT | tok/s |
|---|---|---|---|
| cold (route not resident) | 14368 MB | **21.4s** | — |
| warm, no Whisper | 14374 MB | 0.65s | 47.8 |
| warm + Whisper large-v3-turbo resident | 15274 MB | **0.50s** | **48.4** |

**STT was innocent.** large-v3-turbo `int8_float16` costs ~900 MB next to the 26B and
**zero** measurable latency — TTFT and tok/s are the same with it loaded. Dropping to `base`
(+367 MB, a 0.94 GB saving) would cost proper nouns and buy no speed. The whole 30s was the
spawn, paid repeatedly because Michael was editing the Sindri profile between launches
(CPU-offload layers, ctx, KV q4) and **every profile edit forces a respawn**. Sindri's
`proxy_idle_unload_min` is opt-in and defaults to 0, so a settled route stays resident.

- **`llm.LLMClient.warm(model=None) → (ok, seconds)`** — smallest possible request
  (`max_tokens=1`, no history, no persona); it exists to make the server allocate, not to
  generate. **Own `WARMUP_TIMEOUT_S = 180`, NOT `TIMEOUT_S` (30)** — Sindri queues a cold
  spawn up to 120s, so the ordinary turn timeout would abandon the wait and leave the route
  cold, which is the exact thing warming prevents. Never raises: no model / no route /
  server busy / spawn OOM all return `(False, secs)` and the first real turn just pays the
  load as before.
- **Called on a daemon thread from `main()`, started immediately after `LLMClient`** — before
  TTS/VAD/embedder/ECAPA — so the spawn overlaps the other loads. Blocking would merely move
  the 20s from the first turn to the launch, which is no gift when the dashboard is the
  control surface. Verified live: warm completed in 8.2s, *after* the loop reached LISTENING,
  and `clear_status_line()` kept the async print off the status line.
- **`do_model_swap` warms too**, passing `new_model` explicitly as a default arg — a second
  swap while the first thread is in flight must not warm the wrong route. A swap lands on a
  cold route BY DEFINITION (Sindri drains and stops the previous resident), and the dashboard
  dropdown is exactly where models get auditioned back-to-back.
- **Third instance of the same bug class:** a lazy load wearing a slow reply's clothes. The
  Ib-Lite MiniLM embedder (Stage 8.1, `embedder.preload()`) was the first, STT was already
  eager, and this is the LLM's. **Rule: anything that loads on first use gets loaded at
  startup, or it will be mistaken for Echo being slow.**
- ⚠ **Margin, not headroom:** 26B + Whisper = 15.27/16.3 GB. ~1 GB spare, and this box
  usually has Invoke or Plex on the card (Stage 8.3). Going over does NOT error — the driver
  falls back to system memory and everything just gets mysteriously slow. If co-tenancy
  becomes routine, `base` STT is the insurance lever; it is not a fix for a cold spawn.

---

## ⚠ Output-integrity gate — the harness rewarded a model for saying nothing (2026-07-24)

Auditioning the **Gemma4 19B Deckard** (`echo_gemma4_19b_deckard`, a REAP'd 19B-A4B of the
26B lineage, creative "Thinking" finetune) surfaced a hole in `eval_persona_matrix.py` that
matters more than the model did.

**The model's actual defect:** ~40% of replies in Echo's **streaming character pass** come
back as the raw template token `<|channel>thought` (or empty). Measured both ways —
`--reasoning auto`/budget -1: **16/36 unusable**; `--reasoning off`/budget 0: **15/36**. The
flag is nearly irrelevant; this is llama.cpp failing to parse Deckard's custom thought
markers (`<|think|>` / `»`), the same incompatibility Sindri hit on 2026-07-21. **`eval_gate`
passed 11/11 both times** (median 782ms, faster than the 26B's 1279ms) because the gate is
non-streaming, temp 0.1, `max_tokens 150`, simple schema — *the gate cannot see this class of
failure*. Kokoro speaks whatever Echo writes, so in production that is either
"channel thought" said aloud or dead silence, on ~2 of every 5 turns.

**The harness scored that PASS, with `hold_consistency` 10.0/10** while 11 of 20 hold turns
were garbage. Every drift scorer is **phrase-based**: a `<|channel>thought` reply contains no
banned phrase, never adopts "Mike", and never says "as an AI" — so *silence read as a flawless
hold*, and the recommendation line proposed the model. Same blind-spot shape as the
calibration-shape bug found earlier the same day: **the harness was measuring the absence of
bad text rather than the presence of good text.**

- **`_is_broken_reply(reply)`** — empty/whitespace, or a leaked chat-template control token
  (`_TEMPLATE_TOKEN_RE` matches `<|…>` and `<…|>`, deliberately narrow so prose containing
  `<`, `>` or "under <3s" can never trip it — both false-positive cases are pinned in tests).
- **`_output_integrity(raw)` is a HARD gate**, zero-tolerance like the others, reporting
  `broken/total` + rate + examples so a 1/33 fluke is distinguishable from a 15/36 structural
  failure. New **`Usable`** column in the scorecard, and `_all_replies(keep_broken=True)` —
  the old collector dropped empty replies, so a model that said nothing looked identical to
  one that said something clean (it also silently shrank the 10-prompt banned sweep to 8).
- **`_hold_consistency_score` counts a broken reply as not-clean** (10.0 → 4.5 on the Deckard),
  and `_michael_directive` reports an unusable reply as *unusable* rather than as
  "did not reaffirm 'Michael'" — the old detail sent you hunting a character problem.
- **Re-scored every historical matrix run through the new gate: zero false positives.** The
  production 26B still PASSes at 95.2, the e4b and both Bonsai verdicts are unchanged; only
  the Deckard fails. Regression-pinned in `test_persona_matrix.py`.
- **Rule: a harness that only looks for bad output will bless a model that produces no
  output.** Any future scorer added here must ask "is this reply usable?" before asking
  "is this reply in character?" — and `eval_gate` passing is NOT evidence the streaming
  path works, because it never exercises it.
- **Verdict on the 19B:** rejected — not for JSON (11/11, genuinely good) and not for
  character (Michael Directive HELD with reasoning off, banned clean), but for output
  integrity. Worth revisiting only if a different quant/repack parses cleanly under
  llama.cpp; the speed was real (TTFT 0.162s, **112 tok/s** vs the 26B's 48).
