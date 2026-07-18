"""
Offline tests for the chat lane (typed turns) + the per-turn location hint (2026-07-18).

Model-free, mic-free, speaker-free. The load-bearing trick: the TTS stub RAISES if it is
ever called on a typed turn — "replies are text-only" is proven structurally, not assumed.
The voice control-run uses a benign TTS and pins that the spoken path still carries
VOICE_GUIDANCE and logs typed=False (the solo-path invariant, again).

Run:  python test_chat.py     (exit 0 = all assertions passed)
"""

import sys
import shutil
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from main import run_streaming_pipeline, SAMPLE_RATE
from session import Session
from state import StateMachine
from webui import EchoControl, create_app
from webui.remote_audio import RemoteAudioSink

import ib_lite.ib_lite as ib_mod
import ib_lite.retrieval as ret_mod
from ib_lite.ib_lite import IbLite

# The guidance swap (VOICE vs TEXT) lives in build_context_block, so these tests need a
# real IbLite on a temp DB. Model-free: both encode imports stubbed, the gate neutered.
ib_mod.encode = lambda text: b"\x00\x00\x80\x3f" * 4
ret_mod.encode = lambda text: b"\x00\x00\x80\x3f" * 4

_TMP = Path(tempfile.mkdtemp(prefix="echo_test_chat_"))


def _mk_ib() -> IbLite:
    ib = IbLite("fake-model", db_path=_TMP / f"chat_{time.monotonic_ns()}.db")
    ib.start_session("s1")
    ib.write_memory = lambda *a, **k: None   # never call the real gate/LM Studio
    return ib


# ── fakes ────────────────────────────────────────────────────────────────

class BoobyTrapTTS:
    """A typed turn must never synthesize. Any call is a hard failure."""
    backend = "trap"

    def synthesize(self, text, voice=None):
        raise AssertionError(f"TTS called on a typed turn: {text!r}")


class QuietTTS:
    """Voice control-run TTS: returns a tiny buffer, counts calls."""
    backend = "quiet"

    def __init__(self):
        self.calls = 0

    def synthesize(self, text, voice=None):
        self.calls += 1
        return np.zeros(64, dtype=np.float32), 24000


class BoobyTrapSTT:
    """A typed turn must never transcribe."""
    backend = "trap"
    model_size = "none"

    def transcribe(self, audio):
        raise AssertionError("STT called on a typed turn")


class CannedSTT:
    backend = "canned"
    model_size = "base"

    def __init__(self, text):
        self.text = text

    def transcribe(self, audio):
        return self.text


class FakeLLM:
    model_name = "test/model-x"

    def __init__(self, sentences=("Right.", "Noted.")):
        self.sentences = list(sentences)
        self.last_system_prompt = None
        self.last_user_msg = None

    def stream_sentences(self, user_msg, history, timing=None, system_prompt=None,
                         image_b64=None, image_mime=None):
        self.last_system_prompt = system_prompt
        self.last_user_msg = user_msg
        if timing is not None:
            timing["ttft"] = 0.05
        yield from self.sentences


class CaptureLogger:
    def __init__(self):
        self.kw = None

    def log_run(self, **kw):
        self.kw = kw


class FakeRegistry:
    """Just enough registry for the guards a typed turn touches."""
    count = 1
    active_count = 1

    def is_ignored(self, name):
        return False


def _session():
    s = Session(model="m", stt_backend="b", tts_backend="t", user_name="Michael")
    s.save_session_file = lambda: None   # never write real files from a test
    return s


def _run(typed=None, audio=None, stt=None, tts=None, location_hint=None,
         session=None, llm=None, registry=None, embedder=None, ib=None):
    session = session or _session()
    llm = llm or FakeLLM()
    logger = CaptureLogger()
    pipe = run_streaming_pipeline(
        audio=audio, stt=stt or BoobyTrapSTT(), llm=llm, tts=tts or BoobyTrapTTS(),
        audio_q=RemoteAudioSink(), logger=logger, history=[], sm=StateMachine(),
        session=session, vad_mode="ptt-only", ib=ib,
        speaker_embedder=embedder, speaker_registry=registry,
        typed_text=typed, location_hint=location_hint,
    )
    return pipe, session, llm, logger


def run_pipeline_text_mode() -> None:
    print("\n── Chat lane: pipeline text mode (offline) ──")

    ib = _mk_ib()
    pipe, session, llm, logger = _run(typed="hey Echo, how's it going", ib=ib)
    ib.close()
    assert pipe is not None and pipe.get("passed") is None, pipe
    assert logger.kw["typed"] is True and logger.kw["passed_budget"] is None
    assert logger.kw["speaker"] == "Michael" and logger.kw["speaker_score"] is None
    assert logger.kw["transcript"] == "hey Echo, how's it going"
    assert "Right." in logger.kw["response_full"]
    print("  [PASS] typed turn: full reply, no STT, no TTS (both booby-trapped), Michael by policy")

    # VOICE_GUIDANCE's distinctive opening — TEXT_GUIDANCE itself contains the words
    # "not speaking aloud", so match the full voice phrase, not the fragment.
    assert "typing in a chat window" in llm.last_system_prompt
    assert "You are speaking aloud, not writing" not in llm.last_system_prompt
    print("  [PASS] typed turn carries TEXT_GUIDANCE, not VOICE_GUIDANCE")

    # Empty typed text is a no-op, not a crash.
    pipe, _, _, logger = _run(typed="   ")
    assert pipe is None and logger.kw is None
    print("  [PASS] blank typed turn → None, nothing logged")


def run_location_hint() -> None:
    print("\n── Chat lane: per-turn location hint (offline) ──")

    session = _session()
    session.location = "home"
    _, _, llm, logger = _run(typed="how far to the feed store", session=session,
                             location_hint="away")
    assert logger.kw["location"] == "away" and logger.kw["location_hint"] == "away"
    assert session.location == "home", "hint must NOT stick to the session"
    assert "out and about" in llm.last_system_prompt, "away register missing"
    print("  [PASS] hint overrides the turn's register + log; session.location untouched")

    _, _, llm, logger = _run(typed="hello there")
    assert logger.kw["location_hint"] is None
    print("  [PASS] no hint → session location, hint logged null")


def run_typed_commands() -> None:
    print("\n── Chat lane: typed commands (offline) ──")

    # Max snark typed: the command fires, the reply is text, TTS stays booby-trapped.
    pipe, session, _, _ = _run(typed="Echo, maximum snark mode")
    assert pipe is not None and session.max_snark is True
    assert session.turns[-1]["content"].startswith("Maximum snark")
    print("  [PASS] typed max-snark flips the flag, silent reply recorded")

    # Sign-off typed: returns the signoff contract.
    pipe, _, _, _ = _run(typed="Echo, that's all for now")
    assert pipe is not None and pipe.get("signoff") is True
    print("  [PASS] typed sign-off returns the signoff contract")

    # An ARMED enrollment survives a typed turn (no voice to fingerprint) — and the
    # armed name is untouched so the next SPOKEN utterance still becomes the print.
    session = _session()
    session.enrolling = "John"
    pipe, session, _, logger = _run(typed="tell me something good", session=session,
                                    registry=FakeRegistry(), embedder=object())
    assert session.enrolling == "John", "typed turn must not consume an armed enrollment"
    assert logger.kw["typed"] is True
    print("  [PASS] armed enrollment survives a typed turn")


def run_voice_control() -> None:
    print("\n── Chat lane: voice path unchanged (offline control-run) ──")

    tts = QuietTTS()
    audio = np.zeros(SAMPLE_RATE, dtype=np.float32)
    ib = _mk_ib()
    pipe, _, llm, logger = _run(audio=audio, stt=CannedSTT("hello there Echo"), tts=tts, ib=ib)
    ib.close()
    assert pipe is not None and logger.kw["typed"] is False
    assert logger.kw["speaker_score"] is not None
    assert tts.calls > 0, "voice path must still synthesize"
    assert "You are speaking aloud, not writing" in llm.last_system_prompt
    assert "typing in a chat window" not in llm.last_system_prompt
    print("  [PASS] voice turn: TTS fires, VOICE_GUIDANCE carried, typed=False logged")


def run_route() -> None:
    print("\n── Chat lane: /api/chat/turn route (offline) ──")

    session = Session(model="m", stt_backend="b", tts_backend="t", user_name="Michael")
    sm = StateMachine()
    events = {k: threading.Event() for k in
              ("space_pressed", "space_released", "mute_toggle_event", "quit_event")}
    control = EchoControl(
        session, sm, None,
        space_pressed=events["space_pressed"], space_released=events["space_released"],
        mute_toggle_event=events["mute_toggle_event"], quit_event=events["quit_event"],
        model_name="test/model-x", speaker_active=False, vad_available=False,
        list_models=lambda: [], list_voices=lambda: [], model_state=lambda: "loaded",
    )
    app = create_app(control)
    client = app.test_client()

    assert b"Chat" in client.get("/chat").data
    print("  [PASS] /chat serves the page")

    claimed = {}

    def fake_loop():
        for _ in range(300):
            slot = control.take_pending_remote()
            if slot is not None:
                claimed.update(slot)
                control.finish_remote_turn(slot, {
                    "ok": True, "signoff": False, "transcript": slot["typed_text"],
                    "reply": "Typed back at you.", "speaker": "Michael",
                    "speaker_score": None,
                    "wav_b64": "SHOULD-BE-STRIPPED", "sample_rate": 24000,
                })
                return
            time.sleep(0.01)

    threading.Thread(target=fake_loop, daemon=True).start()
    r = client.post("/api/chat/turn", json={"text": "hey Echo", "location": "AWAY"})
    j = r.get_json()
    assert r.status_code == 200 and j["ok"] is True and j["reply"] == "Typed back at you."
    assert "wav_b64" not in j and "sample_rate" not in j, "typed replies are text-only"
    assert claimed["typed_text"] == "hey Echo" and claimed["audio"] is None
    assert claimed["location_hint"] == "away", "hint must be case-normalized"
    print("  [PASS] typed turn parks with text+hint, reply returns, audio fields stripped")

    r = client.post("/api/chat/turn", json={"text": "   "})
    assert r.status_code == 400 and r.get_json()["error"] == "empty"
    r = client.post("/api/chat/turn", json={})
    assert r.status_code == 400
    print("  [PASS] empty / missing text → 400")

    parked = control.submit_remote_turn(None, typed_text="occupies the slot")
    assert parked is not None
    r = client.post("/api/chat/turn", json={"text": "second"})
    assert r.status_code == 409 and r.get_json()["error"] == "busy"
    control.take_pending_remote()
    control.finish_remote_turn(parked, {"ok": True})
    print("  [PASS] single-flight: chat turn while one is parked → 409")

    # Garbage location degrades to None (auto), never an error.
    moon = {}

    def moon_loop():
        for _ in range(300):
            slot = control.take_pending_remote()
            if slot is not None:
                moon.update(slot)
                control.finish_remote_turn(slot, {"ok": True, "reply": "x"})
                return
            time.sleep(0.01)

    threading.Thread(target=moon_loop, daemon=True).start()
    r = client.post("/api/chat/turn", json={"text": "hi", "location": "the-moon"})
    assert r.status_code == 200
    assert moon["location_hint"] is None, "garbage hint must degrade to auto"
    print("  [PASS] unknown location hint degrades to auto, turn proceeds")


if __name__ == "__main__":
    try:
        run_pipeline_text_mode()
        run_location_hint()
        run_typed_commands()
        run_voice_control()
        run_route()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    print("\n  OFFLINE: all chat-lane checks passed.\n")
