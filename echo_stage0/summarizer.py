"""
Session summarizer for Echo.

Sends the full conversation to LM Studio for structured extraction.
Produces a JSON summary with a frozen schema that Stage 3+ depends on.

FROZEN SCHEMA FIELDS — do not rename:
  topics_discussed, facts_about_user, facts_general, action_items,
  explicitly_remembered, conversation_mood, summary_text

Stage 3 change: facts_general is now a list of {fact, source} objects
instead of flat strings. source is "model_knowledge" or "web_search".
"""

import json
import re
import logging
from openai import OpenAI, APITimeoutError

from llm import LLM_BASE_URL

logger = logging.getLogger(__name__)

SUMMARY_SYSTEM_PROMPT = (
    "You are a session summarizer. Extract structured information from "
    "the conversation below. Respond only with valid JSON matching the schema "
    "provided. Do not include any text outside the JSON object."
)

SUMMARY_USER_TEMPLATE = """Summarize this conversation and extract the following as JSON:
{{
  "topics_discussed": ["list of topics"],
  "facts_about_user": ["personal facts, preferences, opinions expressed by {user_name}"],
  "facts_general": [
    {{
      "fact": "the factual statement",
      "source": "model_knowledge or web_search"
    }}
  ],
  "action_items": ["things {user_name} wants to do or follow up on"],
  "conversation_mood": "one short phrase",
  "summary_text": "2-4 sentence summary"
}}

For facts_general source field:
- Use "model_knowledge" if Echo stated this from its own training data.
- Use "web_search" if the information came from a retrieved external source.
- Currently all facts will be "model_knowledge" — include the field anyway.

Conversation:
{conversation}"""

# Frozen schema fields — do not rename
_REQUIRED_FIELDS = [
    "topics_discussed",
    "facts_about_user",
    "facts_general",
    "action_items",
    "conversation_mood",
    "summary_text",
]

# Fields with defaults
_SCHEMA_DEFAULTS = {
    "topics_discussed": [],
    "facts_about_user": [],
    "facts_general": [],
    "action_items": [],
    "explicitly_remembered": [],
    "conversation_mood": "unknown",
    "summary_text": "",
}


def extract_json(raw: str) -> dict | None:
    """
    Extract a JSON object from an LLM response that may contain
    markdown fences, preamble text, or other noise.

    Returns parsed dict or None if extraction fails.
    """
    # Strip markdown code fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned, flags=re.MULTILINE)

    # Try parsing the cleaned text directly
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try to find a JSON object in the text (first { to last })
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None


def _normalize_facts_general(items: list) -> list[dict]:
    """
    Normalize facts_general to the Stage 3 format: [{fact, source}].

    Handles backward compatibility:
    - Flat strings (Stage 2) -> {"fact": str, "source": "model_knowledge"}
    - Dicts with fact/source -> pass through
    - Anything else -> convert to string and wrap
    """
    result = []
    for item in items:
        if isinstance(item, dict) and "fact" in item:
            # Already in Stage 3 format
            result.append({
                "fact": str(item["fact"]),
                "source": item.get("source", "model_knowledge"),
            })
        elif isinstance(item, str):
            # Stage 2 flat string -> default to model_knowledge
            result.append({
                "fact": item,
                "source": "model_knowledge",
            })
        else:
            # Unexpected format -> convert and flag
            result.append({
                "fact": str(item),
                "source": "model_knowledge",
            })
    return result


def validate_summary(data: dict) -> dict:
    """
    Ensure all frozen schema fields are present with correct types.
    Fills missing fields with defaults. Does NOT rename any fields.
    """
    result = dict(_SCHEMA_DEFAULTS)  # start with defaults
    result.update(data)  # overlay whatever the LLM returned

    # Ensure simple list fields are actually lists
    for field in ["topics_discussed", "facts_about_user",
                  "action_items", "explicitly_remembered"]:
        if not isinstance(result[field], list):
            result[field] = [str(result[field])] if result[field] else []

    # Normalize facts_general to [{fact, source}] format
    if not isinstance(result["facts_general"], list):
        result["facts_general"] = [result["facts_general"]] if result["facts_general"] else []
    result["facts_general"] = _normalize_facts_general(result["facts_general"])

    # Ensure string fields are strings
    for field in ["conversation_mood", "summary_text"]:
        if not isinstance(result[field], str):
            result[field] = str(result[field]) if result[field] else ""

    return result


def generate_summary(
    conversation_text: str,
    user_name: str,
    model: str,
    explicitly_remembered: list[str] | None = None,
) -> dict:
    """
    Call LM Studio to produce a structured session summary.

    Args:
        conversation_text: Formatted conversation turns
        user_name: User's name for the prompt
        model: LM Studio model ID
        explicitly_remembered: List of facts stored via Path A during session

    Returns:
        Validated summary dict with all frozen schema fields
    """
    client = OpenAI(
        base_url=LLM_BASE_URL,
        api_key="not-needed",
        timeout=60,  # generous timeout — correctness > speed
    )

    user_prompt = SUMMARY_USER_TEMPLATE.format(
        user_name=user_name or "the user",
        conversation=conversation_text,
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            stream=False,
        )
        raw_text = response.choices[0].message.content.strip()
    except APITimeoutError:
        logger.error("Summary LLM call timed out after 60s")
        return _error_summary("LLM timeout during summary generation")
    except Exception as e:
        logger.error(f"Summary LLM call failed: {e}")
        return _error_summary(f"LLM error: {e}")

    # Parse JSON from response
    parsed = extract_json(raw_text)
    if parsed is None:
        logger.error(f"Failed to parse summary JSON. Raw response:\n{raw_text}")
        summary = _error_summary("JSON parsing failed")
        summary["_raw_response"] = raw_text
        return summary

    result = validate_summary(parsed)

    # Populate explicitly_remembered from Path A writes this session
    if explicitly_remembered:
        result["explicitly_remembered"] = list(explicitly_remembered)

    return result


def _error_summary(error_msg: str) -> dict:
    """Produce a minimal valid summary when the LLM fails."""
    result = dict(_SCHEMA_DEFAULTS)
    result["summary_text"] = f"[Summary generation failed: {error_msg}]"
    result["_error"] = error_msg
    return result
