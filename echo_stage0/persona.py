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
) -> str:
    """Assemble the full per-turn system prompt.

    Order (PRD §4):
        PERSONA  →  CORE/POLICY slab  →  RETRIEVED MEMORY (if any)  →  ANTI-DRIFT ANCHOR

    Args:
        exchange_count: 1-based count of the exchange this prompt is being built for.
            The anchor is injected when exchange_count > 0 and exchange_count % 8 == 0
            (i.e. exchanges 8, 16, 24, ...).
        snark_level: 0-10; selects the snark context inside the persona block.
        core_block: Ib-Lite's build_context_block() output (voice guidance + Core +
            Policy + Preferences). Empty string when memory is unavailable.
        memory_block: Ib-Lite's per-turn retrieved Fact/Episodic block. Empty when
            retrieval returns nothing above threshold.

    Token budget (PRD §4): if the assembled prompt exceeds TOKEN_BUDGET, the memory
    block is trimmed (oldest/last lines dropped toward k=3). Persona, core, and policy
    are NEVER trimmed.
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

    trimmed_memory = _trim_memory_to_budget(persona, core_block, memory_block, anchor)
    return _join_blocks(persona, core_block, trimmed_memory, anchor)


def _join_blocks(persona: str, core_block: str, memory_block: str, anchor: str) -> str:
    """Join non-empty blocks with blank lines. Drops empties so no dangling headers."""
    parts = [persona]
    if core_block.strip():
        parts.append(core_block.strip())
    if memory_block.strip():
        parts.append(memory_block.strip())
    if anchor.strip():
        parts.append(anchor.strip())
    return "\n\n".join(parts)


def _trim_memory_to_budget(persona: str, core_block: str, memory_block: str, anchor: str) -> str:
    """Shorten ONLY the memory block until the assembled prompt fits TOKEN_BUDGET.

    Persona, core, and policy are never trimmed (PRD §4). The memory block is a header
    line followed by per-item lines; we drop trailing item lines (keeping the header and
    at most the top few items) until we're under budget or down to the header alone.
    """
    if not memory_block.strip():
        return memory_block

    full = _join_blocks(persona, core_block, memory_block, anchor)
    if _estimate_tokens(full) <= TOKEN_BUDGET:
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
        if _estimate_tokens(_join_blocks(persona, core_block, candidate, anchor)) <= TOKEN_BUDGET:
            return candidate

    return "\n".join([header] + items)
