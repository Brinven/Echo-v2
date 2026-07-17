"""
Offline tests for Remote Voice (Level 2) — no mic, no model, no phone.

Covers the three pure/parked pieces:
  - webui.remote_audio.decode_to_pcm16k: wav in (any rate) → 16 kHz mono float32;
    garbage/empty → None; compressed round-trip when PyAV has an encoder for it.
  - webui.remote_audio.RemoteAudioSink: quacks like AudioQueue without blocking or
    playing; wav_bytes()/sink_to_b64() concatenate what the pipeline synthesized.
  - EchoControl submit/take/finish: single-flight park contract (busy refusal, event
    wake, re-arm after finish) + the /api/remote/turn route against a fake main loop.

Run:  python test_remote_voice.py     (exit 0 = all assertions passed)
"""

import io
import sys
import time
import base64
import threading

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
import soundfile as sf

import webui.server as server_mod
from webui import EchoControl, create_app
from webui.remote_audio import (
    SAMPLE_RATE, RemoteAudioSink, decode_to_pcm16k, sink_to_b64,
)
from session import Session
from state import StateMachine


def _wav(seconds=1.0, sr=16000, freq=440.0) -> bytes:
    t = np.linspace(0.0, seconds, int(sr * seconds), endpoint=False)
    sig = (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, sig, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def _encoded(fmt="mp4", codec="aac", sr=44100, seconds=1.0) -> bytes | None:
    """A compressed clip like a phone would upload (iOS = mp4/AAC). None if PyAV
    lacks the encoder — the DEcode path is what ships; encoding is test scaffolding."""
    try:
        import av
        t = np.linspace(0.0, seconds, int(sr * seconds), endpoint=False)
        sig = (0.3 * np.sin(2 * np.pi * 440.0 * t) * 32767).astype(np.int16)
        buf = io.BytesIO()
        with av.open(buf, "w", format=fmt) as container:
            stream = container.add_stream(codec, rate=sr)
            step = 1024  # AAC frame size; feeding exact frames keeps every PyAV happy
            for i in range(0, len(sig) - step + 1, step):
                frame = av.AudioFrame.from_ndarray(sig[i:i + step][None, :],
                                                   format="s16", layout="mono")
                frame.sample_rate = sr
                for pkt in stream.encode(frame):
                    container.mux(pkt)
            for pkt in stream.encode(None):
                container.mux(pkt)
        return buf.getvalue()
    except Exception:
        return None


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


def _fake_main_loop(control, reply="Hello from the desk.") -> threading.Thread:
    """Claim the next parked remote turn and publish a result, like main.handle_remote_turn."""
    def run():
        for _ in range(300):
            slot = control.take_pending_remote()
            if slot is not None:
                control.finish_remote_turn(slot, {
                    "ok": True, "signoff": False, "transcript": "hi there",
                    "reply": reply, "speaker": "Michael", "speaker_score": 0.61,
                })
                return
            time.sleep(0.01)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def run() -> None:
    print("\n── Remote Voice: decode seam (offline) ──")

    pcm = decode_to_pcm16k(_wav(seconds=1.0, sr=16000))
    assert pcm is not None and pcm.dtype == np.float32
    assert abs(len(pcm) - SAMPLE_RATE) < SAMPLE_RATE * 0.02, len(pcm)
    assert float(np.abs(pcm).max()) <= 1.0
    print("  [PASS] 16 kHz wav → float32 pcm, length preserved, values in [-1, 1]")

    pcm44 = decode_to_pcm16k(_wav(seconds=1.0, sr=44100))
    assert pcm44 is not None and abs(len(pcm44) - SAMPLE_RATE) < SAMPLE_RATE * 0.02, len(pcm44)
    print("  [PASS] 44.1 kHz wav resampled to 16 kHz (length ~1s)")

    assert decode_to_pcm16k(b"") is None
    assert decode_to_pcm16k(b"this is not audio " * 64) is None
    print("  [PASS] empty / garbage bytes → None (never raises)")

    aac = _encoded()
    if aac:
        pcm_aac = decode_to_pcm16k(aac)
        assert pcm_aac is not None and abs(len(pcm_aac) - SAMPLE_RATE) < SAMPLE_RATE * 0.1, \
            None if pcm_aac is None else len(pcm_aac)
        print("  [PASS] mp4/AAC (what an iPhone uploads) decodes to ~1s of 16 kHz pcm")
    else:
        print("  [SKIP] PyAV built without an AAC encoder — decode path still covered by wav")

    print("\n── Remote Voice: collecting sink (offline) ──")

    sink = RemoteAudioSink()
    t0 = time.perf_counter()
    sink.start()
    sink.enqueue(np.zeros(24000, dtype=np.float32), 24000)          # 1.0s
    sink.enqueue(np.zeros((12000, 2), dtype=np.float32), 24000)     # 0.5s stereo → mono
    sink.finish()
    sink.wait_done()
    assert (time.perf_counter() - t0) < 0.5, "sink must never block (nothing plays)"
    assert sink.is_playing is False and sink.chunk_count == 2
    wav = sink.wav_bytes()
    assert wav is not None
    data, sr = sf.read(io.BytesIO(wav[0]), dtype="float32")
    assert sr == 24000 and abs(len(data) - 36000) < 10, (sr, len(data))
    print("  [PASS] start/enqueue/finish/wait_done are non-blocking; wav concatenates 1.5s @ 24k")

    mixed = RemoteAudioSink()
    mixed.enqueue(np.zeros(24000, dtype=np.float32), 24000)
    mixed.enqueue(np.zeros(16000, dtype=np.float32), 16000)         # 1s at a different rate
    data2, sr2 = sf.read(io.BytesIO(mixed.wav_bytes()[0]), dtype="float32")
    assert sr2 == 24000 and abs(len(data2) - 48000) < 30, (sr2, len(data2))
    print("  [PASS] a mixed-rate chunk is resampled to the first chunk's rate")

    assert RemoteAudioSink().wav_bytes() is None and sink_to_b64(RemoteAudioSink()) is None
    b64 = sink_to_b64(sink)
    assert b64 is not None and b64[1] == 24000
    rt_data, rt_sr = sf.read(io.BytesIO(base64.b64decode(b64[0])), dtype="float32")
    assert rt_sr == 24000 and len(rt_data) == len(data)
    print("  [PASS] empty sink → None; sink_to_b64 round-trips back to the same audio")

    print("\n── Remote Voice: park contract (offline) ──")

    control, session, sm = _mk_control()
    pcm1 = np.zeros(SAMPLE_RATE, dtype=np.float32)
    slot = control.submit_remote_turn(pcm1)
    assert slot is not None and control.pending_remote is slot
    assert control.submit_remote_turn(pcm1) is None, "second submit while parked must refuse"
    print("  [PASS] submit parks a slot; a second submit is refused (single-flight)")

    taken = control.take_pending_remote()
    assert taken is slot and control.pending_remote is None
    assert control.submit_remote_turn(pcm1) is None, "submit while IN FLIGHT must refuse"
    assert control.snapshot()["remote_busy"] is True
    print("  [PASS] take claims + marks busy; submit still refused mid-flight (snapshot shows it)")

    control.finish_remote_turn(taken, {"ok": True, "reply": "done"})
    assert taken["event"].is_set() and taken["result"]["reply"] == "done"
    assert control.snapshot()["remote_busy"] is False
    assert control.submit_remote_turn(pcm1) is not None, "finish must re-arm the slot"
    control.take_pending_remote()
    control.finish_remote_turn(control.pending_remote or taken, {"ok": True})  # reset busy
    print("  [PASS] finish publishes the result, wakes the waiter, re-arms submissions")

    print("\n── Remote Voice: /api/remote/turn route (offline) ──")

    control, session, sm = _mk_control()
    app = create_app(control)
    client = app.test_client()

    assert b"Hold to Talk" in client.get("/remote").data
    print("  [PASS] /remote serves the phone page")

    _fake_main_loop(control)
    r = client.post("/api/remote/turn", data=_wav(1.0),
                    headers={"Content-Type": "audio/wav"})
    j = r.get_json()
    assert r.status_code == 200 and j["ok"] is True and j["reply"] == "Hello from the desk."
    assert j["speaker"] == "Michael"
    print("  [PASS] a wav upload rides park→claim→finish and returns the published reply")

    r = client.post("/api/remote/turn", data=b"")
    assert r.status_code == 400 and r.get_json()["error"] == "empty"
    r = client.post("/api/remote/turn", data=b"junk" * 200)
    assert r.status_code == 400 and r.get_json()["error"] == "undecodable"
    r = client.post("/api/remote/turn", data=_wav(0.1))
    assert r.status_code == 400 and r.get_json()["error"] == "too-short"
    print("  [PASS] empty / undecodable / sub-0.3s uploads → clean 400s, nothing parked")

    parked = control.submit_remote_turn(np.zeros(SAMPLE_RATE, dtype=np.float32))
    assert parked is not None
    r = client.post("/api/remote/turn", data=_wav(1.0))
    assert r.status_code == 409 and r.get_json()["error"] == "busy"
    control.take_pending_remote()
    control.finish_remote_turn(parked, {"ok": True})
    print("  [PASS] a parked turn makes the next upload 409 busy (single-flight holds over HTTP)")

    old_wait = server_mod.REMOTE_WAIT_S
    server_mod.REMOTE_WAIT_S = 0.2
    try:
        t0 = time.perf_counter()
        r = client.post("/api/remote/turn", data=_wav(1.0))
        took = time.perf_counter() - t0
        assert r.status_code == 504 and r.get_json()["error"] == "timeout"
        assert took < 5.0, took
    finally:
        server_mod.REMOTE_WAIT_S = old_wait
        slot = control.take_pending_remote()
        if slot is not None:
            control.finish_remote_turn(slot, {"ok": False})
    print("  [PASS] no main loop → 504 timeout after REMOTE_WAIT_S (request never hangs)")

    print("\n  All Remote Voice checks passed.\n")


if __name__ == "__main__":
    run()
