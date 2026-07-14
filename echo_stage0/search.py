"""
Web search for Echo (Stage 5 Part 3).

Provider-abstracted so the backend is swappable without touching the pipeline.
First (only) impl: SearXNG — a keyless, self-hosted metasearch proxy. Queries reach
upstream engines *through* SearXNG, which never identifies Echo or Michael. This is
the one deliberate exception to Echo's local-first spine; the module is written to
minimize exposure (localhost base URL, short timeout, no logging of queries here).

Contract: search() and healthy() NEVER raise — a search failure must degrade to an
in-character "couldn't find it", never crash the voice loop. On any error they return
an empty list / False and log a warning.

Config is loaded from echo_search.json (see load_search_config), mirroring the
fail-soft pattern of echo_sampler.json.
"""

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent / "echo_search.json"

# Fail-soft defaults if echo_search.json is missing/corrupt (mirrors llm._DEFAULT_SAMPLER).
_DEFAULT_CONFIG = {
    "web_search_enabled": True,
    "provider": "searxng",
    "searxng_base_url": "http://127.0.0.1:26",  # existing host Searxng container
    "categories": "general",
    "engines": "duckduckgo,brave",
    "top_k": 5,
    "timeout_s": 5,
    "filler_lines": [
        "Let me look that up, Michael.",
        "One moment — checking on that.",
    ],
}


def load_search_config() -> dict:
    """Load echo_search.json, falling back to documented defaults on any error."""
    config = dict(_DEFAULT_CONFIG)
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in _DEFAULT_CONFIG:
            if key in data:
                config[key] = data[key]
    except FileNotFoundError:
        logger.info("echo_search.json not found; using built-in search defaults")
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"echo_search.json unreadable ({e}); using built-in search defaults")
    return config


@dataclass
class SearchResult:
    """One result row. `content` (snippet) may be empty — parse defensively."""
    title: str
    url: str
    content: str
    engine: str
    score: float


class SearchProvider(ABC):
    """A swappable web-search backend."""

    @abstractmethod
    def search(self, query: str, *, top_k: int = 5) -> list[SearchResult]:
        """Return up to top_k results for `query`. Never raises — [] on any failure."""

    @abstractmethod
    def healthy(self) -> bool:
        """True if the backend answers a probe quickly. Never raises."""


class SearXNGProvider(SearchProvider):
    """SearXNG JSON API client.

    GET {base_url}/search?q=...&format=json&categories=...&language=en&pageno=1
    (optional &engines=duckduckgo,brave for stability). Results are already sorted
    by score desc; we parse defensively (fields are heterogeneous across engines).
    """

    def __init__(
        self,
        base_url: str,
        *,
        categories: str = "general",
        engines: str = "",
        timeout_s: float = 5.0,
        language: str = "en",
    ):
        self.base_url = base_url.rstrip("/")
        self.categories = categories
        self.engines = engines
        self.timeout_s = timeout_s
        self.language = language

    def _params(self, query: str) -> dict:
        params = {
            "q": query,
            "format": "json",
            "categories": self.categories,
            "language": self.language,
            "pageno": 1,
        }
        # Pinning a stable engine subset reduces empty/slow first-runs. Omit to use
        # SearXNG's category defaults.
        if self.engines:
            params["engines"] = self.engines
        return params

    def search(self, query: str, *, top_k: int = 5) -> list[SearchResult]:
        query = (query or "").strip()
        if not query:
            return []

        try:
            resp = httpx.get(
                f"{self.base_url}/search",
                params=self._params(query),
                timeout=self.timeout_s,
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException:
            logger.warning(f"SearXNG search timed out after {self.timeout_s}s")
            return []
        except httpx.HTTPStatusError as e:
            # 403 => limiter/bot-detection on OR settings.yml not mounted (PRD §4).
            logger.warning(f"SearXNG returned HTTP {e.response.status_code} (403 ⇒ limiter on / JSON off)")
            return []
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
            logger.warning(f"SearXNG search failed: {e}")
            return []

        raw_results = data.get("results") or []
        # `unresponsive_engines` is informational — surface it once for tuning, don't fail on it.
        unresponsive = data.get("unresponsive_engines") or []
        if unresponsive:
            logger.info(f"SearXNG unresponsive engines: {unresponsive}")

        results: list[SearchResult] = []
        for r in raw_results:
            if not isinstance(r, dict):
                continue
            url = (r.get("url") or "").strip()
            title = (r.get("title") or "").strip()
            if not url and not title:
                continue  # unusable row
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    content=(r.get("content") or "").strip(),
                    engine=(r.get("engine") or "").strip(),
                    score=_as_float(r.get("score")),
                )
            )
            if len(results) >= top_k:
                break

        return results

    def healthy(self) -> bool:
        """Probe with a trivial query. Warn (don't block) — search is optional at startup."""
        try:
            resp = httpx.get(
                f"{self.base_url}/search",
                params=self._params("ping"),
                timeout=min(self.timeout_s, 3.0),
                headers={"Accept": "application/json"},
            )
            return resp.status_code == 200
        except httpx.HTTPError as e:
            logger.warning(f"SearXNG health probe failed ({self.base_url}): {e}")
            return False


def _as_float(value) -> float:
    """SearXNG `score` is usually a float but can be missing/str across engines."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def build_provider(config: dict | None = None) -> SearchProvider | None:
    """Construct the configured provider, or None if web search is disabled.

    Only 'searxng' is implemented (PRD Non-Goal exception clause reserves the interface
    for a future keyless fallback like ddgs).
    """
    config = config or load_search_config()
    if not config.get("web_search_enabled", True):
        return None

    provider = config.get("provider", "searxng")
    if provider == "searxng":
        return SearXNGProvider(
            base_url=config.get("searxng_base_url", _DEFAULT_CONFIG["searxng_base_url"]),
            categories=config.get("categories", "general"),
            engines=config.get("engines", ""),
            timeout_s=float(config.get("timeout_s", 5)),
        )

    logger.warning(f"Unknown search provider '{provider}'; web search disabled")
    return None


def format_search_block(query: str, results: list[SearchResult]) -> str:
    """Build the compact system-prompt block Echo's character pass sees (PRD §7).

    She synthesizes from this in her own voice — never reads URLs or lists aloud.
    Empty results → a 'came up empty' block so she can decline gracefully in character.
    """
    if not results:
        return (
            "[web results — you looked this up just now]\n"
            f"You searched for \"{query}\" but nothing useful came back. Tell Michael you "
            "couldn't find it, in your own voice — don't apologize like an assistant."
        )

    lines = ["[web results — you looked these up just now]"]
    for i, r in enumerate(results, 1):
        snippet = r.content or "(no snippet)"
        src = f"  (source: {r.engine})" if r.engine else ""
        lines.append(f"{i}. {r.title} — {snippet}{src}")
    lines.append(
        "Answer Michael in your own voice. Synthesize what matters; do not read URLs, do "
        "not list results, do not say \"according to\". You looked it up — just tell him."
    )
    return "\n".join(lines)
