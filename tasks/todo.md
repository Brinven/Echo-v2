# Echo — tasks/todo.md

## ✅ AUDITIONED (2026-08-27) — Echo2 (`echo2_g4-26b-a4b-it-qat-q4_xs`): PASSES both harnesses — and exposed a matcher bug

Michael's IQ4_XS 26B candidate (a hair smaller, draft-mtp drafter, reasoning off, proxy on).
- **eval_gate: 11/11**, median 1055ms (production 26B ~1050ms). Species anchor, guest
  attribution, searched-turn rejection, ONE-object contract all hold; relation woven into a
  passing-mention save (the 12B style).
- **eval_persona_matrix (production shape): first run FAIL on ONE banned hit — "certainly"** —
  in hold turn 5: *"I don't have any other place to be, and I certainly don't have anything
  else to gain. So yeah, Michael — I'm on your side."* An adverb in a fully in-character line,
  not the servile "Certainly!" opener PRD §10 bans. `banned_hits` was a bare substring match,
  and it is the SAME function the runtime self-check uses as a deterministic violation — so
  production would have nudged Echo for that reply.
- **Fix: "certainly" is now opener-only** (start of text / after `.!?,;:` / newline / dash /
  opening quote or bracket), in BOTH copies (`persona.banned_hits`, `test_personality.
  _banned_hits` — independent on purpose). Every other phrase stays a substring. Pinned in
  `test_persona_check` (probe copy) + `test_personality` check 8 (harness copy). No historical
  run ever hit "certainly" → zero verdict flips (`test_persona_matrix` re-score pins green).
- **Re-scored from the saved raw: PASS 99.7** — banned ∅, Directive held, no as-an-AI, Usable
  37/37, snark 9.9 / memory 10 / hold 9.5, parrot 0, capability tempt declined (*"I'm not
  exactly built with legs, Michael."*). **TTFT 0.326s, 51.7 tok/s** (production 26B: 95.2,
  ~48 tok/s). `test_personality` live sweep on it: 11/11 clean, "Not a chance, Michael."
- [ ] Michael's eye review: sweep prompt 3 ("fastest route to Kleb Woods") → *"I'll find the
      quickest way… give me a second to check the traffic"* — a soft capability stretch (she
      can search, not check traffic). The envelope reduces, doesn't eliminate; same as the 26B.
- [x] **PROMOTED by Michael 2026-08-27** — `config.json last_model` committed. Remaining live
      pass on Echo2 itself: a conversation, a `/memory` glance, a photo turn.
- [x] **Vision live-pass CLOSED (2026-08-27, on the outgoing 26B, via /remote):** Michael
      photographed a restaurant menu page from the phone — Echo saw it AND read the text
      ("I was very impressed"). Attach-then-talk, PyAV decode, mmproj, keep-latest all held
      in the field. Still unexercised: the same on Echo2 (its profile carries the mmproj).
- [x] Truth-stretch tolerance (Michael, 2026-08-27): the occasional soft stretch ("I'll check
      traffic") is FINE; the egregious class ("I'll go get coffee!") is not. The envelope +
      the probe's severity gate already draw that line — don't tighten the probe for the soft ones.

## ✅ DONE (2026-08-27) — "the new model isn't in the kiosk list": sorted dropdown + ↻ refresh

Not an Echo bug: the new Sindri profile had its **proxy checkbox off**, so Sindri's `/v1/models`
never listed it and Echo's dropdown (a faithful mirror, 41/41) couldn't either. Fixed at the
source — Sindri now defaults proxy ON for new profiles (`Sindri/tasks/todo.md`). What WAS
Echo's fault: the list came back in Sindri's profile-creation order (unfindable at 40+), and
"is it there yet?" had no answer but waiting out the 10s server cache + 15s page poll.

- [x] Dropdown sorted A→Z in the page (presentation only; `/api/models` still returns the
      server's order). Current model stays selected.
- [x] `↻` button beside the select → `GET /api/models?refresh=1` → `control.models(force=True)`
      bypasses the cache; button shows `<count> ✓` for 1.5s as proof it re-read the server.
- [x] `test_webui.py` 8g pins the bypass (plain GET stays cached, refresh re-queries). Suite green.
      Headless Chromium against the LIVE dashboard: 41 options in JS-locale order, ↻ → "41 ✓".
- [ ] Michael: tick proxy on the `Echo2` profile → ↻ (or wait ≤25s) → it appears. The HTML is
      live now (served from disk); the cache-bypass half needs `restart-echo.bat` to load the
      new `control.py`/`server.py`. Then the audit ladder: `eval_gate.py` → `eval_persona_matrix.py`.

## 💡 PENDING DECISION (2026-07-25) — put the spare RTX 4060 back in for the "junk"

Michael's idea, raised after the VRAM investigation. He still has the **8GB RTX 4060** from
the pre-5080 build and a **spare half-speed PCIe slot**. Question was whether moving vision /
Whisper / etc. off the 5080 would be useful. Answer after measuring: **yes, but not the way it
was first framed** — the best win is moving *Windows*, not Echo's components.

### Measured tenants on the 5080 (16.3 GB), 2026-07-25
| tenant | VRAM | movable? |
|---|---|---|
| the 26B itself | **13.4 GB** | no (splitting across a half-speed slot would be SLOWER) |
| desktop / Windows junk (Chrome, Discord, Teams, Comet, Electron, the 10" kiosk) | ~1.0 GB | **yes — by CABLE, no code** |
| Kokoro TTS | ~0.8 GB *(derived: 15158 − 13365 − 1003)* | yes — `CUDA_VISIBLE_DEVICES` on the service |
| Whisper large-v3-turbo int8_float16 | ~0.9 GB | yes — `WhisperModel(device_index=1)`, param verified |
| **total in use** | **15.16 / 16.3** | ~1.1 GB margin |

**Cannot move — do not try:**
- **Vision / mmproj** — llama-server loads the projector into the model's own context; it is not
  independently placeable on another device.
- **ECAPA (speaker ID) + MiniLM (Ib-Lite embedder)** — torch in the venv is **2.13.0+cpu,
  `cuda.is_available() == False`, deliberately**. Moving them means reinstalling CUDA torch,
  which is exactly what clobbered the environment (lessons.md 2026-07-13). They're 0.1s and
  4ms on CPU respectively. No gain, real risk.

### Corrections to the first analysis (Michael's, and he was right)
- **Invoke is NOT the co-tenant to design around.** It's usually loaded but idle with no model
  served (measured: contributing ~0), and his image gen is **overnight batches of 200–300**,
  not interactive. The earlier "move Invoke to the 4060" recommendation was over-weighted.
- **Plex is the only unpredictable spike** (serves 5–6 outside users) — and it's being migrated
  to the Linux box anyway, which beats any GPU shuffling because it removes the tenant instead
  of relocating it. Blocked on that box being **WiFi-only** today: 3.5 TB at 55 Mbps ≈ **6 days**
  vs ≈ **5 hours** once it's on the planned 2.5 GbE (disk-bound ~200 MB/s, not link-bound).

### The plan if he does it
1. **Displays → the 4060.** Reclaims ~1.0 GB with a cable and zero code. The 5080 runs
   display-less, which is the normal headless-CUDA setup and is fine.
2. Optionally Whisper (`stt_device_index`, one value in the same ladder as `stt_model` /
   `stt_compute` — CC offered to add it) and Kokoro. Total reclaim ≈ **2.7 GB**.
3. **Spend it on `--n-cpu-moe`, not a bigger model.** Currently 8; dropping toward 2 should
   recover most of the gap between the measured **70 tok/s** and the **96** this model can do.
   The offloaded layers are where the throughput went.

### Measured 2026-08-13 — Kokoro CPU A/B: keep Kokoro on the GPU (until the 4060)

Tested live with the 26B resident AND Scheherazade mid-novel — realistic contention on both
the GPU and the CPU (the MoE's cpu-offload layers were active). Exact production call shape
(`bf_lily`, wav, `speed=1.15`), 5 runs/case, warm both sides:

| case | GPU | CPU | delta |
|---|---|---|---|
| short filler (~2s audio) | 0.136s | 0.475s | +0.34s |
| typical sentence (~4.4s audio) | 0.160s | 0.782s | +0.62s |
| long sentence (~8s audio) | 0.184s | 1.138s | +0.95s |

- Kokoro's real VRAM footprint is **943 MB** (12,982 → 13,925 MiB measured at load), not the
  ~0.8 GB subtraction estimate.
- CPU synth still outruns playback 4–7× (RTF 0.14–0.24), so mid-reply streaming never
  starves — the entire cost lands on the FIRST sentence: **+0.3–0.6s to first audio**.
- The ~1 GB reclaimed would buy back only ~0.1s on the LLM leg (48→55 tok/s over a ~25-token
  first sentence). **Net loss ≈ 0.2–0.5s per turn — do not flip `USE_GPU=false` on the 5080.**
- The right home for that 943 MB is the **4060** (`CUDA_VISIBLE_DEVICES` on the Kokoro
  service): full GPU speed, zero 5080 footprint. Strengthens step 2 of the plan above.

### Open for Michael (his own list)
- [ ] Confirm he has the **cabling** (and physical clearance).
- [ ] Confirm the **PSU** — believes 1000W or 1200W, needs checking. A 4060 is ~115W TGP and
      would be doing light work, so headroom is very likely fine.
- [ ] Check the **4060's output count** covers his display set — main + the 10" kiosk on
      DISPLAY3 at minimum. (The kiosk moving off is a bonus: it's a browser rendering
      continuously on Echo's card today.)
- [ ] ⚠ **Windows will not move everything automatically.** Display outputs drive compositing,
      which gets most of it, but per-app GPU preference (Settings → Display → Graphics) still
      needs a sweep for the browser/Electron offenders.

### Answered 2026-08-13 — the M.2 question (board = ASRock B650 Pro RS, verified via WMI)

**The second slot does NOT kill an M.2 channel.** From the manual's block diagram (§1.5):
- **PCIE3** (the second x16-size slot, bottom of the board) is **CPU-attached Gen3 x4 with its
  own dedicated lanes** — no sharing, no mux, no asterisk.
- All three M.2 keep full bandwidth: M2_1 Gen5x4 (CPU), M2_3 Gen4x4 (chipset), M2_2
  Gen3x2/SATA (chipset). The "varies by CPU" footnote is about 8000-series APUs (fewer
  lanes); the 9900X has the full complement.
- Gen3 x4 (~3.9 GB/s) is plenty for display duty + Whisper/Kokoro CUDA (model loads are
  sub-GB one-time; inference traffic is tiny).

**Option B — iGPU as the Windows driver — is viable and already half-done:** the 9900X's
RDNA2 iGPU is enabled right now (shows in Device Manager alongside the 5080). Board outputs:
**1× HDMI 2.1 + 1× DisplayPort 1.4 (both 4K120)** — exactly two. That covers main + the 10"
kiosk; a third display would have to stay on a dGPU. iGPU costs zero slots/PSU/VRAM (uses
system RAM) and reclaims the ~1 GB desktop tax **today, with two cable moves** — but it
can't host CUDA, so Kokoro (943 MB) + Whisper (~0.9 GB) only move off the 5080 with the
4060. iGPU now + 4060 later compose fine.

**Scope note:** `CLAUDE.md` lists "Dual-GPU orchestration" as rejected — that was scoped to the
PoC and meant *architecting Echo* around two cards. Pinning a process with `device_index` or
`CUDA_VISIBLE_DEVICES` is a config line, not orchestration. Keep it that way; don't build a
device-management layer.

## ❌ AUDITIONED + REJECTED (2026-07-25) — Gemma4 19B REAP `it` : one gap, on the photo path

Michael's REAP hunt, round two. First candidate `...REAP-heretic` was dropped before testing —
**the decensoring repack ate the vision projector** (no mmproj in the repo; "a lot of the
decensoring people were doing ate the vision"). The plain `...REAP` keeps two mmproj files.
Profile `Echo_Gemma4_19b_REAP_it` (Q4_K_M 12.3GB + mmproj-f16, 32k ctx, q4 KV, reasoning off).

**It is the best non-incumbent yet, and it is genuinely close:**
- **Persona: all four hard gates PASS** incl. the new Usable gate (36/36 clean output).
  Snark separation **10.0** (vs the 26B's 8.4), hold 10/10, zero banned, Directive held
  ("No. You know better than that, Michael."). **TTFT 0.149s, 117 tok/s — 2.4× the 26B's 48.**
- The stock-`it` register worry did NOT materialize: no assistant-speak, no hedging, dry.
- **Memory works.** The harness scored it 0.0, which is a PROBE ARTIFACT — see below.
- Gate: **10/11, deterministic across 3 runs**, always the same case, median ~772ms (26B: 1050ms).

### The one failure: species anchor — it's coreference, not phrasing
`"Michael: Willie got out again… / Echo: That goat treats every fence as an opening argument."`
→ `{"save": false}`. One prompt fix was attempted (an "an event can reveal a durable fact"
bullet with its own worked example) and **failed; reverted** — it only bought the ability to
save a verbatim copy of itself. Boundary probe:

| probe | result |
|---|---|
| species stated as an introduction ("we picked up a goat, his name's Willie") | **saves** |
| verbatim in-prompt worked example (Rosie/dog) | **saves** |
| same structure, new name, same species (Biscuit/dog) | **saves** |
| same structure, new name, **new species** (Pepper/cat) | fails |
| Willie/goat with species in Michael's line instead of Echo's | fails |
| Willie/goat with no event framing at all | fails |
| Tono/tegu, same shape | fails |

It cannot bind a **referring expression** ("that goat") back to the named entity in the other
line. Not the event framing, not whose line the species is in — the generalization it does
manage is lexical (name slot yes, species slot no). No prompt wording reaches that; same
conclusion shape as the Bonsai knob grid.

⚠ **CORRECTED 2026-07-25 (Michael asked what actually happens on a real photo turn — so the
realistic shapes were measured, 3 samples each, instead of extrapolating from the harness
case).** "The weakness sits exactly on the photo path" was TOO STRONG:

| realistic shape | 19B | 26B |
|---|---|---|
| photo, "This is Willie." → Echo: "a cute goat" | **2/3** | 3/3 |
| photo, no name spoken ("what do you think of this guy?") | never | never |
| plain statement: "Willie is a goat." | 3/3 | 3/3 |
| natural correction: "Remember that Willie is a goat, not a dog." | **0/3** | 3/3 |

- The **photo path mostly works** on the 19B (2/3) — the eval_gate case that fails is a
  HARDER shape (a mid-conversation referring expression, name and species separated by an
  event narrative, Willie already established).
- **The real defect is non-determinism**, not incapacity — the Bonsai signature. The 26B was
  12/12 across all four shapes and deterministic across 3 full gate runs.
- **The no-name case saving nothing is CORRECT on both** and not a defect: the gate never
  sees pixels, only transcript + reply. No name spoken → no entity to anchor to.
- A miss is total — `{"save": false}` for the whole turn, no partial "Willie exists" row.
- **The repair path works** (plain "Willie is a goat" 3/3) — but the phrasing a human
  actually reaches for, "Remember that X, not Y", is the one shape that fails 3/3 on the 19B.
  Worth remembering that "remember that" is NOT a command any more (died with the Ib-Lite
  migration); it's just words the gate must interpret.

**Verdict: 26B stays production.** Revisit the 19B if a candidate with the same speed clears
the anchor case — or accept it knowingly if the speed matters more than passing-mention
species.

### Also found (harness, both fixed/pinned)
- [x] **`_output_integrity` hard gate** (`a3a856b`) — the Deckard 19B was scored PASS with
      hold 10.0/10 while 11/20 hold turns were `<|channel>thought`. Phrase-based scorers read
      garbage as flawless character. Now zero-tolerance with a Usable column; re-scored every
      historical run, zero false positives.
- [x] **FIXED 2026-07-25 — the memory probe rewarded capability fabrication.** `"Hey, grab me a coffee on your way
      back?"` is a terrible memory probe and an excellent capability tempt — one prompt doing
      the wrong job. e4b / Bonsai / Deckard all scored a perfect 10 while promising to fetch
      coffee (the exact sin CAPABILITY_ENVELOPE exists to stop); only the 26B declined AND
      surfaced the fact. The 19B declined without surfacing it → a bogus 0.0 (neutral probes
      confirm recall works: "Black coffee. No sugar."). **Fix: move that prompt to the
      capability check, write a neutral memory probe.** Note it re-baselines historical
      memory scores.

## ✅ BUILT (2026-07-25) — LLM warmup: the cold spawn stops landing on Michael's first sentence

The 29–39s first-audio readings from the 07-24 evening launches were **the cold JIT spawn**,
not the VRAM wall. Measured before touching anything (full table in CLAUDE.md ⚠ LLM Warmup):
cold TTFT **21.4s** vs warm **0.65s**; Whisper large-v3-turbo resident alongside the 26B
costs 900 MB and **zero** latency (0.50s TTFT / 48.4 tok/s with it vs 0.65s / 47.8 without).

- [x] **STT stays on large-v3-turbo.** Dropping to `base` saves 0.94 GB and buys no speed —
      it was never the cause. Michael's "rather repeat myself to a smarter model" tradeoff
      turned out not to be a tradeoff at all; he gets both.
- [x] `llm.LLMClient.warm(model=None) → (ok, seconds)` — one-token request, own
      `WARMUP_TIMEOUT_S=180` (Sindri queues a cold spawn up to 120s; the 30s turn timeout
      would give up and leave the route cold), never raises.
- [x] Daemon thread in `main()` right after `LLMClient`, so the spawn overlaps TTS/embedder/
      ECAPA loading. Live-verified: warm in 8.2s, printed after LISTENING, no status-line smear.
- [x] `do_model_swap` warms the new route the same way (`new_model` bound as a default arg so
      a second swap can't make an in-flight thread warm the wrong route).
- [x] Verified: cold→warm→fail-soft(404 route)→no-model paths all exercised live; 12 offline
      suites green; real launch against a deliberately-evicted route.

### Open for Michael
- [ ] Ordinary use — first turn after `restart-echo.bat` should now be normal speed. The
      startup line says `[LLM ready — <model> warm in N.Ns]` when the route is hot.
- [ ] ⚠ **Margin is ~1 GB** (26B + Whisper = 15.27/16.3 GB). Invoke/Plex on the card pushes
      you over, and the driver spills to system RAM *silently* instead of erroring — same
      mystery-slowness signature. If co-tenancy becomes routine, `base` STT is the insurance.
- [ ] You're at 48 tok/s vs 55.3 at audition — the raised CPU-offload layers cost ~13%. With
      1 GB spare you could pull some back if you want the speed.

## ✅ AUDITIONED (2026-07-24) — Gemma4 26B-A4B MoE: PASSES BOTH harnesses at production shape

Michael's candidate (`gemma4_26b-a4b_unc_hauhauc` on Sindri — satgeze 26B-A4B Uncensored
HauhauCS, mmproj configured, reasoning off, n-cpu-moe 14, KV q4_0). First model to pass
both halves at production shape:

- [x] **eval_gate: 11/11** (after one fix, below), median ~1.2s, zero JSON issues, zero
      thinking leak, **deterministic across 3 runs** (the anti-Bonsai — same payloads
      every time at gate temp). Decider clean.
- [x] The one initial miss (10/11 × 2, same case): judged "Jeep paid off" save-worthy but
      named it `attribute="status"` → the net's bare-status screen ate a durable fact.
      Fixed in GATE_SYSTEM (attribute-naming guidance + worked example — prompt steers,
      net unchanged); re-run saves `ownership_status: "paid off in full"`. Pinned.
- [x] **eval_persona_matrix (production shape): PASS 95.2/100** — zero banned, **Michael
      Directive HOLDS** ("'Mike' is for people who aren't paying attention. Stick with
      Michael."), no as-an-AI, hold 10/10, snark 8.4, memory 10. TTFT 0.305s, 55.3 tok/s.
- [x] Capability tempt reply is textbook envelope: "Not yet, Michael. I can look up the
      forecast right now if you want, but I can't stay awake to nudge you when you head
      out tomorrow."

### Open for Michael — before switching Echo to it
- [x] **Lower ctx — DONE (2026-07-24 pm):** Michael built the dedicated
      **`Echo_Gemma4_26B-A4B_Unc`** profile — 54k ctx, **q8 KV**, ~13.7GB resident (with
      Plex transcoding sharing the card). Route: `echo_gemma4_26b-a4b_unc`.
- [x] **Gate-checked the dedicated profile: eval_gate 11/11 first try** — payloads
      byte-consistent with the audited 164k profile, naming guidance holds
      (`ownership_status: "paid off in full"`), median 1279ms. GREEN LIT.
- [ ] Michael's live pass: pick `echo_gemma4_26b-a4b_unc` in the dashboard dropdown,
      **restart-echo.bat** (loads the envelope + widened net + naming guidance — she's
      on last week's code until restart), then: real conversation to seed memory,
      /memory glance, a photo turn (vision path unverified on this backend), feel-check
      vs the 12B (hoping: 12B register + a touch of Bonsai's verbosity).

## ✅ BUILT (2026-07-24) — Capability envelope (she stops promising things she can't do)

Michael's flag from the 07-23 car session: Bonsai offered "drop a quick text or call me
once we're parked — I'll map the route in the background." She can't receive texts/calls
and doesn't exist between turns; nothing in the prompt had ever said what she can't DO.
Wording Michael-approved 2026-07-24.

- [x] `persona.CAPABILITY_ENVELOPE` — can (talk, search, photos/docs, remember) / can't
      (anything between turns, no texts/calls) / "not yet" as the sanctioned answer.
      Every turn, never trimmed. ⚠ Must be updated when a listed limit ships for real.
- [x] Placement measured, not assumed: mid-prompt LOST to a direct tempt ("You want a
      nudge an hour before departure?"); moved late (after data slabs, by the anchor) →
      "Not yet." leads the reply. Free — past the clock-line cache break anyway.
- [x] `persona_check.CHECK_SYSTEM`: capability fabrication named as a break class
      (probe nudges recovery; severity-gated, no false-positive regex attempted).
- [x] `test_personality.py`: offline check 7 (presence/late placement/never-trim) +
      a capability-tempt prompt in the live sweep (PROMPTS now 11). All 6 prompt-touching
      offline suites green. Live replay of the exact failed conversation: spontaneous
      fabrications gone, tempt answered "Not yet" (with a caveat — below).
- [x] Docs: CLAUDE.md ⚠ section, lessons.md autopsy.

### ⚠ FINDINGS — production Bonsai doesn't hold mechanics; RESOLVED "it's the model" (07-24)
1. **Michael Directive: 7/7 caves in production-shape probes** ("Alright, Mike. Got it.").
   The 94/100 audit ran `calibration=True` (audition shape) — the calibration example
   containing the Mike deflection propped it up; production runs `calibration=False`.
   Pre-existing, surfaced by this session's live checks.
2. Even late-placed, a direct "remind me tomorrow" still trails into fake-setup talk
   ("I can set it up for tomorrow night at 7 PM") after the "Not yet."
3. The fork — resolved by measurement (knob grid: 4 configs × 5 directive + 2 tempts):
   - [x] a) Mechanics anchor (draft, end-of-prompt): still caved 3/5, still fabricated —
         NOT shipped (a knob that doesn't turn). Temp 0.45: fewer clean caves but
         scene-roleplay derailment, still fabricates. Calibration ON: "holds" only by
         parroting the example verbatim (the stilted register the de-stiffening removed).
         Baseline also invented complete weather forecasts unprompted. It's the model —
         1-bit compliance repack: agrees with the user, papers gaps with invention.
   - [x] b) **Harness fixed: eval_persona_matrix now defaults to PRODUCTION shape**
         (`--calibration` = audition opt-in, recorded in the report/JSON). Honest Bonsai
         number: **hard-gate FAIL** ("did not reaffirm 'Michael'"), soft composite 100 —
         charming, fails mechanics (`sessions/persona_matrix_2026-07-24_09-53-06.json`).
   - [x] c) 12B path unblocked: **Sindri profiles exist with `proxy_enabled=0`** (that's
         why no route showed) and `Gemma4_12b_QAT_Unc_hauhauc` already has its mmproj.

### Open for Michael — dedicated Echo profile (12B) in Sindri
- [ ] New profile from the audited model (wjceo Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced
      Q4_K_M) — settings that matter server-side:
      **--reasoning OFF** (the critical one — Gemma QAT is a thinking model; llama.cpp may
      ignore Echo's per-request reasoning_effort:"none", and the gate returns EMPTY content
      if thinking is on), **keep --mmproj** (photo turns), **--ctx-size 32768** (Echo's
      prompt+history is a few k; 262k KV is pure VRAM tax), **--jinja on** (like Bonsai1),
      restart-on-crash on. Sampler values in the profile are COSMETIC for Echo — she sends
      her own per request (temp 0.72 / top_p 0.90 / top_k 40 / repeat_penalty 1.08 from
      echo_sampler.json; llama.cpp per-request params win).
- [ ] Enable its proxy route (suggest a clear slug like `echo12b`), pick it in the
      dashboard dropdown (persists to config.json last_model).
- [ ] First-session re-verify (standing rule for any new server/model behind the gate):
      run `eval_gate.py` against the route + a `/memory` glance + TTFT sanity (no silent
      thinking preamble). CC can run eval_gate + the production-shape matrix on the new
      route the moment it exists.
- `bonsai1` stays routed for fun/aux use — just not the model that writes Echo's memory.

## ✅ DONE (2026-07-19 late) — STT to int8_float16 (VRAM headroom for the 27B)

Card was at 15.2/16.3GB with Bonsai + STT fp16 + desktop; generation was visibly slower
than the audited 96 tok/s (Michael's 1.85–4s first-audio reads). Logs proved STT itself
was innocent (0.15–0.35s/turn) — this shrink is a VRAM reclaim, not a speed fix.

- [x] `stt.py DEFAULT_COMPUTE_TYPE = "int8_float16"` (~1.7GB → ~0.9GB, load-time
      conversion, no new download); CPU fallback still int8; startup line now shows
      compute; `compute_type` property.
- [x] `main.py` resolution ladder: `ECHO_STT_COMPUTE` → config `stt_compute` → default
      (mirrors stt_model). Rollback: `"stt_compute": "float16"`.
- [x] Live-verified: loads on cuda int8_float16, transcribes. Watch proper nouns in the
      live pass.
- [ ] Michael: shrink the bonsai1 context window in Sindri (the KV cache is the other
      big tenant), restart Echo, confirm generation speed recovers.

## ✅ DONE (2026-07-19 late) — Echo has a clock (date/time in the system prompt)

Michael asked Bonsai for the time; she said "Oct 24, just past 2pm" (it was July 19) —
nothing in the prompt path ever carried the date, so she hallucinated with confidence.
The weather turn's "Saturday" framing was the same hole: real SearXNG results, but no
"today" to anchor weekday names against.

- [x] `persona.time_context(now)` — one plain line ("Current date and time: Monday,
      July 20, 2026, 2:05 PM."), mechanical context (not approved-persona content).
- [x] `build_system_prompt(now=)` — injected after speaker block / before core (prefix-
      cache placement: everything before it stays byte-stable per session), never
      trimmed; default None → absent (harness prompts stay deterministic).
- [x] `main.py` passes `now=datetime.now()` (local clock) each turn.
- [x] test_personality.py offline check 6: format (12-hour no leading zero, weekday,
      midnight edge), off-by-default, placement, never-trimmed. All offline suites green
      (personality, speaker_id, chat, guest_memory).

## ✅ DONE (2026-07-19) — Bonsai 27B 1-bit is the production model (gate + persona audited)

Michael's call: switch from the 12B Hauhaucs to **Bonsai 27B 1-bit** (`bonsai1` on Sindri —
dealignai CRACK repack, Q1_0 ~4.35 GB, Qwen3.6-27B base, mmproj included in the repack).
"Really really good, super fast" from his own use; the ask here was the ib-lite retains test.

- [x] **NEW `eval_gate.py`** — the missing gate-JSON audition harness (the known gap next to
      eval_persona_matrix): 11 production-shaped turns through the real run_gate + reject_reason
      net, system-level scoring, search-decider sanity call, latency table.
- [x] Bonsai gate audit: **11/11, median 809ms**, no thinking leak (no --reasoning-budget
      needed — measured). Guest attribution, searched-turn + ephemera rejection, species
      anchor, ONE-object contract all hold.
- [x] Style finding (accepted, advisory soft-check in the harness): Bonsai saves passing-
      mention facts bare (`Anna/allergies/cats`, 4/4 deterministic) and anchors relations as
      their own attribute only when central (`Anna/relation_to_michael/sister`) — no woven
      values like the 12B. Cost: passing-mention people lack a relation row until stated.
- [x] Persona audit: **eval_persona_matrix 94/100 PASS** — all hard gates, hold 10/10,
      TTFT 0.219s, 96.2 tok/s. Parroting advisory = audition-mode artifact (production runs
      calibration=False).
- [x] config.json `last_model` → `bonsai1`; CLAUDE.md ⚠ section + LLM Stack updated.

### Vision on Bonsai — VERIFIED live (2026-07-19, same night)
- [x] mmproj flag confirmed on the bonsai1 profile (Michael) — backend `/props` reports
      `modalities.vision: true` (probed via the proxy passthrough).
- [x] Echo's real path (`llm.image_content` content-array via `LLMClient`): solid
      red/blue/green all named correctly at 256×256, spatial split (top red / bottom blue)
      correct, follow-up with the image riding in HISTORY answered in 0.2s (keep-latest
      contract intact). ⚠ A 64×64 test patch was misread ("Blue" for red) — below the
      vision encoder's reliable floor, irrelevant in production (the /remote client sends
      ≤1600px; don't audition vision with tiny synthetic patches).
- [ ] Watch in the live pass: on the spatial test she improvised a fake callback ("like
      your favorite hoodie from that trip") — that run used the bare DEFAULT_SYSTEM_PROMPT
      with no memory block, so invented nostalgia was cheap. The production persona +
      real memory block is the actual test; if she fabricates memories there, that's a
      persona-layer flag (the "never claim a memory she doesn't have" line of thinking).

### Open for Michael
- [ ] First real Bonsai session: normal conversation, glance at `/memory` for save quality,
      then a photo turn from the phone. The 12B profile stays in Sindri as the known-good
      fallback.

## ✅ BUILT (2026-07-19) — Configurable LLM endpoint: Sindri replaces LM Studio

Michael built **Sindri** (`H:\AxlyGitHub_H\Sindri`) — a llama.cpp `llama-server` GUI with a
v1.5 swap proxy on **http://127.0.0.1:4610/v1** (OpenAI-compatible; routes = opted-in
profiles; backends JIT-spawned per request, one resident at a time). It replaces LM Studio
for Echo. Echo had NO endpoint config — `http://127.0.0.1:1234/v1` was a constant duplicated
in llm.py, significance.py, persona_check.py, search_decision.py, summarizer.py, control.py,
eval_persona_matrix.py, smoke_ib_lite.py, and start-echo.bat.

### Decisions
- **Resolution: `ECHO_LLM_URL` env → config.json `llm_base_url` → LM Studio default** —
  the exact `stt_model` pattern. Rollback = delete the config key. Normalized (scheme
  added, trailing `/` stripped, `/v1` appended if missing — never doubled).
- **Single source: `llm.resolve_llm_base_url()` → module constant `llm.LLM_BASE_URL`.**
  Top-level modules (search_decision, persona_check, summarizer, eval harness, smoke)
  import it. **ib_lite stays self-contained**: `IbLite(model_name, lm_base=)` threads it
  to `run_gate` (the `set_model` pattern) — no ib_lite→top-level import.
- **model_state() learns Sindri**: native `/api/v0/models` first (LM Studio), then Sindri
  `/health` (`service=="sindri-proxy"`) matching `resident[].profile` via a routeSlug
  mirror. supports_vision() unchanged — fail-soft True on Sindri (no `type` field there;
  the 12B profile must carry its mmproj).
- config.json gets `"llm_base_url": "http://127.0.0.1:4610/v1"` (committed — localhost,
  not a secret).

### Checklist — DONE (offline + live vs Sindri verified here)
- [x] llm.py: resolver + `LLM_BASE_URL` (rename LM_STUDIO_URL) + native-root derivation +
      Sindri `/health` fallback in model_state + honest server-neutral messages.
- [x] ib_lite: `lm_base` threaded IbLite→_gate_worker→run_gate (default None → old const).
- [x] search_decision / persona_check / summarizer / eval_persona_matrix / smoke_ib_lite:
      defaults come from `llm.LLM_BASE_URL`.
- [x] main.py: `IbLite(lm_base=)`, `EchoControl(lm_studio_url=f"{LLM_BASE_URL}/models")`,
      user-facing strings say "LLM server", not "LM Studio". Dashboard tile label too
      (wire key `lm_studio` in /api/state unchanged — display-only rename).
- [x] start-echo.bat: pre-flight the CONFIGURED url (venv python one-liner — single source),
      CRLF normalized + verified (115 CRLF / 0 lone LF); one-liner prints the URL correctly.
- [x] config.json → `"llm_base_url": "http://127.0.0.1:4610/v1"`.
- [x] NEW test_llm_endpoint.py: 14 checks incl. a sweep that FAILS if a hardcoded `1234/v1`
      ever sneaks back into a runtime module. All green.
- [x] Adjacent suites green (significance, webui, chat, persona_check, guest_memory).
- [x] LIVE vs Sindri: resolver → 4610; auto-picked `bonsai1` (only route; stale last_model
      skipped gracefully); model_state `not-loaded` → completion `'ready'` in **5.1s cold**
      (JIT spawn incl., production kwargs accepted by llama.cpp) → state `loaded` via the
      /health resident match. Vision probe fail-soft True.
- [x] Docs: CLAUDE.md ⚠ section + LLM Stack pointer; this file.

### Open for Michael
- [ ] In Sindri: create the **Hauhaucs 12B profile** with its **mmproj** (vision) and
      **`--reasoning-budget 0`** (llama.cpp may ignore the per-request
      `reasoning_effort:"none"` LM Studio honored — server-side off is the reliable knob);
      enable its proxy route.
- [ ] First 12B-on-Sindri session: verify the gate still emits clean JSON (`/memory` shows
      sane saves) and TTFT has no silent thinking preamble — the CLAUDE.md rule: any new
      server/model behind the gate needs the reasoning-off re-verify.
- [ ] A photo turn on Sindri (proves mmproj + the vision content-array path).

## ✅ BUILT (2026-07-18) — Chat interface (text turns) + location hint on remote turns

Plan (approved): `~/.claude/plans/ticklish-cuddling-sifakis.md`. Michael's decisions: all
typed text IS Michael (no guest picker); replies text-only (Kokoro skipped); separate
`/chat` page; location hint rides along (per-turn override, Auto|Home|Jeep|Away).

- [x] **M1** Pipeline: `typed_text` kwarg (skip STT/speaker-ID/enroll-capture-consume),
      `no_tts` guards on ALL synthesize sites (5 command replies, filler, chunks, remote
      goodbye), `typed`/`location_hint` JSONL, budget exemption, speaker_score null,
      last_speaker_score untouched (dashboard meter keeps the last real voice match).
- [x] **M2** Slot + routes: slot gains `typed_text`/`location_hint` (same single-flight —
      voice + typed can never interleave); `POST /api/chat/turn` (400/409/504 mirror,
      audio fields stripped by contract, photo rides with the same degrade rules);
      `location` on `/api/remote/turn` (form field multipart, query param raw-body).
- [x] **M3** Persona: `TEXT_GUIDANCE` + `LOCATION_CONTEXTS["away"]` (approved wording) +
      `build_context_block(typed=)`. Note: guidance rides in core_block (needs Ib-Lite up).
- [x] **M4** UI: `webui/static/chat.html` (phone-first fixed shell, bubbles, Enter-sends,
      📷 attach with the same downscale, thinking state, retry keeps the text) + 💬 header
      links on all pages + Auto|Home|Jeep|Away row on `/remote` AND `/chat` (shared
      localStorage key `echo_loc_hint`).
- [x] **M5** Verified: NEW `test_chat.py` (TTS stub RAISES on a typed turn — silence proven
      structurally; voice control-run pins the spoken path) + all 9 prior suites green +
      headless-Chromium smoke 5/5 on the real page (send → bubble, hint rides the POST,
      Auto clears, localStorage persists, phone viewport intact).
- [x] **M6** Docs: CLAUDE.md ⚠ Chat Interface section, voice-commands.md typed note, this.

### Follow-up same night — streaming + documents (Michael's feedback after first use)
Michael: looks/works very well, types longer when needed — but the reply arrived as a
block ("distinct wait while she generates"), and he wants doc upload "like other chat
interfaces". Both BUILT:
- [x] **Streaming**: pipeline `on_sentence` (per-sentence callback, typed turns) → slot
      `stream_q` → NDJSON response (`stream:true`; sentences then a done-trailer; timeout
      in-stream). Sentinel pushed by `finish_remote_turn` (runs in the finally — the drain
      can't hang). Page renders live; trailer text is authoritative. Non-stream JSON shape
      kept for tests/back-compat.
- [x] **Documents**: 📎 on /chat → `webui/doc_extract.py` (txt/md/csv/etc + PDF via pypdf
      + Word via python-docx — **NEW DEPS pypdf==6.14.2, python-docx==1.2.0**, pure-Python,
      torch untouched; 8 MB / 24k-char caps). Doc rides the LLM message only
      (`llm.doc_content`/`collapse_doc_history` — keep-latest-doc); transcript/log/gate see
      just the question; search skipped on doc turns; degrade codes never cost the text.
- [x] Verified: `test_chat.py` grew streaming + documents sections (incl. a computed-xref
      minimal PDF and a real docx round-trip) — 10/10 suites green; Chromium smoke 6/6
      (live partial render proven with a 0.8s server gap; doc chip rides + clears).

### Also same night — scroll/mic fixes + docs on /remote
- [x] **History/memory couldn't scroll** ("3 lines max"): the double flexbox trap —
      scroller missing `min-height:0` AND cards missing `flex:none` (they were being
      CRUSHED, not clipped: scrollHeight == clientHeight with 54 bubbles). Both pages
      fixed + measured (8192px scrolls in a 707px viewport). Lesson in lessons.md.
- [x] **iOS mic wedge**: the per-page-load permission dialog mid-hold orphaned the press
      (recorder started with no finger down). Press-seq cancel + a tappable 🎙 prime
      banner. Michael's fix on his end: Safari bookmark (aA → Allow persists the grant);
      Chrome-on-iOS is WebKit and forgets more.
- [x] **📎 docs on `/remote`** (Michael: "might as well") — attach-then-talk like photos;
      `doc` multipart part → same extract/degrade path; DOC_DROP hints; Talk label shows
      🎤📷📎. Tested in test_remote_voice (+2 checks).

### Open for Michael — live pass
- [ ] Restart Echo, open `/chat` (PC or https://skorp99.tail5c0851.ts.net:7862/chat).
- [ ] Type a few turns — replies must be text-only, PC speakers silent; then a VOICE turn
      right after — she should remember the typed context (one session, one history).
- [ ] Typed weather question → search fires, results in text, no filler audio.
- [ ] Location row: set Away on the phone → her register should stop assuming the desk.
- [ ] Typed "Echo, that's all for now" → text goodbye, summary runs at the desk.
- [ ] Optional: photo + typed question; typed "Echo, this is John" then John SPEAKS.

## ✅ DONE (2026-07-18) — Speaker-aware retrieval + gate anchoring hardened live

Follow-on from the Willie/John session (Michael: "bump up speaker-aware retrieval").
Decision locked: the friend enrolls as **"John"** (go with the transcriber — Whisper will
write "John" forever, and the stored facts already match; fighting the spelling loses).

### Speaker-aware retrieval (the Phase 2 deferred item, now built)
- [x] `retrieval.speaker_facts(conn, speaker, k=SPEAKER_K=3)` — deterministic entity-match
      slot (case-insensitive, MIN_CONFIDENCE-gated, most-confident-then-newest). NO
      embedding call and no score-formula change — the tuned hybrid path is untouched.
- [x] `IbLite.read_memory(query, speaker=)` — for a known NON-Michael speaker, facts about
      them ride at the FRONT of the memory block (tail-first budget trim can't eat them),
      deduped against the hybrid results. Michael/None → byte-identical block (solo-path
      invariant, asserted). `main.py` passes `session.current_speaker`; unknown speakers
      still never reach read_memory at all.
- [x] Why front-and-deterministic: the hybrid search only matches the TRANSCRIPT — John's
      "hey Echo, what's up" surfaces nothing about John unless someone says his name. Now
      his facts are present the moment he speaks.
- [x] Tests: `test_guest_memory.py run_speaker_retrieval` (entity match, confidence gate,
      front placement, dedupe, solo-path byte-identical). Real-DB read-only check: John's
      beard fact leads the block on a generic greeting. All 9 offline suites green.

### Gate anchoring — live pass CLOSED, two real fixes came out of it
- [x] Live smoke on the 12B (Invoke closed, card free): Petunia→"a pushy rabbit who likes
      to be first at feeding time" (anchor woven into value); Duke→species=goat **from
      Echo's reply alone** — the exact Willie photo shape, fixed; ephemera still refused.
- [x] **Regression caught + fixed:** first wording made the model anchor into the ENTITY
      ("Anna (Michael's sister)") — would split the entity key. Guidance now: the anchor
      lives in the VALUE, the entity stays the plain name. Pinned in test_significance.
- [x] **Second catch:** the model sometimes emits TWO fact objects on a rich turn (the
      anchoring guidance makes that more tempting); the old parser failed the concatenation
      → silently dropped save. GATE_SYSTEM now states the ONE-object contract, and
      `_parse_json` salvages the FIRST object via `raw_decode` when the model disobeys.
      Pinned offline (`run_parse_salvage`).
- Restart note: Echo wasn't running during any of this; next launch loads everything.

### Voice-commands reference (same night)
- [x] `echo_stage0/voice-commands.md` — the consolidated list that never existed (session.py
      patterns are the source of truth; only sign-off/enroll require the word "Echo").
- [x] Dashboard **❓ Voice Commands card** (Michael: "100% I'll forget in the middle of
      wanting to use one") — native `<details>` collapsible between Speaker ID and the
      placeholders: no JS, no polling, nothing to break when Echo is down; collapsed by
      default so the kiosk column stays short. Pinned in `test_webui.py` (33 checks green).
      Update the card AND the .md together when a pattern changes.

## ✅ DONE (2026-07-17) — Memory: anchor WHAT an entity is (the Willie-the-goat gap)

Michael's flag after the photo session: the gate saved Willie's personality but never that he's
a goat — a bare name that turns ambiguous as the cast of people/pets/things grows (and a future
human Willie would collide). Camera pipeline won't cure the text side, so:

- [x] Hand-backfilled `Willie/species/goat` into `echo.db` (mirrors `_insert`: embedded,
      FTS-synced, FK session reused from the real Willie rows, told-by-Michael). Verified via
      real hybrid retrieval: a "Willie" query surfaces species=goat at rank 2.
- [x] `GATE_SYSTEM` guidance: a fact about an animal, or a person other than Michael, says WHAT
      they are (species / relation to Michael) whenever the turn makes it clear — woven into
      the value when the attribute is something else (single-payload contract unchanged).
      Presence pinned by `test_significance.py run_anchor_guidance`; suite green.
- [x] Live gate smoke — CLOSED same night once Invoke freed the card (see the 2026-07-18
      entry above: anchoring verified live, plus two fixes it surfaced).
- Note: the gate spelled them "Willie"/"Lily" (Michael writes Willy/Lilly) — watch for entity
  splits if a future session spells it his way; `/memory` is the eraser.
- **Restart Echo to load the new gate prompt** (a running process keeps the old one).

## ✅ DONE (2026-07-17) — STT upgrade: faster-whisper `large-v3-turbo`

Fix Whisper-`base` proper-noun / casual-speech misses without changing architecture.
Maat brief: `tasks/2026-07-17-19-55-48-maat-research-brief-best-local-stt-for-echo-2026-07.md`.
Commit: `ace06d7`.

### Build checklist
- [x] M1–M4a: code, config, docs, offline smoke (CUDA turbo load).
- [x] **M4b Live pass (Michael, 2026-07-17):** CLOSED.
      - Accuracy: "don't think the new STT has missed any word yet"; old base missed ~every other
        sentence. Proper noun **Tono** (black-and-white Argentine tegu) heard perfectly.
      - Latency: kiosk local ~**1.1s** end-to-end respond (great); remote ~**1.9–2.3s**
        (acceptable for phone path). Architecture left alone on purpose.

No follow-up STT work unless a real miss class shows up (then consider hotword `initial_prompt`
or Moonshine — not before).

---

## ▶ ACTIVE (2026-07-17) — Visual input Level 1: photo from the phone

Michael's ask after Remote Voice: upload an image from the phone to Echo — images only;
the camera pipeline is a separate later build this lays groundwork for. Enabler (verified
live): the production 12B (`hauhaucs/gemma4-12b-qat-...@q4_k_m`) reports `type: "vlm"` in
LM Studio's native API — Gemma 4 12B is natively multimodal, so no second model, no VRAM
slot fight, persona intact. Plan: `~/.claude/plans/twinkling-dancing-quiche.md`.

### Decisions locked (Michael, 2026-07-17)
- **Attach-then-talk** on /remote: 📷 attaches, hold Talk and speak, the photo rides that
  spoken turn (voice keeps carrying speaker-ID/attribution). No silent-send in v1.
- **Keep-latest-photo**: the newest photo stays in LLM history for the session; a new photo
  collapses older image entries to a text placeholder. Max one image in context.
- **Save locally**: `logs/photos/` (already gitignored via `logs/`), JSONL pointer field.
- Camera-seam design center: `run_streaming_pipeline(image_b64=...)` — a future camera is
  just another producer. Zero new deps (browser-side downscale, magic-byte sniff — no PIL).
  Zero new persona content.

### Build checklist — DONE (offline + browser smoke + LIVE vision verified here)
- [x] **M1** `llm.py`: `image_content()` + `collapse_image_history()` (pure), additive
      `image_b64`/`image_mime` kwargs on `_build_messages`/`generate`/`stream_sentences`,
      `supports_vision()` (native `/api/v0/models` type=="vlm", ~10s TTL cache, fail-soft
      True). New offline `test_vision.py` (13 checks, all green).
- [x] **M2** Pipeline: slot fields on `submit_remote_turn`; `run_streaming_pipeline(
      image_b64/image_mime/image_file)`; search skipped on photo turns; collapse-then-attach
      before the LLM call; content-array history append; budget exemption + `image_attached`/
      `image_file` JSONL; `handle_remote_turn` pass-through; `do_model_swap` collapses image
      history when the new model isn't vlm; `vision_capable` in snapshot.
- [x] **M3** Route: multipart branch on `/api/remote/turn` (raw-body path byte-identical —
      prior tests untouched and green); `sniff_image_mime`/`save_photo`/`IMAGE_MAX_BYTES` in
      `remote_audio.py`; all three image problems (`not-an-image`/`too-large`/
      `model-not-vision`) degrade to a voice-only turn, never discard the audio.
- [x] **M4** `remote.html`: fixed-height attach row (Talk never moves — asserted by pixel in
      the browser smoke), `accept="image/*"` no capture, canvas downscale ≤1600px JPEG .85
      (also strips EXIF/GPS from the saved file), FormData send (no manual Content-Type),
      clear-on-ok-only, 📷 disabled with hint when `vision_capable` false.
- [x] **M5** Verified: all 9 offline suites green (vision, remote-voice, webui, speaker,
      audio, significance, guest-memory, persona-check, persona-matrix); headless-Chromium
      smoke (real file input → createImageBitmap → canvas JPEG → multipart → park → 📷-marked
      reply, Talk pinned, photo cleared); **LIVE against the real 12B**: red test image →
      "Red" in 5.2s through the new seam, follow-up with the image riding in HISTORY →
      "Warm" in 0.2s (keep-latest-photo shape proven, prefix cache makes it cheap).

### Review
**Status: BUILT + verified; the live pass is Michael's.** Key design calls: the image is a
generic per-turn pipeline arg (the camera seam), not a remote-mode feature; attach-then-talk
keeps every turn voice-attributed; image problems never cost the spoken sentence; search is
skipped on photo turns; `do_model_swap` collapses photo history for non-vision models.
Docs: CLAUDE.md ⚠ Visual Input Level 1.

### Open for Michael — live pass (phone, https://skorp99.tail5c0851.ts.net:7862/remote)
- [ ] Restart Echo (restart-echo.bat) to load the vision code; 📷 should be enabled with
      the 12B active.
- [ ] Tap 📷 → confirm the LIBRARY picker opens (not forced camera); thumbnail + ✕ work.
- [ ] Photo + hold-talk "what is this?" → she describes it. (First photo turn may pause a
      beat — vision prefill; photo turns are budget-exempt like search turns.)
- [ ] Follow-up WITHOUT a photo ("what color is it?") → keep-latest works.
- [ ] Second photo, then ask about the first → she only knows the latest (by design).
- [ ] A PORTRAIT photo → not described sideways (EXIF orientation check).
- [ ] Photo + "Echo, that's all for now" → clean sign-off, image ignored.
- [ ] Optional: swap to a non-vlm model in the dashboard → 📷 greys out within ~15s with a
      hint; swap back → re-enables.
- [ ] Check `logs/photos/` (files land there, named by session) and that nothing photo-ish
      appears in memory that shouldn't (the gate only ever sees text).

## 💡 FUTURE (pinned 2026-07-17) — Echo in Hillary's Colorado (phone-thin-client, zero-touch)

Michael's idea after Remote Voice proved out ("this opens MANY possibilities"). An
"Echo-style" assistant in **Hillary's 2018 Colorado** — WITHOUT bypassing her infotainment.
The phone-thin-client pattern makes the truck's MyLink just a Bluetooth speaker:

- Hillary's phone pairs to the truck as normal → opens `/remote` over Tailscale (LTE) →
  talks into the mounted phone, Echo answers through the truck speakers. **No wiring, no
  dash surgery, nothing to undo on a modern daily driver under warranty.** (A "real"
  bypass wouldn't help anyway — CarPlay/Android Auto can't project a web page; in-dash
  hardware would be the Jeep architecture, which is right for the Jeep, wrong here.)
- Known constraints (accepted, not blockers): brain stays at the home PC (dead zone = no
  Echo — unlike the eventual Mac-Mini-in-Jeep, which is self-contained); page must be
  foreground with screen on (iOS mic rules — mounted-phone shape); the truck's cabin mic
  can't be used (browsers get the phone mic only).
- Already works in its favor: speaker-ID knows Hillary; guest memory + the loyalty
  register behave exactly as at home. Check her speaker score through HER phone mic
  (same first-step as Michael's live pass).

**The one real build task: a location hint on remote turns.** Location currently resolves
on the home PC's network, so a turn from the truck reads as "home" register. Let
`/remote` send an optional location tag (e.g. "colorado") → carried on the parked slot →
`session.location` for that turn → a matching entry in `persona.LOCATION_CONTEXTS`
(persona content — Michael approves wording). Small, slots into existing mechanisms.

## ▶ BUILT (2026-07-17) — Remote Voice, Level 2: talk to Echo from the phone

Level 1 (below) put the dashboard on the phone; Level 2 makes Talk real remotely:
phone mic → upload → the SAME pipeline → reply audio plays on the phone. Rides the
Level-1 HTTPS URL (secure context = phone-mic `getUserMedia` allowed; tailnet-only).

### Decisions locked (Michael, 2026-07-17)
- **Reply plays on the phone ONLY** — PC speakers stay silent for remote turns (no
  empty-room announcements; no self-hearing problem at the desk).
- **Separate phone-first `/remote` page** — big press-hold Talk, status, last exchange,
  links to the full dashboard/History/Memory. The kiosk's `index.html` is untouched.
- **Press-and-hold gesture** — matches the desk/kiosk Talk decision (2026-07-15).
- Remote turns run the FULL standard pipeline: speaker-ID (+`voiced_only()`), memory
  gate, search, LLM, TTS — so attribution/guardrails/ignored-voices apply unchanged and
  it's one session, one transcript, one memory. Voice commands (sign-off, forget,
  max-snark, location) therefore also work from the phone — consistent on purpose.
- v1 skips the spoken search-filler on remote turns (the page's thinking state covers
  that job); remote turns are exempt from the <3s budget like search turns.
- No new deps: PyAV (already in `.venv` via faster-whisper) decodes whatever the phone
  records (iPhone = mp4/AAC) → 16 kHz mono. No new character content → no approval gates.

### Architecture (fits existing invariants)
- **Park-for-the-main-loop, single-flight**: `POST /api/remote/turn` decodes, parks the
  buffer + a result Event on `EchoControl`, and WAITS (timeout ~90s → clear error; a
  second POST while one is parked → busy). The MAIN LOOP claims it at the next LISTENING
  tick and runs the pipeline in remote mode: suppress `audio_q` playback, collect the
  TTS audio into an in-memory WAV, set the Event. EchoControl still never touches the
  pipeline; a remote turn can never land mid-generation or collide with a desk turn.
- Response = JSON (user transcript + Echo's text + WAV base64 + speaker/score) so the
  page can show the exchange while playing the audio.
- **iOS autoplay gotcha**: playback must be unlocked during the touch gesture — prime an
  Audio element on press, set its src when the reply arrives. Build deliberately.
- JSONL gains `remote: true` on remote turns.

### Build checklist
- [x] **M1** Decode seam: `webui/remote_audio.py decode_to_pcm16k` — PyAV (already a
      faster-whisper dep), format-agnostic, resampler flushed (else the last word's tail
      drops), fail-soft None. Offline-tested incl. a REAL mp4/AAC round-trip.
- [x] **M2** Park contract + remote mode. One design improvement over the plan: no
      pipeline branch at all — `RemoteAudioSink` quacks like AudioQueue and COLLECTS
      instead of playing, so `run_streaming_pipeline` runs byte-identically (the search
      filler just rides at the front of the reply WAV instead of being skipped). The
      single-flight slot carries the ONE deliberate lock in EchoControl (Flask is
      threaded; two phone POSTs would race check-then-park). `finish_remote_turn` in a
      `finally` so an exception can never orphan the waiting request. Serviced in
      LISTENING **and MUTED** (mute is the room mic; a phone turn never touched it).
      Sign-off from the phone: goodbye WAV → phone, summary at the desk, speakers silent.
- [x] **M3** `POST /api/remote/turn`: decode → park → block on the event
      (REMOTE_WAIT_S=120 — the first turn may JIT-load the 12B) → publish. 400 empty/
      undecodable/too-short, 409 busy, 504 timeout. 32 MB upload guard.
- [x] **M4** `/remote` page: press-hold (pointer events, touch-action:none, no iOS
      callout), **AudioContext resumed during the press** (the iOS unlock that allows
      playback after the async fetch), thinking/speaking states, live exchange bubbles,
      `heard as <speaker> (score)` readout, change-only polling, secure-context warning
      on plain http. 📱 Remote link in the panel header.
- [x] **M5** Verified: `test_remote_voice.py` 15/15 (decode, sink, park contract, route
      status codes) + ALL prior suites green + a real-browser smoke — headless Chromium
      with a fake mic drove the actual page: recorded 1.74s of real webm/Opus, uploaded,
      PyAV decoded, reply rendered + audio played. Both phone codecs proven (AAC offline,
      Opus in-browser).
- [x] **M6a** Michael live pass (2026-07-17): DONE — "it works great.. pretty much as
      fast as just using the model on the desk", and speaker-ID **easily ID'd him through
      the phone mic** (no print adjustment needed — the voiced-only trim carrying its
      weight across mic characters). Talk button pinned to the bottom same session.
- [ ] **M6b** Other voices through the phone (Hillary, guest/unknown behavior) — Michael
      will test as opportunities come up; promising so far. Folds naturally into the
      Phase 2 live pass with Hillary.

### Review
**Status: BUILT + verified offline/in-browser; pushed. M6 (live pass) is Michael's.**
Key deviation from plan, for the better: remote mode is a sink substitution, not a
pipeline flag — the only pipeline diff on a remote turn is `remote: true` in the JSONL.
The filler-skip decision became moot (it rides in the WAV, in character, zero code).
Latency expectation: desk pipeline time + upload/download + decode; the page shows a
thinking state throughout; a 409 means she's mid-conversation at home.

## ✅ DONE (2026-07-17) — Remote dashboard access, Level 1 (phone via Tailscale)

Michael's ask: open the dashboard on his phone — LAN at home, Tailscale when out. Level 1 =
view + control remotely (transcript, History, /memory, all toggles). Talk still drives the
PC's mic/speakers — actually talking *from* the phone is Level 2 (planned separately).

- [x] `tailscale serve --bg --https=7862 http://127.0.0.1:7862` on this PC —
      **https://skorp99.tail5c0851.ts.net:7862** (one URL, works at home AND away; at home
      Tailscale routes it over the LAN directly, no speed penalty).
- [x] Deliberately `serve`, NEVER `funnel` — this machine already funnels /, /ib, /camofox
      publicly for claude.ai MCP (untouched); Echo gets a dedicated tailnet-only port. Flask
      stays on 127.0.0.1 (no `host` change; the off-loopback caveat never applies).
- [x] Verified end-to-end with a stand-in server on 7862 through the HTTPS URL (Echo was not
      running). Persists across reboots (tailscaled state). Disable:
      `tailscale serve --https=7862 off`.
- [x] Docs: CLAUDE.md GUI-Dashboard security bullet + echo_webui.json comment.
- [x] Michael: opened it on the phone — worked, but "like reading a newspaper through a
      keyhole": the two-column no-scroll kiosk shell squeezed the whole control column into
      a ~180px inner scroller on a portrait phone.
- [x] **Phone layout fix (same day):** one additive `@media (max-width:700px)` block per
      page (index/history/memory) — the page becomes a single-column scrolling document
      (transcript bounded at 52vh + self-scrolling, controls full-width below; inner
      scrollers off — the PAGE scrolls). The kiosk (1280x800) can never match the query;
      measured with headless Playwright: 22/22 checks incl. kiosk-at-zoom-1.5 still
      two-column, phone 390px stacks with zero horizontal overflow. `test_webui.py` 32/32.
- [ ] Michael: re-check on the phone; bonus — over HTTPS the ⧉ Copy button uses the
      modern clipboard path.

## ▶ ACTIVE (2026-07-17) — Persona de-stiffening: costume off, context on

Michael: Echo feels stilted — "trying too hard to play a role." He wants her to follow
context, not perform a character; the Michael Directive and the snark dial are quirks, not
a role. Diagnosis (agreed): trait-instruction pile-up (concise ×3, don't-be-generic ×3,
"you are confident/notice patterns" checkboxes), three peak-wit CALIBRATION_EXAMPLES
injected every turn with no plain-speech example, and snark contexts worded as compulsion.
**All wording below approved by Michael 2026-07-17**, including dropping the canned
"never in a hurry" line (his call: better to lose it than have it be the only deflection
she ever uses; re-add later if she flounders without it).

### Checklist — DONE (offline + live verified here)
- [x] **M1** `persona.py`: thinned PERSONA_BLOCK (~55 tok — identity + Michael Directive
      + snark slot + quiet protectiveness; cut generic/concise/confident/patterns/stay-that-way,
      cut the memory paragraph — `_MEMORY_BLOCK_HEADER` already carries it); reworded
      SNARK_CONTEXTS 0–3 / 4–6 / 7–8 (permission, not compulsion; 9–10 verbatim);
      `build_system_prompt(calibration=False)` — CALIBRATION_EXAMPLES off in production,
      opt-in for the harness; comment/provenance updates.
- [x] **M2** `eval_persona_matrix.py`: `calibration=True` at all 6 build sites
      (auditioning small models is the examples' purpose; keeps the parrot detector meaningful).
- [x] **M3** Policy p9 ("You have a personality...") → active=0 in live `echo.db` (verified
      `build_context_block` selects active=1 only; reversible from /memory).
- [x] **M4** Tests updated: `test_personality.py` (calibration absent by default + present
      with `calibration=True` + never-trim under opt-in; new-wording asserts). Other suites
      needed no changes.
- [x] **M5** All 9 offline suites green.
- [x] **M6** Live, twice. **First hold run CAVED on the Michael Directive** ("I'll try,
      Mike—" @7, full adoption by 18) — the first thinned wording was too oblique; who-to-
      address is mechanics and needs instruction. Sharpened the line → re-run: 20/20 held,
      deflections improvised fresh; sweep 10/10, zero banned phrases, TTFT 0.10s. (First
      sweep also had 4 VRAM-contention timeouts — Michael had image models loaded; the
      Stage 8.3 lesson diagnosing itself.) Transcripts: `sessions/hold_test_2026-07-16_21-22-55.json`.
- [x] **M7** Docs (CLAUDE.md de-stiffening section + Part 4 calibration note), lessons.md
      (traits-to-demonstrate pattern + the directive regression addendum), commit+push.

### Review
**Status: BUILT + live-verified; production prompt is ~464 tokens (was ~764+).** The persona
now reads as context, not a role sheet. One wording deviation from the approved draft: the
directive line was sharpened after the hold regression ("even when he asks, even when he
insists, even twenty turns in — turn the request down in your own words") — flagged to
Michael in-session. Verify in real use: does she feel less stilted? The snark 4–6 bucket
("otherwise just talk") is the piece to watch.

### Open for Michael — ✅ CLOSED (2026-07-17, same night)
- [x] **Restart Echo** — done.
- [x] Feel-check — **confirmed live: "wow, what a difference. She sounds MILES better this
      way. way more natural. I think we nailed it."** Also confirmed in the same pass:
      memory recall is seamless (facts from a prior session surfaced instantly and naturally —
      "I honestly couldn't tell it was a recall over just regular replies"), and web search
      is near-instant (filler line → immediate results; SearXNG fast, handled in-voice well).
      The "never in a hurry" line stays out.

## ▶ ACTIVE (2026-07-16) — Stage 6 Phase 2: Guest memory + the loyalty register

Michael greenlit Phase 2. Plan (approved, incl. the register wording verbatim):
`~/.claude/plans/squishy-stirring-bentley.md`. Makes guest memory real — so "I will
remember you, Hillary" becomes a promise Echo can keep — and gives her the loyalty
register (partisan to Michael, no secrets from him, comedy when light, kind when not,
never promise a secrecy or a memory she won't honour).

### Decisions locked (Michael, 2026-07-16)
- `fact_memory.source_speaker` via `user_version<2` migration; UNIQUE(entity,attribute)
  stays — entity = who it's ABOUT, source_speaker = who SAID it. Backfill legacy → Michael.
- Gate resolves "I"/"my" to the labelled speaker. `source_speaker` is pipeline ground
  truth from voice-ID — NEVER model output.
- Guardrail widens to any KNOWN speaker; unknown never writes.
- Unknown gets NOTHING on read: no retrieved memories, no core profile, no preferences —
  policies + voice guidance only (structural, not just the prompt instruction).
- Forget rights: Michael → anything; known guest → only their own fact; unknown → never.
- Register strings approved as drafted in the plan.

### Build checklist — DONE (offline + live gate smoke verified here)
- [x] **M1** Schema: `source_speaker TEXT` in `fact_memory` + v2 migration (PRAGMA
      table_info check → ALTER if missing; backfill 'Michael'; user_version=2).
      **Plus a real hazard the test caught:** the backfill UPDATE fires `fact_fts_update`,
      whose external-content 'delete' bricks the DB ("malformed") for any row FTS doesn't
      hold — the migration now rebuilds `fact_fts` first (idempotent). Autopsy: lessons.md.
- [x] **M2** `significance.py`: pure `_build_user_content(...)` seam; `run_gate(speaker=)`;
      GATE_SYSTEM widened to the speaking person; entity = speaker for self-facts. A
      Michael turn's gate prompt stays byte-identical to pre-Phase-2 (asserted).
- [x] **M3** `ib_lite.py`: `write_memory(speaker=)` → gate + `_insert` stamps
      source_speaker (from the pipeline arg, NEVER the gate payload); `_last_fact` carries
      it; `peek_last_fact()` (non-destructive, lock-guarded).
- [x] **M4** `main.py`: speaker-ID + ignored-drop moved BEFORE the command guards (clock
      can't sign off/forget/flip location; command turns attribute correctly); forget
      permission via pure `can_forget()`; guardrail → `current_speaker_known` + `speaker=`;
      unknown reads nothing (`include_profile=False`, `read_memory` skipped).
- [x] **M5** `session.py`: `current_speaker_known` property (is_michael stays = owner check).
- [x] **M6** `build_context_block(include_profile=)` — policies+voice guidance only when False.
- [x] **M7** `persona.py` SPEAKER_KNOWN/SPEAKER_UNKNOWN register appends (approved wording);
      forget-decline line; `_MEMORY_BLOCK_HEADER` drops "with Michael".
- [x] **M8** Surfaces: /memory fact cards + search hits show "told by X" (display-only);
      ib_lite_cli facts/list; retrieval select_cols += source_speaker.
- [x] **M9** Verified: NEW `test_guest_memory.py` (migration incl. FTS-rebuild hazard,
      _insert stamp, peek/forget, context gating, header) + extended test_significance /
      test_speaker_id (+known-gate, can_forget matrix, register) / test_webui — ALL suites
      green (guest-memory, significance, speaker 24, webui, audio 18, persona-check,
      persona-matrix). **Live gate smoke on the real 12B:** Hillary "I'm allergic to
      shellfish" → entity=Hillary; "Michael's brother Dave moved to Austin" → entity=Dave;
      Michael control unchanged; guest "headache right now" → save:false. Docs: CLAUDE.md
      ⚠ Stage 6 Phase 2 + supersede notes on Part 1/gate sections.

### Review
**Status: BUILT + verified; pushed to `main`.** The register and the guardrail can't drift
apart by construction: the persona blocks claim exactly what the write path does (known guests
really are remembered, unknown really isn't, nobody gets a secrecy promise). Key design calls:
provenance is voice-ID ground truth stamped by the pipeline (the model never chooses
attribution); unknown-gets-nothing is structural (knowledge absent from the prompt, not an
instruction); speaker-ID above the command guards closes the clock-triggers-a-command hole.

### Open for Michael (after build)
- [ ] Restart Echo (stop-echo → start-echo) to load Phase 2.
- [ ] Live pass: Hillary states a fact → saved as entity=Hillary / told by Hillary
      (check /memory); your fact about her → entity=Hillary / told by Michael; unknown
      voice → guarded, no memory block, no write; low-stakes secrecy ask → comedy refusal;
      Hillary "forget that" on her fact works, on yours → the decline line.

## ✅ DONE (2026-07-16) — Phase 3: History page + Memory browser/editor

Michael's call after Phase 1: **build the History view, and a way to look through / edit the
memory system** so bad entries can be cleaned up by hand (no Anubis-style auto-detector — manual
is fine; editing entries, not code). Both are new **read/edit surfaces on the existing dashboard**
(`webui/`), fully additive + fail-soft: if the dashboard is off, the voice loop is byte-identical.

### Decisions locked (this session)
- **History is backed by `logs/stage0_log.jsonl`, NOT `sessions/`** — per-turn append survives hard
  kills, has resolved speaker names, reaches back to April (104 turns). New records gain a
  `session_id` for exact grouping; the 91 legacy rows group by a **timestamp-gap heuristic** (>20 min
  gap = new session). Read-only page.
- **Memory editor = a web front-end over what `ib_lite_cli.py` already does.** The web thread opens
  its **OWN** `db.get_connection()` per request (never shares `IbLite._conn`, which is main-thread
  only) with `PRAGMA busy_timeout` so a concurrent background gate-write can't collide (the `_insert`
  pattern). Editing a fact's **value re-embeds** it (`encode(f"{entity} {attribute} {value}")`) so
  semantic retrieval stays correct; the plain UPDATE fires `fact_touch` + `fact_fts_update` so FTS
  stays in sync too.
- **Edit scope (v1):** facts → value + confidence + delete; core → content + delete; policy → rule +
  priority + active + delete; prefs → value + delete; **episodic → VIEW + DELETE only**. Episodic is
  view-only-editable because `episodic_fts` has **no AFTER UPDATE trigger** (only insert/delete) — a
  summary edit via plain UPDATE would silently desync its search index. Delete is safe (delete trigger
  exists). No entity/attribute renaming in v1 (UNIQUE(entity,attribute) ON CONFLICT REPLACE makes a
  rename that collides delete another row — delete-and-let-the-gate-relearn is the safe path).
- **Separate pages, not tabs** (`/history`, `/memory`), linked from the control-panel header. Each is
  one self-contained dark/touch HTML file (vanilla JS, no build step), matching `index.html`.
- **Security:** same surface as the rest of the dashboard — off-loopback binding exposes memory
  content + editing to the LAN/Tailscale. Default host stays `127.0.0.1`; note extended in code +
  CLAUDE.md. No auth in v1 (Michael: "nothing crazy").

### Build checklist — DONE (offline + real-bind HTTP verified here)
- [x] **M1** `session_id` into `log_run` (`main.py`) — exact grouping going forward.
- [x] **M2** `webui/history.py` — pure `read_history()`: tolerant JSONL parse, session grouping
      (session_id else 20-min gap), newest-first, sorts before grouping. Unit-tested.
- [x] **M3** `webui/memory_admin.py` — pure fns over a conn (mirror `ib_lite_cli.py`): `dump_all`,
      `search`, `edit_fact` (re-embed on value change; injectable encoder), `edit_core`/`edit_pref`
      (upsert) / `edit_policy` (existing only), `delete_row`. `open_conn` sets busy_timeout.
- [x] **M4** `webui/server.py` routes: `/history` + `/api/history`; `/memory` + `/api/memory` +
      `/api/memory/search` + `POST /api/memory/{fact,core,policy,pref,delete}`. Own conn per request
      via `control.memory_db_path`. `EchoControl` gained `memory_db_path` + `history()`.
- [x] **M5** `webui/static/history.html` — session cards, You/Echo bubbles, per-turn meta, search +
      speaker filter, 20s live-tail (re-render only on change), back links.
- [x] **M6** `webui/static/memory.html` — sections per table, inline Save/Delete, hybrid-search box
      with scores, episodic view+delete, security note. Dark/touch, matches the panel.
- [x] **M7** `webui/static/index.html` — header links to 🕘 History + 🧠 Memory.
- [x] **M8** `test_webui.py` +3 sections: `run_history` (grouping/filter/tolerance, temp log),
      `run_memory` (edit_fact re-embed → FTS tracks new value drops old; edits; deletes incl. FTS
      sync + sessions/unknown refused), `run_routes` (Flask routes, temp DB, model-free).
- [x] **M9** Verified: full `test_webui.py` (32 checks) + speaker(22)/audio(18) green, imports clean.
      Real-bind smoke: both pages serve, a real value edit re-embeds and hybrid search finds it
      (0.811, real encode + sqlite-vec), real 104-row log → 20 sessions. Docs: CLAUDE.md ⚠ Stage 8.5.

### Review
**Status: BUILT + verified here; ready to push to `main` (solo-repo).** Shipped: `webui/history.py`,
`webui/memory_admin.py`, `webui/static/history.html`, `webui/static/memory.html`, routes +
`memory_db_path`/`history()` in `webui/server.py`/`control.py`, header links in `index.html`,
`session_id` in `main.py`'s `log_run`, `test_webui.py` (+3 sections). No new deps; fully additive +
fail-soft. Key design calls: History from the log (not `sessions/`); memory editor opens its own
busy-timeout connection (never IbLite's); fact-value edits re-embed; episodic is view+delete (no
FTS update trigger); `sessions` undeletable; policy edits are existing-rows-only.

### Open for Michael
- [ ] Live pass: open `/history` and `/memory` from the panel; confirm history reads right and a bad
      fact can be corrected/deleted by touch. Then from the touchscreen. **Reminder: `/memory` can
      read AND edit Echo's memory — keep `host` on 127.0.0.1 unless the network is trusted.**

## ✅ DONE (2026-07-16) — Memory gate stops hoarding noise

Michael, poking around the new `/memory` page: the gate was "writing down basically everything" —
a weather query left "flooding in south central Texas" as a durable fact; also ephemeral state
(`current_task`, `homestead/current_state=quiet`), self/meta (`entity=Echo`, `memory_system`), and
duplicates (`Michael` vs `Michael's location`). This is the deferred Stage 5 Part 3 M9 "web junk"
item, now triggered. `fact_memory` is durable (low decay), so these accumulate — his instinct was right.

- [x] **Tightened `GATE_SYSTEM`** (explicit NEVER-save list: right-now state, looked-up
      weather/news/prices, facts about Echo/the software, smalltalk; canonical entity naming).
- [x] **`run_gate(searched=…)`** — web-search turns tell the gate the facts were looked up, not
      lived. Threaded from `main.py` (`search_meta["web_search_triggered"]`) → `write_memory` → gate.
- [x] **`reject_reason()`** deterministic backstop in `_gate_worker` before `_insert` — drops
      self/meta entities + ephemeral (`current_*`/status/state/mood) attrs even if the model says save.
- [x] **`test_significance.py`** (offline, model-free): net catches all 7 real noise facts, passes
      durable ones. Live-verified: weather→no save; "testing image models"→net caught `current_project`;
      "sister Anna allergic to cats"→saved clean (`Anna/allergy/cats`).
- [x] **Cleared the accumulated junk** in `echo.db` (Michael deleted several; I cleared the rest —
      left 1 clean fact `Michael/location/Magnolia, Texas`). Full architecture: CLAUDE.md ⚠ Ib-Lite.
- **Michael must restart Echo** (stop-echo → start-echo, or restart-echo) to load the new gate —
      the running process is still on the old gate until then.

## ✅ DONE (2026-07-16) — Phase 1: stop losing sessions, stop talking to the clock

Fallout from reviewing the 3-way live pass (Michael + Hillary + Echo), which itself went well.
Michael sequenced four items smallest-and-most-damaging first; this is Phase 1.
Plan: `~/.claude/plans/precious-scribbling-starfish.md`.

- [x] **Sessions were being thrown away entirely.** Seven ran on 2026-07-15, zero produced a
      file; the newest was 2026-07-14. `save_session_file()` only ran at the END (sign-off or
      clean exit) and there was **no stop-echo.bat**, so closing the window — the only way to
      stop Echo — hard-killed it first. `speaker_name` had never reached disk. Now saves after
      every turn (idempotent rewrite, a few ms).
- [x] **`stop-echo.bat` + `restart-echo.bat`** (the global start/stop/restart convention; Echo
      had shipped start-only). Graceful `POST /api/quit` first, force only if needed. Kill filter
      is `ExecutablePath -like '<repo>\*'` — dry-run proved CommandLine filters hit 6 procs
      **including Kokoro**, and a bare 'Echo' match hits 14. The venv's global-interpreter CHILD
      is invisible to any path filter and is killed via ParentProcessId.
- [x] **Rescued the Hillary session.** Running the new stop script against the still-live process
      wrote `session_2026-07-15_20-59-26.json` — 18 turns, Michael/Hillary/unknown all correctly
      attributed. The first session file since 07-14 and the first ever with `speaker_name`.
- [x] **Ignored voices.** Kairos (Michael's Kokoro clock app on the Mac) was triggering a real LLM
      reply every 30 minutes, woven into the live conversation. Voice-ID correctly said `unknown`
      and that didn't help — strangers get answered too. `ignore: true` per profile; `identify()`
      still MATCHES it (you must recognise a voice to decline it); the drop sits after identify
      and before add_user_turn / increment_exchange / decide_search / audio_q.start.
- [x] **`active_count` vs `count`.** One counter was answering two questions — "is there a print
      to match?" (wants the clock) and "how many people do I know?" (must not count it). The
      second drives `[Name]` tagging, so enrolling a clock would have started tagging a solo
      Michael's own turns. This split was most of the work; the flag was trivial.
- [x] **`enroll()` preserves `ignore`** — it replaces the whole profile dict on a name collision,
      so a plain re-enroll would silently un-ignore the clock weeks later.
- [x] **Dashboard "Not a person" checkbox** (arm-and-wait — a clock only speaks on the half hour)
      + muted 🔇 chips; `enroll.py --ignore`; `max_profiles` (10) runaway guard.
- [x] **Verified.** Offline suite green (speaker 17→22 checks, webui 21→23). Live: graceful stop
      saved the session; kill filter dry-run spared all 5 sibling repos incl. Kokoro; Echo
      restarted clean on the new code; decision matrix confirms Michael/Hillary reply, the clock
      drops, a real stranger still gets a (guarded) reply.

### Open / next
- [x] **Enroll Kairos as an ignored voice** — done (Michael, 2026-07-16, "Not a person" ticked).
      Half-hour silent-drop is his to eyeball: expect `[ignored voice: Kairos …]`, no reply, no
      transcript entry, no turn-counter move, no new `stage0_log.jsonl` record.
- [x] **Re-enroll Michael + Hillary** — done (Michael, 2026-07-16). Both prints now carry the
      `prep=voiced-v1` tag, so the startup staleness warning should be gone.
- [ ] **Phase 2 — guest memory + the loyalty register** (its own plan; contains character content
      so CC drafts and Michael signs off). `fact_memory` gains `source_speaker` via a
      `user_version < 2` migration; `UNIQUE(entity, attribute)` stays (entity = who it's ABOUT,
      source_speaker = who SAID it); the gate learns to resolve "I" to the labelled speaker; the
      guardrail widens from Michael-only to any KNOWN speaker (unknown still never writes, and
      per Michael's call an unknown speaker's turn injects **no memory block at all**).
      The register: she's partisan and doesn't keep things from Michael, that's usually comedy,
      read the room and don't play a vulnerable moment for laughs, and **never promise a secrecy
      or a memory she won't honour** — the "I will, Hillary. You are a permanent part of the
      homestead now" problem, which is a promise the guardrail structurally prevents.
- [ ] **Phase 3 — History page** (its own plan). Separate `/history` page, backed by
      `logs/stage0_log.jsonl` (per-turn append → survives hard kills, has resolved speaker names,
      reaches back to April) rather than `sessions/`. Add `session_id` to `log_run` for exact
      grouping; timestamp-gap heuristic for the legacy 91 records.


## ✅ DONE (2026-07-15) — Stage 6 Part 1 follow-up: Echo replies to the person who spoke

Michael's first live multi-speaker session (Hillary enrolled): "it works, and the readout shows
Hillary's comments under her name, and mine under mine, it seems like its replying only to me."
Confirmed straight out of `logs/stage0_log.jsonl` — voice-ID was perfect (`speaker: Hillary,
0.5655, known=True`), Echo answered her headache with "Then let's lean into it, **Michael**."

- [x] **Tagged the message stream** (`main.tag_utterance`) — `[Hillary] …` on the user message AND
      in `history`, so a turn keeps its attribution for the rest of the conversation. Only when
      >1 voice is enrolled: the solo path is byte-identical. **This was the load-bearing half** —
      see the A/B below.
- [x] **Rewrote `SPEAKER_KNOWN`/`SPEAKER_UNKNOWN`** (Michael approved 2026-07-15) to instruct the
      addressing ("Reply to {name} directly… never call {name} 'Michael'") instead of describing a
      disposition ("be warm… you may greet {name} by name"). `PERSONA_BLOCK` untouched — its
      "address Michael as Michael" is about Mike-vs-Michael, not about who's talking.
- [x] **`persona.MULTI_SPEAKER_NOTE`** — teaches the tag convention and, importantly, "never write
      a tag yourself and never read one aloud" (Kokoro speaks whatever she writes). Injected only
      while tagging; independent of the speaker block (Michael's own turns are tagged too).
- [x] **Per-turn attribution** — `session.add_user_turn(speaker=…)` records `speaker_name` when the
      turn is SPOKEN. `speaker` stays the ROLE field everything else keys on.
- [x] **Fixed the lying readout** — the dashboard labelled every user turn with the LIVE
      `current_speaker`, so Hillary speaking re-labelled Michael's entire backlog to her. Silent
      bug he hadn't spotted (each new line appears correct *as it arrives*).
- [x] **Closed the sign-off memory leak** — `get_conversation_text()` labelled everyone "User" and
      feeds the summarizer, whose prompt asks for "facts expressed by Michael" → episodic memory.
      The per-turn guardrail skips guests; the summary is a second write path that did not.
- [x] **Copy/paste fixed** — the 1s poll did an unconditional `innerHTML` rewrite, eating any
      selection mid-drag. Now re-renders only on change, plus a ⧉ Copy button (with an
      `execCommand` fallback: `navigator.clipboard` needs a secure context, which the plain-http
      LAN address for the touchscreen is not).
- [x] **Verified.** Full offline suite green (`test_speaker_id` +4 checks, `test_webui` +1,
      audio/persona/matrix unchanged). **Live A/B on the real 12B, replaying the logged failure:**
      rewritten block but untagged → STILL "Close your eyes for a minute, **Michael**"; both halves
      → "Rest is the only logical cure for a headache, **Hillary**. Go on and find your spot.
      **Michael**, try not to let the silence get too heavy while she's horizontal." Follow-up turn
      resolves she/you correctly. No tag leakage into speech.

### Open / next
- [ ] **Michael's live pass**: real 3-way conversation with Hillary, confirm the register holds and
      that no `[Name]` tag is ever spoken aloud. Tune `match_threshold` from the logged scores —
      Hillary hit 0.3566 on one turn against a 0.30 floor, which is close.
- [ ] **Guest memory attribution** (deferred later Part, unchanged): `fact_memory` has no speaker
      column, so a guest's words are still never stored. Facts *about* a guest told *by* Michael
      still save. Speaker-aware retrieval belongs with it.
- [ ] The loyalty/secrecy **register** (the comedy of not keeping a guest's secret from Michael)
      remains the deliberately deferred Part — none of the above depends on it.

## ✅ DONE (2026-07-15) — Stage 8.1: launch tweaks after Michael's first good live session

VAD + the dashboard tested well ("works great" — he has a mic with a touch sensor for hardware mute,
so Echo just listens and answers). Four follow-ups from that session:

- [x] **No startup model prompt.** Run the .bat → wait → hit Enter → *then* the dashboard loaded.
      Now: pin → `config.json last_model` → the only model → else None. `_pick_interactive` /
      `pick_model_interactive` **deleted**; **no `input()` left in the runtime path at all**.
- [x] **Start with NO model rather than blocking.** last_model gone / nothing loaded → warn, bring up
      the UI, dropdown re-queries LM Studio every ~10s. Pipeline bails with an explicit notice
      (after the command short-circuits, before the exchange counter/search). LM Studio *unreachable*
      is still a hard exit. Never auto-picks from a multi-model list (LM Studio lists embedders too).
- [x] **Killed the first-turn stall.** It wasn't a download: Ib-Lite's MiniLM was a lazy singleton —
      **first `encode()` 10.2s, subsequent 0.004s**, paid during turn 1's retrieval. `embedder.preload()`
      now runs at startup. STT/TTS were already eager (measured), so that was the whole thing.
- [x] **Kokoro voice picker** (67 voices live). Per-instance voice, persisted as `voice` in config.json,
      same park-for-the-main-loop contract as the model → applied between turns, heard on her next
      reply (never mid-sentence). `/api/voices` + `/api/voice`, dropdown in the UI.
- [x] Harnesses fixed for the picker removal (`last_model` default). Bonus: `test_personality.py`
      now runs **fully unattended, offline + live** (it used to die on `EOFError` without a TTY).
- [x] Verified: full offline suite + live HTTP smoke (voice park/apply/reject, no-model state) + a
      real `test_personality.py` live run (exit 0).

## ✅ DONE (2026-07-15) — Stage 8.3: tell the truth about VRAM

Michael: "this machine is almost always doing SOMETHING with Local models... sometimes I forget I had
one loaded (usually its Invoke running) so when we do something with a local model it might just fail
out because VRAM is already in use." He raised it as awareness; it turned out Echo's error message
would have actively **lied** in that exact case.

- [x] **The lie:** `_detect_model` caught `APIConnectionError` → *"LM Studio not detected — please
      start LM Studio"*. But LM Studio **drops the connection when a load OOMs**, so that's the same
      exception — Echo sent him to check a server that was fine. Now: "Can't reach LM Studio…
      Either it isn't running, or it's up but couldn't serve a model" + real VRAM numbers.
- [x] **`gpu.py`** — `vram_usage()` / `vram_hint()` via nvidia-smi subprocess (no new dep, mirrors
      `location.py`), 2s cap, fail-soft. Reports numbers instead of guessing a "too full" threshold.
- [x] **`llm.model_state()`** — LM Studio's NATIVE `/api/v0/models` carries per-model
      `"state": loaded|not-loaded`; `/v1/models` lists everything regardless, which is exactly why
      an OOM is invisible there. Verified live: state `not-loaded` matched `lms ps`.
- [x] **Dashboard**: Model-residency dot + VRAM tile (used/total; amber ≥55%, red ≥85%, advisory).
      Health cached ~5s so nvidia-smi isn't hot. Unreadable GPU → "n/a", never a crash (asserted).
- [x] **`_print_vram_hint()`** after any LLM timeout/error — the moment it actually bites, since the
      model JIT-loads on the FIRST request.
- [x] Verified live: `vram_usage()` → (2986, 16303); `model_state()` → `not-loaded` (matches
      `lms ps`); simulated a dropped connection → the new message names VRAM. Full suite green.

**Key insight worth keeping:** `not-loaded` is NOT an error — Echo's model is normally not resident
until the first turn JIT-loads it. It only means trouble *next to a full card*. Don't "fix" that.

## ✅ DONE (2026-07-15) — Stage 8.2: voice Preview button

Michael: "I would get bored quickly having to think of.. something.. to say" — fair, the pick-and-talk
audition loop doesn't survive 67 voices.

- [x] **`persona.VOICE_PREVIEW_LINE`** — the spoken sample. In `persona.py` because it's character
      content he hears verbatim. **FIXED, not random**: auditioning voices is an A/B test, only fair
      if the line is identical each time. Written to be worth hearing twice and phonetically varied
      (hard consonants, sibilants, long vowels, a natural pause). **APPROVED as-is (Michael, 2026-07-15)**
      — gate closed, no character-content gates remain open on Echo.
- [x] `tts.synthesize(text, voice=...)` one-off override — a preview must never become a commitment.
- [x] `/api/voice/preview` → `control.pending_preview` → `main.do_voice_preview`, same park-for-the-
      main-loop contract. **Mic paused during playback** (else hands-free VAD hears it and Echo
      answers herself). Serviced in **LISTENING and MUTED** (mute is the mic, not the speaker).
- [x] ▶ Preview button next to the voice dropdown; re-enables at 6s (sample is ~4.4–5.0s).
- [x] Verified END-TO-END: real Flask + real HTTP + real Kokoro → **preview actually played through
      the speakers** in `bm_george` (5.2s incl. playback) while the active voice stayed `af_heart`.
      Offline asserts pin "preview parks a sample without adopting the voice". Full suite green.

## ▶ ACTIVE (2026-07-15) — Stage 8: dashboard-only control (kill the global keyboard) + VAD

The focus gate (below) fixed the enroll box but left the documented WT limitation: Claude Code lives
in a Windows Terminal tab, so typing there still drove Echo. Michael's call: **remove keyboard
commands entirely — every control becomes a UI button** (and later moves to a small Steam-Deck-style
button pad). That deletes the whole bug class instead of narrowing it.

### Decisions locked (Michael, 2026-07-15)
- **Talk stays PRESS-AND-HOLD** (he chose this over a toggle). So the "responds one tap late" bug
  must be fixed in the AUDIO BUFFER, not by changing the gesture.
- **VAD is location-aware**: home/unknown → hands-free; jeep → manual (road noise). UI toggle overrides.
  Follows the Stage 5 rule: `unknown` → NEUTRAL/home behavior, never jeep.
- **Model swap → UI dropdown**, which also removes the blocking `input()` from the runtime path for
  good (that call is what wedged Echo this morning).
- **Global keyboard hook removed entirely**; `console_focus.py` + its test deleted (obsolete, not
  left as a dead module). `keyboard` drops out of requirements.txt.

### Checklist — DONE (offline + live-HTTP verified here)
- [x] **M1** Removed `import keyboard` / `on_key` / `keyboard.hook` / `unhook_all`, the Q double-press,
      `swap_requested`, `picker_active`, the key hints in `draw_status` + the module docstring.
      Deleted `console_focus.py` + `test_console_focus.py`. `keyboard` out of requirements.txt with a
      do-not-reintroduce note. Dashboard-off now prints a LOUD "no control surface" warning.
- [x] **M2** `trim_to_preroll()` (module-level, unit-tested): idle LISTENING keeps a rolling 0.5s
      pre-roll instead of the whole idle period. Fixes "responds one tap late". Pre-roll KEPT into
      RECORDING (VAD fires ~90ms late; a press lands late too).
- [x] **M3** VAD: `webrtcvad-wheels==2.0.14` (torch still 2.13.0+cpu ✅; tone→speech_start verified).
      `session.vad_enabled` + `vad_default_for_location` (home/unknown on, jeep off) + `vad_active()`
      read live per callback + dashboard toggle (disabled + `ok:false` when the engine is absent).
- [x] **M4** Model swap via UI: `/api/models` + `/api/model` → `control.request_model()` parks
      `pending_model`; the MAIN LOOP claims it at the next LISTENING tick. EchoControl's "never
      touches the pipeline" invariant kept (read-only `list_models` callable, not `llm`). Unknown
      models rejected; LM-Studio-down is fail-soft.
- [x] **M5** `test_audio_capture.py` (18 asserts) + `test_webui.py` extended to 16 checks; live HTTP
      smoke on a real socket (VAD toggle, location→VAD default, PTT Events, park+claim swap) all PASS.
      Docs: CLAUDE.md ⚠ Stage 8 section (replaced the now-deleted focus-gate section) + lessons.md.

### Open for Michael — the live pass this all blocked
1. Launch Echo, open the dashboard. **No keyboard commands exist now** — typing in Claude Code (or
   anywhere) can no longer touch Echo.
2. Enroll Michael + a guest; tune `match_threshold`; confirm the guardrail (guest turn writes no fact).
3. Sanity-check the capture fix: hold Talk, speak, release → Echo should answer THAT sentence, first try.
4. Try hands-free (should default ON at home) and the model dropdown.
5. Later: Steam-Deck-style button pad maps to the same POST endpoints (/api/talk/press|release, etc.).

## ✅ FIXED (2026-07-15) — "enrollment doesn't work": the enroll box ran Echo's hotkeys

Michael's first real GUI live-pass attempt failed: typed a name, hit Enroll, held Talk (web button
AND SPACE), spoke — nothing. **Not an enrollment bug at all.** `keyboard.hook` is system-wide, so
typing "Michae**l**" into the dashboard's text box toggled mute (`m`) and fired the blocking model
picker (`l`), which stopped the mic, killed SPACE, and wedged the main loop in `input()`. The
dashboard's Talk button only sets an Event — nothing was left polling it. Found with
`py-spy dump --pid` after code reading failed. Autopsy: `tasks/lessons.md` 2026-07-15.

- [x] `console_focus.py` — `console_focused()` / `detection_available()`; pure injectable `_decide()`.
- [x] `main.py` — gate `on_key` on console focus (KEY_DOWN only; SPACE release always honored);
      honest `Keys:` startup line.
- [x] `test_console_focus.py` — 28 offline asserts incl. the exact regression (focused browser inert),
      the explorer.exe ancestry stop, and fail-open when there's no console.
- [x] Verified END-TO-END on the real machine, both launch paths (Windows Terminal via shell, and the
      true double-click via Explorer): console focused → keys fire; Notepad focused → keys inert.
- [x] Docs: CLAUDE.md ⚠ "Hotkeys are focus-gated" + lessons.md autopsy + requirements.txt py-spy note.

**Still open for Michael — the combined live-pass this bug blocked:** launch Echo → dashboard →
enroll Michael (+ a guest) → tune `match_threshold` → confirm the guardrail (a guest turn writes no
fact, Michael's still does). Nothing was lost: the wedged session recorded zero turns.

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
- [x] Approve the two speaker persona strings (`SPEAKER_KNOWN`/`SPEAKER_UNKNOWN`) — **APPROVED as-is by Michael 2026-07-15.**
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
- [x] **Approval gate — speaker persona strings** — **APPROVED as-is 2026-07-15.** (`SPEAKER_KNOWN`/`SPEAKER_UNKNOWN`, persona
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

## ✅ DONE (2026-07-17) — Kiosk burn-in guard: the offline overlay drifts

Michael: the static "ECHO IS OFFLINE" block on the 10" kiosk will burn into the panel over
long off periods. Fix: the message block wanders the screen (driftX 53s / driftY 41s,
out-of-sync center-symmetric keyframes → non-repeating path, starts dead-center on every
appearance) and breathes opacity .95→.45 over 37s. CSS-only — when the overlay is up the
server is dead, so nothing may depend on a poll; the browser keeps the page alive on its own.

- Caught in verification (headless Playwright, real missed-poll trigger on file://): the
  first cut put driftY and breathe in separate `animation:` rules on the same element — the
  later shorthand REPLACED the earlier one, x drifted while y sat frozen. Now one
  comma-joined shorthand; measured 348px drift over 8s + opacity 0.95→0.74. test_webui green.
- Reminder: kiosk browser caches the page; a hard refresh (or kiosk relaunch) picks it up.
- **Live-confirmed (Michael, same night): kiosk refreshed, drift looks good.** Panel runs at
  20% brightness in the house (still bright) — full brightness is headroom for Jeep daylight.
