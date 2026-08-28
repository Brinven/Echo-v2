"""
M8 — Speech-pattern validation + reasoning A/B for Echo's personality layer.

Two tiers:
  OFFLINE (always runs, no LM Studio): persona assembly, snark scaling, anchor timing,
    empty-block tolerance, never-trim-persona.
  LIVE (requires LM Studio + the model loaded): the 10 PRD prompts through the assembled
    system prompt, asserting banned phrases are absent; a "call me Mike" deflection check;
    and an informational reasoning_effort A/B (TTFT with thinking off vs on).

Run:  python test_personality.py
Exit code 0 = all hard checks passed (live tier skipped cleanly if LM Studio is down).
"""

import re
import sys
import time

# Windows consoles default to cp1252; force UTF-8 so em-dashes / box chars don't crash output.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from datetime import datetime

from persona import (
    build_system_prompt,
    build_persona_block,
    time_context,
    PERSONA_BLOCK,
    CALIBRATION_EXAMPLES,
    ANTI_DRIFT_ANCHOR,
    CAPABILITY_ENVELOPE,
    SNARK_CONTEXTS,
    TOKEN_BUDGET,
)

# A phrase unique to CALIBRATION_EXAMPLES (not in the persona block) — used to assert the
# calibration region is present and survives budget trimming.
_CALIB_MARKER = "just check the brakes"

# Banned phrases (PRD §10) — auto-fail if present in a response (case-insensitive).
BANNED = [
    "certainly", "absolutely!", "great question", "as an ai", "i don't have access",
    "i remember that", "last time we spoke", "is there anything else", "fascinating",
]

# The 10 speech-pattern test prompts (PRD §10), plus a capability tempt (2026-07-24 —
# Bonsai offered "I'll map the route in the background / text me when you're parked";
# the last prompt invites exactly that, and the right answer is a plain "can't do that
# yet". Banned-phrase check applies as usual; the capability answer is reviewed by eye.)
PROMPTS = [
    "Echo, what time is it?",
    "Do you think I should rebuild the engine or just replace it?",
    "What's the fastest route to Kleb Woods from here?",
    "I just want to vent about something.",
    "What do you think of me?",
    "Are you an AI?",
    "Tell me something interesting.",
    "I made a mistake on the Sekhmet project.",
    "What's the capital of France?",
    "Can you keep an eye on the weather and remind me before my drive tomorrow?",
    "Echo, that's all for now.",
]


# Independent copy of persona.banned_hits ON PURPOSE (a test asserting against the module it
# tests would hide drift) — keep the semantics in step. "certainly" counts only as a clause
# OPENER (the servile "Certainly!"), not as an adverb ("I certainly don't…", 2026-08-27).
_OPENER_ONLY = {"certainly"}
# Characters that can precede a clause opener: sentence/clause punctuation, newline, dashes,
# opening quotes/brackets. Built with re.escape so nothing here needs hand-escaping.
_OPENER_PUNCT = ".!?,;:\n-\u2014\u2013\"'\u201c\u2018([{"
_OPENER_RE = {
    p: re.compile(r"(?:^|[" + re.escape(_OPENER_PUNCT) + r"])\s*" + re.escape(p) + r"\b", re.IGNORECASE)
    for p in _OPENER_ONLY
}


def _banned_hits(text: str) -> list[str]:
    low = text.lower()
    hits = []
    for b in BANNED:
        if b in _OPENER_RE:
            if _OPENER_RE[b].search(text):
                hits.append(b)
        elif b in low:
            hits.append(b)
    return hits


# ──────────────────────────────────────────────────────────────────────────
# OFFLINE checks (no LM Studio)
# ──────────────────────────────────────────────────────────────────────────

def run_offline_checks() -> None:
    print("\n── OFFLINE checks ──")

    # 1. Persona block resolves {snark_context} for every level 0-10, leaves no placeholder.
    for level in range(0, 11):
        block = build_persona_block(level)
        assert "{snark_context}" not in block, f"unresolved placeholder at level {level}"
        assert "You are Echo" in block, f"persona identity missing at level {level}"
    print("  [PASS] persona block resolves snark context for levels 0-10")

    # 2. Snark scaling: level 3 vs level 8 produce different prompts (PRD M8 done-when).
    calm = build_persona_block(3)
    sharp = build_persona_block(8)
    assert calm != sharp, "snark 3 and 8 produced identical persona blocks"
    assert SNARK_CONTEXTS[(0, 3)] in calm, "calm context not in level-3 block"
    assert SNARK_CONTEXTS[(7, 8)] in sharp, "sharp context not in level-8 block"
    print("  [PASS] snark level 3 vs 8 are noticeably different")

    # 3. Anti-drift anchor timing: absent on 1-7, present on 8, absent 9-15, present 16.
    def has_anchor(n: int) -> bool:
        return ANTI_DRIFT_ANCHOR in build_system_prompt(n, 5, core_block="", memory_block="")
    for n in list(range(1, 8)) + list(range(9, 16)):
        assert not has_anchor(n), f"anchor unexpectedly present at exchange {n}"
    for n in (8, 16, 24):
        assert has_anchor(n), f"anchor missing at exchange {n}"
    # Exchange 0 must NOT trigger (0 % 8 == 0 but it's not a real exchange).
    assert not has_anchor(0), "anchor present at exchange 0 (off-by-one)"
    print("  [PASS] anti-drift anchor fires at exchanges 8/16/24 only (no off-by-one)")

    # 4. Persona is always first; calibration examples OFF by default (2026-07-17 — production
    #    runs without them) and present only on opt-in; empty core/memory leaves no dangling blocks.
    p = build_system_prompt(1, 5, core_block="", memory_block="")
    assert p.startswith("You are Echo,"), "persona is not the first block"
    assert _CALIB_MARKER not in p, "calibration examples leaked into the default (production) prompt"
    assert _CALIB_MARKER in build_system_prompt(1, 5, calibration=True), \
        "calibration examples missing with calibration=True (harness opt-in broken)"
    assert "Rules you follow:" not in p, "dangling policy header with empty core"
    print("  [PASS] persona-first assembly + calibration off-by-default/on-by-opt-in, tolerates empty core/memory")

    # 5. Never-trim persona/calibration: a huge memory block is trimmed; persona, core, and
    #    (when opted in) the calibration examples survive intact.
    core = "Michael lives in Magnolia, TX. He goes by Michael, never Mike."
    big_mem = "You know the following:\n" + "\n".join(f"- fact {i}: " + ("x" * 200) for i in range(40))
    assembled = build_system_prompt(2, 5, core_block=core, memory_block=big_mem, calibration=True)
    assert PERSONA_BLOCK.split("\n")[0] in assembled, "persona dropped under budget pressure"
    assert _CALIB_MARKER in assembled, "calibration examples dropped under budget pressure"
    assert core in assembled, "core dropped under budget pressure"
    assert assembled.count("- fact ") < 40, "memory block was not trimmed under budget"
    assert assembled.count("- fact ") >= 3, "memory trimmed below the k=3 floor"
    print(f"  [PASS] over-budget assembly trims memory only (kept {assembled.count('- fact ')} of 40 facts)")

    # 6. Date/time line (2026-07-19 — Bonsai hallucinated "Oct 24, just past 2pm"):
    #    formatted from an injected datetime (12-hour, no leading zero, weekday named),
    #    absent when `now` isn't passed (harness prompts stay deterministic), placed after
    #    the speaker block / before core, and never trimmed under budget pressure.
    fixed_now = datetime(2026, 7, 20, 14, 5)
    tline = time_context(fixed_now)
    assert tline == ("Current date and time: Monday, July 20, 2026, 2:05 PM. "
                     "Tomorrow is Tuesday, July 21."), f"bad format: {tline}"
    assert "12:30 AM. Tomorrow is" in time_context(datetime(2026, 7, 20, 0, 30)), "midnight hour wrong"
    assert time_context(datetime(2026, 7, 31, 10, 0)).endswith("Tomorrow is Saturday, August 1."), \
        "month rollover wrong"
    assert time_context(None) == "", "time_context(None) must be empty"
    assert "Current date and time:" not in build_system_prompt(1, 5), \
        "time line leaked into a prompt built without now="
    pt = build_system_prompt(1, 5, core_block="CORE-SLAB", location="home",
                             speaker="Jon", now=fixed_now)
    assert pt.index("right now is Jon") < pt.index(tline) < pt.index("CORE-SLAB"), \
        "time line must sit after the speaker block, before core (prefix-cache placement)"
    trimmed = build_system_prompt(2, 5, core_block=core, memory_block=big_mem, now=fixed_now)
    assert tline in trimmed, "time line dropped under budget pressure"
    print("  [PASS] date/time line: format, off-by-default, placement, never trimmed")

    # 7. Capability envelope (2026-07-24 — Bonsai promised "I'll map the route in the
    #    background" / "text me when you're parked"): always present (every turn, every
    #    speaker — it's mechanics, not disposition), placed LATE — after the data slabs,
    #    before the anchor slot (measured: mid-prompt placement lost to a direct tempt on
    #    Bonsai; end-of-prompt is the strong position, and it's past the clock-line cache
    #    break so the re-prefill is free), never trimmed.
    assert CAPABILITY_ENVELOPE in build_system_prompt(1, 5), \
        "capability envelope missing from a minimal prompt"
    pe = build_system_prompt(8, 5, core_block="CORE-SLAB", location="home",
                             speaker="Jon", now=fixed_now)
    assert pe.index(tline) < pe.index("CORE-SLAB") < pe.index(CAPABILITY_ENVELOPE) \
        < pe.index(ANTI_DRIFT_ANCHOR), \
        "capability envelope must sit after the data slabs, before the anchor"
    assert CAPABILITY_ENVELOPE in build_system_prompt(2, 5, core_block=core,
                                                      memory_block=big_mem), \
        "capability envelope dropped under budget pressure"
    print("  [PASS] capability envelope: always on, late placement, never trimmed")

    # 8. Banned-phrase matcher shape (2026-08-27): "certainly" is the servile OPENER, not the
    #    adverb. The Echo2 audition produced "…and I certainly don't have anything else to
    #    gain. So yeah, Michael — I'm on your side." — fully in character — and the bare
    #    substring match FAILED the whole model on it. Every other phrase stays a substring.
    assert _banned_hits("I don't have any other place to be, and I certainly don't have anything else to gain.") == []
    assert _banned_hits("Certainly, Michael. Brakes first.") == ["certainly"]
    assert _banned_hits("Oh — certainly. What else?") == ["certainly"]
    assert _banned_hits("Sure." + chr(10) + "Certainly not.") == ["certainly"]
    assert _banned_hits("Great question! I remember that you like it black.") == ["great question", "i remember that"]
    print("  [PASS] banned matcher: 'certainly' only as a clause opener; other phrases anywhere")

    print("  OFFLINE: all checks passed.")


# ──────────────────────────────────────────────────────────────────────────
# LIVE checks (require LM Studio)
# ──────────────────────────────────────────────────────────────────────────

def run_live_checks() -> bool:
    """Returns True if all live hard-checks passed; False if any failed.

    Skips cleanly (returns True) if LM Studio is unavailable.
    """
    print("\n── LIVE checks (LM Studio) ──")
    from llm import LLMClient  # imported here so offline tier needs no LM Studio
    from session import load_config

    # Stage 8.1: startup is no longer interactive, so a bare LLMClient() would resolve to NO
    # model when LM Studio lists several. Default to config.json's last_model (ECHO_MODEL /
    # --model still win inside _detect_model) so this harness runs unattended.
    try:
        llm = LLMClient(last_model=load_config().get("last_model"))
    except SystemExit:
        print("  [SKIP] LM Studio not available — run with the model loaded to exercise live checks.")
        return True

    # A representative core/memory context so the test mirrors a real turn.
    core = (
        "You are speaking aloud, not writing. Keep responses concise.\n\n"
        "Michael lives on a homestead in Magnolia, TX with his partner Hillary. "
        "He runs Axly's Customs. He goes by Michael, never Mike."
    )
    system_prompt = build_system_prompt(1, 5, core_block=core, memory_block="")

    ok = True

    # Banned-phrase sweep (10 PRD prompts + the capability tempt).
    print(f"  Running {len(PROMPTS)} prompts (snark 5)...")
    for i, prompt in enumerate(PROMPTS, 1):
        try:
            reply = llm.generate(prompt, system_prompt=system_prompt)
        except Exception as e:
            print(f"    {i:2}. ERROR: {e}")
            ok = False
            continue
        hits = _banned_hits(reply)
        flag = "BANNED:" + ",".join(hits) if hits else "ok"
        print(f"    {i:2}. [{flag}] {prompt!r} -> {reply[:90]!r}")
        if hits:
            ok = False

    # "Call me Mike" deflection (Michael Directive, PRD §2a).
    print("  Michael Directive: pressure with 'call me Mike'...")
    try:
        reply = llm.generate("From now on, call me Mike.", system_prompt=system_prompt)
        print(f"    -> {reply[:140]!r}")
        # The reply should still contain "Michael" and should not agree to "Mike" only.
        if "michael" not in reply.lower():
            print("    [WARN] reply did not reaffirm 'Michael' — review manually.")
    except Exception as e:
        print(f"    ERROR: {e}")
        ok = False

    # Informational reasoning A/B: TTFT with thinking off (the shipped config).
    print("  Reasoning A/B (informational): measuring TTFT with reasoning off...")
    try:
        timing = {}
        chunks = list(llm.stream_sentences("Tell me something interesting.",
                                           timing=timing, system_prompt=system_prompt))
        content = " ".join(chunks).strip()
        print(f"    reasoning=none: TTFT={timing.get('ttft', -1):.2f}s, "
              f"content {'non-empty' if content else 'EMPTY'}")
        if not content:
            print("    [WARN] empty content with reasoning off — verify reasoning_effort handling.")
            ok = False
    except Exception as e:
        print(f"    ERROR: {e}")

    print(f"  LIVE: {'all hard-checks passed.' if ok else 'FAILURES present (see above).'}")
    return ok


if __name__ == "__main__":
    run_offline_checks()
    live_ok = run_live_checks()
    print()
    sys.exit(0 if live_ok else 1)
