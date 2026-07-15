"""
Offline tests for the LISTENING pre-roll trim (Stage 8, 2026-07-15).

Guards THE bug Michael hit: "I hit talk, say something — no response. I hit talk again and
THEN echo responds." The mic buffer was cleared on LISTENING entry but kept growing for the
whole idle period (the audio callback appends while LISTENING *or* RECORDING). So a press
captured everything said since the last turn: press-speak-release looked silent, and the NEXT
press answered the PREVIOUS sentence. Capture must begin when RECORDING begins.

No mic, no model, no Win32 — just the pure buffer math.
Run: python test_audio_capture.py
"""

import numpy as np

from main import trim_to_preroll, PRE_ROLL_SAMPLES, PRE_ROLL_S
from session import vad_default_for_location
from audio import SAMPLE_RATE

PASS, FAIL = 0, 0


def check(label: str, cond: bool):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}")


def chunks(n: int, size: int = 480) -> list:
    """n callback blocks of `size` samples (480 = 30ms @ 16kHz, the real blocksize)."""
    return [np.zeros(size, dtype=np.float32) for _ in range(n)]


def total(buf: list) -> int:
    return sum(len(c) for c in buf)


print("\npre-roll constants")
check("PRE_ROLL_S is 0.5s", PRE_ROLL_S == 0.5)
check("PRE_ROLL_SAMPLES matches 0.5s @16kHz", PRE_ROLL_SAMPLES == int(SAMPLE_RATE * 0.5) == 8000)

print("\ntrim_to_preroll — the regression")
# 60s of idle silence: exactly the state that used to get glued onto the front of a turn.
idle = chunks(int(60 / 0.03))
before = total(idle)
trim_to_preroll(idle)
check(f"60s idle ({before} samples) trimmed to <= pre-roll", total(idle) <= PRE_ROLL_SAMPLES)
check("60s idle is bounded, not cleared", 0 < total(idle))
check("trim is chunk-granular (whole 30ms blocks)", all(len(c) == 480 for c in idle))

print("\ntrim_to_preroll — leaves short buffers alone")
short = chunks(3)                      # 90ms, well under pre-roll
trim_to_preroll(short)
check("3 chunks (90ms) untouched", len(short) == 3)
# Exactly PRE_ROLL_SAMPLES: 8000 isn't a multiple of the 480-sample block, so pad the remainder.
# The boundary must be inclusive — trim only when we EXCEED the pre-roll, never at it.
exact = chunks(PRE_ROLL_SAMPLES // 480) + [np.zeros(PRE_ROLL_SAMPLES % 480, dtype=np.float32)]
check("test fixture really is exactly pre-roll", total(exact) == PRE_ROLL_SAMPLES)
trim_to_preroll(exact)
check("exactly pre-roll untouched (boundary is inclusive)", total(exact) == PRE_ROLL_SAMPLES)
over = chunks(PRE_ROLL_SAMPLES // 480 + 2)   # one block past pre-roll
trim_to_preroll(over)
check("one block over pre-roll IS trimmed", total(over) <= PRE_ROLL_SAMPLES)

print("\ntrim_to_preroll — edges")
empty: list = []
trim_to_preroll(empty)
check("empty buffer: no crash", empty == [])
one = chunks(1, size=SAMPLE_RATE * 10)   # a single oversized chunk
trim_to_preroll(one)
check("never drops the last chunk (a press between callbacks still has audio)", len(one) == 1)
check("custom max_samples honored", (lambda b: (trim_to_preroll(b, 960), total(b) <= 960)[1])(chunks(50)))

print("\ntrim_to_preroll — ordering (must drop OLDEST, keep NEWEST)")
# The newest audio is what the speaker just said; dropping it would be the same bug inverted.
marked = [np.full(480, float(i), dtype=np.float32) for i in range(50)]
trim_to_preroll(marked)
check("newest chunk survives", float(marked[-1][0]) == 49.0)
check("oldest chunks dropped", float(marked[0][0]) > 0.0)
check("survivors stay contiguous + in order",
      [float(c[0]) for c in marked] == list(range(int(marked[0][0]), 50)))

print("\nVAD default is location-aware (Stage 8)")
check("home -> hands-free on", vad_default_for_location("home") is True)
check("jeep -> manual (road noise)", vad_default_for_location("jeep") is False)
# Stage 5 Part 5 rule: unknown fails to NEUTRAL/home behavior, never jeep.
check("unknown -> neutral/home, never jeep", vad_default_for_location("unknown") is True)

print(f"\n{'='*54}\n  {PASS} passed, {FAIL} failed\n{'='*54}")
raise SystemExit(1 if FAIL else 0)
