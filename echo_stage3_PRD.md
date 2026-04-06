# Echo Stage 3 — Memory Writes
## Product Requirements Document

---

## Context

Stages 0-2 are complete. Echo has:
- Sub-1s first audio latency (0.94s avg)
- Working VAD + PTT conversation loop
- Session logging with full turn history
- End-of-session summary JSON with structured facts

Stage 3 adds persistent memory. After this stage, Echo will remember
things across sessions. It will not yet *use* those memories in
conversation — that is Stage 4 (memory reads). Stage 3 is writes only.

---

## Build Order (Important — Follow This Sequence)

**Step 1 first, before installing OpenMemory or writing any memory code.**
Getting the data format right before the writes begin means no cleanup later.

```
1. Update summarizer.py — structured facts_general with source tag
2. Install and configure OpenMemory
3. Implement Path A — "remember that" immediate write
4. Implement Path B — end-of-session summary writer
5. Wire both into the conversation loop
6. Test end-to-end
```

---

## Step 1 — Update summarizer.py (Schema Change)

### The Problem
Stage 2 summarizer writes `facts_general` as a flat list of strings.
We need to know where each fact came from before deciding whether to
store it in memory.

### The Rule
```
facts_about_user              → always write to memory (it's about Michael)
action_items                  → always write to memory (he said he'd do it)
facts_general, source=model   → NEVER write (Echo already knows this)
facts_general, source=web     → write (Echo retrieved this, doesn't natively know it)
```

Web access doesn't exist yet — but the architecture must be ready for it.

### Schema Change

`facts_general` changes from a flat string list to a list of objects:

**Before (Stage 2):**
```json
"facts_general": [
  "Gondolas are built from wood and can last over a century"
]
```

**After (Stage 3):**
```json
"facts_general": [
  {
    "fact": "Gondolas are built from wood and can last over a century",
    "source": "model_knowledge"
  },
  {
    "fact": "The Rialto Bridge restoration completed in 2024 per retrieved article",
    "source": "web_search"
  }
]
```

All other summary fields are unchanged. `facts_about_user`, `action_items`,
`explicitly_remembered`, `topics_discussed`, `conversation_mood`,
and `summary_text` remain exactly as Stage 2 defined them.

### Summarizer LLM Prompt Update

Update the summary extraction prompt to request the new format:

```
"facts_general": [
  {
    "fact": "the factual statement",
    "source": "model_knowledge or web_search"
  }
]

Use "model_knowledge" if Echo stated this from its own training.
Use "web_search" if this information came from a retrieved external source.
Currently all facts will be model_knowledge — include the field anyway.
```

### Backward Compatibility
Old Stage 2 summary files have flat string `facts_general`.
The Stage 3 memory writer must handle both formats:
- If `facts_general` contains strings: treat all as `model_knowledge`, skip
- If `facts_general` contains objects: apply source filter

Do not reprocess old summary files. Just handle both formats gracefully.

---

## Step 2 — Install and Configure OpenMemory

**Repository:** https://github.com/CaviraOSS/OpenMemory

### Installation
Follow the repo's getting-started instructions.
Run as local server on default port (8080).
Use SQLite backend (default) — sufficient for PoC, no Postgres needed.

### Embedding Model
Configure OpenMemory to use Ollama for embeddings (local, no external API).
If Ollama is not installed, use OpenMemory's synthetic embeddings as fallback.
Document which embedding mode is active in README.md.

### OpenMemory User ID
All memories for this PoC are stored under a single user ID: `"echo_michael"`
This is configurable in `config.json`:
```json
{
  "memory_user_id": "echo_michael"
}
```

### Memory Sectors
OpenMemory classifies memories into cognitive sectors automatically.
Do not override this classification in Stage 3 — let OpenMemory decide.
Stage 5 (personality) may revisit sector configuration if needed.

### Decay Configuration
Use OpenMemory defaults for decay rates.
Episodic memories (time-bound events) will decay faster than semantic
memories (persistent facts about Michael) automatically.
Do not tune decay rates in Stage 3.

### Health Check
On Echo startup, ping OpenMemory health endpoint before proceeding:
```
GET http://localhost:8080/health
```
If OpenMemory is not running:
- Print clear warning: "OpenMemory not running. Memories will not be saved this session."
- Continue without memory — do not crash
- Log the warning to session file

---

## Step 3 — Path A: "Remember That" Immediate Write

### Trigger Detection
During conversation, detect the phrase "remember that" in any user turn.

Detection rules:
- Check transcript after STT, before sending to LLM
- Trigger phrases: "remember that", "make sure you remember",
  "don't forget that", "keep in mind that"
- Case insensitive
- If triggered: extract the memory content AND process normally
  (Echo still responds to the statement conversationally)

### What Gets Written
The content immediately preceding or following the trigger phrase.

Examples:
- "Remember that I prefer tea over coffee" → store: "Michael prefers tea over coffee"
- "I hate mushrooms, remember that" → store: "Michael hates mushrooms"
- "Remember that my wife's name is Sarah" → store: "Michael's wife is named Sarah"

The LLM extracts the memory content from context. Use a brief inline
extraction call — not the full summary LLM. Keep it fast:

```
System: Extract the specific fact the user wants remembered.
Return only the fact as a single sentence, starting with the user's name.
Do not add commentary.

User: The user said: "[full user utterance]"
Their name is Michael.
What should be remembered?
```

### Write to OpenMemory
```python
await mem.add(
    content=extracted_fact,
    user_id=config.memory_user_id,
    tags=["explicit", "user_requested"]
)
```

Tag `"explicit"` marks this as user-requested — higher confidence than
summary-derived memories. Stage 4 can use this tag for retrieval priority.

### Echo's Response
After writing, Echo acknowledges naturally in its conversational response.
It does not say "I have stored this in my memory database."
It says something like "I'll remember that" or "Got it" woven into its reply.

The LLM handles this naturally if the system prompt includes:
"When the user asks you to remember something, acknowledge it briefly
and naturally in your response."

Add this line to the Stage 1 system prompt.

### Confirmation in explicitly_remembered
At end of session, `explicitly_remembered` in the summary JSON is populated
with all Path A writes from that session:

```json
"explicitly_remembered": [
  "Michael prefers tea over coffee",
  "Michael's wife is named Sarah"
]
```

This field was included in the Stage 2 schema for exactly this purpose.

---

## Step 4 — Path B: End-of-Session Summary Writer

Triggered during the sign-off sequence, after the summary LLM pass completes.

### What Gets Written

From each summary file, write the following to OpenMemory:

**Always write:**
```python
# facts_about_user — personal facts about Michael
for fact in summary["facts_about_user"]:
    await mem.add(
        content=fact,
        user_id=config.memory_user_id,
        tags=["personal", "session_derived", session_id]
    )

# action_items — things Michael said he'd do
for item in summary["action_items"]:
    await mem.add(
        content=item,
        user_id=config.memory_user_id,
        tags=["action_item", "session_derived", session_id]
    )

# explicitly_remembered — Path A writes (already stored, log for completeness)
# Do NOT re-write these — they were already written during the session
# Just confirm they exist in the session summary
```

**Conditionally write:**
```python
# facts_general — only if source is web_search
for item in summary["facts_general"]:
    source = item.get("source", "model_knowledge")
    if source == "web_search":
        await mem.add(
            content=item["fact"],
            user_id=config.memory_user_id,
            tags=["retrieved", "web_sourced", session_id]
        )
    # model_knowledge: skip entirely
```

**Never write:**
- `topics_discussed` — too generic, not useful as memories
- `conversation_mood` — not useful as a persistent memory
- `summary_text` — too broad, redundant with individual facts
- `facts_general` with `source: "model_knowledge"` — Echo already knows this

### Duplicate Handling
OpenMemory handles semantic deduplication internally.
Do not implement custom deduplication in Stage 3.
If the same fact appears across multiple sessions, OpenMemory's
reinforcement mechanism will increase its salience naturally.

### Timing
Path B runs during the sign-off sequence, after the summary LLM pass,
before the final "Session complete" console message.

The console already shows `[Writing session summary... please wait]`
during this window — no additional UI needed.

---

## Step 5 — Wire Into Conversation Loop

### Startup
```
1. Check OpenMemory health → warn and continue if unavailable
2. Initialize memory client (OpenMemory Python SDK or HTTP calls)
3. Log memory status to console: "[Memory: connected]" or "[Memory: unavailable]"
```

### During Conversation
```
- After each STT pass: check for "remember that" trigger
- If triggered: extract fact → write to OpenMemory → continue to LLM
- LLM receives normal turn (memory extraction is invisible to it
  except for the system prompt line about acknowledging remember requests)
```

### At Sign-Off
```
- After summary LLM pass completes
- Run Path B writer against the summary JSON
- Log count of memories written: "[3 memories written to OpenMemory]"
- Then proceed to normal shutdown
```

---

## OpenMemory Client

Use the OpenMemory Python SDK where possible:
```python
from openmemory.client import Memory
mem = Memory(path='./data/memory.sqlite')  # or server URL
```

If SDK behavior is unclear, fall back to HTTP API calls directly:
```python
import httpx
await httpx.post("http://localhost:8080/memory/add", json={...})
```

Document which approach was used and why in README.md.

---

## New Config Fields

```json
{
  "memory_enabled": true,
  "memory_user_id": "echo_michael",
  "openmemory_url": "http://localhost:8080",
  "remember_triggers": [
    "remember that",
    "make sure you remember",
    "don't forget that",
    "keep in mind that"
  ]
}
```

---

## File Structure

```
echo/
├── main.py           ← update: memory health check on startup
├── stt.py            ← unchanged
├── llm.py            ← unchanged
├── tts.py            ← unchanged
├── vad.py            ← unchanged
├── audio_queue.py    ← unchanged
├── state.py          ← unchanged
├── timer.py          ← unchanged
├── logger.py         ← unchanged
├── session.py        ← unchanged
├── summarizer.py     ← UPDATE: structured facts_general with source tag
├── memory.py         ← NEW: OpenMemory client wrapper, Path A and B writers
├── config.json       ← update: memory fields
├── requirements.txt  ← add openmemory-py (or httpx if using HTTP directly)
├── README.md         ← update: OpenMemory setup instructions
├── logs/
├── sessions/
└── data/             ← NEW: OpenMemory SQLite database location
    └── memory.sqlite
```

---

## Success Criteria

Stage 3 is complete when:

1. **Summarizer updated**: new summary files have structured `facts_general`
   with source tags. Old files handled without crashing.
2. **OpenMemory running**: health check passes on startup, memory client
   initializes without error.
3. **Path A works**: saying "remember that I hate Mondays" during conversation
   results in a memory entry in OpenMemory tagged `["explicit", "user_requested"]`
   within the same conversation turn.
4. **Path A acknowledged**: Echo's response naturally acknowledges the
   remember request without sounding robotic.
5. **Path B works**: after sign-off, `facts_about_user` and `action_items`
   from the summary are written to OpenMemory. `model_knowledge` facts are not.
6. **No double-writes**: `explicitly_remembered` items are NOT re-written
   during Path B — they were already stored during the session.
7. **OpenMemory unavailable handled**: if OpenMemory is not running,
   Echo continues the conversation normally with a console warning.
8. **Verify in OpenMemory dashboard**: open the OpenMemory UI and confirm
   memories are visible, correctly tagged, and readable.

---

## Notes for Claude Code

- Build Step 1 (summarizer update) and test it against a real conversation
  before touching OpenMemory. Confirm the new summary JSON format is correct
  before any memory writes happen.
- `memory.py` is a wrapper — it should not contain business logic about
  what to remember. That logic lives in `session.py` (Path B) and in the
  conversation loop (Path A). `memory.py` just handles the OpenMemory
  client and write operations.
- The `explicitly_remembered` field in the summary is a log of what was
  stored via Path A during that session. It is NOT a write instruction for
  Path B. Path B must check this and skip those items.
- Use `http://127.0.0.1:8080` not `http://localhost:8080` for OpenMemory
  on Windows. Same DNS resolution penalty as LM Studio.
- Test Path A with all trigger variants in config, not just "remember that".
- The memory extraction LLM call for Path A should be fast and cheap —
  use max_tokens=50, temperature=0. We want a single clean sentence, not
  a thoughtful response.
