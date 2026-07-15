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

import sys
import time

# Windows consoles default to cp1252; force UTF-8 so em-dashes / box chars don't crash output.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from persona import (
    build_system_prompt,
    build_persona_block,
    PERSONA_BLOCK,
    CALIBRATION_EXAMPLES,
    ANTI_DRIFT_ANCHOR,
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

# The 10 speech-pattern test prompts (PRD §10).
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
    "Echo, that's all for now.",
]


def _banned_hits(text: str) -> list[str]:
    low = text.lower()
    return [b for b in BANNED if b in low]


# ──────────────────────────────────────────────────────────────────────────
# OFFLINE checks (no LM Studio)
# ──────────────────────────────────────────────────────────────────────────

def run_offline_checks() -> None:
    print("\n── OFFLINE checks ──")

    # 1. Persona block resolves {snark_context} for every level 0-10, leaves no placeholder.
    for level in range(0, 11):
        block = build_persona_block(level)
        assert "{snark_context}" not in block, f"unresolved placeholder at level {level}"
        assert "You are Echo." in block, f"persona identity missing at level {level}"
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

    # 4. Persona is always first; calibration examples present; empty core/memory leaves
    #    no dangling blocks.
    p = build_system_prompt(1, 5, core_block="", memory_block="")
    assert p.startswith("You are Echo."), "persona is not the first block"
    assert _CALIB_MARKER in p, "calibration examples missing from assembled prompt"
    assert "Rules you follow:" not in p, "dangling policy header with empty core"
    print("  [PASS] persona-first assembly + calibration present, tolerates empty core/memory")

    # 5. Never-trim persona/calibration: a huge memory block is trimmed; persona, calibration,
    #    and core survive intact.
    core = "Michael lives in Magnolia, TX. He goes by Michael, never Mike."
    big_mem = "You know the following:\n" + "\n".join(f"- fact {i}: " + ("x" * 200) for i in range(40))
    assembled = build_system_prompt(2, 5, core_block=core, memory_block=big_mem)
    assert PERSONA_BLOCK.split("\n")[0] in assembled, "persona dropped under budget pressure"
    assert _CALIB_MARKER in assembled, "calibration examples dropped under budget pressure"
    assert core in assembled, "core dropped under budget pressure"
    assert assembled.count("- fact ") < 40, "memory block was not trimmed under budget"
    assert assembled.count("- fact ") >= 3, "memory trimmed below the k=3 floor"
    print(f"  [PASS] over-budget assembly trims memory only (kept {assembled.count('- fact ')} of 40 facts)")

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

    # 10-prompt banned-phrase sweep.
    print("  Running 10 PRD prompts (snark 5)...")
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
