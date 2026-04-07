# Echo Stage 4 — Memory Reads
## Product Requirements Document

---

## Context

Stages 0-3 are complete. Echo can:
- Converse with sub-1s first audio latency (0.84s avg, stable)
- Manage sessions with sign-off and conversation logging
- Write memories to OpenMemory via two paths (explicit + summary)

Stage 4 adds memory *reads*. After this stage, Echo will know things
about Michael across sessions — not because it was told in the current
conversation, but because it remembers from before.

---

## The Core Design Principle (Read This First)

Echo must use memories **subtly and naturally**, the way a friend would.

This is not a demo of a memory system. It is a companion that happens
to remember things. The difference is everything.

```
# WRONG — announces memory, breaks immersion:
"I remember you mentioned your brother was visiting. How did that go?"
"Based on our previous conversations, I know you prefer tea."
"Last time we spoke, you said you were working on Echo."

# RIGHT — uses memory naturally, like a person would:
[Michael mentions feeling tired]
Echo: "Sounds like the project is still running you ragged."
[no announcement, no "I remember" — just knowing]

[Michael mentions coffee]
Echo: "Thought you were a tea person?"
[light, natural, doesn't make a production of it]
```

This principle must be embedded in the system prompt and in how
memories are injected. It is not optional behavior — it is the
definition of what Stage 4 is.

---

## Build Order

```
1. Memory retrieval module — query OpenMemory, return ranked results
2. Context injection — format memories into system prompt correctly
3. Session startup retrieval — query on session start
4. Conversational retrieval — query during conversation when relevant
5. System prompt update — instruct Echo on subtle memory use
6. Test and tune
```

---

## Step 1 — Memory Retrieval Module

Add `memory_reader.py` — the read counterpart to Stage 3's `memory.py`.

### Retrieval at Session Start

When Echo starts a new session, query OpenMemory for context about Michael
before the first user utterance. This is a broad retrieval — get a
general picture of who this person is.

```python
async def get_session_context(user_id: str, k: int = 10) -> list[str]:
    """
    Retrieve top-k memories for session context injection.
    Returns list of memory strings, ranked by relevance + recency.
    """
    results = await mem.search(
        query="Michael preferences personality life context",
        user_id=user_id,
        limit=k
    )
    return [r.content for r in results]
```

`k=10` is the starting value. Tune based on:
- How much context the model can handle without degrading
- Whether injecting 10 memories produces noticeably better responses
  than 5
- Context window cost (each memory adds tokens to every LLM call)

### Retrieval During Conversation (Contextual)

At each user turn, do a lightweight semantic search against the
transcript to surface memories relevant to what's being discussed.

```python
async def get_turn_context(
    user_id: str,
    transcript: str,
    k: int = 3
) -> list[str]:
    """
    Retrieve memories relevant to current user utterance.
    Returns up to k results, or empty list if nothing relevant.
    """
    results = await mem.search(
        query=transcript,
        user_id=user_id,
        limit=k,
        min_score=0.6  # only return confident matches
    )
    return [r.content for r in results]
```

**`min_score=0.6` is critical.** Without a confidence threshold,
irrelevant memories get injected and Echo starts making strange
connections. If OpenMemory doesn't expose a score filter directly,
filter the results manually after retrieval.

The turn-level query runs *after* STT, *before* the LLM call.
It must be fast — target < 100ms. If it's consistently slower,
reduce k or disable turn-level retrieval and rely on session-start
context only.

---

## Step 2 — Context Injection Format

Memories are injected into the system prompt as a distinct block.
The format matters — it affects how the model interprets and uses them.

### System Prompt Structure

```
[BASE PERSONA — always present]
You are Echo, a helpful voice assistant running locally on Michael's PC.
Keep responses conversational and concise — you are speaking aloud,
not writing. Avoid lists, bullet points, and markdown formatting.
Prefer 2-4 sentences unless Michael explicitly asks for more detail.
When Michael asks you to remember something, acknowledge it briefly
and naturally in your response.

[MEMORY BLOCK — injected at session start, updated per turn]
You know the following about Michael from previous conversations.
Use this knowledge naturally — the way a close friend would, without
announcing that you remember it. Never say "I remember", "last time
we spoke", or "based on our conversations". Simply know it.

- [memory 1]
- [memory 2]
- [memory 3]
...

[END MEMORY BLOCK]
```

### Injection Rules

**Session start:** inject top-k session context memories into system prompt.
This block stays in the system prompt for the entire session.

**Per turn:** if turn-level retrieval returns new memories not already
in the session block, append them to the memory block before the LLM call.
Do not duplicate — check before appending.

**Memory block size limit:** cap at 15 memories total in the prompt at
any time. If retrieval returns more, rank by score and take top 15.
Beyond 15, context window cost outweighs benefit for a 4B model.

**Empty memory block:** if OpenMemory has no memories yet (fresh install,
first few sessions), omit the memory block entirely. Do not inject an
empty section — it wastes tokens and may confuse the model.

---

## Step 3 — System Prompt Update

The Stage 1 system prompt gets the memory instruction added.
This is the complete updated system prompt for Stage 4:

```
You are Echo, a helpful voice assistant running locally on Michael's PC.
Keep responses conversational and concise — you are speaking aloud,
not writing. Avoid lists, bullet points, and markdown formatting.
Prefer 2-4 sentences unless Michael explicitly asks for more detail.
When Michael asks you to remember something, acknowledge it briefly
and naturally in your response.
Use any personal context you have about Michael naturally, the way a
close friend would — without announcing that you remember it, without
saying "I remember" or "last time we spoke". Simply know it and let
it inform how you talk to him.
```

The memory block (from Step 2) is appended to this base prompt
dynamically at runtime. The base prompt itself does not contain
specific memories.

---

## Handling Stale or Wrong Memories

OpenMemory's decay system handles fading over time automatically.
However, the model may occasionally use a memory that is now outdated
(e.g., "your brother's visit" when that was three months ago).

**Do not build complex staleness detection in Stage 4.**

Instead, rely on two natural mechanisms:
1. OpenMemory decay — episodic memories fade faster than semantic ones
2. Conversation correction — if Echo says something outdated and Michael
   corrects it, the correction becomes context for the rest of the session

If staleness becomes a noticeable problem after testing, address in
Stage 5 or a Stage 4 patch. Do not over-engineer it now.

---

## Performance Constraints

Stage 4 must not meaningfully increase latency. Targets:

| Metric | Stage 3 baseline | Stage 4 target |
|---|---|---|
| First audio | 0.84s avg | ≤ 1.0s avg |
| Session start delay | ~0s | ≤ 500ms added |

Session-start retrieval runs before the first user utterance —
it happens during the brief moment after Echo says its startup
acknowledgment (if any). This is the right time to absorb the
retrieval latency without it affecting conversation latency.

Turn-level retrieval must complete in < 100ms or it will push
first audio over budget. Measure this explicitly and log it.

Add to JSONL log:
```json
{
  "memory_retrieval_ms": 45,
  "memories_injected": 7,
  "turn_memories_added": 1
}
```

---

## Testing Protocol

Stage 4 requires a specific test sequence to verify correctly:

### Test A — Cross-session recall (subtle)
1. In a fresh session, mention something personal casually:
   "I've been really tired lately, been working late on Echo"
2. Sign off properly (summary writes to OpenMemory)
3. Start a new session
4. Have a normal conversation — do NOT mention Echo or being tired
5. At some point mention feeling run-down or stressed
6. **Pass criteria**: Echo responds in a way that reflects knowing
   about the project — without saying "I remember you mentioned Echo"

### Test B — Preference recall
1. In a session, mention a preference:
   "I really can't stand the cold"
2. Sign off
3. New session — discuss travel or weather
4. **Pass criteria**: Echo's suggestions or comments reflect that
   Michael dislikes cold, without announcing it

### Test C — Memory block absent on fresh install
1. Clear OpenMemory database completely
2. Start Echo
3. **Pass criteria**: No memory block in system prompt, Echo behaves
   normally without errors

### Test D — Latency within budget
1. Run 5 turns in a session with memories present
2. **Pass criteria**: first audio ≤ 1.0s avg, memory retrieval logged
   at < 100ms per turn

---

## What Counts as Success

Stage 4 is complete when:

1. **Subtle recall works**: Test A and Test B pass — Echo uses memories
   naturally without announcing them
2. **No false connections**: Echo doesn't inject irrelevant memories
   into unrelated conversations (min_score threshold is doing its job)
3. **Latency maintained**: first audio ≤ 1.0s avg across 5+ turns
4. **Fresh install clean**: no errors or empty memory blocks when
   OpenMemory has no data
5. **Turn retrieval fast**: memory retrieval logged < 100ms per turn
6. **Subjective test**: have a multi-session conversation across 2-3
   sessions. It should feel like talking to someone who *knows* you,
   not someone who is *demonstrating* that they know you.

The last criterion is the most important one. Numbers can pass while
the experience still feels wrong. Trust your ear.

---

## File Structure

```
echo/
├── main.py           ← update: inject memory context before LLM calls
├── stt.py            ← unchanged
├── llm.py            ← update: accept dynamic system prompt with memory block
├── tts.py            ← unchanged
├── vad.py            ← unchanged
├── audio_queue.py    ← unchanged
├── state.py          ← unchanged
├── timer.py          ← update: add memory_retrieval_ms logging
├── logger.py         ← update: add memory fields to JSONL
├── session.py        ← update: trigger session-start retrieval
├── summarizer.py     ← unchanged
├── memory.py         ← unchanged (Stage 3 write module)
├── memory_reader.py  ← NEW: retrieval, context formatting, injection
├── config.json       ← update: add memory_read settings
├── requirements.txt  ← unchanged
└── README.md         ← update with Stage 4 instructions
```

---

## New Config Fields

```json
{
  "memory_read_enabled": true,
  "memory_session_context_k": 10,
  "memory_turn_context_k": 3,
  "memory_turn_min_score": 0.6,
  "memory_max_injected": 15,
  "memory_turn_retrieval_enabled": true
}
```

`memory_turn_retrieval_enabled` allows disabling per-turn retrieval
if latency becomes an issue, falling back to session-start context only.

---

## Notes for Claude Code

- `memory_reader.py` is read-only. It never writes to OpenMemory.
  All writes remain in `memory.py`. Keep these concerns separated.
- The system prompt is now dynamic — it changes per session and per turn.
  `llm.py` must accept the system prompt as a parameter, not hardcode it.
  If it was hardcoded before, refactor this first.
- The memory block instruction ("use this naturally, never announce it")
  is not just politeness — it is a functional constraint that determines
  whether Stage 4 feels right. Do not soften or reword it.
- Test the min_score threshold carefully. Too low: irrelevant memories
  get injected and Echo makes strange leaps. Too high: nothing gets
  retrieved. Start at 0.6 and adjust based on observed behavior.
- Use `http://127.0.0.1:8080` not `http://localhost:8080` for OpenMemory.
  Same Windows DNS penalty as LM Studio.
- Session-start retrieval should complete before Echo's first response.
  Build it into the startup sequence, not the first turn handler.
