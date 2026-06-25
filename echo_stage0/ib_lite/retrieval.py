"""
Hybrid retrieval for Ib-Lite (Fact + Episodic).

    score = (0.5 * BM25) + (0.3 * cosine_similarity) + (0.2 * recency_decay)

  - BM25 comes from FTS5 and is NEGATIVE — flip with * -1 (higher = better).
    The score scale is NOT normalized (BM25 is unbounded), so MIN_SCORE is an
    empirical floor, not a probability. Tune by feel.
  - cosine_similarity = 1 - vec_distance_cosine(stored_embedding, query_embedding).
  - recency_decay = 1 / (1 + days_since); Fact decays on updated_at, Episodic
    (stronger, more recent-biased) on created_at.

Raw speech can contain FTS5 operators/quotes, so the MATCH string is sanitized
(alnum tokens, quoted, OR-joined). If no usable tokens remain we fall back to a
vector-only query rather than risk an fts5 syntax error.
"""

import re
import sqlite3

from .embedder import encode

RETRIEVAL_WEIGHTS = {"fts": 0.5, "vec": 0.3, "recency": 0.2}
TOP_K = 5
MIN_SCORE = 0.4
# Facts below this confidence are suppressed entirely. Default fact confidence
# is 0.85, so normal facts pass; lower a fact via the CLI to soft-hide it.
MIN_CONFIDENCE = 0.15

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "to", "of", "and", "or",
    "in", "on", "for", "it", "i", "you", "he", "she", "that", "this", "do",
    "did", "have", "has", "with", "my", "your", "me", "we", "they", "but",
}


def _fts_match_query(text: str) -> str | None:
    """Turn raw text into a safe FTS5 MATCH string, or None if nothing usable."""
    seen: set[str] = set()
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text.lower()):
        if len(raw) < 2 or raw in _STOPWORDS or raw in seen:
            continue
        seen.add(raw)
        tokens.append(raw)
        if len(tokens) >= 12:
            break
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in tokens)


def _hybrid_search(
    conn: sqlite3.Connection,
    *,
    table: str,
    fts_table: str,
    ts_col: str,
    select_cols: list[str],
    query: str,
    weights: dict = RETRIEVAL_WEIGHTS,
    top_k: int = TOP_K,
    min_score: float = MIN_SCORE,
    confidence_col: str | None = None,
    min_confidence: float = MIN_CONFIDENCE,
) -> list[dict]:
    """Hybrid FTS5 + cosine + recency search.

    When `confidence_col` is given (facts), `confidence` weights the RANK
    (score = base × confidence) and gates via `min_confidence`, while the
    base score (un-weighted) is what's checked against `min_score` — so
    lowering a fact's confidence demotes/hides it without changing the recall
    floor for normal facts. Episodic search passes no confidence_col.
    """
    q_emb = encode(query)
    fts_q = _fts_match_query(query)
    w_fts = float(weights["fts"])
    w_vec = float(weights["vec"])
    w_rec = float(weights["recency"])
    cols = ", ".join(f"m.{c}" for c in select_cols)
    conf_expr = f"COALESCE(m.{confidence_col}, 1.0)" if confidence_col else "1.0"

    if fts_q:
        fts_cte = f"""fts AS (
                SELECT rowid, bm25({fts_table}) * -1 AS fts_score
                FROM {fts_table} WHERE {fts_table} MATCH ?
            ),"""
        fts_join = "LEFT JOIN fts ON fts.rowid = m.rowid"
        base_expr = (f"({w_fts} * COALESCE(fts.fts_score, 0))"
                     f" + ({w_vec} * COALESCE(vec.vec_score, 0))"
                     f" + ({w_rec} * rec.recency)")
        match_cond = "(fts.fts_score IS NOT NULL OR vec.vec_score IS NOT NULL)"
        lead_params: list = [fts_q, q_emb]
    else:
        fts_cte = ""
        fts_join = ""
        base_expr = (f"({w_vec} * COALESCE(vec.vec_score, 0))"
                     f" + ({w_rec} * rec.recency)")
        match_cond = "vec.vec_score IS NOT NULL"
        lead_params = [q_emb]

    conf_filter = " AND confidence >= ?" if confidence_col else ""

    sql = f"""
        WITH {fts_cte}
        vec AS (
            SELECT rowid, 1.0 - vec_distance_cosine(embedding, ?) AS vec_score
            FROM {table} WHERE embedding IS NOT NULL
        ),
        rec AS (
            SELECT rowid,
                1.0 / (1.0 + (julianday('now') - julianday({ts_col}))) AS recency
            FROM {table}
        ),
        scored AS (
            SELECT {cols},
                ROUND({base_expr}, 4) AS base_score,
                ROUND(({base_expr}) * {conf_expr}, 4) AS score
            FROM {table} m
            JOIN rec ON rec.rowid = m.rowid
            {fts_join}
            LEFT JOIN vec ON vec.rowid = m.rowid
            WHERE {match_cond}
        )
        SELECT * FROM scored
        WHERE base_score >= ?{conf_filter}
        ORDER BY score DESC LIMIT ?
    """

    params = lead_params + [min_score]
    if confidence_col:
        params.append(min_confidence)
    params.append(top_k)

    rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def fact_search(conn: sqlite3.Connection, query: str, **kw) -> list[dict]:
    """Top facts for a query (entity/attribute/value/confidence/score)."""
    return _hybrid_search(
        conn,
        table="fact_memory",
        fts_table="fact_fts",
        ts_col="updated_at",
        select_cols=["id", "entity", "attribute", "value", "confidence"],
        query=query,
        confidence_col="confidence",
        **kw,
    )


def episodic_search(conn: sqlite3.Connection, query: str, **kw) -> list[dict]:
    """Top past-session summaries for a query (summary/topics/mood/score)."""
    return _hybrid_search(
        conn,
        table="episodic_memory",
        fts_table="episodic_fts",
        ts_col="created_at",
        select_cols=["id", "session_id", "summary", "key_topics", "mood_signal", "turn_count"],
        query=query,
        **kw,
    )
