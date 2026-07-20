"""
Offline tests for the configurable LLM endpoint (2026-07-19 — Sindri replaces LM Studio).

No network, no model, no LM Studio/Sindri: the resolver takes env/config seams, the
Sindri-health parser is pure, and the threading checks patch run_gate with a capture stub.

Run: python test_llm_endpoint.py
"""

import json
import inspect
import tempfile
import time
from pathlib import Path

import llm
from llm import resolve_llm_base_url, _route_slug, _sindri_state

PASS = "  [PASS]"


def _tmp_config(payload: dict | str) -> Path:
    d = Path(tempfile.mkdtemp(prefix="echo_test_llmurl_"))
    p = d / "config.json"
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload),
                 encoding="utf-8")
    return p


def run_resolver() -> None:
    print("\n── resolve_llm_base_url: precedence + normalization ──")

    cfg = _tmp_config({"llm_base_url": "http://127.0.0.1:4610/v1"})

    # Env wins over config.
    got = resolve_llm_base_url(env={"ECHO_LLM_URL": "http://127.0.0.1:9999/v1"}, config_path=cfg)
    assert got == "http://127.0.0.1:9999/v1", got
    print(f"{PASS} ECHO_LLM_URL env wins over config.json")

    # Config is the normal path.
    got = resolve_llm_base_url(env={}, config_path=cfg)
    assert got == "http://127.0.0.1:4610/v1", got
    print(f"{PASS} config.json llm_base_url used when env is unset")

    # Missing key / missing file / corrupt file → the LM Studio default (rollback path).
    for case, path in [
        ("key absent", _tmp_config({"user_name": "Michael"})),
        ("file absent", Path(tempfile.mkdtemp(prefix="echo_test_llmurl_")) / "nope.json"),
        ("corrupt json", _tmp_config("{not json")),
    ]:
        got = resolve_llm_base_url(env={}, config_path=path)
        assert got == "http://127.0.0.1:1234/v1", (case, got)
    print(f"{PASS} key/file absent + corrupt json all fall back to the LM Studio default")

    # Normalization: trailing slash, missing /v1, missing scheme, already-good, case.
    for raw, want in [
        ("http://127.0.0.1:4610/v1/", "http://127.0.0.1:4610/v1"),
        ("http://127.0.0.1:4610",     "http://127.0.0.1:4610/v1"),
        ("127.0.0.1:4610",            "http://127.0.0.1:4610/v1"),
        ("http://127.0.0.1:4610/V1",  "http://127.0.0.1:4610/V1"),   # /v1 present (any case) — never doubled
        ("http://host:1/v1",          "http://host:1/v1"),
    ]:
        got = resolve_llm_base_url(env={"ECHO_LLM_URL": raw}, config_path=None)
        assert got == want, (raw, got, want)
    print(f"{PASS} normalization: scheme added, trailing / stripped, /v1 appended exactly once")

    # Whitespace-only values are ignored, not dialed.
    got = resolve_llm_base_url(env={"ECHO_LLM_URL": "   "},
                               config_path=_tmp_config({"llm_base_url": "  "}))
    assert got == "http://127.0.0.1:1234/v1", got
    print(f"{PASS} blank env/config values fall through to the default")


def run_sindri_parsing() -> None:
    print("\n── _route_slug + _sindri_state (pure, mirrors sindri-proxy /health) ──")

    assert _route_slug("Bonsai 1") == "bonsai-1"
    assert _route_slug("gemma4.12B_QAT") == "gemma4.12b_qat"
    assert _route_slug("") == "profile"
    assert _route_slug("--Weird//Name--") == "weird-name"
    print(f"{PASS} _route_slug mirrors Sindri's routeSlug (lowercase, runs → '-', trimmed)")

    # Not Sindri → None (so model_state keeps returning 'unknown', never a lie).
    assert _sindri_state({"status": "ok"}, "x") is None
    assert _sindri_state({"service": "other"}, "x") is None
    assert _sindri_state("nope", "x") is None
    print(f"{PASS} non-Sindri payloads → None (fail through to 'unknown')")

    health = {"service": "sindri-proxy", "routes": 2, "conflicts": [],
              "resident": [{"profile": "Bonsai 1", "state": "running", "port": 50001}]}
    assert _sindri_state(health, "bonsai-1") == "loaded"
    assert _sindri_state(health, "Bonsai 1") == "loaded"          # slug-match both sides
    assert _sindri_state(health, "other-route") == "not-loaded"
    print(f"{PASS} resident+running profile matches the route id → 'loaded'")

    # Spawning/loading backends aren't serving yet; empty resident = nothing loaded.
    loading = {"service": "sindri-proxy",
               "resident": [{"profile": "Bonsai 1", "state": "loading"}]}
    assert _sindri_state(loading, "bonsai-1") == "not-loaded"
    assert _sindri_state({"service": "sindri-proxy", "resident": []}, "x") == "not-loaded"
    assert _sindri_state({"service": "sindri-proxy"}, "x") == "not-loaded"
    assert _sindri_state({"service": "sindri-proxy", "resident": ["junk", None]}, "x") == "not-loaded"
    print(f"{PASS} loading/empty/malformed resident entries → 'not-loaded', never a crash")


def run_single_source() -> None:
    print("\n── single source: every consumer resolves through llm.LLM_BASE_URL ──")

    import search_decision
    import persona_check
    import summarizer

    assert inspect.signature(search_decision.decide_search).parameters["lm_base"].default \
        is llm.LLM_BASE_URL
    assert inspect.signature(persona_check.run_self_check).parameters["lm_base"].default \
        is llm.LLM_BASE_URL
    assert summarizer.LLM_BASE_URL is llm.LLM_BASE_URL
    print(f"{PASS} search decider, persona probe, summarizer all default to llm.LLM_BASE_URL")

    from ib_lite import significance
    assert inspect.signature(significance.run_gate).parameters["lm_base"].default is None
    assert significance.DEFAULT_LLM_URL == "http://127.0.0.1:1234/v1"
    print(f"{PASS} run_gate default is None → its own constant (ib_lite stays self-contained)")

    # No stray hardcoded endpoint anywhere in the runtime modules.
    here = Path(__file__).resolve().parent
    offenders = []
    for py in here.glob("*.py"):
        if py.name.startswith("test_") or py.name.startswith("smoke_"):
            continue
        text = py.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if "1234/v1" in line and not line.lstrip().startswith("#") \
                    and py.name not in ("llm.py",):
                offenders.append(f"{py.name}:{i}")
    for py in (here / "ib_lite").glob("*.py"):
        text = py.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if "1234/v1" in line and not line.lstrip().startswith("#") \
                    and "DEFAULT_LLM_URL" not in line:
                offenders.append(f"ib_lite/{py.name}:{i}")
    assert not offenders, f"hardcoded endpoint outside the resolver: {offenders}"
    print(f"{PASS} no hardcoded 1234/v1 outside llm.py + significance's documented fallback")


def run_iblite_threading() -> None:
    print("\n── IbLite threads lm_base to run_gate (temp DB, stubbed gate) ──")

    from ib_lite import IbLite
    import ib_lite.ib_lite as ib_mod

    tmp = Path(tempfile.mkdtemp(prefix="echo_test_lmbase_"))
    ib = IbLite("fake-model", db_path=tmp / "t.db", lm_base="http://test-host:9/v1")
    ib.start_session("s1")

    seen = {}

    def fake_run_gate(turn_text, model, correction=None, searched=False,
                      speaker="Michael", lm_base=None):
        seen["lm_base"] = lm_base
        return {"save": False}

    real = ib_mod.run_gate
    ib_mod.run_gate = fake_run_gate
    try:
        ib._gate_worker("s1", "hello there")   # synchronous — no thread, no race
    finally:
        ib_mod.run_gate = real

    assert seen.get("lm_base") == "http://test-host:9/v1", seen
    print(f"{PASS} _gate_worker passes IbLite's lm_base into run_gate")

    ib2 = IbLite("fake-model", db_path=tmp / "t2.db")
    assert ib2._lm_base is None
    print(f"{PASS} lm_base omitted → None → significance falls back to its own default")


if __name__ == "__main__":
    t0 = time.perf_counter()
    run_resolver()
    run_sindri_parsing()
    run_single_source()
    run_iblite_threading()
    print(f"\nAll LLM-endpoint tests passed in {time.perf_counter() - t0:.2f}s.")
