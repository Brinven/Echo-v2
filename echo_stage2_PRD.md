# Echo Stage 2 — Session Management & Sign-Off
## Product Requirements Document

---

## Context & Stage 1 Results

Stage 1 is complete. Final metrics:

| Metric | Result |
|---|---|
| STT avg | 0.28s |
| TTFT avg | 0.18s |
| First audio avg | 0.94s |
| Total avg | 1.63s |
| Pass rate | 4/4 under 1.5s budget |

Key architectural facts carried forward:
- LM Studio endpoint: `http://127.0.0.1:1234/v1` (not localhost — 2s penalty on Windows)
- Kokoro initialized once at startup, kept warm
- webrtcvad active, PTT fallback available
- Mic disabled during SPEAKING state

---

## What Stage 2 Adds

Stage 1 has no concept of a session — it just runs until Q is pressed.
Stage 2 adds:

1. **Named sessions** with a defined start and end
2. **Sign-off phrase** that triggers graceful session close
3. **Conversation logging** — every turn saved to a per-session JSON file
4. **Session summary** — LLM pass after sign-off that produces a structured
   summary for Stage 3 memory ingestion

Nothing else. Do not add memory reads or writes in Stage 2.
OpenMemory is not installed or referenced in Stage 2.
Stage 2 produces the files that Stage 3 will consume.

---

## Sign-Off Phrase

**Trigger: "Echo, that's all for now"**

Detection rules:
- Run STT on the utterance as normal
- Check transcript for: `"that's all for now"` or `"thats all for now"`
  (handle both apostrophe variants)
- The word "Echo" must appear earlier in the same transcript
- Match is case-insensitive
- Partial matches acceptable: "Echo, I think that's all for now" should trigger
- If matched: enter SIGN-OFF sequence (see below)
- If not matched: process as normal conversation turn

Do not use a keyword spotter for this — run it through the normal
STT pipeline and check the transcript. Latency doesn't matter for
sign-off detection.

---

## Sign-Off Sequence

When sign-off phrase is detected, execute in order:

**Step 1 — Acknowledge**
Echo speaks a warm, brief goodbye. Use the user's name if known
(pulled from session context — see Session Initialization below).

Examples:
- "Goodbye Michael, talk soon."
- "Catch you later, Michael."
- "Take care, Michael. I'll be here."

Keep it to one sentence. Do not recap the conversation. Do not ask
if there's anything else. Just a clean, warm goodbye.

**Step 2 — Notify**
Immediately after audio finishes, print to console:
```
[Writing session summary... please wait]
```
This signals to the user that Echo needs a moment before it's fully off.
Do not speak this — console only.

**Step 3 — Summary LLM Pass**
Call LM Studio with the full conversation log and a structured extraction prompt.
This runs silently. See Summary Format below.

**Step 4 — Save**
Write two files to `./sessions/`:
- Full conversation log (all turns)
- Session summary (structured JSON)

**Step 5 — Shutdown**
Print `[Session complete. Goodbye.]` to console and exit cleanly.

---

## Session Initialization

On startup (before first user utterance), Echo should establish basic
session context. This does NOT require a conversation — it happens
automatically from config and/or a brief startup exchange.

**From config.json (user sets once, persists):**
```json
{
  "user_name": "Michael",
  "user_pronouns": "he/him"
}
```

If `user_name` is set in config: Echo knows the name immediately.
If not set: Echo learns it during conversation if the user mentions it,
or it just doesn't use a name in the goodbye.

Do not ask the user their name on startup. That would be annoying.

---

## Conversation Logging (Per Turn)

Every turn in the conversation is appended to an in-memory log
during the session. On sign-off, this log is written to disk.

**Turn structure:**
```json
{
  "turn_id": 1,
  "timestamp": "2026-04-05T14:32:11",
  "speaker": "user",
  "content": "Earlier, we were talking about Italy...",
  "stt_latency_s": 0.51,
  "first_audio_s": null
}
```

```json
{
  "turn_id": 2,
  "timestamp": "2026-04-05T14:32:13",
  "speaker": "echo",
  "content": "Those lovely Venetian canal boats are called vaporetti...",
  "stt_latency_s": null,
  "first_audio_s": 1.20
}
```

Notes:
- `stt_latency_s` only present on user turns
- `first_audio_s` only present on Echo turns
- Content is the full text — user transcript and Echo's complete response
- Turn IDs are sequential integers starting at 1

---

## Session File Format

**Filename:** `session_YYYY-MM-DD_HH-MM-SS.json`
**Location:** `./sessions/` (auto-created if missing)

```json
{
  "session_id": "2026-04-05_14-30-22",
  "started_at": "2026-04-05T14:30:22",
  "ended_at": "2026-04-05T14:47:15",
  "user_name": "Michael",
  "model": "HauhauCS/Gemma-4-E4B-Uncensored",
  "stt_backend": "faster-whisper",
  "tts_backend": "kokoro-onnx",
  "turn_count": 8,
  "metrics": {
    "stt_avg_s": 0.28,
    "first_audio_avg_s": 0.94,
    "ttft_avg_s": 0.18
  },
  "turns": [
    {
      "turn_id": 1,
      "timestamp": "2026-04-05T14:30:45",
      "speaker": "user",
      "content": "...",
      "stt_latency_s": 0.31,
      "first_audio_s": null
    },
    {
      "turn_id": 2,
      "timestamp": "2026-04-05T14:30:47",
      "speaker": "echo",
      "content": "...",
      "stt_latency_s": null,
      "first_audio_s": 0.89
    }
  ]
}
```

---

## Session Summary Format

A second file is written alongside the session log:

**Filename:** `summary_YYYY-MM-DD_HH-MM-SS.json`
**Location:** `./sessions/` (same directory)

This file is what Stage 3 will read to write memories to OpenMemory.
Its structure must be stable — Stage 3 depends on it.

```json
{
  "session_id": "2026-04-05_14-30-22",
  "generated_at": "2026-04-05T14:47:18",
  "user_name": "Michael",
  "topics_discussed": [
    "Venice canals",
    "gondolas",
    "Venetian heritage"
  ],
  "facts_about_user": [
    "Michael expressed interest in Venetian history and culture",
    "Michael knew that gondolas are passed down through families"
  ],
  "facts_general": [
    "Gondolas are built from wood and can last over a century with proper care",
    "Vaporetti are the public water buses of Venice"
  ],
  "action_items": [],
  "explicitly_remembered": [],
  "conversation_mood": "curious and engaged",
  "summary_text": "Michael and Echo had a conversation about Venice, focusing on canal boats. Topics included the difference between gondolas and vaporetti, the family heritage tradition of gondoliers, and the construction and longevity of gondolas."
}
```

**Field definitions:**
- `topics_discussed`: short list of subjects covered
- `facts_about_user`: things learned about Michael specifically — preferences,
  knowledge, opinions expressed. These become personal memories in Stage 3.
- `facts_general`: factual information discussed, not personal to the user.
  Lower priority for memory storage.
- `action_items`: anything the user said they want to do, follow up on, etc.
- `explicitly_remembered`: empty in Stage 2 (populated in Stage 3 when
  "remember that" is implemented — field included now so format is stable)
- `conversation_mood`: brief characterization of the session tone
- `summary_text`: 2-4 sentence human-readable summary of the session

---

## Summary LLM Prompt

Use this exact prompt structure for the summary pass.
Send the full conversation as context:

```
System: You are a session summarizer. Extract structured information from
the conversation below. Respond only with valid JSON matching the schema
provided. Do not include any text outside the JSON object.

User: Summarize this conversation and extract the following as JSON:
{
  "topics_discussed": ["list of topics"],
  "facts_about_user": ["personal facts, preferences, opinions expressed by the user"],
  "facts_general": ["factual information discussed, not personal to user"],
  "action_items": ["things user wants to do or follow up on"],
  "conversation_mood": "one short phrase",
  "summary_text": "2-4 sentence summary"
}

Conversation:
[FULL CONVERSATION TURNS HERE]
```

Parse the JSON response carefully — small models occasionally add
preamble text or markdown fences. Strip ` ```json ` and ` ``` `
before parsing. If parsing fails, log the raw response and write
a minimal summary file with an error field rather than crashing.

---

## Q Key Behavior Change

In Stage 1, Q triggered immediate shutdown.

In Stage 2, Q behavior:
- If mid-conversation: print warning "Press Q again to exit without saving session."
  Second Q within 3 seconds: exit immediately, no summary written, session log
  still written (partial is better than nothing)
- If no conversation has happened yet: exit immediately
- This prevents accidental session loss

Sign-off phrase remains the clean, intended exit path.

---

## Console UI Updates

Extend Stage 1 status display:

```
╔══════════════════════════════════════════════════════╗
║  ECHO — Stage 2                                      ║
╠══════════════════════════════════════════════════════╣
║  Status:   LISTENING                                 ║
║  Session:  2026-04-05_14-30-22  (turn 4)            ║
║  VAD:      active                                    ║
║  Mute:     off                                       ║
╠══════════════════════════════════════════════════════╣
║  Last:     STT 0.28s │ first audio 0.94s             ║
╚══════════════════════════════════════════════════════╝
  [SPACE: PTT]  [M: mute]  [Q: quit]
  Say "Echo, that's all for now" to end session
```

---

## File Structure

Extend Stage 1 — do not delete existing files:

```
echo/
├── main.py           ← update: session init, sign-off detection
├── stt.py            ← unchanged
├── llm.py            ← unchanged
├── tts.py            ← unchanged
├── vad.py            ← unchanged
├── audio_queue.py    ← unchanged
├── state.py          ← update: add SIGN_OFF state
├── timer.py          ← unchanged
├── logger.py         ← update: extend with session logging
├── session.py        ← NEW: session management, sign-off, summary
├── summarizer.py     ← NEW: LLM summary pass, JSON extraction
├── config.json       ← update: add user_name, user_pronouns fields
├── requirements.txt  ← unchanged
├── README.md         ← update with Stage 2 instructions
├── logs/
│   └── stage0_log.jsonl
└── sessions/         ← NEW: auto-created
    ├── session_2026-04-05_14-30-22.json
    └── summary_2026-04-05_14-30-22.json
```

---

## Success Criteria

Stage 2 is complete when:

1. **Sign-off triggers correctly**: "Echo, that's all for now" consistently
   triggers sign-off and does NOT trigger on similar phrases without "Echo"
2. **Goodbye feels natural**: Echo's farewell is warm and uses the user's name
3. **Session file written correctly**: valid JSON, all turns present, metrics accurate
4. **Summary file written correctly**: valid JSON, facts_about_user contains
   at least one meaningful personal fact from a test conversation
5. **Summary LLM handles JSON failures**: if model returns malformed JSON,
   the app logs the error and writes a partial summary rather than crashing
6. **Q double-press works**: accidental Q doesn't silently lose a session
7. **Sessions folder persists across runs**: files from previous sessions
   are not deleted or overwritten

---

## Notes for Claude Code

- `session.py` and `summarizer.py` are the two new modules. Keep them separate —
  session management (tracking turns, managing state) and summary generation
  (LLM call, JSON extraction) are different responsibilities.
- The summary JSON schema in this PRD is the contract with Stage 3.
  Do not change field names without updating this document first.
  Stage 3 will break silently if field names shift.
- Use the same `http://127.0.0.1:1234/v1` endpoint for the summary LLM call.
  Do not use `localhost`. (See Stage 1 learnings — 2s penalty on Windows.)
- The summary pass does not need to be fast. The user has already said goodbye.
  Correctness matters more than speed here.
- Test the JSON extraction with a deliberately messy model response
  (add markdown fences, add preamble text) to confirm the parser handles it.
- `explicitly_remembered` field is intentionally empty in Stage 2.
  Include it in the schema now so Stage 3 can populate it without
  changing the file format.
