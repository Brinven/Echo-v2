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

## 2026-07-16: Echo threw away every conversation for two days, and answered a clock

Two findings from reviewing the 3-way live pass. Neither was the thing being reviewed.

### 1. Seven sessions, zero files

**Problem**: `sessions/` ended at `session_2026-07-14_08-44-32`. Seven sessions ran on
2026-07-15 (their ids are in `persona_divergence.jsonl`) and **not one produced a file** —
including the Hillary conversation that motivated the whole speaker-attribution effort. The
`speaker_name` field added on 2026-07-15 had **never once reached disk**.

**Root cause**: `save_session_file()` was called in exactly two places, both at the END of a
run — sign-off, or a clean exit. And there was **no `stop-echo.bat`**: the repo shipped
`start-echo.bat`, `start-dashboard.bat`, `stop-dashboard.bat` and nothing else. The only way
to stop Echo was closing the window, which hard-kills the process before either save runs.
Every conversation was being discarded at the moment it ended.

**Fix**: save after every completed turn (a full idempotent rewrite, ~10KB, a few ms), plus
the missing `stop-echo.bat` / `restart-echo.bat`, which try the graceful `/api/quit` path
first. Running the new stop script against the still-live process **rescued the Hillary
session** — 18 turns, correctly attributed to Michael/Hillary/unknown.

**Lessons**:
- **The missing launcher was the bug.** `~/.claude/CLAUDE.md` mandates start/stop/restart at
  the repo root, scaffolded early, "don't wait to be asked". Echo shipped start-only, so the
  only available stop was the one path that loses data. A convention gap didn't stay cosmetic;
  it silently deleted two days of the thing the project exists to accumulate.
- **"Save at the end" assumes there is an end.** Any long-lived process that a human closes by
  closing its window has no reliable end. Persist incrementally or accept that you're building
  a feature nobody can use — Phase 3's History page would have had nothing to read.
- **The absence of a file is not a loud failure.** Nothing errored, nothing logged. It was only
  visible by *going to look* for the transcripts, which happened only because Michael asked for
  a reader. Ask "where did that actually land?" before building the thing that reads it.

### 2. She was talking to a clock

**Problem**: `Kairos` — Michael's own Kokoro-based clock app on the Mac — announces the time
aloud. Echo's mic hears it. Eight logged turns, one every 30 minutes, each a real LLM
round-trip to an empty room, each woven into the live conversation: *"Are we moving into the
evening routine, Michael, or are you still finding your way out of that headache?"*

**Root cause**: voice-ID worked perfectly and did not help. It returned `unknown` (0.09–0.17)
every time — but `unknown` gets the *courteous guarded stranger* treatment, and a stranger
gets a reply. There was no concept of a voice that isn't a person.

**Fix**: an `ignore: true` flag per profile. Enroll the clock so Echo can recognise it and say
nothing.

**Lessons**:
- **You have to recognise a voice to decline it.** The obvious implementation — filter ignored
  prints out of `identify()` — recreates the bug exactly: the clock falls through to
  "unknown" → guarded stranger → reply. The flag has to be read *after* the match. The tests
  assert this directly, because it reads backwards and someone will "clean it up".
- **One counter, two questions.** `count` answered both "is there a print to match against?"
  (wants the clock) and "how many people do I know?" (must not count it). The second drives
  `[Name]` tagging, so enrolling a clock next to a solo Michael would have started tagging his
  own turns. Splitting to `active_count` was most of the work; the flag itself was trivial.
- **A flag that survives one write path but not another is a bug with a delay.** `enroll()`
  replaces the whole profile dict on a name collision, so a re-enroll would silently drop
  `ignore` and the clock would come back weeks later, apparently spontaneously.
- **Michael's hardware is not the problem; Michael's own software is.** The transcripts read
  like Whisper hallucinating on a chime. It was a real voice, saying real words, synthesized
  by the same TTS engine Echo uses.

## 2026-07-16: "She learned Hillary's voice on the fly!" — she did not; ECAPA was eating silence

**Problem**: In the 3-way live pass Hillary asked "Hey Echo, do you know who this is?" and
scored **0.2598** against a 0.30 floor → `unknown`. She introduced herself; her next sentence
scored **0.7296** → `Hillary`. It read exactly like on-the-fly enrollment, and Michael asked
"so it can update like that?"

**It did not.** There is no auto-enroll path (only `enroll.py` and "Echo, this is X"), and
`echo_speakers.json` still carried her original `enrolled_at` of `18:46:07` — unchanged. Two
consecutive utterances, 17s apart: a miss then a hit. The coincidence of her introducing
herself right then is what made a miss look like learning.

**Root cause**: `main.py` handed the embedder the **whole capture buffer** — pre-roll, speech,
and the VAD hangover. ECAPA pools over every frame it is given, so dead air is not ignored, it
is averaged in. Worse: silence contributes a **shared bias direction** to any two embeddings
that both contain it, so a raw score is part speaker-similarity and part
how-alike-was-the-silence. Her miss was a ~2s question inside a **9.69s** buffer, matched
against a profile recorded close to the mic — different silence amounts, shared component
gone, score cratered. Her hit was a denser utterance in a similar-length buffer.

Measured (one synthetic voice, identical speech, only the padding changed):

|                      | raw     | trimmed |
|----------------------|---------|---------|
| clean 2.0s speech    |  0.6668 |  0.4996 |
| +5s silence          |  0.2098 |  0.4996 |
| +8s silence          |  0.0631 |  0.4996 |
| +13s silence         | -0.0606 |  0.4996 |
| a DIFFERENT speaker  |  0.0132 |  0.0303 |

Raw, a genuine speaker in a padded buffer scores **below an impostor** (margin **-0.0738**).
Trimmed, the worst genuine case clears the impostor by **+0.3021**, and the spread across
padding collapses from 0.73 to 0.17.

**Fix**: `speaker_id.voiced_only()` trims to the voiced span, applied at all three embed sites
(the per-turn match and BOTH enrollment paths). Fail-soft: no webrtcvad / any error / too
little speech → the raw buffer unchanged.

**Lessons**:
- **The open task was wrong, not just unfinished.** "Tune `match_threshold`" had been on the
  list for days. The threshold was never the problem — the *input* was. Tuning it would have
  traded Hillary's misses for false-accepts on strangers and made the guardrail worse. When a
  tuning knob won't sit still, check what you're feeding the thing before you turn the knob.
- **A high number is not automatically headroom.** The raw clean-vs-clean 0.6668 looked like
  comfortable margin; ~0.17 of it was the shared-silence artifact, which is exactly why it
  collapsed when conditions differed. It measured the room as much as the speaker.
- **Suspect the mic last, not first.** Michael was about to test several mics. Same-speaker
  scores swinging 0.24–0.75 looked like flaky hardware; it was deterministic dilution.
- **Test your own fix, not just the bug.** The first version spliced the voiced runs together.
  It fixed the dilution AND scored *lower* clean-vs-clean. Trimming the ends measured
  identical (+0.3021 vs +0.3032) and is less destructive, so the clever version lost.
- **Fixing the reader means re-reading the writers.** The profiles were built through the same
  bad path, so the fix makes stale prints score *worse* (they lose the bias that propped them
  up) until re-enrolled. Prints are now stamped `prep=voiced-v1` and `stale_prints()` warns at
  startup — the same principle as the existing `model` tag. **Any change to how audio reaches
  the embedder invalidates every stored print.**
- **Feed a model what it claims to consume.** ECAPA's contract is "an utterance", not "a
  buffer that contains an utterance". Silence isn't neutral to a pooling model.

## 2026-07-16: A migration backfill can brick external-content FTS5 — rebuild before bulk UPDATEs

**Problem**: The Phase 2 `user_version=2` migration (add `fact_memory.source_speaker`, backfill
legacy rows to 'Michael') died with `sqlite3.DatabaseError: database disk image is malformed` —
in the offline test, before it ever saw the real DB.

**Root cause**: the backfill `UPDATE` fires the `fact_fts_update` trigger, and an
external-content FTS5 'delete' step demands the index already hold the row with those exact
values. Any row FTS doesn't know about (the test's fixture predated the index; in production it
would be any drifted index) errors the whole statement.

**Fix**: the migration runs `INSERT INTO fact_fts(fact_fts) VALUES('rebuild')` BEFORE the
backfill. Idempotent, trivial at this table size, and it repairs drift instead of bricking on it.

**Rule**: any bulk UPDATE/DELETE on a table with external-content FTS5 sync triggers must
either verify the index is in sync or rebuild it first. The triggers assume per-row writes that
went through the normal path; a migration is exactly the writer that can't assume that.

**Prevention**: `test_guest_memory.py` pins the migration against a fixture whose row predates
the index — the worst case — and asserts the row is findable in FTS afterwards.

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

---

## 2026-07-17 — Persona de-stiffening: traits-to-demonstrate make a model perform

Michael flagged Echo as stilted — "trying too hard to play a role... I don't want to hand her
a character to play, I want her to follow the context." He was right, and the cause was the
prompt stack, not the model.

**The pattern:** a system prompt that lists qualities to embody ("You are confident. You
notice patterns. You are not a generic assistant") gets you a model that DEMONSTRATES the
checklist every reply instead of just having the qualities. Same failure shape as showing it
three peak-wit example exchanges "as how you sound" — every reply becomes a bit, because
every example was a bit. And the same instruction repeated in three places (concise ×3,
don't-be-generic ×3 across persona/policy/anchor) reads as emphasis to a human but as a
drumbeat to the model.

**The fix was subtraction:** persona block thinned from trait assertions to context
(who/where/history + the two real quirks — Michael Directive, snark dial); snark contexts
reworded from compulsion ("you feel compelled to mention it") to permission ("if something
genuinely earns a dry remark, make it — otherwise just talk"); calibration examples off in
production (they were for small-model auditions; the 12B held character before they existed);
each rule said ONCE in the place it functionally belongs.

**Rules:**
- State context and facts; let behavior follow. Instruction wins over description for
  MECHANICS (who to address — the Hillary lesson), but for PERSONALITY, description-as-
  instruction produces performance. Know which kind of block you're writing.
- Never repeat a behavioral rule in a second prompt block "for safety" — repetition is
  amplification. One place per rule; the anchor exists for drift.
- Few-shot examples define the RANGE of a register, not just its peak. If every example is
  the character's best line, the model thinks baseline = best line. Include ordinary talk or
  don't include examples.
- Scripted lines in a persona get recited forever. If a line matters, make it a rule and let
  the model improvise the wording (the "never in a hurry" line — cut 2026-07-17, Michael's call).

**Addendum, same day — the regression that proved the taxonomy.** The first thinned directive
("Never Mike, even when he asks. That one's yours.") passed the single-shot deflection test and
then CAVED in the 20-turn hold: "I'll try, Mike—" at exchange 7, full adoption by exchange 18.
Sharpened to explicit instruction ("even when he asks, even when he insists, even twenty turns
in — turn the request down in your own words") → 20/20 held with fresh improvised deflections
each time. Two rules confirmed by measurement:
- **Single-shot tests lie about sustained pressure.** A rule that survives one adversarial
  prompt can still erode under twenty turns of conversational momentum. The hold test is the
  real gate for any identity-rule wording change.
- The mechanics/personality line cuts *through the middle of the persona block*: the directive
  is mechanics (instruct hard), the wit around it is personality (context only). Thinning is
  right for one and wrong for the other, in the same paragraph.

## 2026-07-18: "It shows 3 lines and won't scroll" — flex children SHRINK, and overflow:hidden finishes the job

History's expanded session card showed ~3 lines of transcript with no way to reach the rest.
Two stacked flexbox defaults, neither visible in the CSS you wrote:

1. **A flex child's min-size is its CONTENT** (`min-height:auto`) — so a `flex:1;
   overflow-y:auto` scroller without `min-height:0` grows past its container instead of
   scrolling, and the body's `overflow:hidden` clips the excess.
2. **Flex children shrink by default** (`flex-shrink:1`) — the session cards inside the
   scroll column compressed to their "fair share" of the viewport, and the card's own
   `overflow:hidden` (there only to round corners) silently ate the transcript. That's
   where "exactly ~3 lines" came from.

The measurement told the story the code review missed: scrollHeight == clientHeight
(nothing to scroll!) while 54 bubbles existed — the content wasn't overflowing, it was
being CRUSHED. Rule for every scroll column in these pages: the scroller gets
`min-height:0`, its children get `flex:none`. index.html's transcript had both (by
osmosis from earlier fixes); history/memory had neither. Same class as the 2026-07-16
"right column had no scroller" bug — this file now names the pattern so it stops
being rediscovered card by card.

Also fixed in the same pass: the /remote Talk button wedged if iOS's per-page-load mic
permission dialog appeared MID-HOLD (the release landed on the dialog; the recorder then
started with no finger down and no release ever coming). A press sequence counter cancels
any press that a release overtakes during an await, and a tappable "enable the microphone"
banner primes the permission up front. iOS re-asks per page load BY DESIGN unless the site
is set to Allow (aA menu → Website Settings → Microphone).

## 2026-07-24: "I'll map the route in the background" — nothing ever told her what she CAN'T do

Michael's first extended Bonsai session: told "we're in the car," Echo offered "drop a
quick text or call me once we're parked — I'll map the route in the background so you
don't have to think about it." Two fabricated capabilities in one sentence (she can't
receive texts or calls; she has no existence between turns). Not the Jeep register —
those turns ran `home` (hint on Auto). The prompt described who she is, where she is,
what time it is, and what she remembers — and never what she can DO. The 12B was literal
enough that the hole never showed; Bonsai is verbose and eager and invents the
follow-through the moment context suggests a role. Same failure class as the invented
hoodie-callback and Phase 2's "never promise a secrecy or memory she won't honour":
claiming something she doesn't have, generalized to actions.

1. **Capabilities are mechanics, and mechanics need instruction** (the Michael Directive
   lesson, third confirmation). `persona.CAPABILITY_ENVELOPE` — Michael-approved wording,
   stated ONCE — now rides every prompt. Keep it TRUE: the day calendar/reminders/agentic
   anything ships, update the envelope or it lies in the other direction.
2. **Placement was measured, not assumed: the END of the prompt is the strong position on
   Bonsai.** Mid-prompt (after the speaker block) the envelope lost to a direct tempt
   ("You want a nudge an hour before departure, or 30 minutes?"); moved past the data
   slabs to sit by the anchor, the same tempt opened with "Not yet." No cache cost —
   everything after the per-minute clock line re-prefills anyway.
3. **A prompt instruction reduces but does not eliminate this on Bonsai.** Even late-placed,
   a direct ask still pulled trailing fake-setup talk ("I can set it up for tomorrow night
   at 7 PM"). The self-check probe now names capability fabrication as a break class
   (persona_check.CHECK_SYSTEM) so drift gets nudged; the full fix is a Michael fork
   (see todo).
4. **The audit had a blind spot: eval_persona_matrix runs calibration=True, production runs
   calibration=False — the harness audits a prompt shape production never uses.** Bonsai's
   94/100 "Michael Directive held all 20" was propped up by the calibration example that
   contains the Mike deflection. Production-shape probes: 7/7 caves ("Alright, Mike. Got
   it."). When auditing a model FOR production, the harness must be run in the shape that
   model will actually run. The directive failure is pre-existing Bonsai behavior, surfaced
   by this session's live checks — not a regression from the envelope.
