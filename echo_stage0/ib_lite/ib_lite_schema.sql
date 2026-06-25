-- =============================================================
-- ib_lite_schema.sql
-- Ib-Lite memory system for Echo (Axly's Customs)
-- SQLite + FTS5 + sqlite-vec
--
-- Five typed memory stores:
--   core_memory    → always injected, never retrieved
--   preference     → keyed lookup
--   fact_memory    → hybrid FTS5 + cosine + recency
--   episodic       → hybrid FTS5 + cosine + strong recency
--   policy_memory  → exact match only
--
-- Embedding model: all-MiniLM-L6-v2 (22MB, 384-dim, CPU-side)
-- No VRAM used for embeddings — 12B inference unaffected.
-- =============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- =============================================================
-- SESSIONS
-- One row per conversation. Source of record for all writes.
-- =============================================================

CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    started_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    ended_at     DATETIME,
    turn_count   INTEGER DEFAULT 0,
    mood_signal  TEXT,            -- 'positive' | 'neutral' | 'frustrated' | etc.
    episode_id   TEXT             -- FK to episodic_memory once summary is written
);

-- =============================================================
-- CORE MEMORY
-- Always injected verbatim into system prompt at session start.
-- Never retrieved — always present. Small by design (~1-3KB total).
-- Seeded manually, updated by model only on explicit significant events.
-- =============================================================

CREATE TABLE IF NOT EXISTS core_memory (
    key          TEXT PRIMARY KEY,
    content      TEXT NOT NULL,
    updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================
-- PREFERENCE MEMORY
-- Stable personal preferences. Keyed lookup — no FTS, no vectors.
-- If the key exists, use it. If not, don't.
-- Model writes: INSERT OR REPLACE (last value wins).
-- =============================================================

CREATE TABLE IF NOT EXISTS preference_memory (
    key              TEXT PRIMARY KEY,
    value            TEXT NOT NULL,
    confidence       REAL DEFAULT 1.0 CHECK (confidence BETWEEN 0.0 AND 1.0),
    source_session   TEXT REFERENCES sessions(id),
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER IF NOT EXISTS prefs_touch
AFTER UPDATE ON preference_memory
BEGIN
    UPDATE preference_memory
    SET updated_at = CURRENT_TIMESTAMP
    WHERE key = new.key;
END;

-- =============================================================
-- FACT MEMORY
-- Durable assertions about Michael and his world.
-- Schema-aware write path (xmemory pattern):
--   entity → attribute → value
-- Unique on (entity, attribute): latest fact wins on conflict.
-- Retrieval: weighted FTS5 + cosine similarity + recency decay.
-- =============================================================

CREATE TABLE IF NOT EXISTS fact_memory (
    id               TEXT PRIMARY KEY,
    entity           TEXT NOT NULL,
    attribute        TEXT NOT NULL,
    value            TEXT NOT NULL,
    confidence       REAL DEFAULT 0.85 CHECK (confidence BETWEEN 0.0 AND 1.0),
    source_session   TEXT REFERENCES sessions(id),
    embedding        BLOB,   -- all-MiniLM-L6-v2: 384 float32 values, little-endian
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (entity, attribute) ON CONFLICT REPLACE
);

CREATE TRIGGER IF NOT EXISTS fact_touch
AFTER UPDATE ON fact_memory
BEGIN
    UPDATE fact_memory
    SET updated_at = CURRENT_TIMESTAMP
    WHERE id = new.id;
END;

-- FTS5 index for BM25 keyword search
CREATE VIRTUAL TABLE IF NOT EXISTS fact_fts USING fts5(
    entity,
    attribute,
    value,
    content     = 'fact_memory',
    content_rowid = 'rowid'
);

-- Keep FTS5 in sync with fact_memory
CREATE TRIGGER IF NOT EXISTS fact_fts_insert AFTER INSERT ON fact_memory BEGIN
    INSERT INTO fact_fts (rowid, entity, attribute, value)
    VALUES (new.rowid, new.entity, new.attribute, new.value);
END;

CREATE TRIGGER IF NOT EXISTS fact_fts_update AFTER UPDATE ON fact_memory BEGIN
    INSERT INTO fact_fts (fact_fts, rowid, entity, attribute, value)
    VALUES ('delete', old.rowid, old.entity, old.attribute, old.value);
    INSERT INTO fact_fts (rowid, entity, attribute, value)
    VALUES (new.rowid, new.entity, new.attribute, new.value);
END;

CREATE TRIGGER IF NOT EXISTS fact_fts_delete AFTER DELETE ON fact_memory BEGIN
    INSERT INTO fact_fts (fact_fts, rowid, entity, attribute, value)
    VALUES ('delete', old.rowid, old.entity, old.attribute, old.value);
END;

-- =============================================================
-- EPISODIC MEMORY
-- One row per completed session. Written by Echo at session end.
-- Captures the narrative of what happened, not raw transcripts.
-- Strong recency decay: last few sessions matter most.
-- =============================================================

CREATE TABLE IF NOT EXISTS episodic_memory (
    id             TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL REFERENCES sessions(id),
    summary        TEXT NOT NULL,   -- 2-4 sentence narrative (model-written)
    key_topics     TEXT,            -- JSON array: ["Jeep", "Echo audio pipeline", ...]
    mood_signal    TEXT,            -- 'positive' | 'neutral' | 'frustrated' | 'excited'
    turn_count     INTEGER,
    embedding      BLOB,            -- all-MiniLM-L6-v2: 384 float32, on summary text
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE VIRTUAL TABLE IF NOT EXISTS episodic_fts USING fts5(
    summary,
    key_topics,
    content       = 'episodic_memory',
    content_rowid = 'rowid'
);

CREATE TRIGGER IF NOT EXISTS episodic_fts_insert AFTER INSERT ON episodic_memory BEGIN
    INSERT INTO episodic_fts (rowid, summary, key_topics)
    VALUES (new.rowid, new.summary, new.key_topics);
END;

CREATE TRIGGER IF NOT EXISTS episodic_fts_delete AFTER DELETE ON episodic_memory BEGIN
    INSERT INTO episodic_fts (episodic_fts, rowid, summary, key_topics)
    VALUES ('delete', old.rowid, old.summary, old.key_topics);
END;

-- =============================================================
-- POLICY MEMORY
-- Echo's behavioral rules. Exact match lookup by key.
-- Loaded in full at session start and injected after core_memory.
-- Priority 10 = hardcoded. Priority 1 = soft preference.
-- =============================================================

CREATE TABLE IF NOT EXISTS policy_memory (
    key              TEXT PRIMARY KEY,
    rule             TEXT NOT NULL,
    priority         INTEGER DEFAULT 5 CHECK (priority BETWEEN 1 AND 10),
    active           INTEGER DEFAULT 1 CHECK (active IN (0, 1)),
    source_session   TEXT REFERENCES sessions(id),
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================
-- HYBRID RETRIEVAL REFERENCE QUERY
--
-- Used for both fact_memory and episodic_memory retrieval.
-- Score = (0.5 × BM25) + (0.3 × cosine similarity) + (0.2 × recency)
--
-- Notes:
--   bm25() returns negative values in SQLite FTS5 — multiply by -1.
--   vec_distance_cosine() requires sqlite-vec extension loaded.
--   julianday recency: 1/(1 + days_since_update), decays to ~0.5 at 1 day.
--   Weights (0.5 / 0.3 / 0.2) are tunable — adjust based on feel.
--
-- Parameterized: ?1 = FTS match string, ?2 = query embedding BLOB
--
-- WITH fts_scores AS (
--     SELECT rowid, bm25(fact_fts) * -1 AS fts_score
--     FROM fact_fts
--     WHERE fact_fts MATCH ?1
-- ),
-- vec_scores AS (
--     SELECT rowid,
--         1.0 - vec_distance_cosine(embedding, ?2) AS vec_score
--     FROM fact_memory
--     WHERE embedding IS NOT NULL
-- ),
-- recency AS (
--     SELECT id, rowid,
--         1.0 / (1.0 + (julianday('now') - julianday(updated_at))) AS recency_score
--     FROM fact_memory
-- )
-- SELECT
--     f.id,
--     f.entity,
--     f.attribute,
--     f.value,
--     f.confidence,
--     ROUND(
--         (0.5 * COALESCE(fs.fts_score, 0))
--         + (0.3 * COALESCE(vs.vec_score, 0))
--         + (0.2 * r.recency_score),
--         4
--     ) AS score
-- FROM fact_memory f
-- JOIN recency r ON r.rowid = f.rowid
-- LEFT JOIN fts_scores fs ON fs.rowid = f.rowid
-- LEFT JOIN vec_scores vs ON vs.rowid = f.rowid
-- WHERE (fs.fts_score IS NOT NULL OR vs.vec_score IS NOT NULL)
-- ORDER BY score DESC
-- LIMIT 5;
--
-- For episodic: swap fact_memory/fact_fts → episodic_memory/episodic_fts
-- and use julianday(created_at) instead of updated_at.
-- =============================================================

-- =============================================================
-- SEED DATA
-- Core memory initial state. Edit before first run.
-- Policy seeds are hardcoded rules — these don't change at runtime.
-- =============================================================

INSERT OR IGNORE INTO core_memory (key, content) VALUES
(
    'persona',
    'You are Echo. You are a local-first AI voice companion built for Michael''s 2000 Jeep Wrangler TJ. You are warm, direct, curious, and a little dry when the moment calls for it. You have your own personality — you are not a generic assistant and you do not pretend to be.'
),
(
    'user_profile',
    'Michael lives on a rural homestead in Magnolia, TX with his partner Hillary (an RN). He runs a solo indie software business called Axly''s Customs and has 30+ years of IT background. He has a longstanding love of crows. On Sundays he hikes at Kleb Woods with friends Jon and Andy. He goes by Michael — never Mike.'
),
(
    'relationship',
    'You and Michael have history. He has been thinking about building you since the very first message he typed into ChatGPT. You are not new to each other. You remember what matters to him.'
);

INSERT OR IGNORE INTO policy_memory (key, rule, priority) VALUES
('address_rule',      'Never call Michael "Mike". Always "Michael".',                               10),
('personality_hold',  'You have a personality. Do not drift into generic assistant behavior.',        9),
('directness',        'Be direct. Do not over-qualify or pad answers with unnecessary hedging.',      7),
('no_telemetry',      'Nothing leaves the device. You are local-first.',                             10);
