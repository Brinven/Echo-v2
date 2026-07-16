"""
Read/edit helpers for the Memory page (Phase 3) — a web front-end over the same Ib-Lite
curation that ib_lite_cli.py does from the terminal, so a bad memory can be fixed by touch
(no auto-detector; manual cleanup, as Michael asked).

Every function takes a sqlite3 connection. The web layer opens its OWN connection per request
via open_conn() — it NEVER shares IbLite._conn (that connection belongs to the main thread;
sqlite3 connections are not thread-safe). open_conn() sets a busy_timeout so a concurrent
background significance-gate write can't error the editor out with "database is locked" — the
same isolation the gate's own _insert() relies on (WAL + a private connection).

Two schema-driven rules, both load-bearing (see ib_lite_schema.sql):
  - Editing a fact's VALUE re-embeds it, so semantic retrieval keeps finding it, and the plain
    UPDATE fires the fact_touch + fact_fts_update triggers, so BM25/FTS stays in sync.
  - Episodic summaries are NOT editable here. episodic_fts has an insert and a delete trigger
    but NO after-update trigger, so a summary edit via UPDATE would silently desync its search
    index. Episodic is view + delete only (delete IS safe — the delete trigger exists).
"""

from ib_lite import db
from ib_lite.embedder import encode
from ib_lite.retrieval import fact_search, episodic_search

# CLI table name -> (real table, key column). `sessions` is intentionally absent: it is the FK
# parent of facts/prefs/policies/episodic, so deleting one would orphan or cascade-break them.
_DELETABLE = {
    "fact": ("fact_memory", "id"),
    "core": ("core_memory", "key"),
    "policy": ("policy_memory", "key"),
    "pref": ("preference_memory", "key"),
    "preference": ("preference_memory", "key"),
    "episodic": ("episodic_memory", "id"),
    "episode": ("episodic_memory", "id"),
}


def open_conn(db_path=None):
    """A private connection for one request. busy_timeout rides out a concurrent gate write."""
    conn = db.get_connection(db_path)
    conn.execute("PRAGMA busy_timeout=4000")
    return conn


def _rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def dump_all(conn) -> dict:
    """Everything the Memory page shows, table by table. Read-only."""
    counts = {
        tbl: conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        for tbl in ("core_memory", "policy_memory", "preference_memory",
                    "fact_memory", "episodic_memory", "sessions")
    }
    return {
        "counts": counts,
        "core": _rows(conn, "SELECT key, content, updated_at FROM core_memory ORDER BY rowid"),
        "policy": _rows(conn, "SELECT key, rule, priority, active FROM policy_memory "
                              "ORDER BY priority DESC, rowid"),
        "prefs": _rows(conn, "SELECT key, value, confidence, updated_at FROM preference_memory "
                             "ORDER BY updated_at DESC"),
        "facts": _rows(conn, "SELECT id, entity, attribute, value, confidence, updated_at "
                             "FROM fact_memory ORDER BY updated_at DESC"),
        "episodic": _rows(conn, "SELECT id, session_id, summary, key_topics, mood_signal, "
                                "turn_count, created_at FROM episodic_memory ORDER BY created_at DESC"),
    }


def search(conn, query: str) -> dict:
    """Hybrid search exactly the way Echo retrieves per turn — shows the real scores, so you can
    see WHY a bad fact surfaces (or whether down-ranking its confidence hid it)."""
    query = (query or "").strip()
    if not query:
        return {"facts": [], "episodes": []}
    return {"facts": fact_search(conn, query), "episodes": episodic_search(conn, query)}


def _fact(conn, fact_id):
    row = conn.execute(
        "SELECT id, entity, attribute, value, confidence, updated_at FROM fact_memory WHERE id=?",
        (fact_id,)).fetchone()
    return dict(row) if row else None


def edit_fact(conn, fact_id, *, value=None, confidence=None, encoder=encode):
    """Edit a fact's value and/or confidence. A value change re-embeds (encoder is injectable so
    tests stay model-free). Returns the updated row, or None if the id is unknown.

    The UPDATE fires fact_touch (updated_at) and fact_fts_update (re-syncs BM25), so both the
    vector and keyword indexes stay correct — the whole reason this goes through UPDATE and not
    the delete+insert an INSERT OR REPLACE would do.
    """
    row = conn.execute("SELECT entity, attribute, value FROM fact_memory WHERE id=?",
                       (fact_id,)).fetchone()
    if row is None:
        return None
    sets: list[str] = []
    params: list = []
    if value is not None:
        value = str(value).strip()
        if value and value != row["value"]:
            sets += ["value=?", "embedding=?"]
            params += [value, encoder(f"{row['entity']} {row['attribute']} {value}")]
    if confidence is not None:
        sets.append("confidence=?")
        params.append(max(0.0, min(1.0, float(confidence))))
    if sets:
        params.append(fact_id)
        conn.execute(f"UPDATE fact_memory SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
    return _fact(conn, fact_id)


def edit_core(conn, key, content):
    """Create-or-update a core memory (always injected verbatim). None if key/content empty."""
    key = (key or "").strip()
    content = (content or "").strip()
    if not key or not content:
        return None
    conn.execute(
        "INSERT INTO core_memory (key, content) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET content=excluded.content, updated_at=CURRENT_TIMESTAMP",
        (key, content))
    conn.commit()
    r = conn.execute("SELECT key, content, updated_at FROM core_memory WHERE key=?", (key,)).fetchone()
    return dict(r) if r else None


def edit_pref(conn, key, value):
    """Create-or-update a preference. None if key/value empty."""
    key = (key or "").strip()
    value = (value or "").strip()
    if not key or not value:
        return None
    conn.execute(
        "INSERT INTO preference_memory (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value))
    conn.commit()
    r = conn.execute("SELECT key, value, confidence, updated_at FROM preference_memory WHERE key=?",
                     (key,)).fetchone()
    return dict(r) if r else None


def edit_policy(conn, key, *, rule=None, priority=None, active=None):
    """Update an EXISTING policy's rule / priority / active flag. Returns None if the key doesn't
    exist — policy rows are seeded/gate-authored; the editor tweaks and toggles them, it doesn't
    mint new behavioral rules from the touchscreen."""
    key = (key or "").strip()
    if conn.execute("SELECT 1 FROM policy_memory WHERE key=?", (key,)).fetchone() is None:
        return None
    sets: list[str] = []
    params: list = []
    if rule is not None and str(rule).strip():
        sets.append("rule=?")
        params.append(str(rule).strip())
    if priority is not None:
        sets.append("priority=?")
        params.append(max(1, min(10, int(priority))))
    if active is not None:
        sets.append("active=?")
        params.append(1 if active else 0)
    if sets:
        params.append(key)
        conn.execute(f"UPDATE policy_memory SET {', '.join(sets)} WHERE key=?", params)
        conn.commit()
    r = conn.execute("SELECT key, rule, priority, active FROM policy_memory WHERE key=?", (key,)).fetchone()
    return dict(r) if r else None


def delete_row(conn, table: str, ident) -> int:
    """Delete one row. `table` is the CLI name (fact|core|policy|pref|episodic). Returns the
    number of rows deleted (0 for an unknown table or a non-existent id). Plain DELETE fires the
    fts delete triggers, keeping the search indexes in sync."""
    entry = _DELETABLE.get((table or "").lower())
    if not entry:
        return 0
    real, keycol = entry
    cur = conn.execute(f"DELETE FROM {real} WHERE {keycol}=?", (ident,))
    conn.commit()
    return cur.rowcount
