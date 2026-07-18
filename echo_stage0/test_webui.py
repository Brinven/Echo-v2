"""
Offline tests for the Stage 7 dashboard (no live voice loop, no mic, no model).

Drives the Flask routes with the test client against an EchoControl wrapping a real Session +
a StateMachine + an in-memory SpeakerRegistry, and asserts each POST flips the right session
flag / sets the right Event / updates the threshold. Health probes are stubbed (no network).

Run:  python test_webui.py     (exit 0 = all assertions passed)
"""

import sys
import json
import threading
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import webui.control as ctrlmod
from webui import EchoControl, create_app, load_webui_config, memory_admin
from webui import history as history_mod
from session import Session
from state import StateMachine
from speaker_id import SpeakerRegistry
from ib_lite import db

# A stand-in embedder for fact-value edits: a valid 384-float32 zeros blob, so the memory tests
# never load the real MiniLM model (the production route uses memory_admin's default `encode`).
_FAKE_EMB = lambda text: b"\x00" * (384 * 4)


_FAKE_MODELS = ["test/model-x", "test/model-y", "vendor/big-12b@q4"]
_FAKE_VOICES = ["af_heart", "bf_emma", "am_michael"]


def _mk(speaker_active=True, registry=True, vad_available=True, list_models=None,
        list_voices=None, model_state=None):
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
        list_voices=list_voices if list_voices is not None else (lambda: list(_FAKE_VOICES)),
        model_state=model_state if model_state is not None else (lambda: "loaded"),
        voice_name="af_heart",
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

    # 1b. Each transcript turn carries the name recorded when it was SPOKEN, so the UI never has
    # to fall back on the live current_speaker — doing that re-labelled the entire backlog to
    # whoever talked last, and the readout quietly lied about who said what.
    session.add_user_turn("morning", 0.1)
    session.add_echo_turn("Morning, Michael.", 0.4)
    session.current_speaker = "Hillary"
    session.add_user_turn("I have a headache", 0.1, speaker="Hillary")
    turns = client.get("/api/state").get_json()["transcript"]
    assert [(t["speaker"], t["name"]) for t in turns] == [
        ("user", "Michael"), ("echo", None), ("user", "Hillary"),
    ], turns
    print("  [PASS] /api/state transcript keeps per-turn speaker names (not the live speaker)")

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
    assert session.enrolling_ignore is False, "a person must not arm as an ignored voice"
    client.post("/api/enroll", json={"cancel": True})
    assert session.enrolling is None
    print("  [PASS] /api/enroll arms + cancels enrollment (name title-cased)")

    # 6a2. Arming an IGNORED voice (the Kokoro clock) sets BOTH flags, and cancel clears both —
    # a stuck enrolling_ignore would silently enroll the next real person as furniture.
    r = client.post("/api/enroll", json={"name": "kairos", "ignore": True}).get_json()
    assert r["enrolling"] == "Kairos" and r["enrolling_ignore"] is True
    assert session.enrolling == "Kairos" and session.enrolling_ignore is True
    st = client.get("/api/state").get_json()
    assert st["enrolling_ignore"] is True, "the banner needs this to explain the arm-and-wait"
    client.post("/api/enroll", json={"cancel": True})
    assert session.enrolling is None and session.enrolling_ignore is False
    print("  [PASS] /api/enroll arms an ignored voice; cancel clears both flags")

    # 6a3. /api/state splits people from furniture. The chips list PEOPLE — a clock has no
    # business appearing as an enrolled guest.
    ig_control, _, _, ig_reg, _ = _mk()
    ig_reg.config["profiles"].append(
        {"name": "Kairos", "model": "ecapa", "ignore": True, "embedding": [0.0, 1.0, 0.0, 0.0]})
    ig_st = create_app(ig_control).test_client().get("/api/state").get_json()
    assert ig_st["enrolled"] == ["Michael"], ig_st["enrolled"]
    assert ig_st["ignored"] == ["Kairos"], ig_st["ignored"]
    print("  [PASS] /api/state lists people in `enrolled`, furniture in `ignored`")

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

    # 8g. Voice: same park-for-the-main-loop contract as the model (never mid-sentence).
    voices = client.get("/api/voices").get_json()
    assert voices["voices"] == _FAKE_VOICES and voices["current"] == "af_heart"
    r = client.post("/api/voice", json={"name": "bf_emma"}).get_json()
    assert r["ok"] is True and control.pending_voice == "bf_emma"
    st = client.get("/api/state").get_json()
    # Still the OLD voice until the main loop applies it — the swap must not land on the web thread.
    assert st["voice"] == "af_heart" and st["pending_voice"] == "bf_emma"
    assert control.take_pending_voice() == "bf_emma" and control.pending_voice is None
    control.set_voice_name("bf_emma")
    assert client.get("/api/state").get_json()["voice"] == "bf_emma"
    assert control.take_pending_voice() is None
    r = client.post("/api/voice", json={"name": "not_a_voice"}).get_json()
    assert r["ok"] is False and control.pending_voice is None
    print("  [PASS] /api/voices lists; /api/voice parks a valid voice and rejects unknown ones")

    # 8h. Kokoro unreachable → empty list, no exception into the route.
    c4, _, _, _, _ = _mk(list_voices=lambda: (_ for _ in ()).throw(RuntimeError("Kokoro down")))
    cl4 = create_app(c4).test_client()
    assert cl4.get("/api/voices").get_json()["voices"] == []
    assert cl4.post("/api/voice", json={"name": "bf_emma"}).get_json()["ok"] is False
    assert cl4.post("/api/voice/preview", json={"name": "bf_emma"}).get_json()["ok"] is False
    print("  [PASS] /api/voices + preview fail-soft when Kokoro is down")

    # 8i. Preview parks a sample line WITHOUT adopting the voice — a preview is not a commitment.
    control.set_voice_name("af_heart")
    r = client.post("/api/voice/preview", json={"name": "am_michael"}).get_json()
    assert r["ok"] is True and control.pending_preview == "am_michael"
    st = client.get("/api/state").get_json()
    assert st["voice"] == "af_heart", "preview must NOT change the active voice"
    assert control.pending_voice is None, "preview must NOT park a voice change"
    assert control.take_pending_preview() == "am_michael" and control.pending_preview is None
    assert control.take_pending_preview() is None
    r = client.post("/api/voice/preview", json={"name": "not_a_voice"}).get_json()
    assert r["ok"] is False and control.pending_preview is None
    r = client.post("/api/voice/preview", json={}).get_json()
    assert r["ok"] is False and control.pending_preview is None
    print("  [PASS] /api/voice/preview parks a sample without adopting the voice")

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

    # 9b. health() carries VRAM + model residency (Stage 8.3). This box usually has Invoke or a
    # forgotten model on the GPU; Echo's model JIT-loads on the first request, so a full card fails
    # at the first thing Michael says. The dashboard must show it BEFORE that.
    real_get, real_vram = ctrlmod.httpx.get, ctrlmod.gpu.vram_usage
    try:
        ctrlmod.httpx.get = lambda url, timeout=None: object()
        ctrlmod.gpu.vram_usage = lambda: (14000, 16303)
        control._health_ts = 0.0
        h = control.health()
        assert h["vram_used_mb"] == 14000 and h["vram_total_mb"] == 16303
        assert h["model_state"] == "loaded"
        # A GPU that can't be read must degrade to n/a, never crash the health route.
        ctrlmod.gpu.vram_usage = lambda: None
        control._health_ts = 0.0
        h = control.health()
        assert h["vram_used_mb"] is None and h["vram_total_mb"] is None
        # No model_state probe wired (or it fails) → "unknown", not a lie.
        c5, _, _, _, _ = _mk(model_state=lambda: "not-loaded")
        c5._health_ts = 0.0
        assert c5.health()["model_state"] == "not-loaded"
    finally:
        ctrlmod.httpx.get, ctrlmod.gpu.vram_usage = real_get, real_vram
    print("  [PASS] health() reports VRAM + model residency; unreadable GPU → n/a, not a crash")

    # 10. recent_scores returns a list (parses the JSONL log defensively).
    assert isinstance(control.recent_scores(5), list)
    # config loader is fail-soft and complete.
    cfg = load_webui_config()
    for key in ("enabled", "host", "port", "poll_ms"):
        assert key in cfg, key
    print("  [PASS] recent_scores → list; webui config loader complete")

    print("  OFFLINE: all dashboard checks passed.")


# ─────────────────────────── Phase 3: History page ───────────────────────────

def run_history() -> None:
    print("\n── History reader (offline) ──")
    d = Path(tempfile.mkdtemp())
    p = d / "log.jsonl"
    # A deliberately messy fixture: keyed sessions (S1/S2), keyless legacy rows split by a time
    # gap, a couple of malformed lines, and rows missing fields — all of which must be tolerated.
    # S2 is written FIRST (out of chronological order) to prove the reader sorts before grouping.
    lines = [
        json.dumps({"timestamp": "2026-06-01T09:00:00+00:00", "session_id": "S2", "speaker": "Michael",
                    "transcript": "crows again on the fence", "response_full": "They love your homestead, Michael.",
                    "total_latency_s": 1.2, "location": "home", "speaker_known": True, "speaker_score": 0.61}),
        "not valid json",                          # skipped
        "[]",                                      # skipped (not a dict)
        "",                                        # skipped (blank)
        json.dumps({"timestamp": "2026-04-01T10:00:00+00:00", "transcript": "legacy one", "response_full": "ok"}),
        json.dumps({"timestamp": "2026-04-01T10:05:00+00:00", "transcript": "legacy two", "response_full": "sure"}),
        json.dumps({"timestamp": "2026-04-01T12:00:00+00:00", "transcript": "legacy three"}),  # 2h gap → new
        json.dumps({"timestamp": "2026-05-01T09:00:00+00:00", "session_id": "S1", "speaker": "Michael",
                    "transcript": "hello the jeep runs great"}),                                # missing reply
        json.dumps({"timestamp": "2026-05-01T09:01:00+00:00", "session_id": "S1", "speaker": "Hillary",
                    "transcript": "my head hurts", "response_full": "Rest up, Hillary."}),
    ]
    p.write_text("\n".join(lines), encoding="utf-8")

    s = history_mod.read_history(p)["sessions"]
    assert len(s) == 4, [x["session_id"] for x in s]
    assert s[0]["session_id"] == "S2" and s[0]["count"] == 1 and s[0]["speakers"] == ["Michael"] and s[0]["synthetic"] is False
    assert s[1]["session_id"] == "S1" and s[1]["count"] == 2 and s[1]["speakers"] == ["Hillary", "Michael"]
    assert s[2]["synthetic"] is True and s[2]["count"] == 1          # legacy 12:00 (gap from 10:05)
    assert s[3]["synthetic"] is True and s[3]["count"] == 2          # legacy 10:00 + 10:05 (5 min apart)
    assert s[2]["turns"][0]["reply"] == ""                           # missing response_full tolerated
    assert history_mod.read_history(p)["speakers"] == ["Hillary", "Michael"]
    print("  [PASS] grouping: session_id exact, legacy by 20-min gap, newest first, bad lines skipped")

    assert [x["session_id"] for x in history_mod.read_history(p, q="crows")["sessions"]] == ["S2"]
    sp = history_mod.read_history(p, speaker="Hillary")["sessions"]
    assert len(sp) == 1 and sp[0]["session_id"] == "S1" and sp[0]["count"] == 1
    lim = history_mod.read_history(p, limit=1)
    assert len(lim["sessions"]) == 1 and lim["sessions"][0]["session_id"] == "S2" and lim["session_count"] == 4
    assert history_mod.read_history(d / "missing.jsonl")["sessions"] == []      # missing file → empty, not fatal
    print("  [PASS] q / speaker / limit filters + missing-file tolerance")


# ─────────────────────── Phase 3: Memory browser / editor ─────────────────────

def _temp_db() -> Path:
    """A fresh, schema-initialized echo.db with one seeded fact (bypassing the gate)."""
    dbp = Path(tempfile.mkdtemp()) / "echo.db"
    conn = db.get_connection(dbp)
    db.init_schema(conn)     # creates tables + seeds 2 core / 4 policy rows
    conn.execute("INSERT INTO fact_memory (id, entity, attribute, value, source_speaker, embedding)"
                 " VALUES (?,?,?,?,?,?)",
                 ("f1", "Jeep", "type", "2000 Wrangler TJ", "Michael", _FAKE_EMB("")))
    conn.commit()
    conn.close()
    return dbp


def run_memory() -> None:
    print("\n── Memory admin (offline) ──")
    conn = memory_admin.open_conn(_temp_db())

    m = memory_admin.dump_all(conn)
    assert m["counts"]["fact_memory"] == 1 and m["counts"]["core_memory"] == 2 and m["counts"]["policy_memory"] == 4
    assert m["facts"][0]["entity"] == "Jeep"
    # Phase 2 provenance: the Memory page shows WHO said a fact (display-only — no route edits it).
    assert m["facts"][0]["source_speaker"] == "Michael"
    print("  [PASS] dump_all returns counts + all five tables (facts carry source_speaker)")

    # Editing a fact's VALUE re-embeds (fake encoder) AND the AFTER UPDATE trigger re-syncs FTS —
    # the new keyword becomes findable, the old one stops matching. This is the load-bearing rule.
    row = memory_admin.edit_fact(conn, "f1", value="1997 Wrangler sasquatch edition", confidence=0.5, encoder=_FAKE_EMB)
    assert row["value"].startswith("1997") and abs(row["confidence"] - 0.5) < 1e-9
    assert conn.execute("SELECT embedding FROM fact_memory WHERE id='f1'").fetchone()[0] == _FAKE_EMB("")
    assert len(conn.execute("SELECT rowid FROM fact_fts WHERE fact_fts MATCH 'sasquatch'").fetchall()) == 1
    assert conn.execute("SELECT rowid FROM fact_fts WHERE fact_fts MATCH 'tj'").fetchall() == []
    assert memory_admin.edit_fact(conn, "nonexistent", value="x", encoder=_FAKE_EMB) is None
    print("  [PASS] edit_fact re-embeds on value change, keeps FTS in sync; unknown id → None")

    assert memory_admin.edit_core(conn, "user_profile", "Edited profile line.")["content"] == "Edited profile line."
    assert memory_admin.edit_core(conn, "brand_new", "a new core fact")["key"] == "brand_new"
    assert memory_admin.edit_pref(conn, "coffee", "black")["value"] == "black"
    assert memory_admin.edit_policy(conn, "directness", active=False)["active"] == 0
    assert memory_admin.edit_policy(conn, "does_not_exist", rule="x") is None    # editor won't mint policies
    print("  [PASS] edit_core / edit_pref (upsert) + edit_policy (existing rows only)")

    conn.execute("INSERT INTO fact_memory (id, entity, attribute, value) VALUES ('f2','X','y','disposable zebra')")
    conn.commit()
    assert memory_admin.delete_row(conn, "fact", "f2") == 1
    assert conn.execute("SELECT rowid FROM fact_fts WHERE fact_fts MATCH 'zebra'").fetchall() == []
    assert memory_admin.delete_row(conn, "core", "brand_new") == 1
    assert memory_admin.delete_row(conn, "sessions", "anything") == 0            # FK parent — never deletable
    assert memory_admin.delete_row(conn, "bogus", "x") == 0
    conn.execute("INSERT INTO sessions (id) VALUES ('sess1')")
    conn.execute("INSERT INTO episodic_memory (id, session_id, summary) VALUES ('e1','sess1','a summary')")
    conn.commit()
    assert memory_admin.delete_row(conn, "episodic", "e1") == 1
    print("  [PASS] delete_row: fact (FTS synced) / core / episodic; refuses sessions + unknown tables")
    conn.close()


def run_routes() -> None:
    print("\n── Memory + History routes (offline) ──")
    control, session, sm, reg, events = _mk()
    control.memory_db_path = _temp_db()          # never the real echo.db under test
    client = create_app(control).test_client()

    m = client.get("/api/memory").get_json()
    for k in ("counts", "core", "policy", "prefs", "facts", "episodic"):
        assert k in m, k
    assert m["counts"]["fact_memory"] == 1
    print("  [PASS] GET /api/memory dumps the store")

    assert client.post("/api/memory/core", json={"key": "user_profile", "content": "http edit"}).get_json()["core"]["content"] == "http edit"
    assert client.post("/api/memory/pref", json={"key": "tea", "value": "green"}).get_json()["pref"]["value"] == "green"
    assert client.post("/api/memory/policy", json={"key": "directness", "active": False}).get_json()["policy"]["active"] == 0
    assert client.post("/api/memory/policy", json={"key": "nope"}).get_json()["ok"] is False
    # Confidence-only fact edit — no value change, so no embedder call: the route stays model-free.
    assert abs(client.post("/api/memory/fact", json={"id": "f1", "confidence": 0.3}).get_json()["fact"]["confidence"] - 0.3) < 1e-9
    assert client.post("/api/memory/delete", json={"table": "pref", "id": "tea"}).get_json()["deleted"] == 1
    assert client.post("/api/memory/delete", json={"table": "sessions", "id": "x"}).get_json()["ok"] is False
    print("  [PASS] core/pref/policy/fact edits + delete via HTTP; sessions delete refused")

    h = client.get("/api/history").get_json()    # read-only, against the real log — shape only
    for k in ("sessions", "session_count", "speakers"):
        assert k in h, k
    assert isinstance(h["sessions"], list)
    assert b"History" in client.get("/history").data and b"Memory" in client.get("/memory").data
    print("  [PASS] /api/history shape + /history + /memory pages served")

    # The Voice Commands reference card (2026-07-18) — static content, so this is the whole
    # contract: it's served, and the two sharpest lines survive edits (the blind-capture
    # warning and the sign-off phrase Michael forgets mid-want).
    dash = client.get("/").data.decode("utf-8")
    assert "Voice Commands" in dash, "commands card dropped from the dashboard"
    assert "that's all for now" in dash and "stay quiet, let them talk" in dash
    print("  [PASS] Voice Commands card served on the dashboard")


if __name__ == "__main__":
    run()
    run_history()
    run_memory()
    run_routes()
    print()
