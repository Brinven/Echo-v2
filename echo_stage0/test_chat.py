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
         session=None, llm=None, registry=None, embedder=None, ib=None,
         on_sentence=None, doc_text=None, doc_name=None):
    session = session or _session()
    llm = llm or FakeLLM()
    logger = CaptureLogger()
    pipe = run_streaming_pipeline(
        audio=audio, stt=stt or BoobyTrapSTT(), llm=llm, tts=tts or BoobyTrapTTS(),
        audio_q=RemoteAudioSink(), logger=logger, history=[], sm=StateMachine(),
        session=session, vad_mode="ptt-only", ib=ib,
        speaker_embedder=embedder, speaker_registry=registry,
        typed_text=typed, location_hint=location_hint,
        on_sentence=on_sentence, doc_text=doc_text, doc_name=doc_name,
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


def run_streaming() -> None:
    """Chat streaming (2026-07-18): sentences flow out as the model produces them."""
    print("\n── Chat lane: streaming (offline) ──")

    # Pipeline side: on_sentence receives every reply sentence, and a broken consumer
    # never costs the reply.
    got = []
    pipe, _, _, logger = _run(typed="stream me", on_sentence=got.append)
    assert got == ["Right.", "Noted."], got
    assert "Right." in logger.kw["response_full"]
    print("  [PASS] on_sentence gets each sentence; reply still lands in full")

    def boom(_s):
        raise RuntimeError("consumer died")
    pipe, _, _, logger = _run(typed="stream me anyway", on_sentence=boom)
    assert pipe is not None and "Right." in logger.kw["response_full"]
    print("  [PASS] a raising consumer never costs the reply")

    # Slot side: stream=True carries a queue; finish_remote_turn pushes the sentinel.
    control, _, _ = _mk_control()
    slot = control.submit_remote_turn(None, typed_text="x", stream=True)
    assert slot["stream_q"] is not None
    plain = None
    control.take_pending_remote()
    control.finish_remote_turn(slot, {"ok": True, "reply": "done now"})
    kind, payload = slot["stream_q"].get_nowait()
    assert kind == "done" and payload["reply"] == "done now"
    print("  [PASS] stream slot carries a queue; finish pushes the done sentinel")

    plain = control.submit_remote_turn(None, typed_text="y")
    assert plain["stream_q"] is None, "non-stream slots must not grow a queue"
    control.take_pending_remote()
    control.finish_remote_turn(plain, {"ok": True})
    print("  [PASS] non-stream slots unchanged")

    # Route side: stream:true → NDJSON (sentences then a done trailer, audio stripped).
    control, _, _ = _mk_control()
    app = create_app(control)
    client = app.test_client()

    def loop():
        for _ in range(300):
            slot = control.take_pending_remote()
            if slot is not None:
                slot["stream_q"].put(("sentence", "First bit."))
                slot["stream_q"].put(("sentence", "Second bit."))
                control.finish_remote_turn(slot, {
                    "ok": True, "signoff": False, "transcript": "hi",
                    "reply": "First bit. Second bit.", "speaker": "Michael",
                    "speaker_score": None, "wav_b64": "STRIP-ME", "sample_rate": 24000,
                })
                return
            time.sleep(0.01)

    threading.Thread(target=loop, daemon=True).start()
    r = client.post("/api/chat/turn", json={"text": "hi", "stream": True})
    assert r.status_code == 200 and r.mimetype == "application/x-ndjson"
    import json as _json
    lines = [_json.loads(l) for l in r.data.decode("utf-8").splitlines() if l.strip()]
    assert [l.get("sentence") for l in lines[:2]] == ["First bit.", "Second bit."]
    trailer = lines[-1]
    assert trailer["done"] is True and trailer["ok"] is True
    assert trailer["reply"] == "First bit. Second bit."
    assert "wav_b64" not in trailer and "sample_rate" not in trailer
    print("  [PASS] NDJSON route: sentence lines then a clean trailer, audio stripped")


def run_documents() -> None:
    """Document attach (2026-07-18): extract → ride the turn → keep-latest-doc."""
    print("\n── Chat lane: documents (offline) ──")

    from webui.doc_extract import extract_doc, DOC_MAX_CHARS
    from llm import doc_content, collapse_doc_history, DOC_MARKER, DOC_COLLAPSED_NOTE

    assert extract_doc(b"hello notes\nline two", "notes.txt") == "hello notes\nline two"
    assert extract_doc(b"# title\nbody", "readme.md").startswith("# title")
    assert extract_doc(b"", "empty.txt") is None
    assert extract_doc(b"\x00\x01binary", "data.exe") is None, "unknown binary must not pass"
    big = ("word " * (DOC_MAX_CHARS // 2)).encode()
    out = extract_doc(big, "big.txt")
    assert len(out) < DOC_MAX_CHARS + 100 and "truncated" in out
    print("  [PASS] text extraction: plain/md pass, empty/binary refused, cap + marker")

    # A real (tiny) DOCX round-trip — python-docx writes it, extract_doc reads it back.
    import io
    import docx as _docx
    d = _docx.Document()
    d.add_paragraph("The goats are plotting again.")
    buf = io.BytesIO()
    d.save(buf)
    assert "goats are plotting" in extract_doc(buf.getvalue(), "note.docx")
    print("  [PASS] docx round-trip extracts")

    # A minimal but VALID PDF (pypdf requires a byte-accurate xref, so build it computed).
    stream = b"BT /F1 12 Tf 72 720 Td (Feed at dawn) Tj ET"
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length " + str(len(stream)).encode() + b">>stream\n" + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(pdf))
        pdf += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(pdf)
    pdf += b"xref\n0 6\n0000000000 65535 f \n"
    for off in offsets:
        pdf += f"{off:010d} 00000 n \n".encode()
    pdf += (b"trailer\n<</Size 6/Root 1 0 R>>\nstartxref\n"
            + str(xref_pos).encode() + b"\n%%EOF")
    got = extract_doc(bytes(pdf), "chores.pdf")
    assert got and "Feed at dawn" in got, got
    print("  [PASS] pdf extraction reads a real text object")

    # doc_content / collapse round-trip: the fences are load-bearing.
    msg = doc_content("what does it say?", "line one\n\nline two", "notes.txt")
    assert msg.startswith(DOC_MARKER) and msg.endswith("what does it say?")
    history = [{"role": "user", "content": msg},
               {"role": "assistant", "content": "It says lines."},
               {"role": "user", "content": "plain follow-up"}]
    n = collapse_doc_history(history)
    assert n == 1
    assert history[0]["content"] == (
        f"{DOC_MARKER}notes.txt]\n{DOC_COLLAPSED_NOTE}\n\nwhat does it say?")
    assert collapse_doc_history(history) == 0, "collapse must be idempotent"
    assert history[2]["content"] == "plain follow-up"
    print("  [PASS] keep-latest-doc: collapse keeps header+question, idempotent")

    # Pipeline: the doc rides the LLM message and history; transcript/log stay bare.
    ib = _mk_ib()
    pipe, session, llm_f, logger = _run(typed="what's in the file?", ib=ib,
                                        doc_text="secret contents here", doc_name="f.txt")
    ib.close()
    assert "secret contents here" in llm_f.last_user_msg
    assert logger.kw["transcript"] == "what's in the file?", "doc leaked into the transcript"
    assert logger.kw["doc_attached"] is True and logger.kw["doc_name"] == "f.txt"
    assert session.turns[0]["content"] == "what's in the file?"
    print("  [PASS] doc rides the LLM message only; transcript/log/gate see the question")

    # Route: doc_b64 attaches; garbage degrades to a text-only turn with doc_dropped.
    control, _, _ = _mk_control()
    app = create_app(control)
    client = app.test_client()

    seen = {}

    def loop():
        for _ in range(300):
            slot = control.take_pending_remote()
            if slot is not None:
                seen.update({k: slot.get(k) for k in ("doc_text", "doc_name")})
                control.finish_remote_turn(slot, {"ok": True, "reply": "read it",
                                                  "speaker": "Michael", "speaker_score": None})
                return
            time.sleep(0.01)

    import base64 as _b64
    threading.Thread(target=loop, daemon=True).start()
    r = client.post("/api/chat/turn", json={
        "text": "read this", "doc_b64": _b64.b64encode(b"chore list: feed goats").decode(),
        "doc_name": "chores.txt"})
    j = r.get_json()
    assert j["ok"] is True and j["doc_attached"] is True and "doc_dropped" not in j
    assert seen["doc_text"] == "chore list: feed goats" and seen["doc_name"] == "chores.txt"
    print("  [PASS] route: doc extracts on the web thread and rides the slot")

    threading.Thread(target=loop, daemon=True).start()
    r = client.post("/api/chat/turn", json={
        "text": "read this too", "doc_b64": _b64.b64encode(b"\x00\x01\x02junk").decode(),
        "doc_name": "mystery.bin"})
    j = r.get_json()
    assert j["ok"] is True and j["doc_attached"] is False and j["doc_dropped"] == "unreadable"
    print("  [PASS] unreadable doc degrades to a text-only turn, never costs the question")


def _mk_control():
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
    return control, session, sm


if __name__ == "__main__":
    try:
        run_pipeline_text_mode()
        run_location_hint()
        run_typed_commands()
        run_voice_control()
        run_route()
        run_streaming()
        run_documents()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    print("\n  OFFLINE: all chat-lane checks passed.\n")
