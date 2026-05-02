# CLAUDE.md — Echo Project Architectural Context (Updated: Stage 1)

This file exists to give Claude Code the decisions already made about the Echo
project so that code written does not conflict with established architecture.
Do not override these decisions without explicit user instruction.

---

## What Echo Is

Echo is a local-first AI voice companion. It runs entirely on the user's Windows PC.
It is privacy-first: no cloud APIs, no data leaving the machine.

This is a learning project. Clean, readable, modular code is preferred over
clever or optimized code. When in doubt, be explicit.

---

## Current Build Stage

**Stage 0 — Latency Measurement Only**

Nothing in Stage 0 should be built as if it is the final companion. It is a
diagnostic instrument. The goal is accurate measurement, not a good product.

The build order is:

| Stage | Focus |
|---|---|
| 0 | STT → LLM → TTS pipeline latency test |
| 1 | Conversational core (wiring Stage 0 into a working voice loop) |
| 2 | Session management (sign-off, conversation logging) |
| 3 | Memory write (OpenMemory, "remember that" + end-of-session summary) |
| 4 | Memory read (inject relevant memories into system prompt at session start) |
| 5 | Personality layer (stable persona via system prompt) |

Do not build Stage 1+ features into Stage 0. Do not build Stage 2+ features into Stage 1. etc.

---

## Hardware

| Item | Spec |
|---|---|
| OS | Windows 11 |
| CPU | AMD Ryzen 9900x |
| RAM | 64GB DDR5 |
| GPU | NVIDIA RTX 5080 (16GB VRAM) — primary and only GPU for PoC |
| Storage | 2TB+ M.2 free space |

A second GPU (RTX 4060, 8GB) is available but NOT used in the PoC.
It is deferred to post-PoC for running STT/TTS/embeddings separately.
Do not architect for dual-GPU now.

---

## LLM Stack

- **Runtime**: LM Studio, localhost:1234, OpenAI-compatible API (`/v1/`)
- **Package**: `openai` Python package pointed at local endpoint
- **Model selection**: auto-detect from `/v1/models` at startup
- **Preferred models**: Gemma 4B (fast, ~80 tok/s on this hardware) or similar small-medium local model
- **No cloud LLM**: do not add fallback to OpenAI, Anthropic, or any external API
- **Fine-tuning**: considered as a future option, not in scope yet

---

## STT Stack

- Auto-detect between `faster-whisper` (preferred) and `openai-whisper`
- CUDA-accelerated on RTX 5080
- Sample rate: 16kHz mono (Whisper native — no resampling)
- Input: PTT (push-to-talk) via SPACE key for PoC
- VAD (voice activity detection): deferred to Stage 1, with PTT as permanent fallback

---

## TTS Stack

- Kokoro (either `kokoro-onnx` or HuggingFace `kokoro` — auto-detect)
- This was chosen based on direct testing: 82M model, excellent quality, fast on CPU
  (may also run on GPU — test and use whichever is faster)
- No other TTS engines should be added without explicit instruction

---

## Memory System

- **Selected library**: OpenMemory by CaviraOSS
  (https://github.com/CaviraOSS/OpenMemory)
- **Rationale**: local-first, SQLite, Ollama embedding support, MCP endpoint,
  memory decay built in, multi-sector cognitive model
- **NOT in scope for Stage 0**. Do not install or reference it yet.
- Memory architecture decisions already made:

### Memory Write Strategy (Stage 3)
Two paths, both independent:
1. **Explicit**: user says "remember that" → immediate write to OpenMemory, no delay
2. **End-of-session**: user says sign-off phrase → LLM summary pass → writes
   everything else to OpenMemory

"Remember that" items are written immediately and NOT re-processed at sign-off.
The summary pass skips already-stored items.

### Memory Read Strategy (Stage 4)
- At session start, query OpenMemory for relevant context
- Inject retrieved memories into system prompt before first LLM call
- Retrieval is semantic — not a full dump of all memories

### Memory Types
OpenMemory's multi-sector model maps to Echo's needs:
- **Episodic** (high decay): time-bound events (visits, appointments)
- **Semantic** (low decay): persistent facts about people, preferences, relationships
Do not conflate these. Store them with appropriate sector tags.

---

## Personality / Persona

- Name: Echo
- Defined via system prompt (not fine-tuning) for the PoC
- Stage 5 work — do not add personality prompting to Stage 0 or 1 beyond
  the minimal latency-test system prompt
- The problem with previous Echo attempts was an overcomplicated system prompt
  that confused the model. Keep it simple and test incrementally.

---

## Session Management (Stage 2)

- Sessions have an explicit start and end
- Sign-off is a spoken/typed phrase that triggers end-of-session processing
- Heartbeat auto-detection (check every 30 min for unsaved sessions) is
  a future feature — NOT in the PoC
- Conversation logs saved as JSONL per session

---

## What Was Explicitly Rejected

These were considered and rejected — do not reintroduce them:

| Idea | Why rejected |
|---|---|
| Cloud LLM (OpenAI, Anthropic, etc.) | Privacy requirement, local-only |
| MemOS (MemTensor) | Requires external API keys (BaiLian), enterprise overkill |
| SimpleMem (aiming-lab) | Recursive Consolidation not implemented, research-only |
| A-mem (agiresearch) | Research implementation, not production-ready |
| opencode-agent-memory | Designed for OpenCode CLI tool, wrong domain |
| Autonomous email sending | Agentic with real-world write access, out of PoC scope |
| Calendar integration | Out of PoC scope |
| Dual-GPU orchestration | Post-PoC only |
| VAD in Stage 0 | PTT is simpler and sufficient for latency testing |
| Streaming LLM in Stage 0 | Need full-response baseline first; streaming is Stage 1 |

---

## Latency Budget

- Target: **< 3 seconds** total roundtrip (end of speaking → first word of audio)
- This was chosen by the user as "acceptable" for a home companion
- Stage 0 exists to verify this is achievable on this hardware
- If Stage 0 shows consistent > 3s, the model size or STT model must be
  reconsidered before proceeding to Stage 1

---

## Code Standards

- Python 3.10+
- Modular: STT, LLM, TTS are separate modules with clean interfaces
- These modules will be imported by later stages — design interfaces to be stable
- Explicit errors over silent fallbacks
- JSONL for all logging (consistent format across all stages)
- No frameworks (no LangChain, no LlamaIndex) in Stage 0 — raw API calls only
- Windows-compatible paths (use `pathlib.Path`, not hardcoded `/` separators)

---

## Stage 2 Additions

**Sign-off phrase:** "Echo, that's all for now"
- "Echo" must appear in transcript
- Handle both "that's" and "thats" variants
- Partial matches acceptable

**Session files:** `./sessions/session_YYYY-MM-DD_HH-MM-SS.json`
**Summary files:** `./sessions/summary_YYYY-MM-DD_HH-MM-SS.json`

Summary JSON schema is the CONTRACT with Stage 3 — do not change field names:
topics_discussed, facts_about_user, facts_general, action_items,
explicitly_remembered, conversation_mood, summary_text

**user_name** stored in config.json — used in goodbye and summary.
Default: "Michael"

**Q key in Stage 2:** requires double-press mid-conversation to prevent accidental loss.

---

## Stage 3 Additions

**Memory system:** OpenMemory by CaviraOSS, localhost:8080
Use http://127.0.0.1:8080 not localhost (Windows DNS penalty).
User ID: "echo_michael" (configurable in config.json)

**Path A — "remember that":** immediate write during conversation.
Tags: ["explicit", "user_requested"]
Extraction call: max_tokens=50, temperature=0, single sentence output.

**Path B — session summary writer:** runs at sign-off after summary LLM pass.
Write: facts_about_user, action_items, facts_general[source=web_search]
Skip: facts_general[source=model_knowledge], topics_discussed, mood, summary_text
Never re-write explicitly_remembered items — already stored via Path A.

**facts_general schema (updated Stage 3):**
{"fact": "string", "source": "model_knowledge|web_search"}
Backward compat: flat strings treated as model_knowledge (skip).

**memory.py** is a thin client wrapper only — no business logic.
Business logic for what to write lives in session.py (Path B)
and the conversation loop (Path A).

---

## ⚠ Hindsight Backend Notes (post-2026-05-02 swap)

Echo's memory backend is **Hindsight, not OpenMemory** as of 2026-05-02.
`memory.py` was rewritten; the `MemoryClient` surface is preserved so
`memory_reader.py`, `session.py`, and `main.py` are untouched.

Two behavioral differences from OpenMemory worth knowing:

### 1. Writes are async by default

Hindsight's retain runs xAI Grok 4.1 Fast for fact extraction (~10s/call).
That's unacceptable in the voice hot path, so `MemoryClient.add()` always
sends `async=true`. The retain queues immediately and processes in the
background. Practical effect: a "remember that X" said at the end of a
session is **NOT** searchable for ~10s after sign-off. Don't write tests
that immediately recall what was just retained — wait or poll.

### 2. min_score=0.6 is now a rank-based proxy, NOT a true similarity score

Hindsight's `RecallResult` schema does **not** expose a similarity score
field — by design. Hindsight's internal ranker handles relevance, surfaced
through the `budget` (low/mid/high) and `max_tokens` knobs.

`MemoryClient.search()` synthesizes a per-result score as `1.0 - (i / n)`
where `i` is the rank index. So with k=3 results: `[1.00, 0.667, 0.333]`.
With k=5: `[1.00, 0.80, 0.60, 0.40, 0.20]`. With k=10: `[1.00, 0.90, ...]`.

This means `min_score=0.6` (in `memory_reader.get_turn_context`) **no longer
filters by semantic confidence** — it just trims the bottom of the rank list.
With k=3, it keeps the top 2. With k=5, it keeps the top 3.

**If irrelevant memories start getting injected per-turn, the lever is
`budget="low"` inside `MemoryClient.search()` — not min_score.** Tightening
budget reduces the candidate pool Hindsight returns; loosening lets more
through. The synthesized score is downstream of that.

If you ever need a real confidence threshold, options are:
- Use Hindsight's `tags` filter to pre-narrow (e.g. only `personal` tags)
- Use the `types` filter (`world` / `experience` / `observation`) to bias
  toward the right kind of memory
- Set `budget="low"` and trust Hindsight's ranker

Do not try to recompute similarity locally — embeddings live inside
Hindsight; round-tripping them defeats the abstraction.

---

## Stage 4 Additions

**Memory reads:** memory_reader.py — read-only counterpart to memory.py.
Never writes to OpenMemory. All writes stay in memory.py.

**Retrieval:**
- Session start: k=10 broad context query before first utterance
- Per turn: k=3 semantic query against transcript, min_score=0.6
- Cap: 15 memories max in system prompt at any time
- Target: < 100ms per turn retrieval

**System prompt is now dynamic.** llm.py must accept system prompt
as parameter — not hardcoded. Memory block appended at runtime.

**The subtlety rule is functional, not stylistic:**
Echo never says "I remember" or "last time we spoke".
It simply knows. Instruction in system prompt:
"Use this knowledge naturally — the way a close friend would,
without announcing that you remember it."

**Memory block omitted entirely if OpenMemory has no memories.**
Do not inject empty block.

**New JSONL fields:** memory_retrieval_ms, memories_injected, turn_memories_added
