"""
Memory reader for Echo Stage 4.

Read-only counterpart to memory.py. Queries OpenMemory for context
to inject into the system prompt. Never writes to OpenMemory.

Two retrieval modes:
  1. Session start — broad context query (k=10)
  2. Per turn — semantic search against transcript (k=3, min_score=0.6)
"""

import time
import logging

from llm import DEFAULT_SYSTEM_PROMPT
from memory import MemoryClient

logger = logging.getLogger(__name__)

# Memory block template — the subtlety instruction is functional, not stylistic.
# Do not reword it.
_MEMORY_BLOCK = """
You know the following about Michael from previous conversations.
Use this knowledge naturally — the way a close friend would, without
announcing that you remember it. Never say "I remember", "last time
we spoke", or "based on our conversations". Simply know it.

{memories}"""


def get_session_context(
    memory_client: MemoryClient,
    k: int = 10,
) -> list[dict]:
    """
    Retrieve top-k memories for session context injection.

    Runs at session start before the first user utterance.
    Returns list of result dicts with 'content' and 'score' fields.
    """
    if not memory_client.available:
        return []

    try:
        results = memory_client.search(
            query="Michael preferences personality life context",
            limit=k,
        )
        return results if results else []
    except Exception as e:
        logger.error(f"Session context retrieval failed: {e}")
        return []


def get_turn_context(
    memory_client: MemoryClient,
    transcript: str,
    k: int = 3,
    min_score: float = 0.6,
) -> list[dict]:
    """
    Retrieve memories relevant to the current user utterance.

    Returns up to k results above the min_score threshold.
    Must complete in < 100ms to stay within latency budget.
    """
    if not memory_client.available:
        return []

    try:
        results = memory_client.search(query=transcript, limit=k)
        if not results:
            return []

        # Filter by score threshold
        filtered = []
        for r in results:
            score = r.get("score", 0.0) if isinstance(r, dict) else 0.0
            if score >= min_score:
                filtered.append(r)

        return filtered
    except Exception as e:
        logger.error(f"Turn context retrieval failed: {e}")
        return []


def _extract_content(result: dict) -> str:
    """Extract content string from an OpenMemory search result."""
    if isinstance(result, dict):
        return result.get("content", str(result))
    return str(result)


def build_system_prompt(
    session_memories: list[str],
    turn_memories: list[str] | None = None,
    max_memories: int = 15,
) -> str:
    """
    Build the full system prompt with memory block injected.

    Args:
        session_memories: Memory strings from session-start retrieval
        turn_memories: Optional additional memories from turn-level retrieval
        max_memories: Cap on total memories in prompt

    Returns:
        Complete system prompt string
    """
    # Combine and deduplicate
    all_memories = list(session_memories)
    if turn_memories:
        existing = set(all_memories)
        for mem in turn_memories:
            if mem not in existing:
                all_memories.append(mem)
                existing.add(mem)

    # Cap at max
    all_memories = all_memories[:max_memories]

    # If no memories, return base prompt without memory block
    if not all_memories:
        return DEFAULT_SYSTEM_PROMPT

    # Format memory block
    memory_lines = "\n".join(f"- {mem}" for mem in all_memories)
    memory_block = _MEMORY_BLOCK.format(memories=memory_lines)

    return DEFAULT_SYSTEM_PROMPT + "\n" + memory_block


def retrieve_and_build_prompt(
    memory_client: MemoryClient,
    session_memories: list[str],
    transcript: str | None = None,
    turn_k: int = 3,
    turn_min_score: float = 0.6,
    turn_retrieval_enabled: bool = True,
    max_memories: int = 15,
) -> tuple[str, int, float]:
    """
    One-call convenience: retrieve turn memories and build prompt.

    Args:
        memory_client: The MemoryClient instance
        session_memories: Already-retrieved session context strings
        transcript: Current user utterance (None for first turn)
        turn_k: Number of turn-level results to fetch
        turn_min_score: Minimum score threshold for turn results
        turn_retrieval_enabled: Whether to do per-turn retrieval
        max_memories: Cap on total memories

    Returns:
        Tuple of (system_prompt, turn_memories_added, retrieval_ms)
    """
    turn_strings = []
    retrieval_ms = 0.0

    if transcript and turn_retrieval_enabled and memory_client.available:
        t0 = time.perf_counter()
        turn_results = get_turn_context(
            memory_client, transcript, k=turn_k, min_score=turn_min_score,
        )
        retrieval_ms = (time.perf_counter() - t0) * 1000

        turn_strings = [_extract_content(r) for r in turn_results]

    prompt = build_system_prompt(session_memories, turn_strings, max_memories)
    return prompt, len(turn_strings), retrieval_ms
