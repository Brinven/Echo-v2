"""
Significance gate for Ib-Lite.

After each turn, a SEPARATE LLM call (its own system prompt, isolated from
Echo's personality pass) decides whether anything is worth saving and, if so,
returns a strict JSON payload. Runs at temperature 0.1 for near-deterministic
output. Always 127.0.0.1 (localhost adds ~2s DNS penalty on Windows).

This call is NOT on the hot path — IbLite fires it on a background thread after
the turn's audio is delivered.
"""

import re
import json
import logging

from openai import OpenAI, APITimeoutError

logger = logging.getLogger(__name__)

LM_STUDIO_URL = "http://127.0.0.1:1234/v1"

GATE_SYSTEM = """You are Echo's memory gate. After each conversation turn, decide
if anything is worth saving to long-term memory.

Respond ONLY with valid JSON. No explanation, no markdown, no preamble.

If nothing is worth saving:
{"save": false}

If saving a durable fact about Michael or his world:
{"save": true, "type": "fact", "entity": "<entity>", "attribute": "<attribute>", "value": "<value>"}

If saving a personal preference:
{"save": true, "type": "preference", "key": "<key>", "value": "<value>"}

If saving a behavioral rule for Echo:
{"save": true, "type": "policy", "key": "<key>", "rule": "<rule>", "priority": <1-10>}

Rules:
- Only save NEW information, not things already obvious or already known.
- Do not save smalltalk, greetings, or things Echo should already know.
- Be specific: "Jeep needs new shocks" not "car stuff".
- Facts use entity/attribute/value: entity="Michael", attribute="favorite_bird", value="crows".
- If uncertain, return {"save": false}.
"""


def _parse_json(raw: str) -> dict:
    """Best-effort extraction of a JSON object from an LLM response."""
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
    return {"save": False, "_error": "json_parse_failed", "_raw": raw[:300]}


def run_gate(
    turn_text: str,
    model: str,
    correction: str | None = None,
    lm_base: str = LM_STUDIO_URL,
) -> dict:
    """Run the significance gate on a single turn.

    Args:
        turn_text: the turn transcript (user utterance + Echo reply).
        model: the LM Studio model id (auto-detected by the pipeline).
        correction: if set, a validation error from a prior attempt — the model
            is asked to re-emit corrected JSON.

    Returns a parsed dict; {"save": false} on any failure (never raises).
    """
    client = OpenAI(base_url=lm_base, api_key="not-needed", timeout=10)

    user_content = f"Turn transcript:\n{turn_text}"
    if correction:
        user_content += (
            f"\n\nYour previous JSON was invalid: {correction}\n"
            "Respond again with corrected JSON only."
        )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": GATE_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            temperature=0.1,
            max_tokens=150,
            # Gemma 4 12B QAT is a THINKING model in LM Studio: without this it
            # spends the whole token budget in reasoning_content and returns an
            # empty `content` (finish_reason=length). reasoning_effort="none"
            # disables thinking -> clean JSON in ~1s. (low / enable_thinking flags
            # do NOT work for this template; only "none" does.)
            reasoning_effort="none",
            stream=False,
        )
    except APITimeoutError:
        logger.warning("significance gate timed out")
        return {"save": False, "_error": "timeout"}
    except Exception as e:
        logger.error(f"significance gate call failed: {e}")
        return {"save": False, "_error": str(e)}

    raw = (resp.choices[0].message.content or "").strip()
    return _parse_json(raw)
