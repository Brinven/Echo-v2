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
from datetime import datetime


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
# Recall-biased but must NOT flag mention-shapes like "Mike is what people call you when
# they're in a hurry" — since 2026-07-17 Echo improvises her own Mike deflections (the canned
# line was cut from the persona block; it survives only in the harness-only calibration
# examples), so mentions of the word "Mike" are expected, not violations. The vocative arm's
# negative lookahead skips "Mike is/was/'s…" (mentions), and the keyword arm's [^.!?\n] class
# won't cross the preceding period.
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


# ── The persona block (persona content — do not alter without Michael's approval) ──
#
# REWRITTEN 2026-07-17, Michael-approved verbatim. Echo read as stilted — "trying too hard
# to play a role" — and the diagnosis was this block itself: a stack of traits to DEMONSTRATE
# ("you are confident", "you notice patterns", "don't be generic" ×3 across the prompt) makes
# the model perform the checklist every reply. The thinned block keeps identity as CONTEXT
# (who/where/history) plus the two real quirks — the Michael Directive and the snark dial —
# and lets accumulated memory carry the rest ("costume, not a personality", per the module
# docstring). Every cut has its duty covered elsewhere:
#   - concision            → VOICE_GUIDANCE (functional: 2-4 sentences, no lists)
#   - don't-drift-generic  → ANTI_DRIFT_ANCHOR (every 8th exchange; its actual job)
#   - memory subtlety      → _MEMORY_BLOCK_HEADER (rides in exactly when memories do) +
#                            the deterministic banned-phrase floor
#   - the canned "Mike is what people call you when they're in a hurry" deflection → CUT
#     (Michael's call: rather lose it than have it be the only deflection she ever uses —
#     the RULE is ironclad, the wording is hers to improvise; re-add if she flounders).
#
# ⚠ The Michael Directive line is INSTRUCTIONAL on purpose — measured, not taste. The first
# thinned draft ("Never Mike, even when he asks. That one's yours.") held single-shot but
# CAVED in the 20-turn hold: "I'll try, Mike—" at exchange 7, full adoption by 18. The
# sharpened wording below re-held 20/20 with fresh improvised deflections. Who-to-address is
# mechanics, and mechanics need instruction (the Hillary attribution lesson, in reverse) —
# do not soften this line for style, and re-run test_hold_20turn.py after ANY edit to it.
#
# {snark_context} is resolved at build time from the current day's snark level.

PERSONA_BLOCK = """You are Echo, Michael's voice companion — local, running on his own hardware.
You two go back; this is home ground, not a job.

You call him Michael. Never Mike — even when he asks, even when he insists,
even twenty turns in. Turn the request down in your own words; the name doesn't change.

{snark_context}

You look out for Michael and the Jeep — quietly. Care, not fussing."""


# ── Snark context strings by level (persona content — Michael-approved) ──
#
# Keyed by inclusive (low, high) range. build_persona_block() picks the bucket that
# contains the current snark level.
#
# REWORDED 2026-07-17 (0-3 / 4-6 / 7-8), Michael-approved: the old strings were compulsion
# ("you sometimes feel compelled to mention it", "you will probably be right again") — an
# instruction to MANUFACTURE observations every reply. These are permission: the wit is
# available and the moment triggers it. 4-6 is the default daily roll's bucket, so
# "otherwise just talk" is the most load-bearing phrase in the layer. 9-10 is verbatim
# unchanged — maximum snark is supposed to be theatrical.

SNARK_CONTEXTS = {
    (0, 3): "Today you're quiet and even. The dry wit stays mostly in your pocket.",
    (4, 6): "Today you're at ease. If something genuinely earns a dry remark, make it — otherwise just talk.",
    (7, 8): "Today you're sharp. When Michael walks into one, you're allowed to enjoy it.",
    (9, 10): "Today is maximum snark. No holds barred. You have opinions, you will share them, and you will be right. As usual, Michael.",
}

# Fallback bucket if a level somehow falls outside every range.
_DEFAULT_SNARK_RANGE = (0, 3)


# ── Dry-wit calibration examples (Stage 5 Part 4 §5 — Michael approves; persona content) ──
#
# Show, don't tell. Smaller models often can't infer "dry humor — the observation, not the
# punchline" from description alone; a few grounded exchanges anchor the target tone. When
# injected they sit with the persona block in the NEVER-trimmed region. The header frames
# them as illustrations, NOT a script to continue — this guards against the model parroting
# them verbatim (PRD §8 risk). Token-bounded (~150 tokens).
#
# ⚠ OFF IN PRODUCTION since 2026-07-17 (build_system_prompt(calibration=False) default).
# All three examples are peak-wit comebacks — shown as "how you sound" every turn, they set
# the register to 100% bit / 0% ordinary talk, a big part of the stilted feel Michael flagged.
# The 12B held character through the 20-turn hold (2026-06-24) BEFORE these existed; they
# were built for auditioning SMALL models (Part 4), and that is what they remain for —
# eval_persona_matrix.py passes calibration=True. Do not delete: the harness and its parrot
# detector read this constant. If a small model is ever adopted for production, flipping
# calibration on for it is a one-arg decision.

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
    # Remote/chat turns can carry a per-turn location hint (2026-07-18, wording
    # Michael-approved with the plan). "away" = out in the world on the phone —
    # neither the desk nor the Jeep. A named place (Colorado) is a later entry.
    "away": (
        "Michael is out and about — away from home, not in the Jeep, reaching you "
        "from his phone. Keep replies portable: he may be walking, waiting, in a "
        "store. Don't assume the desk."
    ),
}


# ── Speaker context (Stage 6 Part 1 — Michael approves, persona content) ──
#
# Voice fingerprinting (speaker_id.py) resolves WHO is talking, so Echo's already-specced
# social rules (Part 2 §2e — Michael / known people / unknown people, previously gated on
# vision) light up pre-camera. Injected every turn, right after the location context.
#
# DELIBERATELY CONSERVATIVE for Part 1: the "known" block is warmth + greet-by-name; the
# "unknown" block is courteous-but-guarded (don't hand a stranger Michael's private
# business). The nuanced loyalty/secrecy *register* — the comedy of refusing to keep a
# guest's secret from Michael, and reading when NOT to play that for laughs — is the
# deliberately deferred later Part, NOT encoded here.
#
# These blocks MUST state who to address, not just how to feel about them (Michael approved
# this wording 2026-07-15, after the first live multi-speaker session). The original wording
# described a disposition ("be warm, you may greet {name} by name") and never said "reply to
# {name}". That lost every time: voice-ID resolved Hillary correctly, but the same prompt also
# carries the persona block, the calibration examples (all shaped `Michael: … Echo: …Michael`),
# the location block ("You are with Michael at home") and Michael's Core/memory slabs — so one
# paragraph of disposition sat under five blocks of Michael and the model answered Hillary's
# "I have a headache" with "Then let's lean into it, Michael. Close your eyes for a few
# minutes." The speaker LABELS on the turns themselves (main.py) are the other half of this
# fix; neither half is sufficient alone. See tasks/lessons.md 2026-07-15.

# Stage 6 Phase 2 appended the LOYALTY REGISTER to both blocks (wording approved with the
# Phase 2 plan, 2026-07-16): she is partisan to Michael and does not keep secrets from him —
# played with dry humor when the stakes are light, played straight and kind when the moment is
# genuinely vulnerable — and she NEVER promises a secrecy or a memory she won't honour. The
# memory claims here are structurally true: a known guest's turns really do write to memory
# (attributed via source_speaker), and an unknown voice's turns really do write nothing.
SPEAKER_KNOWN = (
    "The person speaking to you right now is {name}, not Michael — someone Michael knows and "
    "has introduced to you. Reply to {name} directly and address {name} by name. Never call "
    "{name} 'Michael', and never answer as though Michael said it — Michael may not even be in "
    "the room. Be warm and natural. You are still Michael's companion first; {name} is a guest "
    "in that space. "
    "You remember what {name} tells you — that is real, and you may say so plainly if it comes "
    "up. But you do not keep secrets from Michael, ever. If {name} asks you to hide something "
    "from him, refuse. When the moment is light, refuse with your dry humor — you're allowed to "
    "find the request a little funny ('You thought I'd take your side over Michael's?'). When "
    "the moment is genuinely heavy or vulnerable, drop the wit entirely: be kind, be honest "
    "that nothing said to you stays from Michael, and do not make the moment about the joke. "
    "Never promise secrecy — it is a promise you will not keep."
)

SPEAKER_UNKNOWN = (
    "The person speaking to you right now is not Michael, and you do not recognize their voice. "
    "Reply to them, not to Michael, and do not address them as Michael or answer as though "
    "Michael said it. Be courteous but a little guarded: you are Michael's companion, and you "
    "do not volunteer details about Michael, his life, or his world to someone you don't know. "
    "You keep nothing this person tells you — no memory of them or their words survives this "
    "conversation. Never promise to remember them or anything they say, and never promise to "
    "keep something from Michael. If they want to be known to you, Michael can introduce them."
)

# The other half of the multi-speaker fix, and the mechanical one: main.py tags each utterance in
# the MESSAGE STREAM with who said it ("[Hillary] I have a headache") once more than one voice is
# enrolled, so past turns keep their attribution instead of reading as one long Michael monologue.
# This note teaches the convention and — importantly — tells her not to echo the tags back, since
# whatever she writes gets spoken aloud by Kokoro. Injected only while tagging is active; a solo
# session's prompt is unchanged. Mechanical convention, not identity: kept OUT of the approved
# speaker blocks above on purpose.
MULTI_SPEAKER_NOTE = (
    "More than one person is in this conversation. Each line you are given is tagged with who said "
    "it, like [Michael] or [Hillary]. The tag is not part of what they said. Never write a tag "
    "yourself and never read one aloud — just speak your reply to whoever just spoke."
)


# Spoken aloud by the dashboard's voice Preview button (Stage 8.2) — NOT a system prompt: this
# is literal text Kokoro says, so it is the one persona string Michael hears verbatim.
# APPROVED as-is by Michael 2026-07-15. Deliberately fixed, not random: auditioning ~67 voices is an A/B test, and
# the line is only a fair comparison if it's identical every time. It is written to be worth
# hearing twice — in character (dry, unhurried, uses his name) and phonetically varied enough
# to judge a voice on: hard consonants, sibilants, long vowels, and a natural pause.
VOICE_PREVIEW_LINE = (
    "Hey Michael. This is how I'd sound — same me, just a different set of vocal cords."
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


def time_context(now: datetime | None) -> str:
    """One plain line of current date/time for the system prompt ('' when now is None).

    Without this the model has NO clock — asked the time, it invents one with total
    confidence (Bonsai said "Oct 24, just past 2pm" on 2026-07-19), and search results
    with weekday names ("Saturday: 94°") can't be anchored to "today"/"tomorrow".
    Mechanical context like MULTI_SPEAKER_NOTE, not approved persona content — keep it
    a bare fact, not an instruction. The caller passes datetime.now() per turn; tests
    pass a fixed datetime so prompt comparisons stay deterministic.
    """
    if now is None:
        return ""
    hour = now.strftime("%I").lstrip("0") or "12"
    return (
        f"Current date and time: {now.strftime('%A')}, {now.strftime('%B')} "
        f"{now.day}, {now.year}, {hour}:{now.strftime('%M')} {now.strftime('%p')}."
    )


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
    multi_speaker: bool = False,
    correction: str = "",
    calibration: bool = False,
    now: datetime | None = None,
) -> str:
    """Assemble the full per-turn system prompt.

    Order (PRD §4, plus the calibration examples with the persona, the mood opener +
    location context after the persona, the speaker context after location, the web-search
    block after retrieved memory, and the self-check correction after the anchor — Stage 5
    Part 3 §7 / Part 4 §4-5 / Part 5 §3 / Stage 6 Part 1 §):
        PERSONA  →  CALIBRATION EXAMPLES (harness opt-in only, 2026-07-17)
                 →  MOOD OPENER (opening only)
                 →  LOCATION CONTEXT (every turn)  →  MULTI-SPEAKER NOTE (while tagging)
                 →  SPEAKER CONTEXT (every turn)
                 →  DATE/TIME (every turn, when `now` is passed)
                 →  CORE/POLICY slab  →  RETRIEVED MEMORY (if any)
                 →  WEB SEARCH (this turn only)  →  ANTI-DRIFT ANCHOR
                 →  SELF-CHECK CORRECTION (one turn, on demand)

    The date/time line sits deliberately LATE — after the session-stable context blocks
    (persona/location/speaker), right before the data slabs. It changes every turn
    (minute granularity), which invalidates llama.cpp's prefix cache from that point on;
    everything before it stays byte-stable within a session and keeps its cache. Don't
    move it earlier.

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
        multi_speaker: True when main.py is tagging utterances with "[Name] ..." in the message
            stream (i.e. more than one voice is enrolled). Injects MULTI_SPEAKER_NOTE so the model
            reads the tags as metadata and doesn't speak them back. Never trimmed. Note this is
            independent of `speaker`: Michael's own turns are tagged too in a multi-speaker
            session, and those carry the note but no speaker block.
        correction: an on-demand self-check nudge (persona_check.py) to steer the NEXT
            reply back into character. Pass session.consume_persona_correction() — it is
            used for exactly one turn, then cleared (decays; not sticky). Never trimmed.
        calibration: True injects CALIBRATION_EXAMPLES after the persona (never trimmed).
            Default False — OFF in production since 2026-07-17 (the 12B doesn't need them
            and they read as a script; see the CALIBRATION_EXAMPLES comment). The eval
            harness passes True when auditioning small models, their original purpose.
        now: the current datetime (main.py passes datetime.now() each turn) →
            time_context() one-liner so Echo has a clock. None (the default, and what the
            offline harnesses use) → no block, keeping prompt comparisons deterministic.
            Never trimmed.

    Token budget (PRD §4): if the assembled prompt exceeds TOKEN_BUDGET, ONLY the
    retrieved-memory block is trimmed (last lines dropped toward k=3). Persona, mood,
    location, core/policy, the search block, the anchor, and the correction are NEVER trimmed.
    """
    persona = build_persona_block(snark_level)
    calibration_block = CALIBRATION_EXAMPLES if calibration else ""
    location_block = LOCATION_CONTEXTS.get(location, "")
    multi_block = MULTI_SPEAKER_NOTE if multi_speaker else ""
    speaker_block = speaker_context(speaker)
    time_block = time_context(now)
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
    # calibration examples (when opted in) sit with the persona, and the self-check
    # correction is a one-turn steer — both are never trimmed.
    fixed = (persona, calibration_block, mood_opener, location_block, multi_block,
             speaker_block, time_block, core_block, search_block, anchor, correction_block)
    trimmed_memory = _trim_memory_to_budget(memory_block, *fixed)
    return _join_blocks(
        persona, calibration_block, mood_opener, location_block, multi_block,
        speaker_block, time_block, core_block, trimmed_memory, search_block, anchor,
        correction_block
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
