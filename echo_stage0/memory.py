"""
Hindsight client wrapper for Echo.

Replaces the original embedded OpenMemory backend with the Axly Hindsight
service running locally at http://localhost:8888 (PM2 process
hindsight-memory). The MemoryClient surface is preserved so memory_reader.py,
session.py, and main.py continue to work without changes.

Why Hindsight instead of OpenMemory:
- Shared with claude.ai and the Claude Code plugin (one source of truth across
  every Axly project).
- Biomimetic memory model (entities, observations, temporal links) gives
  richer recall context than OpenMemory's flat semantic store.
- Configured per-project via bank_id (Echo uses bank "echo").

Two operational notes:
- Writes ALWAYS use async=true. Hindsight's retain endpoint runs xAI Grok 4.1
  Fast for fact extraction and takes ~10s per call -- unacceptable in the
  voice hot path. Async retains return immediately (queued, processed in
  background). Echo never blocks on memory writes.
- Recall has no exposed similarity score (unlike OpenMemory's min_score).
  Hindsight's internal ranker handles relevance; we cap with `budget` and
  Python-side `[:k]`. memory_reader.py's old min_score parameter is now
  ignored -- ranking + budget do the equivalent job.
"""

import os
import re
import json
import logging
from pathlib import Path
import requests
from openai import OpenAI

logger = logging.getLogger(__name__)

# ---- config -----------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
LM_STUDIO_URL = "http://127.0.0.1:1234/v1"

# "Remember that" trigger phrases (unchanged from OpenMemory era)
_REMEMBER_PATTERNS = [
    re.compile(r"\bremember\s+that\b", re.IGNORECASE),
    re.compile(r"\bmake\s+sure\s+you\s+remember\b", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+forget\s+that\b", re.IGNORECASE),
    re.compile(r"\bkeep\s+in\s+mind\s+that\b", re.IGNORECASE),
]


def has_remember_trigger(transcript: str) -> bool:
    """Check if transcript contains a 'remember that' trigger phrase."""
    return any(p.search(transcript) for p in _REMEMBER_PATTERNS)


def _load_hindsight_config() -> dict:
    """
    Resolve Hindsight URL/key/bank from config.json with env-var override.
    Env vars take precedence so the same checked-in config can run against
    any Hindsight instance (local, tailnet, or via the OAuth shim).
    """
    cfg = {}
    if _CONFIG_PATH.exists():
        try:
            cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"failed to read {_CONFIG_PATH}: {e}")

    return {
        "url": os.environ.get("HINDSIGHT_URL") or cfg.get("hindsight_url", "http://127.0.0.1:8888"),
        "api_key": os.environ.get("HINDSIGHT_API_KEY") or cfg.get("hindsight_api_key"),
        "bank_id": os.environ.get("HINDSIGHT_BANK_ID") or cfg.get("hindsight_bank_id", "echo"),
        "tenant": os.environ.get("HINDSIGHT_TENANT") or cfg.get("hindsight_tenant", "default"),
    }


# ---- client ----------------------------------------------------------------------

class MemoryClient:
    """
    Hindsight-backed memory client. Drop-in replacement for the original
    OpenMemory wrapper -- same surface, different backend.

    Args:
        user_id: legacy OpenMemory parameter, retained for call-site compat.
            Ignored; bank routing comes from hindsight_bank_id in config.
    """

    def __init__(self, user_id: str = "echo_michael"):
        # Kept on the instance for log lines and any future per-user routing,
        # but not used as a Hindsight key (banks are project-scoped, not user-scoped).
        self._user_id = user_id

        cfg = _load_hindsight_config()
        self._base_url = cfg["url"].rstrip("/")
        self._bank_id = cfg["bank_id"]
        self._tenant = cfg["tenant"]
        self._api_key = cfg["api_key"]

        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "echo-voice-companion/0.4",
        })
        if self._api_key:
            self._session.headers["Authorization"] = f"Bearer {self._api_key}"

        self._available = self._probe_health()
        if self._available:
            print(f"  Memory: Hindsight connected ({self._base_url}, bank={self._bank_id})")
        else:
            print(f"  Memory: Hindsight unreachable at {self._base_url}")
            print("  WARNING: Memories will not be saved this session.")

    # ----- internals --------------------------------------------------------------

    def _probe_health(self) -> bool:
        """Verify the Hindsight service is reachable and the bank exists."""
        try:
            r = self._session.get(f"{self._base_url}/health", timeout=3)
            if r.status_code != 200:
                logger.error(f"Hindsight /health returned {r.status_code}")
                return False
        except Exception as e:
            logger.error(f"Hindsight /health failed: {e}")
            return False

        if not self._api_key:
            logger.error("HINDSIGHT_API_KEY is not set; Hindsight requires Bearer auth")
            return False

        # Confirm the bank exists. PUT is idempotent; we'd rather create-if-missing
        # than fail mid-session, but only do this once (during init).
        try:
            r = self._session.put(
                f"{self._base_url}/v1/{self._tenant}/banks/{self._bank_id}",
                json={"name": f"Echo ({self._user_id})"},
                timeout=10,
            )
            return r.status_code in (200, 201)
        except Exception as e:
            logger.error(f"Hindsight bank PUT failed: {e}")
            return False

    def _bank_path(self, suffix: str = "") -> str:
        return f"{self._base_url}/v1/{self._tenant}/banks/{self._bank_id}{suffix}"

    # ----- public surface (matches the old OpenMemory wrapper) -------------------

    @property
    def available(self) -> bool:
        return self._available

    def add(self, content: str, tags: list[str] | None = None) -> dict | None:
        """
        Write a memory. Always async (non-blocking) -- Hindsight runs Grok for
        fact extraction which is too slow for the voice hot path.

        Args:
            content: the memory text
            tags: optional tags for categorization

        Returns:
            Hindsight retain response dict on success, None on failure.
        """
        if not self._available or not content or not content.strip():
            return None

        body = {
            "items": [{
                "content": content.strip(),
                "context": "echo-conversation",
                "tags": tags or [],
            }],
            "async": True,
        }
        try:
            r = self._session.post(self._bank_path("/memories"), json=body, timeout=10)
            if not r.ok:
                logger.error(f"Hindsight retain failed {r.status_code}: {r.text[:200]}")
                return None
            return r.json()
        except Exception as e:
            logger.error(f"Hindsight retain exception: {e}")
            return None

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """
        Recall memories matching a query.

        Returns a list of dicts shaped like the old OpenMemory result format
        ({"content": str, "score": float, "id": str, "tags": [...]}) so
        memory_reader.py works unchanged. The score is rank-based: top hit
        gets 1.0, last hit gets ~0. Hindsight's own ranker chose the order.
        """
        if not self._available or not query or not query.strip():
            return []

        # budget="low" for tight per-turn calls (k<=5), "mid" otherwise.
        budget = "low" if limit <= 5 else "mid"
        body = {
            "query": query.strip(),
            "budget": budget,
            "max_tokens": 1024 if limit <= 5 else 2048,
        }
        try:
            r = self._session.post(self._bank_path("/memories/recall"), json=body, timeout=10)
            if not r.ok:
                logger.error(f"Hindsight recall failed {r.status_code}: {r.text[:200]}")
                return []
            results = (r.json() or {}).get("results") or []
        except Exception as e:
            logger.error(f"Hindsight recall exception: {e}")
            return []

        # Cap at requested limit and shape into OpenMemory-compatible dicts.
        results = results[:limit]
        n = len(results)
        out = []
        for i, hit in enumerate(results):
            out.append({
                "id": hit.get("id"),
                "content": hit.get("text") or "",
                "score": (1.0 - (i / n)) if n > 0 else 0.0,
                "tags": hit.get("tags") or [],
                "type": hit.get("type"),
                "context": hit.get("context"),
                "mentioned_at": hit.get("mentioned_at"),
            })
        return out

    def history(self, limit: int = 20) -> list[dict]:
        """Get recent memories (chronological list, not semantic recall)."""
        if not self._available:
            return []
        try:
            r = self._session.get(
                self._bank_path("/memories/list"),
                params={"limit": limit},
                timeout=10,
            )
            if not r.ok:
                logger.error(f"Hindsight list failed {r.status_code}: {r.text[:200]}")
                return []
            return ((r.json() or {}).get("items") or [])
        except Exception as e:
            logger.error(f"Hindsight list exception: {e}")
            return []


# ---- LLM-driven fact extraction (unchanged from OpenMemory era) -------------------

def extract_remember_fact(transcript: str, user_name: str, model: str) -> str | None:
    """
    Use LLM to extract the specific fact the user wants remembered.
    Fast, cheap call: max_tokens=50, temperature=0.
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
        fact = fact.strip('"\'')
        return fact if fact else None
    except Exception as e:
        logger.error(f"Remember fact extraction failed: {e}")
        return None


# ---- session-summary writer (Path B; unchanged signature) ------------------------

def write_session_memories(
    memory_client: MemoryClient,
    summary: dict,
    session_id: str,
    explicitly_remembered: list[str],
) -> int:
    """
    Path B: write session summary facts to Hindsight.

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

    for fact in summary.get("facts_about_user", []):
        if isinstance(fact, str) and fact.strip():
            if memory_client.add(content=fact, tags=["personal", "session_derived", session_id]):
                count += 1

    for item in summary.get("action_items", []):
        if isinstance(item, str) and item.strip():
            if memory_client.add(content=item, tags=["action_item", "session_derived", session_id]):
                count += 1

    for item in summary.get("facts_general", []):
        if isinstance(item, dict):
            source = item.get("source", "model_knowledge")
            fact_text = item.get("fact", "")
            if source == "web_search" and fact_text.strip():
                if memory_client.add(content=fact_text, tags=["retrieved", "web_sourced", session_id]):
                    count += 1
        # Flat strings (legacy Stage 2 format) -> implicit model_knowledge -> skip.

    if explicitly_remembered:
        logger.info(
            f"Skipping {len(explicitly_remembered)} explicitly-remembered items "
            "(already stored via Path A)"
        )

    return count
