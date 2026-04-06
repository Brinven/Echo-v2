"""
OpenMemory client wrapper for Echo.

Thin wrapper around the OpenMemory embedded SDK.
No business logic — just add/search/health operations.
Business logic for what to write lives in session.py (Path B)
and the conversation loop (Path A).

Uses the embedded Memory class directly (no server needed).
SDK has a broken LangChain connector import — we patch around it.
"""

import sys
import os
import asyncio
import logging
import re
from pathlib import Path
from openai import OpenAI, APITimeoutError

logger = logging.getLogger(__name__)

# Patch broken LangChain import in openmemory
sys.modules.setdefault("openmemory.connectors", type(sys)("fake"))
sys.modules.setdefault("openmemory.connectors.langchain", type(sys)("fake"))

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "memory.sqlite"
LM_STUDIO_URL = "http://127.0.0.1:1234/v1"

# "Remember that" trigger phrases
_REMEMBER_PATTERNS = [
    re.compile(r"\bremember\s+that\b", re.IGNORECASE),
    re.compile(r"\bmake\s+sure\s+you\s+remember\b", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+forget\s+that\b", re.IGNORECASE),
    re.compile(r"\bkeep\s+in\s+mind\s+that\b", re.IGNORECASE),
]


def has_remember_trigger(transcript: str) -> bool:
    """Check if transcript contains a 'remember that' trigger phrase."""
    return any(p.search(transcript) for p in _REMEMBER_PATTERNS)


class MemoryClient:
    """Wrapper around OpenMemory's embedded Memory class."""

    def __init__(self, user_id: str = "echo_michael"):
        self._user_id = user_id
        self._available = False
        self._mem = None
        self._init_memory()

    def _init_memory(self):
        """Initialize the OpenMemory client."""
        try:
            os.environ["OPENMEMORY_DB_URL"] = f"sqlite:///{DB_PATH}"
            DB_PATH.parent.mkdir(exist_ok=True)

            from openmemory.main import Memory
            self._mem = Memory(user=self._user_id)
            self._available = True
            print(f"  Memory: connected (user: {self._user_id})")
        except Exception as e:
            logger.error(f"OpenMemory init failed: {e}")
            print(f"  Memory: unavailable ({e})")
            print("  WARNING: Memories will not be saved this session.")

    @property
    def available(self) -> bool:
        return self._available

    def add(self, content: str, tags: list[str] | None = None) -> dict | None:
        """
        Write a memory to OpenMemory.

        Args:
            content: The memory text
            tags: Optional tags for categorization

        Returns:
            Result dict with memory ID, or None on failure
        """
        if not self._available:
            return None

        try:
            result = asyncio.run(
                self._mem.add(content, user_id=self._user_id, tags=tags or [])
            )
            return result
        except Exception as e:
            logger.error(f"Memory add failed: {e}")
            return None

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Search memories by semantic similarity."""
        if not self._available:
            return []

        try:
            results = asyncio.run(
                self._mem.search(query, user_id=self._user_id, limit=limit)
            )
            return results
        except Exception as e:
            logger.error(f"Memory search failed: {e}")
            return []

    def history(self, limit: int = 20) -> list[dict]:
        """Get recent memory history."""
        if not self._available:
            return []

        try:
            return self._mem.history(self._user_id, limit=limit)
        except Exception as e:
            logger.error(f"Memory history failed: {e}")
            return []


def extract_remember_fact(transcript: str, user_name: str, model: str) -> str | None:
    """
    Use LLM to extract the specific fact the user wants remembered.

    Fast, cheap call: max_tokens=50, temperature=0.

    Args:
        transcript: Full user utterance
        user_name: User's name
        model: LM Studio model ID

    Returns:
        Extracted fact as a single sentence, or None on failure
    """
    client = OpenAI(
        base_url=LM_STUDIO_URL,
        api_key="not-needed",
        timeout=15,
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract the specific fact the user wants remembered. "
                        "Return only the fact as a single sentence, starting with "
                        "the user's name. Do not add commentary."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f'The user said: "{transcript}"\n'
                        f"Their name is {user_name}.\n"
                        "What should be remembered?"
                    ),
                },
            ],
            max_tokens=50,
            temperature=0,
            stream=False,
        )
        fact = response.choices[0].message.content.strip()
        # Clean up: remove quotes, ensure it starts with the name
        fact = fact.strip('"\'')
        return fact if fact else None
    except Exception as e:
        logger.error(f"Remember fact extraction failed: {e}")
        return None


def write_session_memories(
    memory_client: MemoryClient,
    summary: dict,
    session_id: str,
    explicitly_remembered: list[str],
) -> int:
    """
    Path B: Write session summary facts to OpenMemory.

    Writes:
      - facts_about_user (always)
      - action_items (always)
      - facts_general where source=web_search (conditionally)

    Skips:
      - facts_general where source=model_knowledge
      - topics_discussed, conversation_mood, summary_text
      - explicitly_remembered items (already stored via Path A)

    Returns count of memories written.
    """
    if not memory_client.available:
        return 0

    count = 0

    # facts_about_user — always write
    for fact in summary.get("facts_about_user", []):
        if isinstance(fact, str) and fact.strip():
            result = memory_client.add(
                content=fact,
                tags=["personal", "session_derived", session_id],
            )
            if result:
                count += 1

    # action_items — always write
    for item in summary.get("action_items", []):
        if isinstance(item, str) and item.strip():
            result = memory_client.add(
                content=item,
                tags=["action_item", "session_derived", session_id],
            )
            if result:
                count += 1

    # facts_general — only web_search sources
    for item in summary.get("facts_general", []):
        if isinstance(item, dict):
            source = item.get("source", "model_knowledge")
            fact_text = item.get("fact", "")
            if source == "web_search" and fact_text.strip():
                result = memory_client.add(
                    content=fact_text,
                    tags=["retrieved", "web_sourced", session_id],
                )
                if result:
                    count += 1
        # Flat strings (old Stage 2 format) -> model_knowledge, skip
        # (backward compat: don't write these)

    # explicitly_remembered: do NOT re-write — already stored via Path A
    # Just log for confirmation
    if explicitly_remembered:
        logger.info(
            f"Skipping {len(explicitly_remembered)} explicitly remembered items "
            "(already stored via Path A)"
        )

    return count
