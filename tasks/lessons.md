# Echo — Lessons Learned

## 2026-04-05: Windows `localhost` adds ~2s to every HTTP request

**Problem**: LLM TTFT was 2.3s in the pipeline but 0.2s in standalone tests.
Traced to `client.chat.completions.create()` blocking for 2.05s before returning.

**Root cause**: On Windows, `localhost` resolves to both `::1` (IPv6) and
`127.0.0.1` (IPv4). The `httpx` library (used by the `openai` Python client)
tries IPv6 first, which times out after ~2s when the server only listens on
IPv4, then falls back. Standalone tests were fast because httpx's connection
pool reused the successful IPv4 connection between rapid calls. In the real
pipeline, connections expired between calls.

**Fix**: Use `127.0.0.1` instead of `localhost` in all HTTP URLs.

**Rule**: On Windows, ALWAYS use `127.0.0.1` for local service URLs, never
`localhost`. This applies to LM Studio, Kokoro-FastAPI, and any other local
API server.

**Impact**: First-audio dropped from 5.3s to 0.8s (6.5x improvement).

## 2026-06-24: Gemma 4 12B QAT is a thinking model — gate returned empty output

**Problem**: The Ib-Lite significance gate returned empty `content` for fact/preference
turns (json_parse_failed) while correctly returning `{"save": false}` for smalltalk. The
empty ones also took longer (4.0s vs 3.2s).

**Root cause**: `gemma-4-12b-it-qat@q4_k_xl` in LM Studio is a *thinking* model. It emitted
all its output into `reasoning_content` (397 reasoning tokens), hit `max_tokens=150`
(finish_reason=length), and produced an EMPTY `content` — the intended JSON never made it
out of the reasoning channel. Smalltalk happened to fit a short answer.

**Fix**: Pass `reasoning_effort="none"` on the gate completion. Verified the alternatives
do NOT work for this template: `reasoning_effort="low"` still burned 147 reasoning tokens;
`chat_template_kwargs.enable_thinking=false` likewise; raising `max_tokens=700` just burned
697 reasoning tokens in 16s without finishing. Only `"none"` disabled it → clean JSON in ~1s.

**Rule**: When calling a local model for STRUCTURED output (JSON gates, extractors), assume
it may be a thinking model and explicitly disable reasoning with `reasoning_effort="none"`.
Check `finish_reason` and `reasoning_tokens` when `content` comes back empty. (Cross-project
LM Studio fact — also retained to `axly-infra`.)

**Prevention**: Any future structured-output call against an unknown LM Studio model should
default to `reasoning_effort="none"` and validate the JSON, rather than trusting `content`.

## 2026-07-13: "Not loading" — shared global Python lost the whole voice-pipeline stack

**Problem**: `start-echo.bat` did nothing / exited immediately after ~3 weeks on the shelf.
Launch died at the very first import (`audio.py` → `sounddevice`), which prints an error and
calls `sys.exit()`, killing the whole process before any engine started.

**Root cause**: `start-echo.bat` ran bare `python main.py` against *system* Python 3.11.9 (no
venv). Since the last live run (2026-06-24), that shared global env had been silently clobbered:
`torch` was downgraded to the CPU wheel (`2.11.0+cpu`, `cuda.is_available()=False`) and the
entire audio stack — `sounddevice`, `soundfile`, `faster-whisper`, `ctranslate2`, `webrtcvad` —
was gone. Only the Ib-Lite deps survived. Almost certainly another project's `pip install`
stepping on the shared interpreter. The code and both servers (LM Studio :1234,
Kokoro-FastAPI :8880) were fine the whole time.

**Fix**: Gave Echo a dedicated venv at `echo_stage0/.venv` and pointed `start-echo.bat` at it
(`.venv\Scripts\python.exe`, with a guard that errors clearly if the venv is missing).
Reinstalled the full stack there. Did NOT reinstall CUDA torch: faster-whisper does CUDA via
`ctranslate2` (independent of torch) and the Ib-Lite embedder is CPU-by-design — so the CPU
torch wheel is correct and lighter. Verified faster-whisper loads `float16` on the RTX 5080.

**Rule**: A project with heavy/native deps must NOT rely on the shared global Python — pin it to
its own venv so another project's install can't clobber it. (Cross-project env lesson.)

**Prevention**: `start-echo.bat` now fails loudly if `.venv` is missing (no silent bare-python
fallback), so a wiped env surfaces immediately instead of a cryptic import death.

**Also**: `webrtcvad` has no prebuilt wheel and fails to build on Windows/Py3.11 (needs a C
compiler), which aborted the whole `pip install -r requirements.txt` (pip rolls back the batch).
Made it optional in requirements.txt — PTT (SPACE) is the default input and `vad.py` degrades to
PTT-only gracefully, so it's zero functional loss. For hands-free VAD later: `pip install
webrtcvad-wheels` (drop-in, same `import webrtcvad`).

## 2026-07-15: "Enrollment doesn't work" — typing a NAME into the dashboard ran Echo's hotkeys

**Problem**: Michael typed his name into the Stage 7 dashboard's enroll box, hit Enroll, then held
Talk (both the web button and SPACE) and spoke. Nothing happened — no "hearing me" indication, no
reaction on release. The dashboard kept polling fine and Kokoro's console showed activity.

**Root cause**: `main.py` installs `keyboard.hook(on_key)` — a SYSTEM-WIDE low-level Windows hook.
It fires for every keystroke on the machine regardless of focus. That was harmless while Echo was
CLI-only, but Stage 7 added a **text input** (the enroll name box), and the two designs are
fundamentally incompatible: typing "Michae**l**" ran Echo's hotkeys. The `m` toggled mute (→ MUTED),
and the `l` set `swap_requested`. `swap_requested` is only serviced from the LISTENING branch, so it
sat pending until Michael clicked Mute to undo the mute — at which point LISTENING re-entered, saw
the stale flag, and called `do_model_swap()`, which:
  1. `picker_active.set()` → `on_key` early-returns, so **SPACE was dead**,
  2. `audio_stream.stop()` → **the mic was off**,
  3. blocked on `input()` (llm.py:216) → **the main loop stopped polling**, so the dashboard's
     Talk button was dead too (it only sets `space_pressed`; nothing was reading it).
Diagnosed with `py-spy dump --pid <pid>`: MainThread parked in `_pick_interactive` → `input()`.
The Kokoro "activity" was a red herring — the dashboard's health tile polls `:8880/health` every 5s.

**Fix — attempt 1 (focus gate, SUPERSEDED same day)**: `console_focus.py` gated `on_key` on Echo's
console having focus. It fixed the enroll box, but its documented limitation bit within minutes:
Windows Terminal tabs are indistinguishable from each other, and Claude Code lives in a WT tab — so
typing there still drove Echo. I had called that blast radius "far smaller"; for someone who works
in WT tabs all day it was a *daily* problem, not an edge case. **Lesson: a limitation you can only
describe in prose, rather than test, is a limitation you have not really accepted.**

**Fix — attempt 2 (Stage 8, shipped)**: **remove the global hook entirely.** Every control moved to
the dashboard (Talk/Mute/Max Snark/Hands-free/Home/Jeep/Web/Model/Stop/Enroll/Threshold), `keyboard`
dropped out of requirements.txt with a do-not-reintroduce note, and `console_focus.py` +
`test_console_focus.py` were **deleted** rather than left as a dead module. Ctrl+C still stops it.
The mid-chat model swap became a UI dropdown that PARKS the choice for the main loop, which deletes
the blocking `input()` from the runtime path for good.

**Rule**: A system-wide input hook and an app text box cannot coexist — and scoping the hook is a
patch, not a fix. When a project grows a second input surface (GUI/web/touch), the global hook is
the thing to *delete*, not narrow. One surface, one owner of input.

**Prevention**: `keyboard` is out of requirements.txt with the reason inline. The startup line now
warns LOUDLY if the dashboard fails to start, because it is the only control surface.

**Bonus bug found by the rewrite** — "hit talk, speak, nothing; hit talk again → Echo answers the
FIRST sentence." `audio_vad_callback` appends whenever `sm.can_record`, which is true in **LISTENING
as well as RECORDING**, and the buffer was only cleared on LISTENING *entry*. So a press captured
everything since the last turn. Fixed with `trim_to_preroll`: while idle, keep only a rolling 0.5s
pre-roll (kept, not cleared, entering RECORDING — VAD fires ~90ms late and a press always lands
slightly late). **Rule: "when does capture START?" must have exactly one answer.** A state machine
where the recording buffer fills in a state called LISTENING is a latent bug waiting for a UI that
presses the button differently than you did.

**Windows gotchas found while fixing this (all measured, not assumed):**
- **`GetConsoleWindow()` == `GetForegroundWindow()` only works in classic conhost.** With Windows
  Terminal as the default terminal (this machine), GetConsoleWindow returns a window owned by our
  own `cmd.exe` while the foreground window is `windowsterminal.exe` — the HWNDs never match.
- **Windows Terminal is NOT an ancestor process.** It attaches over ConPTY rather than spawning
  the shell, so walking the process tree does not find it. An ancestry-only fallback = dead keyboard.
- **`WT_SESSION` is NOT a reliable "am I in WT?" signal.** It isn't set when a .bat is double-clicked
  from Explorer: Windows hands the already-running console off to WT, and env vars can't be injected
  into a live process. Verified both launch paths; the fix keys off the foreground *exe name* instead.
- **`IsWindowVisible(GetConsoleWindow())` returned True under WT**, so "hidden window" is not a
  usable ConPTY signal either.
- The ancestry walk must **stop at `explorer.exe`** — otherwise double-clicking the .bat makes the
  desktop (and every folder window) count as "console focused".

**Bug autopsy**: this bug was *born* the moment Stage 7 shipped a text input; nothing about Stage 6
enrollment was broken. The class to watch: **a new UI surface silently invalidating a global
assumption made by an older layer.** Worth asking, on every new surface: what did the old layer
assume about exclusivity that is no longer true?

**Tooling**: for a hung/unresponsive loop, `py-spy dump --pid <pid>` gives every thread's stack of a
LIVE process without restarting or instrumenting it. It found this in one shot after theory failed.

## 2026-07-15: Echo answered a guest as if Michael said it — the prompt lost to the message stream

**Problem**: First live multi-speaker session (Hillary's voice enrolled). Voice-ID worked
perfectly — the logs show `speaker: Hillary, score 0.5655, known=True`. Echo still replied to
Michael:

> **Hillary**: "It will be a bit quieter, but that would be really much better too, because I
> have a headache."
> **Echo**: "Then let's lean into it, **Michael**. Close your eyes for a few minutes and let the
> house settle; the software can wait until your head clears."

She took Hillary's headache, addressed it to Michael, and told *him* to lie down. The next turn
was worse: Hillary said "it sounds like she was thinking it was you" and Echo answered "that's a
fair assumption for **her** to make" — third-personing the person she was talking to.

**Root cause — two halves, and the obvious one was the weaker one.**

1. *The speaker block described a disposition, not an instruction.* `SPEAKER_KNOWN` said "You are
   speaking with Hillary… be warm and natural — you may greet Hillary by name. You are still
   Michael's companion first." It never said **reply to Hillary**. And it was one paragraph
   against five Michael-shaped blocks in the same prompt: the persona block, the calibration
   examples (all literally `Michael: … / Echo: …Michael`), the location block ("You are with
   Michael at home"), the Core slab, and the retrieved memories.
2. *The message stream had no attribution at all.* `history` was `{"role": "user", "content":
   transcript}` — bare text. Hillary's line landed as message #9 in what read to the model as one
   continuous Michael monologue, so it continued the pattern. It also had no way to resolve "she"
   vs "you" a few turns later, because nothing in the history said who said what.

**The A/B that settled it** (replayed the exact logged exchange against the 12B): with the
rewritten speaker block but *untagged* turns, it STILL said "Close your eyes for a minute,
Michael." With tagging as well: "Rest is the only logical cure for a headache, **Hillary**. Go on
and find your spot. **Michael**, try not to let the silence get too heavy while she's horizontal."

**Rule**: **Per-turn facts belong on the turn, not in the system prompt.** A system prompt sets
standing disposition; it cannot win an argument against the shape of the conversation itself. If a
fact changes per turn (who is speaking), it has to ride on that turn in the message stream. The
prompt block is worth having — it carries the *register* — but never ship it as the only carrier
of a per-turn fact and assume it will hold.

**Corollary — write instructions, not vibes.** "Be warm with {name}" is a feeling. "Reply to
{name} directly, never call {name} 'Michael'" is an instruction. Under prompt pressure only the
second one survives.

**Tag format**: `[Hillary] text`, not `Hillary: text` — the calibration examples are shaped
`Michael: … / Echo: …`, so a colon-tagged user message reads as that script and invites the model
to complete with a spoken "Echo:" prefix. Whatever she writes gets read aloud by Kokoro.

**Bug autopsy — the category.** Both halves of this are the same mistake: *assuming a signal is
present because it exists somewhere in context.* The speaker WAS resolved, it WAS in the prompt,
and the dashboard DID display it — but the model's actual input never carried it where it
mattered. Worth asking on any new per-turn signal (the camera will be next): does this reach the
model on the turn it describes, or only as a standing note that something like it might be true?

**Two silent bugs found in the same sweep** — both the same "current value, historical data"
confusion:
- The dashboard labelled *every* user turn with the LIVE `current_speaker`, so the moment Hillary
  spoke, Michael's whole backlog silently re-labelled to "Hillary". Michael never noticed because
  each new line appears under the right name *as it arrives*. Fix: turns record `speaker_name` at
  the time they're spoken; the UI renders that.
- `get_conversation_text()` labelled everyone "User", and it feeds the sign-off summarizer, whose
  prompt asks for "facts expressed by Michael" → `summary_text` → episodic memory. **The
  Stage 6 memory guardrail held per-turn and leaked at sign-off**: the per-turn gate correctly
  skips guests, but the summary is a SECOND write path that nobody had gated. A guardrail is only
  as good as the number of write paths you checked.

**Copy/paste in the dashboard**: `renderState` polls every 1000ms and did an unconditional
`t.innerHTML = …`, destroying any text selection mid-drag ("nothing stays selected"). Fix: only
re-render when the payload actually changed, plus a ⧉ Copy button. **Rule**: a polling renderer
must be a no-op when nothing changed — the DOM is user state (selection, focus, scroll), not just
output.
