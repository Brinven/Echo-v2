"""
Flask dashboard server for Echo (Stage 7).

A tiny web server that runs in a daemon thread inside the Echo process and serves the
single-page touch dashboard + a small JSON control API backed by EchoControl.

Fail-soft, like search/location: never raises into the voice loop. Disabled in config,
flask missing, or the port already taken → warn and return None; Echo runs exactly as
before. flask is imported lazily (inside create_app / the presence check) so importing
this module never requires it.

Config: echo_webui.json (load_webui_config), mirroring search.load_search_config.
"""

import json
import time
import queue
import socket
import base64
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "echo_webui.json"
_STATIC_DIR = Path(__file__).resolve().parent / "static"

_DEFAULT_CONFIG = {
    "enabled": True,
    "host": "127.0.0.1",
    "port": 7862,
    "poll_ms": 1000,
}

# How long a phone turn may wait for the main loop before the request 504s. Generous on
# purpose: the FIRST turn can JIT-load the 12B (30s+), and a search turn adds seconds more.
# A timeout doesn't cancel the turn — the main loop may still finish it locally.
REMOTE_WAIT_S = 120


def load_webui_config() -> dict:
    """Load echo_webui.json, falling back to documented defaults on any error."""
    config = dict(_DEFAULT_CONFIG)
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in _DEFAULT_CONFIG:
            if key in data:
                config[key] = data[key]
    except FileNotFoundError:
        logger.info("echo_webui.json not found; using built-in dashboard defaults")
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"echo_webui.json unreadable ({e}); using built-in dashboard defaults")
    return config


def create_app(control):
    """Build the Flask app. Routes are thin — all logic lives on EchoControl / memory_admin."""
    from flask import Flask, jsonify, request, send_from_directory

    # Imported here, not at module top, so importing the webui package stays cheap and
    # flask-free (memory_admin pulls in ib_lite). Only paid when the dashboard is actually built.
    from . import memory_admin
    from . import remote_audio
    from . import doc_extract

    app = Flask(__name__, static_folder=None)
    # Remote Voice uploads: a minute of phone AAC is ~1 MB; 32 MB is a runaway guard,
    # not a target. Flask returns 413 above this instead of buffering without bound.
    app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024

    @app.get("/")
    def index():
        return send_from_directory(_STATIC_DIR, "index.html")

    # ── reads (polled) ──
    @app.get("/api/state")
    def api_state():
        return jsonify(control.snapshot())

    @app.get("/api/health")
    def api_health():
        return jsonify(control.health())

    @app.get("/api/scores")
    def api_scores():
        return jsonify(control.recent_scores())

    # ── writes (mirror on_key / voice overrides) ──
    @app.post("/api/talk/press")
    def api_talk_press():
        control.talk_press()
        return jsonify(ok=True)

    @app.post("/api/talk/release")
    def api_talk_release():
        control.talk_release()
        return jsonify(ok=True)

    @app.post("/api/mute")
    def api_mute():
        return jsonify(ok=True, muted=control.toggle_mute())

    @app.post("/api/snark")
    def api_snark():
        data = request.get_json(silent=True) or {}
        if data.get("max"):
            return jsonify(ok=True, max_snark=control.toggle_max_snark())
        return jsonify(ok=True, snark=control.set_snark(data.get("level", 5)))

    @app.post("/api/location")
    def api_location():
        data = request.get_json(silent=True) or {}
        loc = control.set_location(data.get("location", ""))
        return jsonify(ok=loc is not None, location=loc)

    @app.post("/api/websearch")
    def api_websearch():
        data = request.get_json(silent=True) or {}
        return jsonify(ok=True, web_search_off=control.set_web_search(data.get("off", False)))

    @app.post("/api/vad")
    def api_vad():
        """Hands-free toggle. ok=False when webrtcvad isn't installed (nothing to turn on)."""
        data = request.get_json(silent=True) or {}
        if "enabled" in data:
            enabled = control.set_vad(bool(data["enabled"]))
        else:
            enabled = control.toggle_vad()
        return jsonify(ok=control.vad_available, vad_enabled=enabled)

    # ── model swap (Stage 8: replaces the L key's blocking picker) ──
    @app.get("/api/models")
    def api_models():
        # ?refresh=1 bypasses the ~10s cache (the dropdown's ↻ button).
        force = request.args.get("refresh", "") in ("1", "true")
        return jsonify(models=control.models(force=force), current=control.snapshot()["model"])

    @app.post("/api/model")
    def api_model():
        """Park a swap for the main loop; it applies between turns, never mid-generation."""
        data = request.get_json(silent=True) or {}
        ok = control.request_model(data.get("name", ""))
        return jsonify(ok=ok, pending_model=control.pending_model)

    # ── Kokoro voice (Stage 8.1) ──
    @app.get("/api/voices")
    def api_voices():
        return jsonify(voices=control.voices(), current=control.snapshot()["voice"])

    @app.post("/api/voice")
    def api_voice():
        """Park a voice change; applied between turns so it never changes mid-sentence."""
        data = request.get_json(silent=True) or {}
        ok = control.request_voice(data.get("name", ""))
        return jsonify(ok=ok, pending_voice=control.pending_voice)

    @app.post("/api/voice/preview")
    def api_voice_preview():
        """Speak a fixed sample line in a voice WITHOUT adopting it. Played by the main loop."""
        data = request.get_json(silent=True) or {}
        ok = control.request_preview(data.get("name", ""))
        return jsonify(ok=ok, pending_preview=control.pending_preview)

    @app.post("/api/enroll")
    def api_enroll():
        data = request.get_json(silent=True) or {}
        if data.get("cancel"):
            control.cancel_enroll()
            return jsonify(ok=True, enrolling=None)
        ok = control.start_enroll(data.get("name", ""), ignore=bool(data.get("ignore")))
        return jsonify(ok=ok, enrolling=control.session.enrolling,
                       enrolling_ignore=control.session.enrolling_ignore)

    @app.post("/api/threshold")
    def api_threshold():
        data = request.get_json(silent=True) or {}
        v = control.set_threshold(data.get("value"))
        return jsonify(ok=v is not None, match_threshold=v)

    @app.post("/api/quit")
    def api_quit():
        control.request_quit()
        return jsonify(ok=True)

    # ── Remote Voice (Level 2): talk to Echo from the phone ──
    @app.get("/remote")
    def remote_page():
        return send_from_directory(_STATIC_DIR, "remote.html")

    def _clean_location(value) -> str | None:
        """Validate a per-turn location hint; anything unrecognized degrades to None
        (auto) rather than erroring — a bad hint must never cost the turn."""
        v = (value or "").strip().lower()
        return v if v in ("home", "jeep", "away") else None

    @app.post("/api/remote/turn")
    def api_remote_turn():
        """One phone utterance in, one Echo reply out (optionally with a photo riding it).

        Two body shapes: the original raw recorded blob (voice-only — byte-identical to
        Level 2), or multipart/form-data with an `audio` part and an optional `image` part
        (attach-then-talk, vision Level 1). Decoded HERE on the web thread (CPU, cheap,
        touches nothing shared), then parked for the main loop via the same contract as
        model/voice/preview. This request BLOCKS until the main loop publishes the result —
        that's the point: the phone wants the answer.

        Image problems degrade to a voice-only turn (`image_dropped` says why) — a photo
        must never cost Michael the sentence he just spoke.
        """
        image_b64 = image_mime = image_file = image_dropped = None
        doc_text = doc_name = doc_dropped = None
        # Per-turn location hint (2026-07-18): form field on multipart, query param on the
        # raw-body shape (which has no fields). Missing/invalid → None → session location.
        location_hint = _clean_location(request.args.get("location"))
        if (request.content_type or "").startswith("multipart/form-data"):
            location_hint = _clean_location(request.form.get("location")) or location_hint
            audio_part = request.files.get("audio")
            raw = audio_part.read() if audio_part else b""
            # Document on a VOICE turn (2026-07-18, Michael: "might as well") — attach-then-
            # talk like a photo, same degrade rules, extracted here on the web thread.
            doc_part = request.files.get("doc")
            docb = doc_part.read() if doc_part else b""
            if docb:
                doc_name = (doc_part.filename or "attachment").strip()[:120]
                if len(docb) > doc_extract.DOC_MAX_BYTES:
                    doc_dropped = "too-large"
                else:
                    doc_text = doc_extract.extract_doc(docb, doc_name)
                    if doc_text is None:
                        doc_dropped = "unreadable"
                if doc_text is None:
                    doc_name = None
            image_part = request.files.get("image")
            img = image_part.read() if image_part else b""
            if img:
                mime = remote_audio.sniff_image_mime(img)
                if mime is None:
                    image_dropped = "not-an-image"
                elif len(img) > remote_audio.IMAGE_MAX_BYTES:
                    image_dropped = "too-large"
                elif not control.vision_capable():
                    # Server-side belt behind the client's greyed-out button: the model
                    # can change between the page's polls.
                    image_dropped = "model-not-vision"
                else:
                    image_mime = mime
                    image_b64 = base64.b64encode(img).decode("ascii")
                    # Fail-soft: None just means no pointer in the log; the turn proceeds.
                    image_file = remote_audio.save_photo(img, mime,
                                                         control.session.session_id)
        else:
            raw = request.get_data(cache=False)
        if not raw:
            return jsonify(ok=False, error="empty"), 400
        pcm = remote_audio.decode_to_pcm16k(raw)
        if pcm is None:
            return jsonify(ok=False, error="undecodable"), 400
        if len(pcm) < remote_audio.SAMPLE_RATE * 0.3:
            # Mirrors the pipeline's own too-short floor so the phone gets a clean 400
            # instead of burning a park/claim cycle on a reply that would be None anyway.
            return jsonify(ok=False, error="too-short"), 400
        slot = control.submit_remote_turn(pcm, image_b64=image_b64,
                                          image_mime=image_mime, image_file=image_file,
                                          location_hint=location_hint,
                                          doc_text=doc_text, doc_name=doc_name)
        if slot is None:
            return jsonify(ok=False, error="busy"), 409
        if not slot["event"].wait(timeout=REMOTE_WAIT_S):
            return jsonify(ok=False, error="timeout"), 504
        result = dict(slot["result"] or {"ok": False, "error": "no-result"})
        result["image_attached"] = image_b64 is not None
        if image_dropped:
            result["image_dropped"] = image_dropped
        result["doc_attached"] = doc_text is not None
        if doc_dropped:
            result["doc_dropped"] = doc_dropped
        return jsonify(result)

    # ── Chat lane (2026-07-18): typed turns through the same pipeline ──
    @app.get("/chat")
    def chat_page():
        return send_from_directory(_STATIC_DIR, "chat.html")

    @app.post("/api/chat/turn")
    def api_chat_turn():
        """One typed turn in, one TEXT reply out. Same park contract + status codes as
        /api/remote/turn, minus audio in both directions: the text runs the FULL pipeline
        (commands, search, memory gate, persona) with TTS skipped entirely. All typed text
        is Michael by policy (his call — no guest picker). An optional photo rides the turn
        exactly like attach-then-talk, with the same degrade-to-text-only rules.
        """
        data = request.get_json(silent=True) or {}
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify(ok=False, error="empty"), 400
        location_hint = _clean_location(data.get("location"))
        image_b64 = image_mime = image_file = image_dropped = None
        if data.get("image_b64"):
            try:
                img = base64.b64decode(data["image_b64"])
            except Exception:
                img = b""
            mime = remote_audio.sniff_image_mime(img) if img else None
            if mime is None:
                image_dropped = "not-an-image"
            elif len(img) > remote_audio.IMAGE_MAX_BYTES:
                image_dropped = "too-large"
            elif not control.vision_capable():
                image_dropped = "model-not-vision"
            else:
                image_mime = mime
                image_b64 = base64.b64encode(img).decode("ascii")
                image_file = remote_audio.save_photo(img, mime,
                                                     control.session.session_id)

        # Document attach (2026-07-18): extracted to plain text HERE (web thread), rides
        # the turn like a photo. Same degrade rule: a bad doc never costs the question.
        doc_text = doc_name = doc_dropped = None
        if data.get("doc_b64"):
            try:
                doc_bytes = base64.b64decode(data["doc_b64"])
            except Exception:
                doc_bytes = b""
            doc_name = (data.get("doc_name") or "attachment").strip()[:120]
            if not doc_bytes:
                doc_dropped = "not-a-doc"
            elif len(doc_bytes) > doc_extract.DOC_MAX_BYTES:
                doc_dropped = "too-large"
            else:
                doc_text = doc_extract.extract_doc(doc_bytes, doc_name)
                if doc_text is None:
                    doc_dropped = "unreadable"
            if doc_text is None:
                doc_name = None

        stream = bool(data.get("stream"))
        slot = control.submit_remote_turn(None, image_b64=image_b64,
                                          image_mime=image_mime, image_file=image_file,
                                          typed_text=text, location_hint=location_hint,
                                          stream=stream,
                                          doc_text=doc_text, doc_name=doc_name)
        if slot is None:
            return jsonify(ok=False, error="busy"), 409

        def _finalize(result: dict) -> dict:
            result = dict(result or {"ok": False, "error": "no-result"})
            result["image_attached"] = image_b64 is not None
            if image_dropped:
                result["image_dropped"] = image_dropped
            result["doc_attached"] = doc_text is not None
            if doc_dropped:
                result["doc_dropped"] = doc_dropped
            result.pop("wav_b64", None)   # belt: a typed reply is text-only by contract
            result.pop("sample_rate", None)
            return result

        if stream:
            # NDJSON: one {"sentence": ...} line per reply sentence as the model produces
            # it, then a single trailer line with done=true + the authoritative result.
            # The sentinel is pushed by finish_remote_turn (inside the main loop's
            # finally), so this drain can't hang on a pipeline exception.
            def drain():
                deadline = time.monotonic() + REMOTE_WAIT_S
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        yield json.dumps({"done": True, "ok": False,
                                          "error": "timeout"}) + "\n"
                        return
                    try:
                        kind, payload = slot["stream_q"].get(timeout=min(remaining, 1.0))
                    except queue.Empty:
                        continue
                    if kind == "sentence":
                        yield json.dumps({"sentence": payload}) + "\n"
                    else:
                        out = _finalize(payload)
                        out["done"] = True
                        yield json.dumps(out) + "\n"
                        return
            return app.response_class(drain(), mimetype="application/x-ndjson")

        if not slot["event"].wait(timeout=REMOTE_WAIT_S):
            return jsonify(ok=False, error="timeout"), 504
        return jsonify(_finalize(slot["result"]))

    # ── History page (Phase 3): read-only view over logs/stage0_log.jsonl ──
    @app.get("/history")
    def history_page():
        return send_from_directory(_STATIC_DIR, "history.html")

    @app.get("/api/history")
    def api_history():
        return jsonify(control.history(
            q=request.args.get("q") or None,
            speaker=request.args.get("speaker") or None,
            limit=request.args.get("limit", type=int),
        ))

    # ── Memory browser / editor (Phase 3): a web front-end over ib_lite_cli's curation ──
    # Each route opens its OWN connection to control.memory_db_path (real echo.db in production,
    # a temp DB under test) so it never shares IbLite's main-thread connection. See memory_admin.
    @app.get("/memory")
    def memory_page():
        return send_from_directory(_STATIC_DIR, "memory.html")

    @app.get("/api/memory")
    def api_memory():
        conn = memory_admin.open_conn(control.memory_db_path)
        try:
            return jsonify(memory_admin.dump_all(conn))
        finally:
            conn.close()

    @app.get("/api/memory/search")
    def api_memory_search():
        conn = memory_admin.open_conn(control.memory_db_path)
        try:
            return jsonify(memory_admin.search(conn, request.args.get("q", "")))
        finally:
            conn.close()

    @app.post("/api/memory/fact")
    def api_memory_fact():
        data = request.get_json(silent=True) or {}
        conn = memory_admin.open_conn(control.memory_db_path)
        try:
            row = memory_admin.edit_fact(conn, data.get("id"),
                                         value=data.get("value"), confidence=data.get("confidence"))
            return jsonify(ok=row is not None, fact=row)
        finally:
            conn.close()

    @app.post("/api/memory/core")
    def api_memory_core():
        data = request.get_json(silent=True) or {}
        conn = memory_admin.open_conn(control.memory_db_path)
        try:
            row = memory_admin.edit_core(conn, data.get("key"), data.get("content"))
            return jsonify(ok=row is not None, core=row)
        finally:
            conn.close()

    @app.post("/api/memory/policy")
    def api_memory_policy():
        data = request.get_json(silent=True) or {}
        conn = memory_admin.open_conn(control.memory_db_path)
        try:
            row = memory_admin.edit_policy(conn, data.get("key"), rule=data.get("rule"),
                                           priority=data.get("priority"), active=data.get("active"))
            return jsonify(ok=row is not None, policy=row)
        finally:
            conn.close()

    @app.post("/api/memory/pref")
    def api_memory_pref():
        data = request.get_json(silent=True) or {}
        conn = memory_admin.open_conn(control.memory_db_path)
        try:
            row = memory_admin.edit_pref(conn, data.get("key"), data.get("value"))
            return jsonify(ok=row is not None, pref=row)
        finally:
            conn.close()

    @app.post("/api/memory/delete")
    def api_memory_delete():
        data = request.get_json(silent=True) or {}
        conn = memory_admin.open_conn(control.memory_db_path)
        try:
            n = memory_admin.delete_row(conn, data.get("table", ""), data.get("id"))
            return jsonify(ok=n > 0, deleted=n)
        finally:
            conn.close()

    return app


def _port_free(host: str, port: int) -> bool:
    """True if (host, port) is bindable. NOTE: no SO_REUSEADDR — on Windows it lets you bind an
    already-in-use port (unlike Linux), which would defeat this 'port taken' check. A plain bind
    fails with EADDRINUSE/WSAEADDRINUSE on both platforms when the port is genuinely in use."""
    probe_host = "" if host in ("0.0.0.0", "") else host
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((probe_host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def start_webui(control, config: dict | None = None):
    """Start the dashboard in a daemon thread. Returns (thread, url) or None. Never raises."""
    config = config or load_webui_config()
    if not config.get("enabled", True):
        logger.info("dashboard disabled (echo_webui.json)")
        return None
    try:
        import flask  # noqa: F401 — presence check only
    except ImportError:
        logger.warning("flask not installed; dashboard off (pip install flask==3.1.3)")
        return None

    # Quiet werkzeug's per-request logging — the dashboard polls ~2×/sec and would otherwise
    # flood the terminal where Echo's live status line lives.
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    host = config.get("host", "127.0.0.1")
    port = int(config.get("port", 7862))
    if not _port_free(host, port):
        logger.warning(f"dashboard port {port} is in use; dashboard off (voice loop unaffected)")
        return None

    try:
        app = create_app(control)
    except Exception as e:
        logger.warning(f"could not build dashboard app ({e}); dashboard off")
        return None

    def _run():
        try:
            app.run(host=host, port=port, threaded=True, use_reloader=False, debug=False)
        except Exception as e:
            logger.warning(f"dashboard server stopped ({e})")

    thread = threading.Thread(target=_run, name="echo-webui", daemon=True)
    thread.start()
    shown_host = host if host not in ("0.0.0.0", "") else "127.0.0.1"
    return thread, f"http://{shown_host}:{port}"
