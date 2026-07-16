"""
Read-side helper for the History page (Phase 3).

Parses logs/stage0_log.jsonl — one JSON record per completed turn, appended live so it
survives a hard kill (see logger.py) — into conversation "sessions" for a read-only web view.

Records reach back to April and the field set has grown over time, so every read is by .get()
and a malformed line is skipped, never fatal. New records carry `session_id` (main.py) for exact
grouping; the ~90 legacy rows that predate it are grouped by a timestamp gap — a silence longer
than GAP_SECONDS starts a new session.

Pure functions over a file path: read_history() is unit-tested against a temp log. The Flask
route calls it through EchoControl.history().
"""

import json
from datetime import datetime
from pathlib import Path

# A pause longer than this between consecutive turns starts a new (legacy) session bucket.
# Only used for records that have no session_id; keyed records group exactly.
GAP_SECONDS = 20 * 60


def _turn(rec: dict) -> dict:
    """One log record -> the fields the History page shows. Tolerant of missing keys."""
    return {
        "time": rec.get("timestamp"),
        "speaker": rec.get("speaker"),           # resolved name, or None on legacy rows
        "known": rec.get("speaker_known"),
        "score": rec.get("speaker_score"),
        "said": rec.get("transcript") or "",
        "reply": rec.get("response_full") or rec.get("response_preview") or "",
        "location": rec.get("location"),
        "latency": rec.get("total_latency_s"),
        "model": rec.get("model") or "",
        "searched": bool(rec.get("web_search_triggered")),
        "query": rec.get("search_query"),
        "memories": rec.get("memories_injected"),
    }


def _ts_seconds(ts: str) -> float | None:
    """ISO timestamp -> epoch seconds, or None. Used only for the legacy gap heuristic."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return None


def _parse(log_path: Path) -> list[dict]:
    """All well-formed records, ascending by timestamp. Bad lines are skipped, not fatal."""
    try:
        lines = Path(log_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    recs: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            recs.append(rec)
    # ISO-8601 UTC timestamps sort lexicographically == chronologically. Append order is already
    # ascending, but sort defensively (a rescued/merged log could be out of order).
    recs.sort(key=lambda r: r.get("timestamp") or "")
    return recs


def _group(recs: list[dict]) -> list[dict]:
    """Split the ascending record stream into sessions.

    A record with `session_id` continues that exact session (or opens it). A record without one
    continues the current legacy group only if the previous record was also keyless AND within
    GAP_SECONDS — otherwise it opens a new synthetic session. Turns stay in spoken order.
    """
    groups: list[dict] = []
    prev_ts: float | None = None

    for rec in recs:
        sid = rec.get("session_id")
        ts = _ts_seconds(rec.get("timestamp") or "")
        last = groups[-1] if groups else None

        if sid:
            same = last is not None and last["_sid"] == sid
        else:
            within = prev_ts is not None and ts is not None and (ts - prev_ts) <= GAP_SECONDS
            same = last is not None and last["_sid"] is None and within

        if not same:
            label = str(sid) if sid else "legacy:" + (rec.get("timestamp") or "?")
            last = {"_sid": sid, "session_id": label, "synthetic": sid is None, "turns": []}
            groups.append(last)

        last["turns"].append(_turn(rec))
        if ts is not None:
            prev_ts = ts

    return groups


def read_history(log_path, *, q: str | None = None, speaker: str | None = None,
                 limit: int | None = None) -> dict:
    """Grouped conversation history, newest session first.

    q       — case-insensitive substring over what was said + Echo's reply (drops non-matching
              turns, then sessions with no surviving turn).
    speaker — keep only turns spoken by this name (legacy turns have no speaker and drop out).
    limit   — cap the number of sessions returned (after filtering).

    Returns {sessions, session_count, speakers} where `speakers` is the full facet across the
    unfiltered log (so the UI's filter dropdown lists everyone, not just the current view).
    """
    groups = _group(_parse(log_path))
    q_l = (q or "").strip().lower()
    sp = (speaker or "").strip().lower()

    out: list[dict] = []
    for g in groups:
        turns = g["turns"]
        if sp:
            turns = [t for t in turns if (t["speaker"] or "").lower() == sp]
        if q_l:
            turns = [t for t in turns
                     if q_l in (t["said"] or "").lower() or q_l in (t["reply"] or "").lower()]
        if not turns:
            continue
        out.append({
            "session_id": g["session_id"],
            "synthetic": g["synthetic"],
            "started": turns[0]["time"],
            "ended": turns[-1]["time"],
            "count": len(turns),
            "speakers": sorted({t["speaker"] for t in turns if t["speaker"]}),
            "turns": turns,
        })

    out.reverse()  # newest session first
    total = len(out)
    if limit:
        out = out[: int(limit)]

    all_speakers = sorted({t["speaker"] for g in groups for t in g["turns"] if t["speaker"]})
    return {"sessions": out, "session_count": total, "speakers": all_speakers}
