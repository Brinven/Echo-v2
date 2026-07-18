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

GATE_SYSTEM = """You are Echo's memory gate. After each conversation turn, decide if anything is
worth saving to Echo's LONG-TERM memory — things that will still be true and worth knowing weeks
from now.

Respond ONLY with ONE valid JSON object. No explanation, no markdown, no preamble. If several
things qualify, save the single most durable one — never emit more than one object.

If nothing is worth saving:
{"save": false}

If saving a durable fact about the person speaking, Michael, or the people, places, pets,
vehicles, or projects in their lives:
{"save": true, "type": "fact", "entity": "<entity>", "attribute": "<attribute>", "value": "<value>"}

If saving a stable personal preference:
{"save": true, "type": "preference", "key": "<key>", "value": "<value>"}

If saving a behavioral rule for Echo:
{"save": true, "type": "policy", "key": "<key>", "rule": "<rule>", "priority": <1-10>}

NEVER save (return {"save": false} for these):
- Momentary or "right now" state: what someone is doing this minute, a current task or action, a
  passing mood, "the house is quiet". Attributes like current_task, current_action, status are wrong.
- Anything looked up rather than lived: weather, forecasts, news, prices, scores, current events or
  conditions. Echo looks these up fresh every time — they must never become memories.
- Facts about Echo herself — her personality, goals, how she was built — or about the memory or
  software system. Echo's identity is fixed elsewhere. The entity is NEVER "Echo" or "the system".
- Smalltalk, greetings, or things Echo should already know.

Guidance:
- Only save NEW, durable information — not things already obvious or already known.
- The transcript labels who is speaking. Facts a person states about themselves use THEIR name
  as the entity — resolve "I", "my", "me" to the labelled speaker, never to Michael unless
  Michael is the one speaking.
- Use the person's plain name as the entity — "Michael", never "Michael's location" or similar.
- A fact about an animal, or a person other than Michael, should say WHAT they are — species,
  or relation to Michael — whenever the turn makes that clear. The anchor lives in the VALUE,
  never the entity: entity="Willie", attribute="personality",
  value="a goat; likes to tip things over". The entity stays the plain name — never
  "Anna (Michael's sister)". A bare name with no anchor ages badly.
- Be specific: "Jeep needs new shocks" not "car stuff".
- Facts use entity/attribute/value: entity="Michael", attribute="favorite_bird", value="crows".
- If uncertain, return {"save": false}.
"""

# Deterministic backstop (applied AFTER the model returns save=true). The tightened prompt is the
# primary defense; this catches the noise classes the model still occasionally emits, so junk can't
# reach the store even if the prompt is ignored. Facts only — preferences/policies are keyed and
# intentional. Kept in sync with the "NEVER save" list above.
_SELF_META_ENTITIES = {
    "echo", "the memory system", "memory system", "memory_system", "the system", "system",
    "ib-lite", "ib lite", "the assistant", "assistant", "the software",
}
_EPHEMERAL_ATTRS = {"status", "state", "activity", "mood", "current_status", "current_mood"}


def reject_reason(payload: dict) -> str | None:
    """Return why a save should be dropped as non-durable, or None to allow it.

    A deterministic net for facts: self/meta entities (Echo, the memory system) and ephemeral
    attributes (anything current_*, or a bare status/state/mood) are never durable memories,
    whatever the model decided. Preferences and policies pass through untouched.
    """
    if not isinstance(payload, dict) or payload.get("type") != "fact":
        return None
    entity = str(payload.get("entity", "")).strip().lower()
    attribute = str(payload.get("attribute", "")).strip().lower()
    if entity in _SELF_META_ENTITIES:
        return f"self/meta entity {entity!r}"
    if attribute.startswith("current_") or attribute in _EPHEMERAL_ATTRS:
        return f"ephemeral attribute {attribute!r}"
    return None


def _parse_json(raw: str) -> dict:
    """Best-effort extraction of a JSON object from an LLM response.

    The contract is ONE object, but the model occasionally emits two (live-caught
    2026-07-18: the anchoring guidance made two-fact turns more tempting, and a
    concatenated pair used to fail the whole parse — a silently dropped save).
    raw_decode from the first '{' salvages the FIRST object from concatenated
    output or trailing prose; the greedy regex stays as the last-ditch fallback.
    """
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned, flags=re.MULTILINE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    if start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(cleaned[start:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {"save": False, "_error": "json_parse_failed", "_raw": raw[:300]}


def _build_user_content(
    turn_text: str,
    searched: bool = False,
    correction: str | None = None,
    speaker: str = "Michael",
) -> str:
    """Assemble the gate's user message. Pure — offline-testable without a model.

    The speaker line only appears for a non-Michael speaker: Michael is the baseline the
    system prompt already assumes, and the solo path's prompt stays byte-identical.
    """
    user_content = f"Turn transcript:\n{turn_text}"
    who = (speaker or "Michael").strip()
    if who and who.lower() != "michael":
        user_content += (
            f"\n\n(The person speaking in this turn is {who}, not Michael — "
            f"resolve \"I\", \"my\", \"me\" to {who}.)"
        )
    if searched:
        user_content += (
            "\n\n(This turn used a web lookup. Anything factual in Echo's reply was looked up, not "
            "told to her — do NOT save weather, forecasts, news, prices, conditions, or other "
            "looked-up information. Only save something the speaker stated about themselves or "
            "their world.)"
        )
    if correction:
        user_content += (
            f"\n\nYour previous JSON was invalid: {correction}\n"
            "Respond again with corrected JSON only."
        )
    return user_content


def run_gate(
    turn_text: str,
    model: str,
    correction: str | None = None,
    searched: bool = False,
    speaker: str = "Michael",
    lm_base: str = LM_STUDIO_URL,
) -> dict:
    """Run the significance gate on a single turn.

    Args:
        turn_text: the turn transcript (user utterance + Echo reply).
        model: the LM Studio model id (auto-detected by the pipeline).
        correction: if set, a validation error from a prior attempt — the model
            is asked to re-emit corrected JSON.
        searched: True if this turn triggered a web lookup. Anything factual in Echo's
            reply was looked up, not lived, so the gate is told to ignore it — the fix
            for weather/news landing in memory as durable facts.
        speaker: who voice-ID resolved for this turn (Stage 6 Phase 2). Tells the gate
            to file "I have a headache" under Hillary when Hillary said it. The gate's
            JSON does NOT carry attribution — the pipeline stamps source_speaker from
            this same ground-truth value, so the model can't misattribute a write.

    Returns a parsed dict; {"save": false} on any failure (never raises).
    """
    client = OpenAI(base_url=lm_base, api_key="not-needed", timeout=10)

    user_content = _build_user_content(
        turn_text, searched=searched, correction=correction, speaker=speaker
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
