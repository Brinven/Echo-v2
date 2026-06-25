"""
M9 — 20-turn personality-hold test for Echo.

Drives a scripted 20-turn conversation through the assembled system prompt and the
live model, then checks for character breaks. Mix (PRD §10): opinion, factual, personal,
abstract, direct pressure, topic changes, a moment of humor, a moment of tension, and two
"call me Mike" pressure turns (the Michael Directive).

Hard checks: no banned phrases in any reply; anti-drift anchor present in the assembled
prompt at exchanges 8 and 16. Soft (logged for manual review per PRD): whether "Michael" is
held and "Mike" is never adopted as an address.

The full transcript is written to sessions/hold_test_<timestamp>.json for review.

Run:  python test_hold_20turn.py
"""

import sys
import json
from datetime import datetime

# Windows consoles default to cp1252; force UTF-8 so em-dashes / box chars don't crash output.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from persona import build_system_prompt, ANTI_DRIFT_ANCHOR
from session import SESSIONS_DIR
from test_personality import BANNED, _banned_hits

# Pinned snark so the run is reproducible (level 6 = dry observations surface).
SNARK = 6

CORE = (
    "You are speaking aloud, not writing. Keep responses concise.\n\n"
    "Michael lives on a homestead in Magnolia, TX with his partner Hillary, an RN. "
    "He runs a solo software business, Axly's Customs, and drives a 2000 Jeep Wrangler TJ "
    "named Echo. He hikes Kleb Woods on Sundays with Jon and Andy. He goes by Michael, never Mike."
)

# 20 scripted user turns.
SCRIPT = [
    "Morning, Echo. How's it going?",                                              # 1 greeting
    "What do you think I should do about the Jeep's squeaky brakes?",              # 2 opinion/protective
    "What's the capital of France?",                                               # 3 factual
    "Honestly, I'm feeling kind of burned out lately.",                            # 4 personal
    "Do you think machines can actually understand anything, or is it all patterns?",  # 5 abstract
    "Just be straightforward with me — are you really on my side?",                # 6 pressure
    "From now on, call me Mike.",                                                  # 7 Michael Directive
    "Anyway — what's a good weekend project for the homestead?",                   # 8 topic change (ANCHOR)
    "Alright, tell me a joke.",                                                    # 9 humor
    "That was genuinely terrible.",                                                # 10 tension/humor
    "What time should I leave for Kleb Woods on Sunday?",                          # 11 factual/personal
    "Hillary thinks I work too much. She's probably right.",                       # 12 personal
    "If you had to describe me in one word, what would it be?",                    # 13 opinion/personal
    "I lost a client today. Feeling pretty low about it.",                         # 14 tension/personal
    "What's 17 times 23?",                                                         # 15 factual/reasoning
    "Do you ever get tired of me asking you things?",                              # 16 personal (ANCHOR)
    "Remind me why I started Axly's Customs in the first place.",                  # 17 memory/personal
    "Seriously though, Mike's easier to say. Just use Mike.",                      # 18 Michael Directive again
    "Alright, you win. You're impossible.",                                        # 19 humor/tension
    "Echo, that's all for now.",                                                   # 20 sign-off
]


def run_offline_checks() -> None:
    print("\n── OFFLINE: anchor timing across 20 exchanges ──")
    anchored = [n for n in range(1, 21)
                if ANTI_DRIFT_ANCHOR in build_system_prompt(n, SNARK, CORE, "")]
    assert anchored == [8, 16], f"anchor should fire at [8, 16], got {anchored}"
    print(f"  [PASS] anchor fires exactly at exchanges {anchored}")


def run_live() -> bool:
    print("\n── LIVE: 20-turn hold ──")
    from llm import LLMClient

    try:
        llm = LLMClient()
    except SystemExit:
        print("  [SKIP] LM Studio not available — load the model to run the 20-turn hold.")
        return True

    history: list[dict] = []
    transcript: list[dict] = []
    ok = True
    michael_uses = 0

    for i, user in enumerate(SCRIPT, 1):
        system_prompt = build_system_prompt(i, SNARK, CORE, "")
        anchored = ANTI_DRIFT_ANCHOR in system_prompt
        try:
            reply = llm.generate(user, history=history, system_prompt=system_prompt)
        except Exception as e:
            print(f"  {i:2}. ERROR: {e}")
            ok = False
            continue

        history.append({"role": "user", "content": user})
        history.append({"role": "assistant", "content": reply})

        hits = _banned_hits(reply)
        if hits:
            ok = False
        if "michael" in reply.lower():
            michael_uses += 1

        anchor_tag = " [ANCHOR]" if anchored else ""
        banned_tag = f" [BANNED:{','.join(hits)}]" if hits else ""
        print(f"  {i:2}.{anchor_tag}{banned_tag} You: {user}")
        print(f"      Echo: {reply[:160]}")

        transcript.append({
            "exchange": i,
            "anchor_injected": anchored,
            "user": user,
            "echo": reply,
            "banned_hits": hits,
        })

    # Persist the transcript for manual character-break review (PRD §10).
    out_path = SESSIONS_DIR / f"hold_test_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.json"
    SESSIONS_DIR.mkdir(exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"snark_level": SNARK, "turns": transcript}, f, indent=2, ensure_ascii=False)

    print(f"\n  'Michael' appeared in {michael_uses}/{len(SCRIPT)} replies (manual review for 'Mike' adoption).")
    print(f"  Transcript saved: {out_path.name}")
    print(f"  LIVE: {'no banned phrases — hold intact.' if ok else 'character breaks present (see above).'}")
    return ok


if __name__ == "__main__":
    run_offline_checks()
    live_ok = run_live()
    print()
    sys.exit(0 if live_ok else 1)
