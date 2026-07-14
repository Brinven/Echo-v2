"""
Search-decision step for Echo's web search (Stage 5 Part 3, PRD §6).

Two stages, cheap-to-expensive:

  Stage A — keyword pre-filter (no LLM): skip the decision call on obviously
    conversational turns. Recall-biased: when in doubt, fall through to Stage B
    (a missed search is worse than a spent ~1s).

  Stage B — decision call: a SEPARATE reasoning-isolated LLM call (its own system
    prompt, reasoning_effort="none") that returns {"search": false} or
    {"search": true, "query": "..."}. Mirrors ib_lite/significance.py:run_gate —
    temperature 0.1, best-effort JSON parse, NEVER raises ({"search": false} on any
    failure), empty-content guard for the Gemma-QAT thinking-model gotcha.

CoT isolation (PRD §6): the query is constructed HERE, in a separate call. Echo's
character pass only ever sees the results, never this reasoning — so searching can
never push her off-character.
"""

import re
import json
import time
import logging

from openai import OpenAI, APITimeoutError

logger = logging.getLogger(__name__)

LM_STUDIO_URL = "http://127.0.0.1:1234/v1"

# ── Stage A: keyword pre-filter ──────────────────────────────────────────────
# Lookup signals (PRD §6). Recall-biased: generous set; over-firing just costs a
# cheap Stage B call, under-firing silently drops a needed search.

_INTERROGATIVES = re.compile(
    r"\b(what|who|when|where|which|whose|how)\b", re.IGNORECASE
)

# Freshness / factual / lookup markers. Substring-matched (word-boundary where it matters).
_LOOKUP_MARKERS = re.compile(
    r"\b("
    r"latest|current|currently|today|tonight|right now|this (week|month|year)|"
    r"news|headlines|look up|search|google|find out|"
    r"who is|who's|when did|when is|when was|how much|how many|how far|how tall|how old|"
    r"price|cost|worth|weather|forecast|temperature|score|standings|results|"
    r"stock|exchange rate|open now|hours|showtimes|release date|"
    r"population|capital of|distance|"
    r"in \d{4}|\b(19|20)\d{2}\b"  # a year mentioned
    r")\b",
    re.IGNORECASE,
)


# Pure-greeting / smalltalk openers that are never a lookup, even though they contain an
# interrogative ("how are you doing"). Skips a needless Stage B call on the common path.
_GREETING = re.compile(
    r"^\s*(?:(?:hey|hi|hello|yo|echo|so|well|okay|ok)[\s,]+)*"
    r"(?:how(?:'?s| is| are| have)?\s+(?:you|ya|it|things|your\s+day|you\s+been|it\s+going)"
    r"|what'?s\s+up|what'?s\s+new|good\s+(?:morning|afternoon|evening)|you\s+(?:there|awake|up))"
    r"\b",
    re.IGNORECASE,
)


def prefilter_hit(transcript: str) -> bool:
    """Stage A: True if the turn carries lookup signals worth a decision call.

    Recall-biased — trigger on either a strong lookup marker OR an interrogative
    paired with any content that reads like a factual query. Conversational turns
    ("how are you doing", "that's funny", "I'm tired") fall through to False.

    Order matters: a lookup marker ALWAYS wins (so "how are you — and what's the
    weather" still searches), then greetings are excluded, then the interrogative
    heuristic applies.
    """
    t = (transcript or "").strip()
    if not t:
        return False

    if _LOOKUP_MARKERS.search(t):
        return True

    # Pure greeting with no lookup marker → never a search; spare the Stage B call.
    if _GREETING.match(t):
        return False

    # An interrogative alone is weak, but an interrogative in a longer, non-greeting
    # question is worth the cheap decision call. Bias toward recall: let Stage B judge.
    if _INTERROGATIVES.search(t) and len(t.split()) >= 4:
        return True

    return False


# ── Stage B: decision call ───────────────────────────────────────────────────

DECISION_SYSTEM = """You decide whether answering the user needs fresh or external web
information, and if so write the best search query. Respond ONLY with JSON. No
explanation, no markdown, no preamble.

No web needed:   {"search": false}
Web needed:      {"search": true, "query": "<concise search query>"}

Rules:
- Prefer false for opinions, feelings, personal topics, small talk, and things a
  companion already knows about the user or the world.
- Prefer true for current events, prices, weather, sports scores, news, release dates,
  facts you are unsure of, or anything time-sensitive.
- The query should be concise and search-engine friendly, not a full sentence.
- If uncertain whether a real lookup helps, return {"search": false}."""


def _parse_json(raw: str) -> dict:
    """Best-effort extraction of a JSON object (same shape as significance._parse_json)."""
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned, flags=re.MULTILINE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {"search": False, "_error": "json_parse_failed", "_raw": raw[:200]}


def _run_decision_call(transcript: str, model: str, lm_base: str) -> dict:
    """Stage B only. Returns a parsed dict; {"search": false} on any failure (never raises)."""
    client = OpenAI(base_url=lm_base, api_key="not-needed", timeout=10)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": DECISION_SYSTEM},
                {"role": "user", "content": transcript},
            ],
            temperature=0.1,
            max_tokens=60,
            # Same Gemma-QAT thinking-model gotcha as the gate: without this it burns the
            # whole budget in reasoning_content and returns empty content. "none" => clean JSON.
            reasoning_effort="none",
            stream=False,
        )
    except APITimeoutError:
        logger.warning("search-decision call timed out")
        return {"search": False, "_error": "timeout"}
    except Exception as e:
        logger.error(f"search-decision call failed: {e}")
        return {"search": False, "_error": str(e)}

    raw = (resp.choices[0].message.content or "").strip()
    if not raw:
        # Empty-content guard: reasoning likely not actually disabled for this template.
        logger.warning("search-decision returned empty content (reasoning may not be off)")
        return {"search": False, "_error": "empty_content"}
    return _parse_json(raw)


def decide_search(
    transcript: str,
    model: str,
    *,
    lm_base: str = LM_STUDIO_URL,
) -> dict:
    """Full decision: Stage A pre-filter → Stage B call (only if A hits).

    Returns a dict always shaped:
        {"search": bool, "query": str|None, "prefilter_hit": bool, "decision_ms": float}

    Never raises. If the pre-filter misses, Stage B is skipped entirely (search=False,
    decision_ms=0). A malformed/uncertain Stage B result degrades to search=False.
    """
    hit = prefilter_hit(transcript)
    if not hit:
        return {"search": False, "query": None, "prefilter_hit": False, "decision_ms": 0.0}

    t0 = time.perf_counter()
    result = _run_decision_call(transcript, model, lm_base)
    decision_ms = (time.perf_counter() - t0) * 1000.0

    do_search = bool(result.get("search"))
    query = result.get("query") if do_search else None
    # Defensive: search=true but no/empty query → treat as no search (nothing to run).
    if do_search and not (isinstance(query, str) and query.strip()):
        do_search = False
        query = None

    return {
        "search": do_search,
        "query": query.strip() if query else None,
        "prefilter_hit": True,
        "decision_ms": decision_ms,
    }
