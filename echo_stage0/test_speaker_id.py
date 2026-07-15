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
from speaker_id import SpeakerRegistry, load_speaker_config, _MODEL_TAG
from persona import speaker_context, build_system_prompt, SPEAKER_KNOWN, SPEAKER_UNKNOWN
from session import is_enroll_command, is_enroll_cancel, Session


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
    assert "speaking with Jon" in p
    assert p.index("home") < p.index("speaking with Jon") < p.index("CORE-SLAB"), "prompt order wrong"
    # Michael → no known/unknown block injected.
    pm = build_system_prompt(1, 5, core_block="CORE-SLAB", speaker="Michael")
    assert "do not recognize" not in pm and "someone Michael knows" not in pm
    print("  [PASS] prompt: speaker block after location / before core; Michael → no block")

    # 9. never trimmed: the speaker block survives an over-budget memory block.
    big_mem = "You know the following:\n" + "\n".join(f"- fact {i}: " + ("x" * 200) for i in range(40))
    p2 = build_system_prompt(2, 5, core_block="core", memory_block=big_mem, speaker="Jon")
    assert "speaking with Jon" in p2, "speaker block trimmed under budget pressure"
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

    print("  OFFLINE: all speaker-awareness checks passed.")


if __name__ == "__main__":
    run()
    print()
