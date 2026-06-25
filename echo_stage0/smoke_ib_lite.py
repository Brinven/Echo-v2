"""
Smoke test for Ib-Lite (Echo's local SQLite memory).

Validates the full path from the PRD:
  Core injection -> write one Fact -> retrieve it -> session end -> Episodic write

Runs against a throwaway DB in the temp dir (never touches echo.db). The live
significance gate is exercised only if LM Studio is reachable; otherwise it is
skipped (non-fatal) so this test is green on any machine.

Usage:
  cd echo_stage0
  python smoke_ib_lite.py
"""

import sys
import tempfile
from pathlib import Path

from ib_lite import IbLite
from ib_lite.retrieval import fact_search, episodic_search
from ib_lite.significance import run_gate
from ib_lite.schema import validate_write

GREEN = "\033[32m"; RED = "\033[31m"; CYAN = "\033[36m"; DIM = "\033[2m"; RESET = "\033[0m"


def _fresh_db() -> Path:
    db = Path(tempfile.gettempdir()) / "echo_smoke.db"
    for p in [db, Path(str(db) + "-wal"), Path(str(db) + "-shm")]:
        if p.exists():
            p.unlink()
    return db


def _detect_model() -> str | None:
    """Return the id of a model loaded in LM Studio, or None if unreachable."""
    try:
        from openai import OpenAI
        models = OpenAI(base_url="http://127.0.0.1:1234/v1", api_key="x").models.list()
        return models.data[0].id if models.data else None
    except Exception:
        return None


def main() -> int:
    db = _fresh_db()
    print(f"{CYAN}[1] init IbLite (temp db){RESET}")
    ib = IbLite("smoke-model", db_path=db)
    if not ib.available:
        print(f"{RED}FAIL: IbLite unavailable{RESET}")
        return 1

    print(f"{CYAN}[2] Core + Policy injection{RESET}")
    base = ib.build_context_block()
    assert "Echo" in base and "Michael" in base, "core persona missing"
    assert "Mike" in base, "address-rule policy missing"
    print(f"  ok ({len(base)} chars)\n")

    sid = "smoke-session"
    ib.start_session(sid)

    print(f"{CYAN}[3] write one Fact + verify FTS sync{RESET}")
    ib._insert(sid, {"type": "fact", "entity": "Michael",
                     "attribute": "favorite_bird", "value": "crows"})
    n_fact = ib._conn.execute("SELECT COUNT(*) FROM fact_memory").fetchone()[0]
    n_fts = ib._conn.execute("SELECT COUNT(*) FROM fact_fts").fetchone()[0]
    assert n_fact == 1 and n_fts == 1, f"fact/fts mismatch: {n_fact}/{n_fts}"
    print(f"  ok (fact_memory={n_fact}, fact_fts={n_fts})\n")

    print(f"{CYAN}[4] hybrid retrieval surfaces the Fact{RESET}")
    hits = fact_search(ib._conn, "which bird does Michael love")
    print(f"  hits: {[(h['attribute'], h['value'], h['score']) for h in hits]}")
    assert any(h["value"] == "crows" for h in hits), "fact not retrieved"
    print("  ok\n")

    print(f"{CYAN}[5] live significance gate (skipped if LM Studio is down){RESET}")
    model = _detect_model()
    if model is None:
        print(f"  {DIM}skipped: LM Studio not reachable / no model loaded{RESET}\n")
    else:
        turn = "Michael: The Jeep needs new shocks before the next trail run.\nEcho: Noted."
        payload = run_gate(turn, model)
        if payload.get("_error"):
            print(f"  {DIM}skipped: {payload.get('_error')}{RESET}\n")
        else:
            ok, err = validate_write(payload) if payload.get("save") else (True, "")
            assert payload.get("save") and ok, f"live gate produced no valid write: {payload} {err}"
            print(f"  ok ({model}) -> {payload}\n")

    print(f"{CYAN}[6] session end -> Episodic write (before ended_at){RESET}")
    summary = {
        "summary_text": "Michael and Echo talked about crows and the Jeep's new shocks.",
        "topics_discussed": ["crows", "Jeep shocks"],
        "conversation_mood": "warm",
    }
    wrote = ib.end_session(sid, summary, turn_count=4)
    assert wrote, "episodic not written"
    sess = ib._conn.execute(
        "SELECT ended_at, episode_id FROM sessions WHERE id = ?", (sid,)
    ).fetchone()
    assert sess["ended_at"] and sess["episode_id"], "session not closed after episodic write"
    eps = episodic_search(ib._conn, "Jeep shocks and crows")
    assert eps and "shocks" in eps[0]["summary"], "episodic not retrievable"
    print(f"  ok (episode_id={sess['episode_id']}, retrieved {len(eps)})\n")

    ib.close()
    print(f"{GREEN}SMOKE PASSED{RESET}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(f"{RED}FAIL: {e}{RESET}")
        sys.exit(1)
