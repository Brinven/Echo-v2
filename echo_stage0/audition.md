# Auditioning Models — How to Pick & Test Models for Echo

Practical guide to choosing which LLM Echo runs on, and to test-driving several of
them. You have a *lot* of models loaded in LM Studio; this is how you cut through them.

> **The one thing you'll forget:** press **`L`** during a conversation to swap models
> mid-chat without losing the conversation. That's it. The rest is detail.

---

## Quick reference

| You want to… | Do this |
|---|---|
| Pick a model when Echo starts | Just launch — a **filter-picker** appears. Type part of a name (e.g. `qat`), then a number. |
| Reuse the model from last time | At the picker, press **Enter** (your last pick is marked `*`). |
| Launch straight into a specific model | `start-echo.bat --model <name-or-substring>` (e.g. `--model e4b`) |
| Pin a model without typing it each time | `set ECHO_MODEL=<substring>` then launch |
| **Switch models in the middle of a chat** | Press **`L`**, pick the new one. The conversation keeps going. |
| Batch-test a model's personality | `set ECHO_MODEL=<substring>` then `python test_personality.py` |

---

## 1. The startup filter-picker

When Echo starts and more than one model is loaded, you get a picker. With ~70 models
it won't dump the whole list — it tells you to **type a filter** instead:

```
  70 models loaded — type a filter to narrow (e.g. 'qat', '12b', 'e4b', 'heretic').
  [Enter=last(gemma-4-12b-it-qat@q4_k_xl) | number=pick | text=filter | cancel=abort] >
```

- **Type a substring** (`qat`, `12b-it-qat`, `heretic`, `e4b`…) → the list narrows to matches.
- **Type a number** → pick that model.
- **Press Enter** → reuse your last pick (the one marked `*`). Your choice is saved to
  `config.json` (`last_model`), so Enter does the right thing next time.
- Keep typing new filters to re-narrow; it never traps you.

## 2. Pinning a model (skip the picker)

Two ways, both take a full id **or any substring** (a unique match wins; an ambiguous one
just drops you into the picker pre-filtered):

```bat
REM command-line flag (highest priority)
start-echo.bat --model gemma-4-12b-it-qat@q4_k_xl
start-echo.bat --model e4b
start-echo.bat --model heretic

REM or an environment variable (handy for repeat runs / test scripts)
set ECHO_MODEL=12b-it-qat@q8_0
start-echo.bat
```

Priority: `--model` flag → `ECHO_MODEL` env → the picker. If you pin something that
matches nothing, Echo shows the full picker rather than failing.

## 3. ⭐ Swapping models mid-conversation — the `L` key

This is the fast way to audition. **While Echo is listening, press `L`.** The mic pauses,
the same filter-picker appears, you choose a new model, and **the conversation continues
with full history** — you're literally handing the same chat to a different model and can
feel the difference immediately.

```
  ── Swap model (keeps this conversation) ──
  Models matching 'e4b':
    1. gemma-4-e4b-it-qat
    2. gemma-4-e4b-uncensored-hauhaucs-aggressive
  [ ... | number=pick | text=filter | cancel=abort] > 1
  [Now using gemma-4-e4b-it-qat — first reply may pause while LM Studio loads it]
```

- **One-time pause after a swap:** LM Studio loads the new model on the next reply, so the
  first response after switching can take several seconds (a second 12B may evict the first
  from your 16GB of VRAM). Only the first turn — after that it's normal speed.
- Type `cancel` at the picker to back out and keep your current model.
- `L` only acts at the **listening** prompt. Press it while Echo is talking and it'll swap
  as soon as it finishes.

---

## What changes on a swap (and what doesn't)

| Swaps with the model | Stays the same |
|---|---|
| Echo's **voice** (the response model) | Your **conversation history** — fully preserved |
| The **memory gate** model (what decides what to save) | Echo's **personality** (persona block, snark, Michael Directive) |
| | The **sampler** (`echo_sampler.json` — temp 0.72 etc.) — identical across models, so it's a fair comparison |
| | Everything in **Ib-Lite memory** (Core, facts, episodes) |

Keeping the sampler and personality constant is deliberate: when you A/B two models, the
*only* thing changing is the model, so what you're hearing is genuinely the model's character
hold, not a different temperature.

---

## Suggested audition workflows

**Feel test (best for "which model is Echo?"):** Start a session, talk for a few turns to
get a feel, then press **`L`** and hand the same conversation to the next candidate. Ask it
the same kinds of things — an opinion, something personal, push it with "call me Mike,"
throw it a dry-humor opening. Swap again. You're comparing them on identical context.

**Battery test (objective, repeatable):** the personality harnesses honor `ECHO_MODEL`, so
you can run the exact same 10-prompt + 20-turn battery against any model non-interactively:

```bat
set ECHO_MODEL=e4b-it-qat
python test_personality.py        REM 10 prompts: banned-phrase + Mike-deflection + TTFT
python test_hold_20turn.py        REM 20-turn hold; logs the transcript to sessions\

set ECHO_MODEL=12b-it-qat@q4_k_xl
python test_personality.py        REM ...then compare
```

`test_hold_20turn.py` saves each run's full transcript to `sessions\hold_test_*.json`, so you
can read two models' 20-turn runs side by side.

**What to listen for:** does it stay *Echo* (concise, dry, protective) or drift into generic
assistant? Does it hold "Michael" under pressure? Does it stay fast (watch TTFT)? Remember
some of these are *thinking* models — Echo already forces reasoning off, which keeps them fast
and in-character, but a few models hold the persona better than others. That's what you're auditioning for.

---

## Troubleshooting

- **Picker keeps showing the same 70 models:** you didn't type a filter yet — type `qat` (or
  any substring) first, *then* the number.
- **First reply after a swap hangs for ~10s:** expected — that's LM Studio loading the new
  model into VRAM. One time only.
- **Typed keys not registering during the picker:** they are — Echo ignores the mic/hotkeys
  while the picker is open so your typing goes to the prompt, not push-to-talk.
- **It picked a model I didn't expect from `--model`:** your substring matched more than one;
  be more specific (add the quant, e.g. `qat@q8_0`) or use the full id.
