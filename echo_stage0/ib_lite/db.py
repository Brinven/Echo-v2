"""
SQLite connection management + schema init for Ib-Lite.

Every connection:
  - uses WAL (concurrent reader during a background write thread)
  - enforces foreign keys (fact/preference/policy.source_session -> sessions.id)
  - loads the sqlite-vec extension (vec_distance_cosine on raw float32 BLOBs)

Paths are __file__-relative so Echo can be launched from any CWD.

IMPORTANT: recursive_triggers is left OFF (the SQLite default). The schema's
`fact_touch` AFTER UPDATE trigger does a nested UPDATE on the same row; with
recursive triggers enabled it would loop forever. Do not turn it on.
"""

import sqlite3
from pathlib import Path

import sqlite_vec

_PKG_DIR = Path(__file__).resolve().parent
DB_PATH = _PKG_DIR.parent / "echo.db"          # echo_stage0/echo.db
SCHEMA_PATH = _PKG_DIR / "ib_lite_schema.sql"


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a connection with WAL, FK enforcement, and sqlite-vec loaded.

    Each thread must call this for its own connection — sqlite3 connections
    are not safe to share across threads.
    """
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Run the schema file (idempotent — every statement is CREATE/INSERT OR IGNORE)."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
