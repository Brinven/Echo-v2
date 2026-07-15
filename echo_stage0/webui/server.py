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
import socket
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
    """Build the Flask app. Routes are thin — all logic lives on EchoControl."""
    from flask import Flask, jsonify, request, send_from_directory

    app = Flask(__name__, static_folder=None)

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
        return jsonify(models=control.models(), current=control.snapshot()["model"])

    @app.post("/api/model")
    def api_model():
        """Park a swap for the main loop; it applies between turns, never mid-generation."""
        data = request.get_json(silent=True) or {}
        ok = control.request_model(data.get("name", ""))
        return jsonify(ok=ok, pending_model=control.pending_model)

    @app.post("/api/enroll")
    def api_enroll():
        data = request.get_json(silent=True) or {}
        if data.get("cancel"):
            control.cancel_enroll()
            return jsonify(ok=True, enrolling=None)
        ok = control.start_enroll(data.get("name", ""))
        return jsonify(ok=ok, enrolling=control.session.enrolling)

    @app.post("/api/threshold")
    def api_threshold():
        data = request.get_json(silent=True) or {}
        v = control.set_threshold(data.get("value"))
        return jsonify(ok=v is not None, match_threshold=v)

    @app.post("/api/quit")
    def api_quit():
        control.request_quit()
        return jsonify(ok=True)

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
