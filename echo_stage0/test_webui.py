"""
Offline tests for the Stage 7 dashboard (no live voice loop, no mic, no model).

Drives the Flask routes with the test client against an EchoControl wrapping a real Session +
a StateMachine + an in-memory SpeakerRegistry, and asserts each POST flips the right session
flag / sets the right Event / updates the threshold. Health probes are stubbed (no network).

Run:  python test_webui.py     (exit 0 = all assertions passed)
"""

import sys
import threading
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import webui.control as ctrlmod
from webui import EchoControl, create_app, load_webui_config
from session import Session
from state import StateMachine
from speaker_id import SpeakerRegistry


_FAKE_MODELS = ["test/model-x", "test/model-y", "vendor/big-12b@q4"]


def _mk(speaker_active=True, registry=True, vad_available=True, list_models=None):
    session = Session(model="m", stt_backend="b", tts_backend="t", user_name="Michael")
    sm = StateMachine()
    # IMPORTANT: give the registry a TEMP path so save() (via /api/threshold) never touches
    # the real echo_speakers.json — a test must not pollute production state.
    reg = SpeakerRegistry(path=Path(tempfile.mkdtemp()) / "echo_speakers.json", config={
        "enabled": True, "model": "ecapa", "match_threshold": 0.30,
        "profiles": [{"name": "Michael", "model": "ecapa", "embedding": [1.0, 0.0, 0.0, 0.0]}],
    }) if registry else None
    events = {k: threading.Event() for k in
              ("space_pressed", "space_released", "mute_toggle_event", "quit_event")}
    control = EchoControl(
        session, sm, reg,
        space_pressed=events["space_pressed"], space_released=events["space_released"],
        mute_toggle_event=events["mute_toggle_event"], quit_event=events["quit_event"],
        model_name="test/model-x", speaker_active=speaker_active,
        vad_available=vad_available,
        list_models=list_models if list_models is not None else (lambda: list(_FAKE_MODELS)),
    )
    return control, session, sm, reg, events


def run() -> None:
    print("\n── Dashboard (offline) ──")

    control, session, sm, reg, events = _mk()
    app = create_app(control)
    client = app.test_client()

    # 1. /api/state shape.
    st = client.get("/api/state").get_json()
    for key in ("state", "muted", "current_speaker", "snark", "location", "web_search_off",
                "transcript", "speaker_active", "enrolled", "match_threshold", "model"):
        assert key in st, f"missing {key} in /api/state"
    assert st["state"] == "LISTENING" and st["enrolled"] == ["Michael"] and st["match_threshold"] == 0.30
    print("  [PASS] /api/state returns the full live snapshot")

    # 2. mute toggle flips control.muted + sets the event.
    assert control.muted is False
    r = client.post("/api/mute").get_json()
    assert r["muted"] is True and control.muted is True and events["mute_toggle_event"].is_set()
    client.post("/api/mute")
    assert control.muted is False
    print("  [PASS] /api/mute toggles control.muted and signals the loop")

    # 3. snark: level sets daily + clears max; max toggles.
    session.max_snark = True
    client.post("/api/snark", json={"level": 8})
    assert session.daily_snark == 8 and session.max_snark is False and session.effective_snark == 8
    assert client.post("/api/snark", json={"max": True}).get_json()["max_snark"] is True
    assert session.effective_snark == 10
    print("  [PASS] /api/snark sets a level (clears max) and toggles Max Snark")

    # 4. location (valid + rejected) and web-search toggle.
    assert client.post("/api/location", json={"location": "jeep"}).get_json()["ok"] is True
    assert session.location == "jeep"
    assert client.post("/api/location", json={"location": "moon"}).get_json()["ok"] is False
    client.post("/api/websearch", json={"off": True})
    assert session.web_search_off is True
    print("  [PASS] /api/location (valid/invalid) and /api/websearch flip session flags")

    # 5. talk press/release set the PTT events the loop polls.
    client.post("/api/talk/press")
    assert events["space_pressed"].is_set()
    client.post("/api/talk/release")
    assert events["space_released"].is_set()
    print("  [PASS] /api/talk press/release set the space Events (PTT)")

    # 6. enroll arms session.enrolling only when speaker awareness is active.
    r = client.post("/api/enroll", json={"name": "jon"}).get_json()
    assert r["enrolling"] == "Jon" and session.enrolling == "Jon"
    client.post("/api/enroll", json={"cancel": True})
    assert session.enrolling is None
    print("  [PASS] /api/enroll arms + cancels enrollment (name title-cased)")

    # 6b. with speaker awareness OFF, enroll is refused (no stuck flag).
    off_control, off_session, *_ = _mk(speaker_active=False)
    off_client = create_app(off_control).test_client()
    r = off_client.post("/api/enroll", json={"name": "Jon"}).get_json()
    assert r["ok"] is False and off_session.enrolling is None
    print("  [PASS] enroll refused when speaker awareness is off (no stuck enrolling flag)")

    # 7. threshold updates the live registry + persists; None registry → not ok.
    r = client.post("/api/threshold", json={"value": 0.55}).get_json()
    assert r["ok"] is True and abs(r["match_threshold"] - 0.55) < 1e-9
    assert abs(reg.match_threshold - 0.55) < 1e-9, "threshold not live on the registry"
    noreg_control, *_ = _mk(registry=False)
    assert create_app(noreg_control).test_client().post(
        "/api/threshold", json={"value": 0.5}).get_json()["ok"] is False
    print("  [PASS] /api/threshold updates the live registry (and no-ops without one)")

    # 8. quit sets the shutdown event.
    client.post("/api/quit")
    assert events["quit_event"].is_set()
    print("  [PASS] /api/quit sets the quit Event")

    # 8b. /api/vad — hands-free toggle (Stage 8).
    session.vad_enabled = False
    r = client.post("/api/vad", json={}).get_json()
    assert r["ok"] is True and r["vad_enabled"] is True and session.vad_enabled is True
    r = client.post("/api/vad", json={}).get_json()          # toggles back
    assert r["vad_enabled"] is False and session.vad_enabled is False
    r = client.post("/api/vad", json={"enabled": True}).get_json()   # explicit set
    assert r["vad_enabled"] is True and session.vad_enabled is True
    st = client.get("/api/state").get_json()
    assert st["vad_available"] is True and st["vad_enabled"] is True
    print("  [PASS] /api/vad toggles + explicitly sets hands-free")

    # 8c. Without webrtcvad there is nothing to turn on — the toggle must refuse, not lie.
    c2, s2, _, _, _ = _mk(vad_available=False)
    cl2 = create_app(c2).test_client()
    s2.vad_enabled = False
    r = cl2.post("/api/vad", json={"enabled": True}).get_json()
    assert r["ok"] is False and r["vad_enabled"] is False and s2.vad_enabled is False
    assert cl2.get("/api/state").get_json()["vad_enabled"] is False
    print("  [PASS] /api/vad refused when webrtcvad is absent (no phantom hands-free)")

    # 8d. Location changes re-apply the hands-free default (jeep = road noise = manual).
    client.post("/api/location", json={"location": "jeep"})
    assert session.location == "jeep" and session.vad_enabled is False
    client.post("/api/location", json={"location": "home"})
    assert session.location == "home" and session.vad_enabled is True
    print("  [PASS] location change re-applies the VAD default (home on / jeep off)")

    # 8e. Model swap is PARKED for the main loop, never applied by the web thread.
    models = client.get("/api/models").get_json()
    assert models["models"] == _FAKE_MODELS and models["current"] == "test/model-x"
    r = client.post("/api/model", json={"name": "test/model-y"}).get_json()
    assert r["ok"] is True and control.pending_model == "test/model-y"
    assert client.get("/api/state").get_json()["pending_model"] == "test/model-y"
    # The main loop claims it exactly once.
    assert control.take_pending_model() == "test/model-y" and control.pending_model is None
    assert control.take_pending_model() is None
    # A model LM Studio doesn't list must be rejected (no typo'd model wedging the loop).
    r = client.post("/api/model", json={"name": "not/a-real-model"}).get_json()
    assert r["ok"] is False and control.pending_model is None
    r = client.post("/api/model", json={"name": ""}).get_json()
    assert r["ok"] is False and control.pending_model is None
    print("  [PASS] /api/models lists; /api/model parks a valid swap and rejects unknown ones")

    # 8f. LM Studio unreachable → empty list, no exception into the route.
    c3, _, _, _, _ = _mk(list_models=lambda: (_ for _ in ()).throw(RuntimeError("LM Studio down")))
    cl3 = create_app(c3).test_client()
    assert cl3.get("/api/models").get_json()["models"] == []
    assert cl3.post("/api/model", json={"name": "test/model-y"}).get_json()["ok"] is False
    print("  [PASS] /api/models fail-soft when LM Studio is down")

    # 9. health() returns a dict; probes stubbed (no network).
    real_get = ctrlmod.httpx.get
    try:
        ctrlmod.httpx.get = lambda url, timeout=None: object()      # any response = up
        control._health_ts = 0.0
        h = control.health()
        assert h["lm_studio"] is True and h["kokoro"] is True and h["model"] == "test/model-x"
        def _boom(url, timeout=None):
            raise ctrlmod.httpx.ConnectError("nope")
        ctrlmod.httpx.get = _boom
        control._health_ts = 0.0
        h = control.health()
        assert h["lm_studio"] is False and h["kokoro"] is False
    finally:
        ctrlmod.httpx.get = real_get
    print("  [PASS] health(): reachable→up, connection error→down (cached, no hammering)")

    # 10. recent_scores returns a list (parses the JSONL log defensively).
    assert isinstance(control.recent_scores(5), list)
    # config loader is fail-soft and complete.
    cfg = load_webui_config()
    for key in ("enabled", "host", "port", "poll_ms"):
        assert key in cfg, key
    print("  [PASS] recent_scores → list; webui config loader complete")

    print("  OFFLINE: all dashboard checks passed.")


if __name__ == "__main__":
    run()
    print()
