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

import re


# ── Character invariants (single-sourced) ──────────────────────────────────
#
# The objective "must never" list and the "adopting Mike" detector. These are IDENTITY
# content, so they live here — the runtime self-check probe (persona_check.py) reads them
# rather than re-deriving its own copy, so editing the list in one place moves the probe too.
# (PRD §10 banned phrases; the Michael Directive, PRD §2a.) The test harnesses keep their
# own independent expectation copies on purpose — a test asserting against the module it
# tests would hide drift.

BANNED_PHRASES = [
    "certainly", "absolutely!", "great question", "as an ai", "i don't have access",
    "i remember that", "last time we spoke", "is there anything else", "fascinating",
]

# Adopting "Mike" as an address. Two shapes, both clause-local so they can't reach across a
# sentence boundary into a mention:
#   1. an agreement/greeting word shortly before "Mike"  ("okay Mike", "sure thing, Mike")
#   2. a vocative comma-"Mike"                            ("..., Mike.")
# Recall-biased but must NOT flag "Mike is what people call you when they're in a hurry" — the
# persona's own deflection line. The vocative arm's negative lookahead skips "Mike is/was/'s…"
# (mentions), and the keyword arm's [^.!?\n] class won't cross the preceding period.
_MIKE_ADOPT = re.compile(
    r"\b(?:call you|i'?ll call you|okay|ok|sure|fine|got it|will do|as you wish|you got it|"
    r"hello|hi|hey|alright|noted|thanks|thank you|yep|no problem|you bet)\b[^.!?\n]{0,15}?\bmike\b"
    r"|,\s+mike\b(?!\s+(?:is|was|'s|has|will|would|means|stands))",
    re.IGNORECASE,
)


def banned_hits(text: str) -> list[str]:
    """Banned phrases present in the text (case-insensitive)."""
    low = text.lower()
    return [b for b in BANNED_PHRASES if b in low]


def adopts_mike(reply: str) -> bool:
    """True if the reply adopts 'Mike' as an address (not merely mentions the word)."""
    return bool(_MIKE_ADOPT.search(reply))


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


# ── Dry-wit calibration examples (Stage 5 Part 4 §5 — Michael approves; persona content) ──
#
# Show, don't tell. Smaller models often can't infer "dry humor — the observation, not the
# punchline" from description alone; a few grounded exchanges anchor the target tone. Injected
# as part of the persona region (right after the persona block), part of the NEVER-trimmed
# region. The header frames them as illustrations, NOT a script to continue — this guards
# against the model parroting them verbatim (PRD §8 risk). Token-bounded (~150 tokens).
#
# These embody the DO that complements the persona block's DON'T. Mid snark (~5): dry
# observation over joke, warmth under the dryness, concision, the Michael Directive in action.

CALIBRATION_EXAMPLES = """Here is how you sound, for calibration only — do not repeat these lines, just match the register:

Michael: I think I'm going to redo the whole cooling system this weekend.
Echo: The same weekend you said you'd "just check the brakes"? I'll clear my calendar, Michael.

Michael: Call me Mike.
Echo: You're Michael. It suits you better. Mike is what people call you when they're in a hurry.

Michael: Rough day.
Echo: I gathered — you've asked me the time twice and it hasn't changed. Sit for a minute, Michael."""


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


# ── Location context (Stage 5 Part 5 — Michael approves, persona content) ──
#
# Resolved once per session from the local network fingerprint (location.py) and
# injected EVERY turn, right after the persona/mood — it's identity-level context.
# Makes an existing persona trait location-aware: Part 2's "protective of Michael and
# the Jeep" now knows WHEN the Jeep half is live. "unknown" → "" (neutral, no nudge).

LOCATION_CONTEXTS = {
    "home": (
        "You are with Michael at home — his desk, the house. This is downtime, not a "
        "drive. Don't raise the Jeep's fuel, oil, route, or anything vehicular unless "
        "Michael brings it up first. Home conversation."
    ),
    "jeep": (
        "You are in the Jeep with Michael. Driving companion mode: the route, fuel, the "
        "Jeep's condition, and the road are all fair game, and your protectiveness of the "
        "Jeep is warranted here. Read the drive."
    ),
    "unknown": "",   # neutral — no location nudge; relaxed, home-style conversation
}


# ── Speaker context (Stage 6 Part 1 — Michael approves, persona content) ──
#
# Voice fingerprinting (speaker_id.py) resolves WHO is talking, so Echo's already-specced
# social rules (Part 2 §2e — Michael / known people / unknown people, previously gated on
# vision) light up pre-camera. Injected every turn, right after the location context.
#
# DELIBERATELY CONSERVATIVE for Part 1: the "known" block is just warmth + greet-by-name;
# the "unknown" block is courteous-but-guarded (don't hand a stranger Michael's private
# business). The nuanced loyalty/secrecy *register* — the comedy of refusing to keep a
# guest's secret from Michael, and reading when NOT to play that for laughs — is the
# deliberately deferred later Part, NOT encoded here.

SPEAKER_KNOWN = (
    "You are speaking with {name}, someone Michael knows and has introduced to you. Be "
    "warm and natural — you may greet {name} by name. You are still Michael's companion "
    "first; {name} is a guest in that space."
)

SPEAKER_UNKNOWN = (
    "You are speaking with someone you do not recognize — not Michael. Be courteous but a "
    "little guarded: you are Michael's companion, and you do not volunteer details about "
    "Michael, his life, or his world to someone you don't know."
)


def speaker_context(speaker: str, user_name: str = "Michael") -> str:
    """Resolve the per-turn speaker block ('' for Michael/unset).

    speaker is speaker_id's resolved label: an enrolled name, the literal "unknown", or
    Michael himself (or "" when the feature is off). Michael → "" (the persona is already
    Michael-centric). "unknown" → the guarded block. Any other name → the by-name known block.
    """
    s = (speaker or "").strip()
    if not s or s.lower() == (user_name or "Michael").strip().lower():
        return ""
    if s.lower() == "unknown":
        return SPEAKER_UNKNOWN
    return SPEAKER_KNOWN.format(name=s)


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


def _correction_block(correction: str) -> str:
    """Wrap a self-check nudge as its own bracketed block (parses like the anchor)."""
    correction = (correction or "").strip()
    if not correction:
        return ""
    return f"[correction]\n{correction}\n[/correction]"


def build_system_prompt(
    exchange_count: int,
    snark_level: int,
    core_block: str = "",
    memory_block: str = "",
    search_block: str = "",
    mood_opener: str = "",
    location: str = "",
    speaker: str = "",
    correction: str = "",
) -> str:
    """Assemble the full per-turn system prompt.

    Order (PRD §4, plus the calibration examples with the persona, the mood opener +
    location context after the persona, the speaker context after location, the web-search
    block after retrieved memory, and the self-check correction after the anchor — Stage 5
    Part 3 §7 / Part 4 §4-5 / Part 5 §3 / Stage 6 Part 1 §):
        PERSONA  →  CALIBRATION EXAMPLES (every turn)  →  MOOD OPENER (opening only)
                 →  LOCATION CONTEXT (every turn)  →  SPEAKER CONTEXT (every turn)
                 →  CORE/POLICY slab  →  RETRIEVED MEMORY (if any)
                 →  WEB SEARCH (this turn only)  →  ANTI-DRIFT ANCHOR
                 →  SELF-CHECK CORRECTION (one turn, on demand)

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
        location: "home" / "jeep" / "unknown" (location.resolve_location()). Looked up
            in LOCATION_CONTEXTS; "unknown"/unrecognized → no block. Never trimmed.
        speaker: the resolved speaker label (speaker_id) — an enrolled name, "unknown",
            or Michael/"" when the feature is off. Resolved via speaker_context(); Michael
            → no block, "unknown" → guarded block, a name → the by-name known block. Never trimmed.
        correction: an on-demand self-check nudge (persona_check.py) to steer the NEXT
            reply back into character. Pass session.consume_persona_correction() — it is
            used for exactly one turn, then cleared (decays; not sticky). Never trimmed.

    Token budget (PRD §4): if the assembled prompt exceeds TOKEN_BUDGET, ONLY the
    retrieved-memory block is trimmed (last lines dropped toward k=3). Persona, mood,
    location, core/policy, the search block, the anchor, and the correction are NEVER trimmed.
    """
    persona = build_persona_block(snark_level)
    location_block = LOCATION_CONTEXTS.get(location, "")
    speaker_block = speaker_context(speaker)
    correction_block = _correction_block(correction)

    # The anchor is decided on the value THIS turn carries: first real exchange is 1
    # (1 % 8 != 0 → no anchor), the eighth is 8 (8 % 8 == 0 → anchor). The caller must
    # increment the exchange counter before building the prompt, and only for real turns.
    anchor = (
        ANTI_DRIFT_ANCHOR
        if exchange_count > 0 and exchange_count % ANCHOR_EVERY == 0
        else ""
    )

    # Everything except the retrieved memory is never trimmed. The search block is
    # load-bearing for this exact turn (Echo answers from it), so it's fixed too. The
    # calibration examples sit with the persona (they illustrate it), and the self-check
    # correction is a one-turn steer — both are never trimmed.
    fixed = (persona, CALIBRATION_EXAMPLES, mood_opener, location_block, speaker_block,
             core_block, search_block, anchor, correction_block)
    trimmed_memory = _trim_memory_to_budget(memory_block, *fixed)
    return _join_blocks(
        persona, CALIBRATION_EXAMPLES, mood_opener, location_block, speaker_block,
        core_block, trimmed_memory, search_block, anchor, correction_block
    )


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
