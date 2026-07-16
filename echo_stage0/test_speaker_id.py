"""
Offline tests for speaker awareness (Stage 6 Part 1).

No model, no mic, no LM Studio: embeddings are injected as plain numpy vectors, so the
identify math, registry I/O, enroll-command parsing, persona speaker block + prompt order
+ never-trim, the session flags, and the memory guardrail DECISION are all exercised
without SpeechBrain installed.

Run:  python test_speaker_id.py     (exit 0 = all assertions passed)
"""

import sys
import json
import os
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np

import speaker_id
from speaker_id import SpeakerRegistry, load_speaker_config, voiced_only, _MODEL_TAG, _PREP_TAG
from persona import (
    speaker_context, build_system_prompt, SPEAKER_KNOWN, SPEAKER_UNKNOWN, MULTI_SPEAKER_NOTE,
)
from session import is_enroll_command, is_enroll_cancel, Session
# main imports the audio stack (sounddevice/torch); tag_utterance is pure string work, but it
# lives in main.py next to the pipeline that uses it, so the import cost rides along.
from main import tag_utterance


def _reg(profiles, **cfg) -> SpeakerRegistry:
    """A registry backed by an in-memory config (never touches echo_speakers.json)."""
    data = {"enabled": True, "match_threshold": 0.30, "model": _MODEL_TAG,
            "profiles": profiles}
    data.update(cfg)
    return SpeakerRegistry(config=data)


def _prof(name, vec, model=_MODEL_TAG):
    return {"name": name, "model": model, "embedding": [float(x) for x in vec]}


def run() -> None:
    print("\n── Speaker awareness (offline) ──")

    # 1. identify(): cosine match above threshold, reject below, empty registry.
    reg = _reg([_prof("Michael", [1, 0, 0, 0]), _prof("Jon", [0, 1, 0, 0])])
    name, score = reg.identify(np.array([0.9, 0.1, 0.0, 0.0], dtype=np.float32))
    assert name == "Michael" and score > 0.9, (name, score)
    name, score = reg.identify(np.array([0.0, 0.0, 1.0, 0.0], dtype=np.float32))  # orthogonal
    assert name is None and score < 0.30, (name, score)
    none_name, none_score = _reg([]).identify(np.array([1, 0, 0, 0], dtype=np.float32))
    assert none_name is None and none_score == 0.0
    print("  [PASS] identify: nearest match above threshold, orthogonal → None, empty → (None, 0.0)")

    # 2. threshold boundary is honored (same query, different floor).
    q = np.array([0.9, 0.1, 0.0, 0.0], dtype=np.float32)
    assert reg.identify(q, threshold=0.99)[0] == "Michael"      # 0.994 ≥ 0.99
    assert reg.identify(q, threshold=0.999)[0] is None          # 0.994 < 0.999
    print("  [PASS] threshold boundary respected")

    # 3. model-tag mismatch and shape mismatch are skipped, not mis-compared.
    mixed = _reg([_prof("Old", [1, 0, 0, 0], model="resemblyzer"),   # wrong model → skip
                  _prof("Bad", [1, 0, 0], model=_MODEL_TAG)])         # wrong dim → skip
    assert mixed.identify(np.array([1, 0, 0, 0], dtype=np.float32)) == (None, 0.0)
    print("  [PASS] identify skips mismatched-model and wrong-shape prints")

    # 4. enroll upsert + remove, then save→reload round-trip (serialization).
    tmp = Path(tempfile.mkdtemp()) / "echo_speakers.json"
    r = SpeakerRegistry(path=tmp, config={"enabled": False, "model": _MODEL_TAG, "profiles": []})
    r.enroll("Michael", np.array([1, 0, 0, 0], dtype=np.float32))
    r.enroll("Jon", np.array([0, 1, 0, 0], dtype=np.float32))
    r.enroll("Michael", np.array([0, 0, 1, 0], dtype=np.float32))     # upsert, not duplicate
    assert r.count == 2 and set(n.lower() for n in r.names) == {"michael", "jon"}
    assert r.has("michael") and not r.has("nobody")
    assert r.remove("Jon") is True and r.remove("Ghost") is False and r.count == 1
    r.config["enabled"] = True
    r.save()
    reloaded = SpeakerRegistry(config=json.load(open(tmp, encoding="utf-8")))
    assert reloaded.count == 1 and reloaded.names == ["Michael"] and reloaded.enabled is True
    os.unlink(tmp)
    print("  [PASS] enroll upsert / remove / save→reload round-trip")

    # 5. load_speaker_config fail-soft: a missing file disables the feature (→ assume Michael).
    saved_path = speaker_id._CONFIG_PATH
    try:
        speaker_id._CONFIG_PATH = Path(tempfile.gettempdir()) / "does_not_exist_echo_speakers.json"
        cfg = load_speaker_config()
        assert cfg["enabled"] is False and isinstance(cfg["profiles"], list)
    finally:
        speaker_id._CONFIG_PATH = saved_path
    # Whatever the real file says, the loader returns a complete dict.
    real = load_speaker_config()
    for key in ("enabled", "match_threshold", "model", "profiles"):
        assert key in real, key
    print("  [PASS] config loader is fail-soft and always returns a complete dict")

    # 6. enroll-command parsing (session): captures a name, guards against sentences/stopwords.
    assert is_enroll_command("Echo, this is Jon") == "Jon"
    assert is_enroll_command("echo this is sarah") == "Sarah"
    assert is_enroll_command("Echo, remember Jon's voice") == "Jon"
    assert is_enroll_command("Echo, this is important because the weather turned bad today") is None
    assert is_enroll_command("Echo, this is me") is None            # stopword
    assert is_enroll_command("what's the weather like") is None
    assert is_enroll_cancel("cancel") and is_enroll_cancel("never mind")
    assert not is_enroll_cancel("hello there")
    print("  [PASS] enroll-command: names captured, long sentences / stopwords / cancel handled")

    # 7. persona speaker_context: Michael/'' → nothing, unknown → guarded, a name → by-name.
    assert speaker_context("Michael") == "" and speaker_context("") == ""
    assert speaker_context("michael") == ""                          # owner match is case-insensitive
    assert speaker_context("unknown") == SPEAKER_UNKNOWN
    assert speaker_context("Jon") == SPEAKER_KNOWN.format(name="Jon") and "Jon" in speaker_context("Jon")
    print("  [PASS] speaker_context: Michael→'', unknown→guarded, name→by-name block")

    # 8. build_system_prompt: speaker block present, ordered after location / before core.
    p = build_system_prompt(1, 5, core_block="CORE-SLAB", location="home", speaker="Jon")
    assert "right now is Jon" in p
    assert p.index("home") < p.index("right now is Jon") < p.index("CORE-SLAB"), "prompt order wrong"
    # Michael → no known/unknown block injected.
    pm = build_system_prompt(1, 5, core_block="CORE-SLAB", speaker="Michael")
    assert "do not recognize" not in pm and "someone Michael knows" not in pm
    print("  [PASS] prompt: speaker block after location / before core; Michael → no block")

    # 8b. The speaker blocks must INSTRUCT the addressing, not merely describe a disposition —
    # the point of the 2026-07-15 rewrite. The old wording ("be warm, you may greet Jon by name")
    # lost to the five Michael-shaped blocks around it: Echo answered Hillary's "I have a
    # headache" with "Then let's lean into it, Michael."
    known = speaker_context("Jon")
    assert "not Michael" in known and "Reply to Jon directly" in known
    assert "Never call Jon 'Michael'" in known
    assert "not to Michael" in SPEAKER_UNKNOWN and "do not address them as Michael" in SPEAKER_UNKNOWN
    print("  [PASS] speaker blocks instruct WHO to address, not just how to feel")

    # 9. never trimmed: the speaker block survives an over-budget memory block.
    big_mem = "You know the following:\n" + "\n".join(f"- fact {i}: " + ("x" * 200) for i in range(40))
    p2 = build_system_prompt(2, 5, core_block="core", memory_block=big_mem, speaker="Jon")
    assert "right now is Jon" in p2, "speaker block trimmed under budget pressure"
    assert p2.count("- fact ") < 40, "memory not trimmed (test setup wrong)"
    print("  [PASS] speaker block never trimmed when memory is over budget")

    # 10. session flags + the memory GUARDRAIL DECISION (current_speaker_is_michael gates the write).
    s = Session(model="m", stt_backend="b", tts_backend="t", user_name="Michael")
    assert s.current_speaker == "Michael" and s.current_speaker_is_michael is True
    s.current_speaker = "Jon"
    assert s.current_speaker_is_michael is False            # guest → main.py skips ib.write_memory
    s.current_speaker = "unknown"
    assert s.current_speaker_is_michael is False            # unknown → write withheld
    s.current_speaker = "michael"
    assert s.current_speaker_is_michael is True             # case-insensitive owner
    assert s.enrolling is None
    print("  [PASS] session: current_speaker default Michael; guardrail decision True only for owner")

    # ── Multi-speaker attribution (2026-07-15): the system prompt alone could not carry this. ──

    # 11. tag_utterance: off for a solo roster (byte-identical to pre-Stage-6), on above one.
    assert tag_utterance("hello there", "Michael", False) == "hello there"
    assert tag_utterance("hello there", "Hillary", True) == "[Hillary] hello there"
    assert tag_utterance("hello there", "Michael", True) == "[Michael] hello there"   # owner tagged too
    assert tag_utterance("hello there", "", True) == "[unknown] hello there"          # never a bare tag
    print("  [PASS] tag_utterance: solo → untouched, multi → [Name] prefix, empty → [unknown]")

    # 12. MULTI_SPEAKER_NOTE rides with the tagging, not with the speaker block: Michael's own
    # turns are tagged in a multi-speaker session and carry the note with NO speaker block.
    pn = build_system_prompt(1, 5, core_block="CORE-SLAB", speaker="Michael", multi_speaker=True)
    assert MULTI_SPEAKER_NOTE in pn and "someone Michael knows" not in pn
    assert build_system_prompt(1, 5, speaker="Michael").count(MULTI_SPEAKER_NOTE) == 0   # solo → absent
    pn2 = build_system_prompt(2, 5, core_block="core", memory_block=big_mem,
                              speaker="Jon", multi_speaker=True)
    assert MULTI_SPEAKER_NOTE in pn2, "multi-speaker note trimmed under budget pressure"
    print("  [PASS] multi-speaker note: only while tagging, independent of the speaker block, never trimmed")

    # 13. Turns record WHO SPOKE at the time. current_speaker is live, so anything that renders
    # history with it re-attributes the backlog to whoever talked last (the dashboard did).
    s2 = Session(model="m", stt_backend="b", tts_backend="t", user_name="Michael")
    s2.add_user_turn("morning", 0.1)                       # defaults to current_speaker (Michael)
    s2.add_echo_turn("Morning, Michael.", 0.5)
    s2.current_speaker = "Hillary"
    s2.add_user_turn("I have a headache", 0.1, speaker="Hillary")
    assert [t.get("speaker_name") for t in s2.turns if t["speaker"] == "user"] == ["Michael", "Hillary"]
    assert s2.turns[0]["speaker"] == "user", "role field must stay the role"

    # 14. …and the sign-off summary sees those names. This is a SECOND write path into memory
    # (summary_text → episodic) that does NOT pass the per-turn guardrail: labelling everyone
    # "User" while asking the summarizer for "facts expressed by Michael" filed Hillary's
    # headache under Michael.
    text = s2.get_conversation_text()
    assert "Michael: morning" in text and "Hillary: I have a headache" in text
    assert "Echo: Morning, Michael." in text
    assert "User:" not in text, "guest turns still anonymised into the summary prompt"
    print("  [PASS] per-turn attribution survives into the dashboard payload + the summary prompt")

    # 15. voiced_only(): trims the dead air the embedder would otherwise pool over.
    # Model-free — a sine burst stands in for speech, digital silence for the pre-roll and
    # the VAD hangover. webrtcvad classifies frames on real speech, so this asserts the
    # CONTRACT (never longer, never raises, contiguous span, fail-soft), not the VAD's taste.
    sr = 16000
    tone = (0.3 * np.sin(2 * np.pi * 180 * np.arange(int(1.5 * sr)) / sr)).astype(np.float32)
    quiet = np.zeros(int(4.0 * sr), dtype=np.float32)
    padded = np.concatenate([quiet, tone, quiet])
    out = voiced_only(padded)
    assert len(out) <= len(padded), "must never grow the buffer"
    assert out.dtype == np.float32
    # Whatever survives must be a contiguous slice of the input — trimmed, never spliced.
    if len(out) < len(padded):
        assert any(np.array_equal(out, padded[i:i + len(out)])
                   for i in range(0, len(padded) - len(out) + 1, 480)), "must be a contiguous span"
    print("  [PASS] voiced_only: returns a contiguous span, never longer than its input")

    # Fail-soft: pure silence has no voiced frame to anchor on, and a runt buffer is not
    # worth trimming — both must hand back the original rather than an empty array. An
    # empty buffer into ECAPA would be a crash in the voice loop.
    assert len(voiced_only(np.zeros(int(3.0 * sr), dtype=np.float32))) == int(3.0 * sr)
    tiny = np.ones(10, dtype=np.float32)
    assert np.array_equal(voiced_only(tiny), tiny), "sub-frame buffer → unchanged"
    assert len(voiced_only(np.concatenate([quiet, tone[:int(0.1 * sr)], quiet]))) > 0
    print("  [PASS] voiced_only: silence / runt / too-little-speech fall back to the raw buffer")

    # 16. Prints made before the silence-trim fix are flagged, but still MATCH. A stale print
    # scores worse than it should (it lost the shared-silence bias a raw query used to give
    # it back) — that is a re-enroll prompt, not a reason to stop recognising someone.
    stale_reg = _reg([_prof("Michael", [1, 0, 0, 0])])           # _prof stamps no 'prep'
    assert stale_reg.stale_prints() == ["Michael"]
    assert stale_reg.identify(np.array([0.9, 0.1, 0.0, 0.0], dtype=np.float32))[0] == "Michael", \
        "a stale print must still identify — warn, don't silently stop knowing someone"
    fresh = _reg([])
    fresh.enroll("Hillary", np.array([0, 1, 0, 0], dtype=np.float32))
    assert fresh.stale_prints() == [], "a freshly enrolled print is never stale"
    assert fresh.profiles[0]["prep"] == _PREP_TAG
    print("  [PASS] stale prints flagged for re-enrol but still identify; new prints stamped")

    print("  OFFLINE: all speaker-awareness checks passed.")


if __name__ == "__main__":
    run()
    print()
