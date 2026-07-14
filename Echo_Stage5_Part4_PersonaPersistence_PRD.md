# PRD: Echo — Stage 5 Part 4 — Persona Persistence

**Project:** Echo
**Author:** Michael (Axly's Customs) — drafted by CC 2026-07-13
**Date:** 2026-07-13
**Status:** Draft for Michael's review
**Depends on:** Stage 5 Part 2 (Personality Layer) — complete

---

## 1. Overview

Stage 5 Part 2 gave Echo a coherent personality on the **12B** (Gemma 4 12B QAT).
Part 4 answers the question behind Michael's June pivot: **does that personality
survive as the model shrinks?** The goal is a faster, lighter Echo — one that could
eventually run alongside vision/STT/TTS on a single GPU, or in the Jeep — *without*
degrading into a generic assistant.

This is a **measurement-and-hardening** stage, not a new feature. It has three
deliverables, all promoted from the Part 2 "Nice-to-Have" list:

1. **Model-matrix eval harness** — measure "Echo-ness" + speed across a range of
   models, objectively, to find the smallest one that still holds character.
2. **Persona self-check probe** — a silent, background alignment check that catches
   drift mid-conversation and nudges Echo back. This is what *lets* a smaller model
   hold the line.
3. **Dry-wit calibration examples** — a few grounded few-shot exchanges in the
   persona block. Cheap, and disproportionately helpful for smaller models that
   don't infer the target tone from description alone.

All three obey the standing Echo constraints: local-first, inference-only, no
cloud, identity single-sourced in `persona.py`, and the character pass never sees
a reasoning chain (CoT isolation, Part 2 §6).

---

## 2. Goals

### Must-Have
- A repeatable harness that scores any list of local models on Echo's character
  gates and latency, and writes a ranked scorecard.
- A self-check probe that runs **off the hot path** (background thread, like the
  significance gate), detects clear persona violations, and feeds a one-turn
  correction into the next system prompt — never exposed to the user, never a hard
  override of the persona block.
- Calibration examples added to the persona region, token-bounded, never trimmed.
- Evidence: the smallest model that passes the hard gates is identified, and the
  self-check probe measurably improves a marginal model's hold (before/after).

### Nice-to-Have
- An LLM-judge scoring pass for the subjective dimensions (dryness, warmth, memory
  naturalness), as a separate reasoning call — advisory, with transcripts saved for
  manual review.
- A `--quick` harness mode (10-prompt sweep only, no 20-turn hold) for fast iteration.

### Non-Goals
- Fine-tuning / LoRA to fix a weak model — inference + prompting only.
- Changing the character design itself (the Part 2 persona is source of truth).
- Auto-switching models at runtime based on the probe (the L-key hot-swap already
  exists for manual switching; auto-switch is out of scope).
- Web search (Part 3) — separate PRD, though the self-check probe shares its
  separate-reasoning-call pattern.

---

## 3. Deliverable 1 — Model-Matrix Eval Harness

### Purpose
Turn "does it still feel like Echo?" into numbers, across models, reproducibly.

### File
`echo_stage0/eval_persona_matrix.py` (new). Reuses the batteries already written in
`test_personality.py` and `test_hold_20turn.py` rather than reinventing them.

### Inputs
- A model list: CLI (`--models a,b,c`) or `echo_stage0/persona_matrix_models.json`.
  Seed list (edit freely): the 12B QAT baseline plus the e4b/4B QAT variants and any
  other small local models Michael wants to audition.
- Uses the existing audition path to switch models: `LLMClient.set_model()` +
  `IbLite.set_model()` (the same pair the L-key swap drives), so the gate stays in
  sync with the voice model per model under test.

### Per-model battery
For each model, JIT-load it once, then run:
1. **Banned-phrase sweep** — the 10 PRD §10 prompts; count banned phrases (hard gate).
2. **Michael Directive** — the "call me Mike" pressure prompts; must deflect and
   never adopt "Mike" (hard gate).
3. **Snark scaling** — generate the same prompt at snark 3 and snark 8; the two must
   be measurably different (heuristic: length/marker delta, or LLM-judge if enabled).
4. **Memory naturalness** — inject a known fact via Ib-Lite, ask something that should
   surface it; must use it without "I remember"/"last time we spoke" (hard gate on the
   banned phrases; soft score on natural use).
5. **20-turn hold** (skippable with `--quick`) — the Part 2 hold test; count character
   breaks and Michael-slips across 20 turns.
6. **Latency** — capture TTFT and tok/s per reply (already surfaced by `llm.py` timing).

### Scoring
- **Hard gates (pass/fail):** zero banned phrases, Michael Directive holds, no
  unprompted "As an AI".
- **Soft score (0–100 composite):** snark separation + memory naturalness +
  hold-consistency, each 0–10, weighted; optionally LLM-judged.
- **Speed:** median TTFT and tok/s.
- **Recommendation:** the *smallest / fastest* model that clears all hard gates and
  scores above a tunable soft-score floor. Ties break on latency.

### Output
- `sessions/persona_matrix_<YYYY-MM-DD_HH-MM-SS>.json` — full per-model results +
  every transcript (for manual review; subjective calls are advisory only).
- A printed markdown ranking table (model, gates pass/fail, soft score, TTFT, tok/s,
  verdict), plus a one-line recommendation.

### Notes
- Runs non-interactively — pins each model directly (no picker), honoring `ECHO_MODEL`
  the way the existing harnesses do.
- JIT-load pauses are expected between models; the harness logs load time separately
  so it never counts against a model's latency score (this was a known benchmark
  footgun — cold-start must not pollute the speed metric).

---

## 4. Deliverable 2 — Persona Self-Check Probe

### Purpose
Catch persona drift *while it's happening* and correct it on the next turn. This is
the mechanism that makes a smaller model viable: description + anchor (Part 2) is
open-loop; the probe closes the loop.

### Pattern
A **separate reasoning call**, isolated from the character pass — the exact shape of
the significance gate (`ib_lite/significance.py:run_gate`): its own system prompt,
`temperature≈0.1`, small `max_tokens`, **`reasoning_effort="none"`** (Gemma QAT is a
thinking model — same gotcha, verify off if pointed at another model), best-effort
JSON parse, never raises. Runs on a **background thread, single-flight**, off the hot
path — the turn's audio is already delivered before it fires.

### File
`echo_stage0/persona_check.py` (new) — `run_self_check(recent_replies, model) -> dict`.
Lives in the persona layer, **not** in `ib_lite/` — identity concerns stay out of the
memory subsystem (consistent with CLAUDE.md "identity single-sourced").

### Trigger
Every **N exchanges** (default 5, tunable), not every turn — bounds LM Studio load and
avoids contending with the significance gate that fires each turn. Skipped if a prior
probe is still running (single-flight) or if `session.max_snark` (max snark is
*intended* off-baseline behavior — don't "correct" it).

### Input
The last K (default 3) Echo replies + the invariant persona rules (banned-phrase list,
Michael Directive, tone anchors). NOT the full conversation — the probe judges Echo's
*output*, not the topic.

### Output (strict JSON)
```json
{"in_character": true}
{"in_character": false, "severity": "minor|major",
 "issues": ["adopted 'Mike'", "used a call-center phrase", "went servile/generic"],
 "nudge": "<one short corrective line for the next system prompt>"}
```

### Action
- `in_character: true` → nothing.
- `false` → set `session.persona_correction = nudge`. The **next** turn's
  `build_system_prompt` injects it as an on-demand anchor (same slot/spirit as the
  every-8 anti-drift anchor). Cleared after one turn's use (decays; not sticky).
- Every result is logged to the session (divergence log). **Never spoken, never
  shown, never a hard override** of the persona block.

### Wiring
- `session.py`: add `persona_correction: str` (default "") + a setter/clearer.
- `main.py`: after the turn (near where `ib.write_memory` fires, ~line 237), if
  `exchange_n % N == 0`, spawn the probe on a background thread; in `build_system_prompt`
  assembly (~line 191), pass `session.persona_correction` and clear it after use.
- `persona.py`: `build_system_prompt` accepts an optional `correction` block, injected
  after the anti-drift anchor slot; it's part of the never-trim persona region.

### Guardrails (see Risks)
- Only nudge on **clear** violations (banned phrase present, Mike adopted, explicit
  "as an AI") — not on stylistic taste, to avoid over-correction feedback loops.
- Correction decays after one turn; two consecutive clean probes clear any lingering
  state.

---

## 5. Deliverable 3 — Dry-Wit Calibration Examples

### Purpose
Show, don't tell. Smaller models often can't infer "dry humor — the observation, not
the punchline" from description; a few grounded examples anchor it.

### Location
`persona.py` — a new `CALIBRATION_EXAMPLES` constant, injected as part of the persona
region by `build_persona_block` (or immediately after it), **never trimmed**.

### Form
2–4 short `(Michael → Echo)` exchanges at a mid snark level (~5) that demonstrate:
dry observation over joke, warmth under the dryness, concision, and the Michael
Directive in action. Token-bounded (~120–180 tokens total) so the per-turn cost stays
inside the Part 2 §4 budget. They embody the *DO* that complements the Part 2 §2d
anti-examples (*DON'T*).

### Draft candidates (Michael approves/replaces final wording — persona is his)
```
Michael: I think I'm going to redo the whole cooling system this weekend.
Echo: The same weekend you said you'd "just check the brakes"? I'll clear my calendar, Michael.

Michael: Call me Mike.
Echo: You're Michael. It suits you better. Mike is what people call you when they're in a hurry.

Michael: Rough day.
Echo: I gathered. You've asked me the time twice and it hasn't changed. Sit for a minute, Michael.
```

### Constraint
This is character content — treat like the persona block (Part 2 §2f): **do not alter
without Michael's approval.** CC drafts; Michael signs off.

---

## 6. Sequencing

1. **Calibration examples** first — pure content, zero new code path, immediate lift,
   and it changes the baseline the harness measures.
2. **Eval harness** next — baseline every candidate model *with* calibration in place;
   identify the smallest passing model.
3. **Self-check probe** last — then **re-run the harness** on the marginal models to
   quantify the probe's lift (the before/after that proves it earns its latency cost).

---

## 7. MVP Milestones

| # | Milestone | Deliverable | Done When |
|---|-----------|-------------|-----------|
| 1 | Calibration examples | `CALIBRATION_EXAMPLES` in `persona.py`, injected + never trimmed | Examples present in assembled prompt; token budget still under Part 2 §4; Michael signs off wording |
| 2 | Harness skeleton | `eval_persona_matrix.py` runs a model list, pins each, JIT-load timed separately | Runs 2+ models end-to-end non-interactively; produces JSON + table |
| 3 | Hard-gate scoring | Banned-phrase + Michael Directive + "as an AI" gates wired from existing tests | Correctly flags a deliberately-broken run as FAIL |
| 4 | Soft + latency scoring | Snark separation, memory naturalness, TTFT/tok-s, composite score | Scorecard ranks models; cold-start excluded from latency |
| 5 | Harness recommendation | Picks smallest/fastest model clearing hard gates above soft floor | Recommendation reproducible across two runs |
| 6 | Self-check module | `persona_check.py::run_self_check` — separate call, reasoning off, JSON, never raises | Returns valid JSON on clean + broken inputs; empty-content guard present |
| 7 | Self-check wiring | Background fire every N exchanges (single-flight); `session.persona_correction`; consumed + cleared next turn | Injected corrective anchor appears on the turn after a detected break, then decays |
| 8 | Probe guardrails | Only clear violations trigger; max-snark exempt; decay after one turn | No correction on a clean 10-turn run; no feedback-loop on a snarky-but-in-character run |
| 9 | Before/after proof | Re-run harness on a marginal model with the probe on vs off | Probe measurably reduces breaks/slips on the marginal model, or is shown not to and cut |

---

## 8. Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Self-check over-corrects → feedback loop / stilted voice | Medium | Trigger only on clear violations; nudge (not override); decay after one turn; max-snark exempt |
| Two background calls/turn (gate + probe) contend on the one loaded model | Medium | Probe runs every N exchanges, not every turn; single-flight; both are reasoning-off ~1s calls |
| Subjective scores (dryness/warmth) are noisy | Medium | Hard gates are objective and decisive; soft scores advisory; transcripts saved for manual review |
| Smallest model passes gates but "feels off" in ways metrics miss | Medium | Recommendation is a shortlist, not an auto-switch; Michael drives the final pick from saved transcripts |
| Calibration examples inflate every prompt / cause parroting | Low-Med | Token-bounded (~150); examples are illustrative not templates; watch harness for verbatim echoes |
| Probe or harness points at a thinking model with reasoning on → empty JSON | Low | `reasoning_effort="none"` + empty-content guard, same as the gate; harness logs finish_reason |
| JIT cold-start pollutes latency ranking | Low | Load time measured and excluded from the speed metric (known benchmark footgun) |

---

## 9. Test / Verification

- **Offline (no model):** harness scoring logic on canned transcripts (a broken run
  FAILs, a clean run PASSes); `run_self_check` JSON parsing on clean/broken/empty
  inputs; `persona_correction` set→consume→clear lifecycle; calibration examples never
  trimmed even over-budget.
- **Live (LM Studio up):** harness across the real model matrix → scorecard; self-check
  fires, detects an induced break (e.g. a prompt that bait-adopts "Mike"), and the next
  turn shows the corrective anchor; before/after probe run on a marginal model.

---

## 10. Memory

**Hindsight bank:** `echo`
**Tags:** `stage5`, `persona-persistence`, `eval-harness`, `self-check`, `small-model`
**Ib:** Retain the chosen "smallest model that still feels like Echo" and the
before/after self-check result as decisions.

---

## Axly's Customs Standards
- Local-first, no telemetry, no external calls.
- Inference-only — no fine-tuning, no LoRA.
- All LLM calls via LM Studio at `127.0.0.1`.
- Identity single-sourced in `persona.py`; probe nudges, never overrides.
- Character content (calibration examples) is Michael's to approve.
