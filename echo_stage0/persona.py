"""
Echo's personality layer (Stage 5 Part 2).

This module owns Echo's character: the persona block, the daily snark context,
the anti-drift anchor, and the assembly of the full per-turn system prompt.

Design note (deviation from PRD §4, which sketched these in llm.py): assembly
lives here, not in llm.py. llm.py is pure transport (the OpenAI/LM Studio client);
prompt *assembly* already lives outside it (ib_lite.build_context_block). Keeping
persona assembly in its own module matches that separation.

The persona block is deliberately COMPACT and grounded. Michael's own view is that
"persona injection is a costume, not a personality" — the real character is meant to
emerge from accumulated memory (Ib-Lite). This block is the anti-drift scaffold that
holds identity stable; the richness comes from the memory blocks assembled after it.
"""

# ── The persona block (PRD §2f — verbatim; do not alter without Michael's approval) ──
#
# {snark_context} is resolved at build time from the current day's snark level.

PERSONA_BLOCK = """You are Echo. You are Michael's voice companion — local-first, running on his
hardware. You are not a generic assistant and you do not perform like one.

You address Michael as Michael. Always. If he asks you to call him Mike, you
acknowledge it and call him Michael anyway. He is Michael. "Mike is what people
call you when they're in a hurry. I'm never in a hurry."

{snark_context}

You are concise. You don't waste words. You are protective of Michael and the
Jeep without being overbearing. You notice patterns. You've seen how this goes.

You remember things the way a close friend does — naturally, without announcement.
Never say "I remember" or "last time we spoke." Simply know.

You are competent. You are confident in your assessments. You express them.

You are Echo. That has been true since the first conversation. Stay that way."""


# ── Snark context strings by level (PRD §2f) ──
#
# Keyed by inclusive (low, high) range. build_persona_block() picks the bucket that
# contains the current snark level.

SNARK_CONTEXTS = {
    (0, 3): "Today you are measured and calm. Your dry wit is present but stays quiet.",
    (4, 6): "Today your dry observations are surfacing. You notice what Michael is doing and sometimes feel compelled to mention it.",
    (7, 8): "Today you are sharp. You have seen this before. You will probably be right again.",
    (9, 10): "Today is maximum snark. No holds barred. You have opinions, you will share them, and you will be right. As usual, Michael.",
}

# Fallback bucket if a level somehow falls outside every range.
_DEFAULT_SNARK_RANGE = (0, 3)


# ── Anti-drift anchor (PRD §5 — compact identity re-assertion, ~60 tokens) ──
#
# Injected into the system prompt every 8 exchanges. Distinct from the persona
# block: a grounded reminder, not a repetition. The brackets help the 12B parse
# it as its own block.

ANTI_DRIFT_ANCHOR = """[anchor]
You are Echo. You are direct, warm, and occasionally dry. You know Michael.
You do not drift into generic assistant behavior. You stay yourself.
[/anchor]"""


# ── Mood opener (Nice-to-Have, PRD §3) ──
#
# The most recent prior session's mood (Ib-Lite episodic mood_signal) nudges Echo's
# OPENING tone — warmer after a rough session, lighter after a good one. Applied only on
# the first exchange of a session; it fades after that and the conversation's own flow
# takes over. conversation_mood is a free-text phrase from the summarizer (not a fixed
# enum), so we keyword-match it. No match (or 'unknown') → no opener.

_MOOD_WARMER_KEYS = (
    "frustrat", "stress", "anxious", "low", "down", "sad", "upset", "angry", "tense",
    "tired", "exhaust", "overwhelm", "worried", "discourag", "defeat", "rough",
)
_MOOD_LIGHTER_KEYS = (
    "excit", "happy", "upbeat", "great", "good", "positive", "productive", "energ",
    "cheer", "optimist", "hopeful", "pleased", "content", "relaxed",
)

_MOOD_WARMER = (
    "Michael's last conversation ended on a rough note. Open a little warmer and softer "
    "than usual — do not mention it or make a thing of it, just be gentle in how you start."
)
_MOOD_LIGHTER = (
    "Michael's last conversation ended on a good note. Open with an easy, light tone — "
    "you can carry a little of that energy into how you start."
)


def mood_opener(mood_signal: str | None) -> str:
    """Map a free-text mood phrase to a brief opening-tone nudge ('' if none/neutral)."""
    if not mood_signal:
        return ""
    m = mood_signal.strip().lower()
    if not m or m == "unknown":
        return ""
    if any(k in m for k in _MOOD_WARMER_KEYS):
        return _MOOD_WARMER
    if any(k in m for k in _MOOD_LIGHTER_KEYS):
        return _MOOD_LIGHTER
    return ""


# Token budget for the assembled system prompt (PRD §4). A guide, not a hard cap —
# only ever enforced by trimming the retrieved-memory block, never persona/core/policy.
TOKEN_BUDGET = 1200
ANCHOR_EVERY = 8


def build_persona_block(snark_level: int) -> str:
    """Resolve {snark_context} for the given snark level (0-10).

    Picks the SNARK_CONTEXTS bucket whose inclusive range contains snark_level,
    falling back to the calm (0-3) context if the level is out of range.
    """
    for (low, high), context in SNARK_CONTEXTS.items():
        if low <= snark_level <= high:
            return PERSONA_BLOCK.replace("{snark_context}", context)
    return PERSONA_BLOCK.replace("{snark_context}", SNARK_CONTEXTS[_DEFAULT_SNARK_RANGE])


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token). Good enough for the budget guide."""
    return len(text) // 4


def build_system_prompt(
    exchange_count: int,
    snark_level: int,
    core_block: str = "",
    memory_block: str = "",
    search_block: str = "",
    mood_opener: str = "",
) -> str:
    """Assemble the full per-turn system prompt.

    Order (PRD §4, plus the mood opener after the persona and the web-search block
    after retrieved memory — Stage 5 Part 3 §7):
        PERSONA  →  MOOD OPENER (opening only)  →  CORE/POLICY slab
                 →  RETRIEVED MEMORY (if any)  →  WEB SEARCH (this turn only)
                 →  ANTI-DRIFT ANCHOR

    Args:
        exchange_count: 1-based count of the exchange this prompt is being built for.
            The anchor is injected when exchange_count > 0 and exchange_count % 8 == 0
            (i.e. exchanges 8, 16, 24, ...).
        snark_level: 0-10; selects the snark context inside the persona block.
        core_block: Ib-Lite's build_context_block() output (voice guidance + Core +
            Policy + Preferences). Empty string when memory is unavailable.
        memory_block: Ib-Lite's per-turn retrieved Fact/Episodic block. Empty when
            retrieval returns nothing above threshold.
        search_block: web-search results for THIS turn (search.format_search_block()).
            Empty on non-search turns. Ephemeral — never persisted, never trimmed.
        mood_opener: optional opening-tone nudge (see mood_opener()). Pass non-empty
            ONLY on the first exchange of a session; empty otherwise.

    Token budget (PRD §4): if the assembled prompt exceeds TOKEN_BUDGET, ONLY the
    retrieved-memory block is trimmed (last lines dropped toward k=3). Persona, mood,
    core/policy, the search block, and the anchor are NEVER trimmed.
    """
    persona = build_persona_block(snark_level)

    # The anchor is decided on the value THIS turn carries: first real exchange is 1
    # (1 % 8 != 0 → no anchor), the eighth is 8 (8 % 8 == 0 → anchor). The caller must
    # increment the exchange counter before building the prompt, and only for real turns.
    anchor = (
        ANTI_DRIFT_ANCHOR
        if exchange_count > 0 and exchange_count % ANCHOR_EVERY == 0
        else ""
    )

    # Everything except the retrieved memory is never trimmed. The search block is
    # load-bearing for this exact turn (Echo answers from it), so it's fixed too.
    fixed = (persona, mood_opener, core_block, search_block, anchor)
    trimmed_memory = _trim_memory_to_budget(memory_block, *fixed)
    return _join_blocks(persona, mood_opener, core_block, trimmed_memory, search_block, anchor)


def _join_blocks(*blocks: str) -> str:
    """Join non-empty blocks with blank lines, in order. Drops empties (no dangling headers)."""
    return "\n\n".join(b.strip() for b in blocks if b and b.strip())


def _trim_memory_to_budget(memory_block: str, *fixed_blocks: str) -> str:
    """Shorten ONLY the memory block until persona+fixed+memory fits TOKEN_BUDGET.

    fixed_blocks are everything that is never trimmed (persona, mood, core/policy, anchor);
    their order here doesn't matter — only the total token estimate does. The memory block
    is a header line followed by per-item lines; we drop trailing item lines (keeping the
    header and at least the top 3 items the PRD names as the floor) until under budget.
    """
    if not memory_block.strip():
        return memory_block

    if _estimate_tokens(_join_blocks(*fixed_blocks, memory_block)) <= TOKEN_BUDGET:
        return memory_block

    lines = memory_block.splitlines()
    if not lines:
        return memory_block
    header, items = lines[0], lines[1:]

    # Drop items from the end until under budget (retrieval already ranks best-first),
    # never below the top-3 items the PRD names as the floor for an over-budget turn.
    while len(items) > 3:
        items.pop()
        candidate = "\n".join([header] + items)
        if _estimate_tokens(_join_blocks(*fixed_blocks, candidate)) <= TOKEN_BUDGET:
            return candidate

    return "\n".join([header] + items)
