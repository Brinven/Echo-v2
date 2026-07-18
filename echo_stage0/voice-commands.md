# Echo — Voice Commands

The complete list of spoken commands, matched in `session.py` (patterns are the source of
truth — update this file when they change). Commands are handled as early guards in
`run_streaming_pipeline`: they don't advance the exchange counter, don't hit the memory
gate, and (since Stage 6 Phase 2) run AFTER speaker-ID — an ignored voice can trigger
nothing, and unknown voices can't sign off or forget.

| Command | Say | Effect |
|---|---|---|
| Sign off | "Echo… that's all for now" | Summary pass + memory write, session ends. Works from the phone (goodbye plays there; summary runs at the desk). |
| Forget | "forget that" / "scratch that" / "don't remember that" / "forget what I just said (told you)" / "forget what you just saved (stored)" | Deletes the most recent fact saved this session. Michael → anything; known guest → only a fact THEY said; unknown → declined ("That one isn't yours to take back…"). |
| Max snark | "max snark" / "maximum snark" | Locks snark 10 for the session (dashboard Max button does the same). Resets next launch. |
| Web search off | "stay offline" / "go offline" / "get offline" | Disables web search for the session. |
| Web search on | "go online" / "go back online" / "back online" | Re-enables it. |
| Location: Jeep | "we're in the Jeep" / "get (hop) in the Jeep" / "jeep mode" / "in the jeep now" | Jeep register + hands-free VAD defaults OFF. |
| Location: home | "we're home" / "we're at home" / "we're in the house" / "back home" / "home mode" | Home register + hands-free VAD defaults ON. |
| Enroll a voice | "Echo, this is \<Name\>" / "Echo, remember \<Name\>'s voice" | Utterance must be ≤8 words. Arms the capture: **the NEXT utterance heard becomes \<Name\>'s voiceprint, whoever speaks it** — arm it, stay quiet, let them talk. |
| Cancel enroll | "cancel" / "never mind" / "stop" / "forget it" | Aborts an armed enrollment (only matters while one is armed). |

Notes:

- **Only sign-off and enroll require the word "Echo".** The rest match on the phrase alone —
  a stray "scratch that" mid-story can fire the forget path (low-stakes: it removes at most
  the one most-recent saved fact).
- There is **no "remember that" command** — the old Stage-3 explicit-save path died with the
  Hindsight→Ib-Lite migration. The significance gate saves durable facts automatically.
- Botched enrollment: re-enrolling under the same name replaces the whole profile cleanly
  (the `ignore` flag is preserved on re-enroll).
