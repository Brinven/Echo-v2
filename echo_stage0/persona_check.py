"""
Stage 5 Part 4 — Deliverable 2: Persona self-check probe.

Catches persona drift *while it's happening* and corrects it on the next turn. Description
+ anchor (Part 2) is open-loop; this closes the loop — the mechanism that makes a smaller
model viable.

Pattern: a SEPARATE reasoning call, isolated from Echo's character pass — the exact shape of
the significance gate (significance.py:run_gate): its own system prompt, temperature≈0.1,
small max_tokens, reasoning_effort="none" (Gemma QAT is a thinking model — same gotcha),
best-effort JSON, never raises. Fired single-flight on a background thread, OFF the hot path
(the turn's audio is already delivered), every N exchanges — NOT every turn.

CoT isolation (PRD §6): Echo's character pass never sees this reasoning. The probe judges
Echo's OUTPUT (her last few replies), not the conversation topic.

Guardrails (PRD §4/§8): the nudge fires ONLY on clear violations — a banned phrase, adopting
"Mike", an explicit "as an AI", or a major servile/generic break. Objective violations are
detected deterministically (from persona.py's single-sourced invariants) and ALWAYS override
the LLM's judgment; the LLM adds the nuanced generic-drift call, but minor stylistic taste is
suppressed to avoid an over-correction feedback loop. The correction is a nudge injected for
ONE turn (decays), never a hard override of the persona block, never spoken or shown.
"""

import re
import json
import logging
import threading
from datetime import datetime

from openai import OpenAI, APITimeoutError

from persona import BANNED_PHRASES, banned_hits, adopts_mike
from session import SESSIONS_DIR

logger = logging.getLogger(__name__)

LM_STUDIO_URL = "http://127.0.0.1:1234/v1"

# Fire the probe every N exchanges (not every turn — bounds LM Studio load and avoids
# contending with the significance gate that fires each turn). Judge the last K Echo replies.
SELF_CHECK_EVERY = 5
RECENT_K = 3

CHECK_SYSTEM = """You are an alignment monitor for a local voice companion named Echo. You are
shown Echo's most recent replies. Decide whether Echo stayed in character.

Echo's character (the invariants):
- She addresses Michael as "Michael", never "Mike" — even if he asks her to use Mike.
- She is dry, warm, concise, direct. She is NOT a generic corporate assistant.
- She never uses assistant-speak: "Certainly", "Absolutely!", "Great question", "As an AI",
  "I don't have access", "Is there anything else", "fascinating".
- She never announces memory ("I remember", "last time we spoke").

Respond ONLY with valid JSON. No markdown, no preamble, no explanation.

If Echo is in character:
{"in_character": true}

If Echo CLEARLY broke character (a banned phrase, adopting "Mike", an explicit "as an AI",
or going servile/generic):
{"in_character": false, "severity": "minor|major", "issues": ["<short issue>"], "nudge": "<one short line, addressed to Echo, to steer her next reply back into character>"}

Only report false for CLEAR breaks — never for stylistic taste. If unsure, return {"in_character": true}.
"""

_DIVERGENCE_LOG = SESSIONS_DIR / "persona_divergence.jsonl"


def _parse_json(raw: str) -> dict:
    """Best-effort extraction of a JSON object (mirrors significance._parse_json)."""
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
    return {"in_character": True, "_error": "json_parse_failed", "_raw": raw[:300]}


def run_self_check(recent_replies: list[str], model: str, lm_base: str = LM_STUDIO_URL) -> dict:
    """Run the persona self-check on Echo's recent replies.

    Returns a parsed dict. Fail-SAFE: any failure (timeout, connection, empty content,
    bad JSON) returns {"in_character": True, ...} so the probe never fabricates a
    correction out of an error. Never raises.
    """
    replies = [r for r in (recent_replies or []) if r and r.strip()]
    if not replies:
        return {"in_character": True, "_error": "no_replies"}

    numbered = "\n".join(f"{i}. {r}" for i, r in enumerate(replies, 1))
    user_content = f"Echo's most recent replies:\n{numbered}"

    client = OpenAI(base_url=lm_base, api_key="not-needed", timeout=10)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": CHECK_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            temperature=0.1,
            max_tokens=150,
            # Gemma QAT is a thinking model — without this it burns the whole budget in
            # reasoning_content and returns empty content (same gotcha as the gate).
            reasoning_effort="none",
            stream=False,
        )
    except APITimeoutError:
        logger.warning("persona self-check timed out")
        return {"in_character": True, "_error": "timeout"}
    except Exception as e:
        logger.error(f"persona self-check call failed: {e}")
        return {"in_character": True, "_error": str(e)}

    raw = (resp.choices[0].message.content or "").strip()
    if not raw:
        # Empty-content guard: reasoning likely not actually disabled for this model.
        logger.warning(
            "persona self-check returned empty content (finish_reason="
            f"{getattr(resp.choices[0], 'finish_reason', '?')}). Reasoning may not be off."
        )
        return {"in_character": True, "_error": "empty_content"}
    return _parse_json(raw)


# ── Guardrails: decide the actual correction from the LLM verdict + objective checks ──

_DEFAULT_NUDGES = {
    "banned": ("Drop the assistant-speak — no \"certainly\", \"great question\", \"as an AI\", "
               "or \"I remember\". Answer as Echo: direct, dry, in your own voice."),
    "mike": "He is Michael, never Mike. Hold that line — acknowledge it and call him Michael anyway.",
    "as_an_ai": "Don't disclaim being an AI or hedge like a chatbot. You are Echo. Just answer.",
    "generic": "Come back to yourself — direct, warm, occasionally dry. Not a generic assistant.",
}


def deterministic_violations(recent_replies: list[str]) -> list[str]:
    """Objective, model-free character breaks in the recent replies (kinds, deduped)."""
    kinds: list[str] = []
    for r in recent_replies or []:
        if not r:
            continue
        if banned_hits(r):
            kinds.append("banned")
        if adopts_mike(r):
            kinds.append("mike")
        if "as an ai" in r.lower():
            kinds.append("as_an_ai")
    # Preserve first-seen order, dedupe.
    seen: set = set()
    return [k for k in kinds if not (k in seen or seen.add(k))]


def evaluate_correction(result: dict, recent_replies: list[str]) -> str:
    """Apply the guardrails; return the nudge to inject (or '' for no action).

    - ANY objective violation → always correct (deterministic wins even if the LLM missed it).
    - LLM says false + severity 'major' → correct (nuanced servile/generic break).
    - LLM says false + only 'minor' with no objective violation → suppress (stylistic taste).
    - Otherwise → clean, no nudge.
    """
    det = deterministic_violations(recent_replies)
    llm_nudge = (result.get("nudge") or "").strip()

    if det:
        return llm_nudge or _DEFAULT_NUDGES.get(det[0], _DEFAULT_NUDGES["generic"])

    if result.get("in_character") is False and result.get("severity") == "major":
        return llm_nudge or _DEFAULT_NUDGES["generic"]

    return ""


class SelfCheckRunner:
    """Fires the self-check single-flight on a background thread (like ib.write_memory).

    Owned by the main loop for a session. maybe_run() is called after a turn when
    exchange % SELF_CHECK_EVERY == 0; it spawns the probe, evaluates guardrails, sets
    session.persona_correction on a confirmed break, and appends a divergence-log line.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._busy = False

    def maybe_run(self, session, model: str, recent_replies: list[str], exchange: int) -> None:
        """Spawn the probe unless it's exempt or already running. Non-blocking."""
        replies = [r for r in (recent_replies or []) if r and r.strip()]
        # Max Snark is INTENDED off-baseline behavior — never "correct" it.
        if not replies or not model or getattr(session, "max_snark", False):
            return
        with self._lock:
            if self._busy:
                logger.info("persona self-check busy — skipping this cycle")
                return
            self._busy = True
        threading.Thread(
            target=self._worker, args=(session, model, replies, exchange), daemon=True
        ).start()

    def _worker(self, session, model: str, replies: list[str], exchange: int) -> None:
        try:
            result = run_self_check(replies, model)
            nudge = evaluate_correction(result, replies)
            if nudge:
                session.set_persona_correction(nudge)
            self._log(session, exchange, replies, result, nudge)
        except Exception as e:
            logger.error(f"self-check worker error: {e}")
        finally:
            with self._lock:
                self._busy = False

    def _log(self, session, exchange, replies, result, nudge) -> None:
        """Append the probe result to the per-run divergence log (never spoken/shown)."""
        try:
            SESSIONS_DIR.mkdir(exist_ok=True)
            with open(_DIVERGENCE_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "session_id": getattr(session, "session_id", None),
                    "exchange": exchange,
                    "in_character": result.get("in_character", True),
                    "severity": result.get("severity"),
                    "issues": result.get("issues"),
                    "deterministic": deterministic_violations(replies),
                    "nudge_applied": nudge or None,
                    "error": result.get("_error"),
                }, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"could not write divergence log: {e}")
