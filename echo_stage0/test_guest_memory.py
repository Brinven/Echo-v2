"""
Offline tests for Stage 6 Phase 2 — guest memory (source_speaker provenance).

Model-free and mic-free: a temp DB stands in for echo.db (never the production file),
and the MiniLM encoder is stubbed so nothing loads. Covers:

  - the user_version=2 migration (v1-shaped DB gains source_speaker, backfilled 'Michael';
    a fresh DB is already-shaped and the migration is idempotent)
  - _insert stamps source_speaker from the pipeline arg (never the gate payload)
  - peek_last_fact (non-destructive) + forget_last_fact still syncs FTS
  - build_context_block(include_profile=False) keeps policies, drops profile/prefs
  - the memory-block header no longer claims every fact came from Michael

Run:  python test_guest_memory.py     (exit 0 = all assertions passed)
"""

import sys
import shutil
import sqlite3
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from ib_lite import db
import ib_lite.ib_lite as ib_mod
from ib_lite.ib_lite import IbLite, _MEMORY_BLOCK_HEADER

# Stub the embedder at the module ib_lite.py actually calls — keeps every test model-free.
ib_mod.encode = lambda text: b"\x00\x00\x80\x3f" * 4   # 4 float32s, content irrelevant


def _fact_payload(entity="Hillary", attribute="ailment", value="headaches"):
    return {"save": True, "type": "fact", "entity": entity, "attribute": attribute, "value": value}


def run_migration() -> None:
    print("\n── Phase 2: v2 migration (offline, temp DB) ──")
    tmp = Path(tempfile.mkdtemp(prefix="echo_test_mig_"))

    # 1. A v1-shaped DB: fact_memory WITHOUT source_speaker, one legacy fact, user_version=1.
    legacy = tmp / "legacy.db"
    conn = sqlite3.connect(str(legacy))
    conn.execute("""
        CREATE TABLE fact_memory (
            id TEXT PRIMARY KEY, entity TEXT NOT NULL, attribute TEXT NOT NULL,
            value TEXT NOT NULL, confidence REAL DEFAULT 0.85, source_session TEXT,
            embedding BLOB, created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (entity, attribute) ON CONFLICT REPLACE)
    """)
    conn.execute("INSERT INTO fact_memory (id, entity, attribute, value) VALUES "
                 "('f1', 'Michael', 'location', 'Magnolia, Texas')")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    conn = db.get_connection(legacy)
    db.init_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(fact_memory)")}
    assert "source_speaker" in cols, "migration did not add source_speaker"
    row = conn.execute("SELECT source_speaker FROM fact_memory WHERE id='f1'").fetchone()
    assert row["source_speaker"] == "Michael", \
        "legacy fact not backfilled to Michael (the Part-1 guardrail wrote everything)"
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    # The migration rebuilds fact_fts before the backfill UPDATE — without that, the
    # fact_fts_update trigger's 'delete' step bricks the DB ("malformed") for any row the
    # index doesn't hold. f1 predates the index here, so it must be findable afterwards.
    hits = conn.execute("SELECT rowid FROM fact_fts WHERE fact_fts MATCH 'Magnolia'").fetchall()
    assert len(hits) == 1, "FTS not rebuilt/synced by the migration"
    conn.close()
    print("  [PASS] v1 DB gains source_speaker, backfilled to Michael, FTS rebuilt, user_version=2")

    # 2. A fresh DB already has the column from the schema file — the guarded ALTER must not
    #    double-add, and re-running init_schema is idempotent.
    fresh = tmp / "fresh.db"
    conn = db.get_connection(fresh)
    db.init_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(fact_memory)")}
    assert "source_speaker" in cols
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    db.init_schema(conn)   # re-run: must not raise (ALTER is table_info-guarded)
    conn.close()
    print("  [PASS] fresh DB is already-shaped; migration re-run is idempotent")

    shutil.rmtree(tmp, ignore_errors=True)


def run_provenance() -> None:
    print("\n── Phase 2: _insert stamps source_speaker; peek/forget (offline, temp DB) ──")
    tmp = Path(tempfile.mkdtemp(prefix="echo_test_prov_"))
    ib = IbLite("fake-model", db_path=tmp / "test.db")
    assert ib.available
    ib.start_session("s1")

    # The speaker arg — voice-ID ground truth — is what lands on the row. The payload has no
    # source_speaker field at all; the gate model never chooses attribution.
    ib._insert("s1", _fact_payload(), speaker="Hillary")
    row = ib._conn.execute(
        "SELECT entity, source_speaker FROM fact_memory WHERE entity='Hillary'").fetchone()
    assert row is not None and row["source_speaker"] == "Hillary"
    print("  [PASS] fact row carries source_speaker=Hillary from the pipeline arg")

    # peek is non-destructive and carries the provenance the forget-permission check needs.
    peek = ib.peek_last_fact()
    assert peek and peek["source_speaker"] == "Hillary" and peek["entity"] == "Hillary"
    assert ib.peek_last_fact() is not None, "peek must not consume the fact"
    print("  [PASS] peek_last_fact returns provenance without consuming")

    # Re-saving the same (entity, attribute) from another speaker UPSERTs the provenance —
    # latest statement wins, same as value.
    ib._insert("s1", _fact_payload(value="migraines"), speaker="Michael")
    row = ib._conn.execute(
        "SELECT value, source_speaker FROM fact_memory WHERE entity='Hillary'").fetchone()
    assert row["value"] == "migraines" and row["source_speaker"] == "Michael"
    n = ib._conn.execute("SELECT COUNT(*) FROM fact_memory WHERE entity='Hillary'").fetchone()[0]
    assert n == 1, "UPSERT must update in place (FTS depends on it), not add a row"
    print("  [PASS] re-stating a fact UPSERTs value AND source_speaker in place")

    # Forget still deletes and keeps FTS in sync (the delete trigger fires).
    forgotten = ib.forget_last_fact()
    assert forgotten and forgotten["source_speaker"] == "Michael"
    left = ib._conn.execute("SELECT COUNT(*) FROM fact_memory").fetchone()[0]
    fts = ib._conn.execute("SELECT COUNT(*) FROM fact_fts WHERE fact_fts MATCH 'Hillary'").fetchone()[0]
    assert left == 0 and fts == 0 and ib.peek_last_fact() is None
    print("  [PASS] forget_last_fact returns provenance, clears the row + FTS + peek")

    ib.close()
    shutil.rmtree(tmp, ignore_errors=True)


def run_context_gating() -> None:
    print("\n── Phase 2: build_context_block(include_profile=) + header (offline, temp DB) ──")
    tmp = Path(tempfile.mkdtemp(prefix="echo_test_ctx_"))
    ib = IbLite("fake-model", db_path=tmp / "test.db")
    ib.start_session("s1")
    ib._conn.execute("INSERT INTO preference_memory (key, value) VALUES ('coffee', 'black')")
    ib._conn.commit()

    full = ib.build_context_block()
    assert "Michael lives" in full, "core profile missing from the full block"
    assert "Rules you follow" in full and "coffee" in full
    print("  [PASS] include_profile=True (default): profile + policies + prefs, unchanged")

    # Unknown speaker on the mic: the stranger prompt carries behavior, not knowledge.
    guarded = ib.build_context_block(include_profile=False)
    assert "Michael lives" not in guarded, "core profile leaked to an unknown speaker"
    assert "preferences" not in guarded and "coffee" not in guarded, "prefs leaked"
    assert "Rules you follow" in guarded, "behavior policies must survive"
    assert guarded.startswith("You are speaking aloud"), "voice guidance must survive"
    print("  [PASS] include_profile=False: no profile, no prefs — policies + voice guidance only")

    # Facts can now come from any known speaker; the header must not claim otherwise.
    assert "with Michael" not in _MEMORY_BLOCK_HEADER
    assert "Simply know it." in _MEMORY_BLOCK_HEADER, "the functional subtlety rule must stay"
    print("  [PASS] memory-block header is speaker-neutral; subtlety rule intact")

    ib.close()
    shutil.rmtree(tmp, ignore_errors=True)


def run_speaker_retrieval() -> None:
    """Speaker-aware retrieval (2026-07-18): facts ABOUT the speaker ride without being named.

    The hybrid search only matches the transcript; speaker_facts is the deterministic
    entity-match slot that fixes "Jon says hey and Echo knows nothing about Jon".
    """
    print("\n── Speaker-aware retrieval: speaker_facts + read_memory(speaker=) (offline, temp DB) ──")
    import ib_lite.retrieval as ret_mod
    from ib_lite.retrieval import speaker_facts, SPEAKER_K

    # read_memory's hybrid path calls retrieval's own encode import — stub it like ib_mod's.
    ret_mod.encode = lambda text: b"\x00\x00\x80\x3f" * 4

    tmp = Path(tempfile.mkdtemp(prefix="echo_test_spk_"))
    ib = IbLite("fake-model", db_path=tmp / "test.db")
    ib.start_session("s1")
    ib._insert("s1", _fact_payload("John", "description", "fantastic beard, friendly"), speaker="Michael")
    ib._insert("s1", _fact_payload("John", "drink", "black coffee"), speaker="John")
    ib._insert("s1", _fact_payload("Willie", "species", "goat"), speaker="Michael")
    ib._insert("s1", _fact_payload("Michael", "location", "Magnolia, Texas"), speaker="Michael")

    # Entity match is case-insensitive, capped at SPEAKER_K, and never leaks other entities.
    facts = speaker_facts(ib._conn, "john")
    assert {f["entity"] for f in facts} == {"John"}, "speaker slot leaked other entities"
    assert len(facts) == 2 and len(facts) <= SPEAKER_K
    print("  [PASS] speaker_facts: case-insensitive entity match, John rows only")

    # The confidence gate applies — a soft-hidden fact (CLI dial-down) stays hidden here too.
    ib._conn.execute("UPDATE fact_memory SET confidence = 0.05 WHERE attribute = 'drink'")
    ib._conn.commit()
    facts = speaker_facts(ib._conn, "John")
    assert [f["attribute"] for f in facts] == ["description"], "confidence gate ignored"
    assert speaker_facts(ib._conn, "") == [] and speaker_facts(ib._conn, "  ") == []
    print("  [PASS] MIN_CONFIDENCE gate applies; empty speaker → no slot")

    # A guest's facts lead the block even when the transcript never names them, and the
    # dedupe keeps a fact that surfaced both ways to one line.
    block, _, count = ib.read_memory("hey echo how is it going", speaker="John")
    assert count >= 1
    first_item = block.splitlines()[1]
    assert first_item.startswith("- John"), f"speaker fact not front-loaded: {first_item!r}"
    assert block.count("- John — description") == 1, "dedupe failed — fact listed twice"
    print("  [PASS] read_memory(speaker=guest): guest facts lead the block, deduped")

    # Michael/None → byte-identical block to the pre-feature call (the solo-path
    # invariant). Compare block + count, not the tuple — element [1] is wall-clock ms.
    base_block, _, base_count = ib.read_memory("hey echo")
    for who in ("Michael", "michael", None):
        blk, _, cnt = ib.read_memory("hey echo", speaker=who)
        assert (blk, cnt) == (base_block, base_count), f"solo path changed for speaker={who!r}"
    print("  [PASS] Michael/None speaker → read_memory block byte-identical (solo path unchanged)")

    ib.close()
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    run_migration()
    run_provenance()
    run_context_gating()
    run_speaker_retrieval()
    print("\n  OFFLINE: all guest-memory checks passed.\n")
