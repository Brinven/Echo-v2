# Echo — tasks/todo.md

## ▶ ACTIVE (2026-07-14) — Stage 5 Part 3: Web Search

Michael's call (2026-07-14): build **Part 3 (Web Search)** → then **Part 5 (Location)**.
Part 4 is **penciled DONE** (see below); revisit only if a model swap needs it.

**§6 decision (resolved):** keyword pre-filter → decision call (NOT decide-every-turn).
Protects the <3s feel, recall-biased so misses fall through to the cheap Stage B call,
tunable from logs. Reversible.

### Build checklist (PRD §12 milestones)
- [x] **M1 — SearXNG up.** ✅ **Reused Michael's EXISTING `Searxng` container on
      `127.0.0.1:26`** — already JSON-enabled + limiter off (verified live: weather query
      returned real JSON). No new container. My initial duplicate (`echo-searxng`) was torn
      down. `searxng/docker-compose.yml` kept as a **localhost-only fallback** (port 8890,
      not running); `searxng/README.md` documents the real setup. `echo_search.json` created
      (base_url→:26). ⚠ Existing container is `0.0.0.0:26` (LAN-exposed) — hardening note for
      Michael, non-blocking (Echo's own traffic is loopback).
      *Gotcha found:* port **8888 is taken by a native uvicorn app** (PID-owned), not Docker.
- [x] **M2 — `search.py`.** ✅ `SearchProvider` ABC, `SearXNGProvider`, `SearchResult`,
      `healthy()`, `load_search_config()`, `build_provider()`, `format_search_block()`.
      Uses httpx (already an openai dep — no new dep). Defensive `.get()` parsing; 5s
      timeout; never raises. **Live-verified against :26** — healthy()=True, 5 real weather
      results parsed, populated + empty-results blocks format correctly.
- [x] **M3 — Decision call (`search_decision.py`).** ✅ Stage A `prefilter_hit()` (regex,
      recall-biased) + Stage B `decide_search()` (reasoning-off JSON, mirrors
      `significance.py:run_gate`, never raises, empty-content guard). **Live-verified** on a
      6-prompt mixed sweep: lookups→search+query, personal/opinion/greeting→false, joke
      skipped at Stage A (0ms). Stage B ~0.7–1.5s.
- [x] **M4 — Result injection.** ✅ `search_block` arg added to
      `persona.build_system_prompt` (after memory, before anchor; never trimmed).
      **Verified:** order memory→search→anchor holds; anchor timing intact (exch 1 none, exch 8 yes).
- [x] **M5 — Latency filler.** ✅ In `main.py run_streaming_pipeline`: search step sits after the
      sign-off/forget/max-snark short-circuits, before assembly. `audio_q.start()` moved up so the
      filler + streamed answer share ONE playback cycle (filler enqueues first, plays while search
      runs). Rotating filler via `_pick_filler`. Search turns marked exempt from <3s PASS/FAIL.
- [x] **M6 — Toggle + transparency.** ✅ `web_search_enabled` (echo_search.json → `build_provider`
      returns None if off). Startup `healthy()` probe (warn-don't-block). Graceful SearXNG-down →
      `search()` returns [] → in-character decline (verified against a dead port). Voice off-switch
      `is_stay_offline`/`is_go_online` + `session.web_search_off` (verified).
- [x] **M7 — Logging.** ✅ All 8 fields wired via `**search_meta`; search turns log
      `passed_budget=None` (excluded from pass-rate).
- [x] **M8 — End-to-end (headless).** ✅ Live model + live SearXNG, 4-prompt sweep: weather →
      searched, "83°, thunderstorm, keep it in mind for the Jeep" (in-voice, no URLs/"according to");
      Artemis news → searched, real dates; crows opinion → NO search, pure Echo; "how are you doing"
      → NO search. Zero banned phrases. **Remaining: Michael's live mic/keyboard pass** (10-prompt +
      real audio) — user-run, like the personality harnesses.
      *Refinement from the live test:* added a greeting stoplist to `prefilter_hit` so pure smalltalk
      ("hey echo how are you doing") skips the Stage B call entirely.
- [ ] **M9 — Memory (NTH).** Provenance/exclusion only if logs show junk facts. Deferred — the gate
      already sees searched turns (main.py); revisit only if real logs show ephemeral web junk.

### Tailscale / firewall (road web-search prep — future Jeep deployment)
- Firewall break-glass (added by Michael 2026-07-14, elevated):
  `New-NetFirewallRule -DisplayName "Echo SearXNG (Tailscale/LAN)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 26 -Profile Any`
- ⚠ Host-to-**self** `100.86.181.37:26` still times out even WITH the rule — but that's a known
  Tailscale self-hairpin dead-end, NOT proof the remote path is blocked. **Definitive test = curl
  from the `echo` Mac node** (`100.94.68.70`): `curl "http://100.86.181.37:26/search?q=test&format=json"`.
- Recommended Jeep architecture: run SearXNG **on the Mac Mini itself** (`searxng_base_url` →
  `http://127.0.0.1:8888`) → self-contained road search, no home-PC dependency. One-line config swap
  (provider abstraction already supports it). Tailscale-to-home is the zero-setup fallback.

### Deferred / queued
- **Stage 5 Part 4 — Persona Persistence** → `Echo_Stage5_Part4_PersonaPersistence_PRD.md`
  **PENCILED DONE (2026-07-14):** Michael settled on **Gemma 4 12B QAT (Hauhaucs decensored)**
  as the persona-persistence pick — "penciled," pending the inevitable next great open model.
  Eval harness / self-check probe / dry-wit calibration examples NOT built; revisit only if a
  model change makes it necessary. Part 3's separate-reasoning-call infra would still feed the
  self-check probe if resurrected.
- **Stage 5 Part 5 — Location / Context Awareness** → `Echo_Stage5_Part5_LocationAwareness_PRD.md`
  **NEXT after Part 3.** Home-vs-Jeep via LAN presence (gateway-MAC fingerprint) + voice
  override. Reuses the snark/mood context-block pattern. Michael's rationale: location grounding
  makes Echo "act normally" instead of roleplaying — knowing where she is settles the register.

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
  surfaces to whom, and whether she keeps a guest's aside *from* Michael (his device →
  he has full access — see Michael's lean below); (c) **speaker-aware retrieval**
  — bias toward the current speaker's relevant memories; (d) **unknown speaker →
  ephemeral/guarded** by default (privacy + noise control).
- **Michael's lean (2026-07-14) on the privacy boundary:** Echo is *his*,
  unapologetically — the loyalty-blab is in-character and played for comedy (guest asks
  her to keep something from Michael → "Seriously? You thought I'd take your side over
  Michael's?"). Natural extension of the Michael Directive (she's partisan, not a neutral
  vault), so "doesn't keep secrets from Michael" is arguably the right default for a
  personal device. **Michael wants to sit with this — it deserves real thought.** The
  nuance for his time: it's not blab-vs-vault, it's Echo's *judgment about register* —
  loyalty-comedy lands when stakes are low, but her competence + warmth should read the
  room and NOT play a genuinely vulnerable moment for laughs. Design it as *when the snark
  is the right tone*, not a binary secrecy flag. Reassurance: this is a persona/policy
  **tone** decision, not architecture (storage stays a simple "told-by" tag), so it can
  be decided late without blocking anything.
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
