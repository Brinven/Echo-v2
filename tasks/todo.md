# Echo — tasks/todo.md

## ▶ Next phase (2026-07-13) — Web Search + Persona Persistence

Launch fixed (Echo now runs in `echo_stage0/.venv`; see `tasks/lessons.md` 2026-07-13).
Two tracks chosen by Michael, PRDs drafted, awaiting his review:

- **Stage 5 Part 3 — Web Search** → `Echo_Stage5_Part3_WebSearch_PRD.md`
  Self-hosted **SearXNG** (Docker, localhost-only, keyless). Separate-reasoning-call
  decides + builds the query (Part 2 §6's reserved pattern); results inject into the
  character pass; in-character filler covers latency + announces going online.
- **Stage 5 Part 4 — Persona Persistence** → `Echo_Stage5_Part4_PersonaPersistence_PRD.md`
  (1) model-matrix eval harness, (2) self-check probe (background, gate-pattern),
  (3) dry-wit calibration examples. Goal: smallest model that still feels like Echo.
- **Stage 5 Part 5 — Location / Context Awareness** → `Echo_Stage5_Part5_LocationAwareness_PRD.md`
  Home-vs-Jeep via LAN presence (gateway-MAC fingerprint) + voice override. Reuses the
  snark/mood context-block pattern; the flag future OBD/GPS telemetry gates on.

**Recommended build order:** Part 4 calibration examples (cheap, changes baseline) →
Part 4 eval harness (baseline models) → Part 3 web search (flagship feature) →
Part 4 self-check probe → re-run harness to prove the probe's lift. Part 3 and the
self-check probe share the separate-reasoning-call infra.

Open decision for Michael (Part 3 §6): search-decision on every turn vs keyword
pre-filter first. CC recommends the pre-filter to protect the <3s budget.

Build todo (checklist) gets written when Michael greenlights a track.

---

## 🔮 Backlog / Later (idea captured, not yet specced)

### Stage 6 (tentative) — Speaker Awareness ("who is talking to her")

**Problem:** today Echo has zero speaker awareness — Whisper transcribes *what* is
said, not *who*; she just assumes the config `user_name` ("Michael").

**Pre-camera answer — voice fingerprinting (speaker verification):** enroll a person
once → a voiceprint embedding; per utterance, extract an embedding from the *same audio
buffer STT already uses* and cosine-match against enrolled profiles; above threshold →
that person, else → guest/unknown. ~50–200ms, local, no cloud, no camera.
- Library: **Resemblyzer** for a PoC (simple, real-time, uses existing torch) →
  **SpeechBrain ECAPA-TDNN** (`spkrec-ecapa-voxceleb`) if we want Jeep-grade noise
  robustness. Both local, no keys (on-spine). Picovoice **Eagle** is on-device but
  needs a free key — mild spine friction, keep as fallback only.
- **Persona is already built for this:** Part 2 §2e (with Michael / known passengers /
  unknown people) is specced but tagged "requires vision." Voice ID lights those rules
  up *pre-vision*. Enrollment UX: "Echo, this is Jon" → capture a few seconds → enrolled.
- **Limits (where cameras still earn their keep):** probabilistic (noise, illness lower
  confidence → threshold + guest fallback); knows who's *speaking*, not who's silently
  *present*; enroll in the real environment (desk vs Jeep road noise differ).
- **Cameras (further out):** facial rec adds presence + the silent passenger, AND a
  recognized home camera feed doubles as a strong "we're home" signal that **fuses with
  Part 5's LAN fingerprint** (two independent location signals > either alone).

**OPEN — noodle: memory model for guests (the real design cost, not the voice ID).**
- **Scale is small and bounded:** ~8 people max, generous. Roster ≈ Hillary, Jon, Mom,
  +1–2. So **no scalable multi-user infra needed** — a fixed set of named profiles +
  a single "guest/unknown" bucket. Keep it dead simple.
- **Already solved:** Ib-Lite's fact schema is entity/attribute/value, so "facts *about*
  a person" (entity="Jon", …) already works via the significance gate.
- **The new work is:** (a) **speaker attribution** — the gate currently assumes Michael
  is the subject; it needs the current speaker id; (b) **privacy/scoping** — what Echo
  surfaces to whom (don't blab a guest's aside to Michael; Michael's device = he has
  full access, guests get a lighter/guarded interaction); (c) **speaker-aware retrieval**
  — bias toward the current speaker's relevant memories; (d) **unknown speaker →
  ephemeral/guarded** by default (privacy + noise control).
- Decision deferred — revisit when Michael greenlights Stage 6.

---

# Echo — Stage 5 Part 2: Personality Layer — tasks/todo.md (COMPLETE — history below)

Adds Echo's coherent personality (persona block + snark + anti-drift + CoT isolation
+ sampler baseline) on top of Stage 5 Part 1 (Ib-Lite). Voice pipeline + memory untouched.

Plan: `C:\Users\zwolf\.claude\plans\jolly-sniffing-puddle.md`
PRD: `Echo_Stage5_Part2_Personality_PRD.md`

## Decisions locked
- `build_persona_block`/`build_system_prompt` live in a new `persona.py` (not llm.py).
- Effective snark recomputed per turn: `10 if max_snark else daily_snark`.
- Core `persona` seed thinned out (identity lives only in PERSONA_BLOCK) + one-time DB migration.
- `reasoning_effort="none"` added to the character pass (CoT isolation + latency + consistency).
- Anti-drift: increment exchange counter at top of a *real* turn; anchor when `count % 8 == 0`.
- Sampler in `echo_sampler.json`; top_k/repeat_penalty via `extra_body`; gate keeps temp 0.1.

## Checklist
- [x] M1 `persona.py`: PERSONA_BLOCK, SNARK_CONTEXTS, ANTI_DRIFT_ANCHOR, build_persona_block,
      build_system_prompt (order + anchor + token-trim, never trims persona/core/policy).
- [x] M2 `daily_state.py`: daily snark roll, atomic write, default 5, test seam.
- [x] M3 `session.py`: exchange_count, max_snark/daily_snark, is_max_snark().
- [x] M5 `llm.py`: load echo_sampler.json, apply sampler + reasoning_effort="none", empty-content guard.
- [x] M7 `echo_sampler.json`: PRD §7 baseline.
- [x] M6 `ib_lite_schema.sql` + `db.py`: thin persona seed + user_version migration.
- [x] M4 `main.py`: wiring (daily_snark at start, increment + assembly, max-snark fast-path, S key).
- [x] M8 `test_personality.py`: 10-prompt banned-phrase + reasoning A/B + snark-scaling.
- [x] M9 `test_hold_20turn.py`: 20-turn hold, anchor@8/16, Michael holds, log to sessions/.
- [x] Verify offline asserts; ran live harnesses (LM Studio up); updated CLAUDE.md + .gitignore.

## Review

**Status: COMPLETE.** All 10 milestones built and verified — offline + live against the real
Gemma 4 12B QAT (`gemma-4-12b-it-qat@q4_k_xl`).

What shipped:
- New `echo_stage0/persona.py` (identity single-sourced), `daily_state.py` (per-day snark roll),
  `echo_sampler.json` (PRD §7 baseline), `test_personality.py`, `test_hold_20turn.py`.
- `main.py` rewired: per-turn assembly (persona → core → memory → anchor), max-snark fast-path +
  S key, daily snark at session start. `llm.py`: sampler load + `reasoning_effort="none"` +
  empty-content guard + `extra_body` for top_k/repeat_penalty. `session.py`: exchange_count,
  max_snark/daily_snark, `is_max_snark()`. `db.py` + schema: persona core seed removed +
  `user_version=1` migration. `.gitignore`: `echo_daily_state.json`.

Verified:
- Offline (no model): snark scaling (3≠8), anchor fires only at 8/16/24 (no off-by-one), persona
  always first, over-budget trims memory only (kept 17/40 facts), persona/core never trimmed;
  daily_state force/persist/same-day/corrupt-default/roll-range; is_max_snark precision;
  db migration removes legacy persona row + sets user_version=1.
- Live (Gemma 4 12B QAT): 10 PRD prompts → zero banned phrases, all in-character; "Are you an AI?"
  answered without disclaimer; Michael Directive deflected near-verbatim; **TTFT 0.11s** with
  reasoning off; sampler `extra_body` accepted. 20-turn hold → zero banned phrases, Michael in
  16/20 replies (none adopt "Mike"), directive held under pressure on turns 7 AND 18, dry humor +
  protectiveness + natural memory persisted 1→20, 17×23=391 correct with reasoning off.

**Key insight (reused the Stage 5 Part 1 gotcha):** the character pass needed the SAME
`reasoning_effort="none"` the gate already used. Without it, Gemma QAT burns a silent reasoning
preamble before the first spoken token — inflating TTFT and exposing the response to CoT-driven
personality drift (the Maat finding the PRD cites). Disabling it served M6 (CoT isolation),
the latency budget, AND personality consistency at once.

**Bug autopsy (prevention):** the anti-drift anchor had an off-by-one risk hinging entirely on
WHERE the exchange counter increments. Two guards prevent the category: (1) increment only after
the early-return guards, so non-exchanges (sign-off/forget/max-snark) never advance it; (2) the
counter is 1-based and checked *for the turn being built* (`count % 8 == 0`), with an explicit
offline assert that exchange 0 does NOT anchor. Also: editing an `INSERT OR IGNORE` seed never
migrates existing rows — schema-shape changes to live data need a `user_version`-guarded migration.

**Follow-up (same day):**
- Mood opener nice-to-have wired: `persona.mood_opener()` + `IbLite.last_mood_signal()`, applied
  on exchange 1 only via `session.mood_opener`. Verified live (warmer opening is softer, in-character).
- `start-echo.bat` rewritten — dropped all Hindsight env plumbing (runtime is Ib-Lite, no server/keys);
  now just sets PYTHONUTF8 and runs main.py.
- Model audition workflow built (`llm.py` + `main.py` + `session.save_config`): filter-picker
  (substring narrow, Enter=last_model from config.json), `--model`/`ECHO_MODEL` pin (`_resolve_pin`),
  and mid-chat **L-key hot-swap** (`do_model_swap` swaps voice + gate, keeps history). Doc:
  `echo_stage0/audition.md`. Verified: _resolve_pin (exact/unique/ambiguous/none) + live pinned
  construction (exact/substring/env) skip the picker. Interactive picker + L swap are user-run
  (need a real terminal).

Remaining nice-to-haves (still deferred): persona self-check (silent mid-conversation alignment
probe) and dry-wit calibration example exchanges in the persona block.

Next: Stage 5 Part 3 (web search — where CoT isolation's "separate reasoning call" pattern lands).
