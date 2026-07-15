# Echo — tasks/todo.md

## ▶ ACTIVE (2026-07-15) — Stage 7: GUI Dashboard / Control Panel (v1)

Michael pivoted to the GUI so the touchscreen becomes Echo's control surface AND the speaker
live-pass is done by touch (enroll button + threshold slider) instead of CLI. Plan:
`~/.claude/plans/lexical-baking-hippo.md`. Full architecture: CLAUDE.md ⚠ "GUI Dashboard" section.

### Decisions locked (this session)
- **Embedded Flask server thread** inside the Echo process, behind an `EchoControl` bridge that
  drives Echo through the SAME events/flags the keyboard sets — never the STT/LLM/TTS pipeline.
- **Flask + vanilla HTML/JS** (no npm / no build step). Full **touch control surface** incl. a
  press-hold Talk (PTT) button. Additive + fail-soft (disabled/flask-missing/port-taken → loop unaffected).

### Build checklist — DONE (offline-verified here)
- [x] **M1** `webui/control.py` — `EchoControl` (snapshot/health/recent_scores reads; talk/mute/
      snark/location/websearch/enroll/threshold/quit writes).
- [x] **M2** `webui/server.py` — Flask app + routes + `start_webui` (daemon thread, fail-soft,
      `_port_free` without SO_REUSEADDR, werkzeug logging silenced) + `load_webui_config`.
- [x] **M3** `webui/static/index.html` — dark/high-contrast/big-touch UI, polling, all controls +
      speaker panel + camera/sensor placeholders.
- [x] **M4** `main.py`/`session.py` wiring — build `EchoControl` + `start_webui`; route
      `on_key`/`muted` through the bridge; `session.last_speaker_score`; startup `Dashboard:` line.
- [x] **M5** `echo_webui.json` (committed) + `requirements.txt` `flask==3.1.3` (installed into `.venv`, torch untouched).
- [x] **M6** `test_webui.py` — offline Flask `test_client`: `/api/state` shape, each POST flips the
      right flag/Event/threshold, health stubbed, enroll-off refusal, no-registry no-op. + real-bind smoke.

### Verification — DONE
- ✅ `test_webui.py` + all prior suites green; `py_compile` clean; `main.py`/`webui` import clean.
- ✅ Real Flask bind smoke: serves the 15 KB dashboard (Talk button + threshold slider), `/api/state`
  200; **port-taken → None, disabled → None** (fail-soft); werkzeug request-spam suppressed.
- ✅ Two bugs caught + fixed during the build: test wrote to the real `echo_speakers.json` (now temp
  path; seed restored); Windows `SO_REUSEADDR` defeated the port-taken check (removed).

### Open — Michael's (the two-in-one live pass)
- [ ] Launch Echo → open `http://127.0.0.1:7862` on the PC: confirm health tiles + live transcript;
      use **Talk** to converse; **enroll Michael + a guest via the button**; **tune `match_threshold`
      with the slider watching live scores**; confirm a guest turn writes no fact; exercise the toggles.
      Then repeat from the 10" touchscreen (set `host` to the LAN/Tailscale IP).
- [ ] Approve the two speaker persona strings (`SPEAKER_KNOWN`/`SPEAKER_UNKNOWN`) — carried over from Stage 6.
- Commit: built + offline-verified; ready to push to `main`.

---

## ✅ DONE (2026-07-15) — Stage 6 Part 1: Speaker Awareness (voice-ID + attribution)

Michael greenlit Stage 6. Part 1 = the **mechanics only** (voice fingerprinting + who's-talking
attribution); the loyalty/secrecy-**register** policy is a deliberately deferred later Part.
Plan: `~/.claude/plans/lexical-baking-hippo.md`. Full architecture: CLAUDE.md ⚠ section.

### Decisions locked (this session)
- **Scope:** mechanics first; privacy-register policy is a later Part.
- **Model:** **SpeechBrain ECAPA-TDNN** (192-dim, noise-robust endgame → no re-enrollment for the
  Jeep), behind a swappable `SpeakerEmbedder` ABC. Chosen over Resemblyzer: no C-extension build,
  reuses existing transformers/torch/hf deps, actively maintained. CPU-only (VRAM → 12B).
- **Enrollment:** **both** — `enroll.py` CLI and in-conversation "Echo, this is Jon".
- **Guardrail:** only Michael's turns write to memory → the gate stays Michael-only *by
  construction*; `ib_lite/significance.py` untouched. Unknown → guarded, never misattributed.

### Build checklist (milestones)
- [x] **M1** `speaker_id.py` — `SpeakerEmbedder` ABC + `ECAPAEmbedder` (CPU, L2-norm) +
      `SpeakerRegistry` (identify/enroll/remove/save) + `build_embedder` fail-soft + config loader.
- [x] **M2** `echo_speakers.json` (gitignored, `enabled:false` seed) + `.example.json` + `.gitignore`
      (voiceprints + `models/`); `requirements.txt` `speechbrain==1.1.0` with the torchaudio-CPU caveat.
- [x] **M3** `enroll.py` CLI (record → embed → save; `--seconds`/`--samples`/`--list`/`--rm`;
      auto-enables on first profile).
- [x] **M4** `persona.py` — `SPEAKER_KNOWN`/`SPEAKER_UNKNOWN` + `speaker_context()` +
      `build_system_prompt(speaker=…)` after location / before core, never trimmed.
- [x] **M5** `session.py` — `current_speaker`, `enrolling`, `is_enroll_command`/`is_enroll_cancel`,
      `current_speaker_is_michael` (the guardrail decision).
- [x] **M6** `main.py` — startup embedder build (only when enabled + ≥1 profile), per-turn
      identify + resolve, inline `[speaker: …]` line, startup status + Michael-not-enrolled warn.
- [x] **M7** `main.py` — enrollment state machine (command turn arms → capture turn saves;
      cancel / too-short re-prompt), both as non-gated early guards.
- [x] **M8** `main.py` — attribution guardrail (label = current_speaker; skip `write_memory` when
      ≠ Michael) + `speaker`/`speaker_score`/`speaker_known` JSONL fields.
- [x] **M9** `test_speaker_id.py` — offline/model-free: identify math + threshold + model/shape
      skip, registry round-trip, enroll-command parsing, `speaker_context` + prompt order +
      never-trim, session flags + guardrail decision.

### Verification — DONE (offline, run here)
- ✅ `test_speaker_id.py` all green (10 checks). `test_persona_check.py` + `test_persona_matrix.py`
  still green (the `build_system_prompt` `speaker` arg didn't regress callers — verified no
  positional `correction` caller exists). `py_compile` clean on all 5 touched files. `main.py` /
  `enroll.py` import clean. `build_embedder` verified to degrade to None (→ assume Michael) with
  SpeechBrain absent.

### Open — Michael's (not yet done)
- [ ] **Live pass** (mic + model): `pip install torchaudio --index-url .../whl/cpu` then
      `pip install speechbrain==1.1.0` into `.venv`; `python enroll.py Michael` + a guest (first run
      pulls ~89 MB ECAPA); run a session — confirm Michael IDs, a known guest is greeted by name, an
      unknown voice gets the guarded register, **tune `match_threshold`** from logged scores, and
      verify a guest turn writes NO fact to `echo.db` while Michael's still does.
- [ ] **Approval gate — speaker persona strings** (`SPEAKER_KNOWN`/`SPEAKER_UNKNOWN`, persona
      content, like the Part-5 LOCATION_CONTEXTS gate). Approve/tweak, then it's closed out.
- Commit: built + offline-verified code is ready to push to `main` (solo-repo workflow) —
  pending Michael's go / whether to fold in the live-pass tweaks first.

---

## ✅ DONE (2026-07-15) — Stage 5 Part 4: Persona Persistence (un-penciled)

Michael's call this session: **build the Part 4 deliverables** that were penciled-DONE
but never built. PRD: `Echo_Stage5_Part4_PersonaPersistence_PRD.md`. This is a
measurement-and-hardening stage (no new user feature): make Echo's character *survive
model shrink* so a smaller/faster model can eventually run alongside vision/STT/TTS.

**Sequencing (PRD §6):** calibration examples → eval harness → self-check probe → re-run
harness for the probe's before/after lift.

### Decisions locked (for this build)
- **Memory-naturalness test injects the known fact via the `memory_block` prompt arg,
  NOT the live `echo.db`.** The harness must never pollute Michael's production memory
  with test facts. (Documented deviation from PRD §3's "via Ib-Lite" — same intent, safer.)
- **Harness uses `LLMClient` directly, no `IbLite`.** It writes no memory; the self-check
  probe takes the model-name string directly (like the gate). `IbLite.set_model` only
  matters in the live pipeline, not the harness.
- **Self-check probe mirrors `significance.py:run_gate` exactly** — own client, own system
  prompt, `temperature≈0.1`, small `max_tokens`, `reasoning_effort="none"`, best-effort JSON,
  never raises, empty-content guard. Single-flight background thread like `ib.write_memory`.
- **Probe cadence:** every N=5 exchanges (tunable), last K=3 Echo replies, skipped under
  `max_snark` (intended off-baseline) and while a prior probe runs.
- **Correction is a nudge, not an override**; injected after the anti-drift anchor; decays
  after one turn (cleared on consume). Only CLEAR violations trigger it.

### Approval gates (character content — Michael signs off, per PRD) — ✅ BOTH CLOSED 2026-07-15
- [x] **Calibration example wording** (PRD §5) — **Michael signed off: KEEP the 3 examples as-is.**
      Rationale: the production/persona model is the 12B (held character cleanly in the 20-turn
      hold, no parroting); the examples measurably help small models hit register; the e4b
      parroting is useful *audition data*, not a production defect; the header already frames them
      as non-scripts. Revisit only if a small model is actually adopted.
- [x] **Final model-matrix list** — **Michael chose the Gemma small-ladder.** `persona_matrix_models.json`
      now holds exact live ids: `hauhaucs/gemma4-12b-qat-uncensored-hauhaucs-balanced@q4_k_m` (12B
      baseline/pick) + `gemma-4-e4b-it-qat` (~4B plain-QAT control) + `gemma-4-e4b-uncensored-hauhaucs-aggressive`
      (~4B, same tuner) + `gemma-4-e2b-uncensored-hauhaucs-aggressive` (~2B, small extreme). VRAM-fit
      ladder controlling for tuner/quant — targets the "run alongside vision/STT/TTS in 16GB" goal.

### Build checklist (PRD §7 milestones)
- [x] **M1 — Calibration examples.** `CALIBRATION_EXAMPLES` in `persona.py`, injected into the
      never-trimmed persona region; `test_personality.py` never-trim assertion updated. ✅
- [x] **M2 — Harness skeleton.** `eval_persona_matrix.py`: model list (`--models` / `ECHO_MODEL`
      / json), resolves each vs LM Studio (exact/unique substring; SKIP if not loaded), pins to
      skip the picker, JIT-load timed separately. ✅ Live: ran on the real roster.
- [x] **M3 — Hard-gate scoring.** Banned + Michael Directive + "as an AI"; broken canned run →
      FAIL. ✅ (`test_persona_matrix.py`)
- [x] **M4 — Soft + latency scoring.** Snark separation, memory naturalness, hold consistency;
      composite 0–100; median TTFT/tok-s; **cold-start excluded**. ✅ **+ parrot detector** (NTH,
      caught the e4b echoing calibration lines).
- [x] **M5 — Recommendation.** Smallest/fastest passer above soft floor; `--quick` mode. ✅
- [x] **M6 — Self-check module.** `persona_check.py::run_self_check` — separate reasoning call,
      reasoning off, strict JSON, never raises, empty-content guard, fail-SAFE. ✅ Live: clean→
      `in_character:true`, broken→flags Certainly/As-an-AI/Mike + clean nudge.
- [x] **M7 — Self-check wiring.** `SelfCheckRunner` background single-flight every N=5;
      `session.persona_correction` set→consume→clear; `build_system_prompt(correction=...)`
      injects `[correction]` after the anchor (never trimmed); `main.py` fires it off the hot
      path beside `ib.write_memory`. ✅
- [x] **M8 — Probe guardrails.** `evaluate_correction`: objective breaks always override;
      major→correct; minor-with-no-objective suppressed; max-snark exempt; one-turn decay. ✅
- [~] **M9 — Before/after proof.** `--probe` runs the self-check inline during the hold
      (mechanism BUILT + live-validated on e4b: probe fired@5 on real robotic drift, correction
      injected@6). **The actual before/after comparison on a marginal model is Michael-run:**
      `eval_persona_matrix.py --models <marginal> ` then `--probe`, compare the Hold column.

### Verification — DONE (offline, run here)
- ✅ `test_personality.py` (calibration present + never-trimmed), `test_persona_matrix.py`
  (broken FAILs / clean PASSes, all heuristics, parrot detection, recommendation), and
  `test_persona_check.py` (JSON parse clean/broken/empty, guardrails, correction lifecycle,
  `[correction]` inject + never-trim, runner max-snark exemption). All green.
- ✅ `py_compile` on all touched files. Live: harness quick + full `--probe` runs on
  `gemma-4-e4b-it-qat` (PASS, composite 100, TTFT ~0.085s, 148 tok/s); `run_self_check`
  clean/broken.

### ✅ Resolved — Michael's gates (both closed 2026-07-15)
- [x] **`CALIBRATION_EXAMPLES` wording** → **KEEP as-is.** The parroting finding was confined to
      the marginal e4b (an audition candidate), not the 12B production model. No code change.
- [x] **`persona_matrix_models.json`** → **Gemma small-ladder** wired with exact live ids (12B
      Hauhaucs baseline + e4b plain-QAT control + e4b Hauhaucs + e2b Hauhaucs). The old seed's
      `gemma-4-12b-it-qat` was an ambiguous substring and `gemma-4-4b-it-qat` wasn't loaded — both fixed.
- **Committed 2026-07-15** (the earlier "nothing committed until sign-off" note was overtaken —
  Part 4 code shipped in `b356f57`; this commit closes the two character-content gates on top of it).

### Model-audition constraints (locked 2026-07-15, Michael)
- **DENSE ONLY — no MoEs in the audition, ever.** In Echo the voice model *is* the Ib-Lite
  gate model (significance gate + `search_decision` + `persona_check` are all structured-JSON
  calls on the same loaded model). MoEs with 1–4B active silently emit malformed/decoupled JSON
  under structured prompts (the LFM2-speeddemon 29/30-false-boolean pattern; axly-infra lesson),
  which would corrupt memory *writes*, not just replies. The ladder is already all-dense (Gemma
  e4b/e2b are *effective*-dense MatFormer, not sparse-expert). Stay dense.
- **Harness gap to close before any real shrink:** `eval_persona_matrix.py` scores persona +
  latency, NOT gate-JSON reliability. "e4b passes the persona gates" is necessary, not sufficient —
  a swap candidate inherits gate/search/probe duty. If a shrink gets serious, ADD a JSON-discipline
  gate (run the significance gate over a battery, count malformed/empty). Note: Ib-Lite's per-turn
  gate is small-context / simple-schema (unlike the >10k-token consolidation that broke small models
  in Hindsight), so a *dense* 4B may genuinely pass — but it's untested. Don't assume; measure.
- **12B is natively multimodal → this inverts Part 4's shrink premise.** Gemma 12B does vision
  itself. The "shrink persona to make room for vision" pressure assumed vision was a *separate*
  VRAM-eating model; if the 12B pulls persona + vision double-duty, keeping it *collapses two
  models into one slot* and may be MORE VRAM-efficient than small-persona + separate-vision.
  Feeds Stage 6 camera-fusion (a recognized home-camera feed = a 2nd independent "we're home"
  signal alongside Part 5's LAN fingerprint). Harness stays useful as *measurement*; the
  strategic case for shrinking is weaker than it first looked. Michael: "hopeful we can use
  that when the time comes."

### Review
**Status: DONE — Part 4 built, tested, committed (`b356f57`); both character-content gates closed 2026-07-15.**
Shipped: `persona.py` (`CALIBRATION_EXAMPLES` + single-sourced `BANNED_PHRASES`/`adopts_mike`/
`banned_hits` + `correction` arg + `_correction_block`), `eval_persona_matrix.py` (harness +
`--probe` + parrot detector), `persona_check.py` (probe + `SelfCheckRunner` + guardrails),
`persona_matrix_models.json` (seed), `session.py` (`persona_correction` set/consume/clear),
`main.py` (probe wiring + correction consume), `test_persona_matrix.py`, `test_persona_check.py`,
`test_personality.py` (calibration assert).
Key live finding: the **e4b QAT passes the hard gates but drifts robotic under pressure** and
the probe catches it — exactly the small-model erosion Part 4 targets. The harness turns
"does it still feel like Echo?" into a reproducible scorecard for auditioning the smaller models.

---

## ✅ DONE (2026-07-14) — Stage 5 Part 3: Web Search

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
  **BUILT 2026-07-14, awaiting Michael's sign-off on the LOCATION_CONTEXTS persona wording.**
  - [x] M1 `location.py` — gateway-MAC + known-host probe, ~2s cap, fail-soft, test seam.
        **Live: resolves `home` on the desk** (MAC matched); all seam branches verified
        (match→home, no-match→jeep, error/disabled/no-fingerprint→unknown, MAC colon/dash normalize).
  - [x] M2 Config — `echo_location.json` pre-filled with real gateway `172.16.0.1` /
        `3C-37-86-97-0D-7F`, **gitignored** (home fingerprint); `echo_location.example.json`
        committed as the template (+ MAC lookup one-liner).
  - [x] M3 `LOCATION_CONTEXTS` + `location` arg in `build_system_prompt` (after mood, before
        core; never trimmed). Order + presence + no-trim verified.
  - [x] M4 `session.location` + `is_location_override()` (jeep/home/none; rejects "drove the jeep home").
  - [x] M5 `main.py` — resolve@start (beside snark), per-turn inject, voice-override fast-path
        (not gated, no counter advance), startup status line.
  - [x] M6 Behavior proof (live): same prompt → home = desk/downtime (no Jeep talk); jeep =
        tire pressure + route + protectiveness. Clean split.
  - [x] M7 `location` logged in JSONL.
  - [ ] M8 (NTH) periodic re-check — off by default (`recheck_interval_min: 0`); deferred.
  - **Michael's gate:** approve/tweak the two context strings (persona content, PRD §5), then commit.

---

## 🔮 Backlog / Later (idea captured, not yet specced)

### Stage 6 (tentative) — Speaker Awareness ("who is talking to her")

> **Part 1 (voice-ID + attribution mechanics) is BUILT — see the ACTIVE section at the top.**
> The design notes below remain the reference for the LATER Parts (guest-memory attribution,
> speaker-aware retrieval, and the loyalty/secrecy-register policy Michael wants to sit with).

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
