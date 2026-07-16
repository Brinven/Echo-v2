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
    _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply one-time, versioned migrations tracked by PRAGMA user_version.

    The schema seeds are INSERT OR IGNORE and only fire on a fresh row, so editing the
    seed file cannot retro-remove data from an already-initialized echo.db. Schema-shape
    changes that must touch existing rows go here, guarded by user_version so each runs
    exactly once.

    v1 (Stage 5 Part 2): identity moved out of core_memory into persona.PERSONA_BLOCK.
        Drop the legacy 'persona' core row so it can't duplicate/contradict the block.

    v2 (Stage 6 Phase 2): fact_memory gains source_speaker (who SAID it; entity is who
        it's ABOUT). A fresh DB already has the column from the schema file, so the ALTER
        is guarded by PRAGMA table_info. Legacy facts backfill to 'Michael' — honest, not
        a guess: the Stage 6 Part 1 guardrail let ONLY Michael's turns write facts.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]

    if version < 1:
        conn.execute("DELETE FROM core_memory WHERE key = 'persona'")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()

    if version < 2:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(fact_memory)")}
        if "source_speaker" not in cols:
            conn.execute("ALTER TABLE fact_memory ADD COLUMN source_speaker TEXT")
        # Rebuild the external-content FTS index BEFORE the backfill: the UPDATE below fires
        # fact_fts_update, whose 'delete' step raises "database disk image is malformed" for
        # any row the index doesn't already hold. A healthy DB is unaffected (rebuild is
        # idempotent and the table is tiny); a drifted one is repaired instead of bricked.
        conn.execute("INSERT INTO fact_fts(fact_fts) VALUES('rebuild')")
        conn.execute(
            "UPDATE fact_memory SET source_speaker = 'Michael' WHERE source_speaker IS NULL"
        )
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
