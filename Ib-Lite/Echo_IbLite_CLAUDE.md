# CLAUDE.md — Echo / Ib-Lite Memory Subsystem

## What You Are Building

Ib-Lite is Echo's memory layer, replacing the existing OpenMemory/Hindsight HTTP
backend. It is a self-contained SQLite database with five typed memory tables,
FTS5 keyword search, sqlite-vec semantic search, and a 12B-model-driven write path.

**Stages 0–4 of Echo are COMPLETE and working.** You are touching ONLY the memory
layer. Do not modify anything related to audio, VAD, STT, TTS, or latency.

---

## Stack

| Component          | Technology                                    | Notes                                        |
|--------------------|-----------------------------------------------|----------------------------------------------|
| Language           | Python 3.11+                                  | Match existing Echo scripts                  |
| Database           | SQLite (via `sqlite3` stdlib)                 | WAL mode, foreign keys ON                    |
| Keyword search     | FTS5 (built into SQLite)                      | BM25 via `bm25()` — returns negative, flip   |
| Vector search      | `sqlite-vec` extension (`vec0.dll` on Windows)| Load via `conn.load_extension()`             |
| Embeddings         | `sentence-transformers` — all-MiniLM-L6-v2   | 22MB, 384-dim, CPU only — never GPU          |
| LLM (gate prompts) | LM Studio OpenAI-compatible API               | Use `127.0.0.1`, never `localhost`           |
| Schema file        | `ib_lite_schema.sql`                          | Run once on first init                       |

---

## What Is Being Replaced

These things **exist in the current codebase** and are being **deleted or gutted**:

- `memory_reader.py` — **DELETE**. Replace with `ib_lite.py` reader functions.
- Any `MemoryClient`, `OpenMemory`, or `Hindsight` imports — **REMOVE ALL**.
- Stage 3 write paths (Path A "remember that" and Path B summary JSON writes to
  OpenMemory) — **REMOVE**. Replace with Ib-Lite significance gate write path.
- The `async=true` Hindsight retain pattern — **GONE**. Ib-Lite writes are synchronous
  SQLite inserts. No async memory lag.
- HTTP memory calls to external server — **GONE**. Everything is local SQLite.

**Keep everything else.** The `./sessions/` directory and session JSON files can
remain as a historical log — just don't read from them for memory anymore.

---

## File Structure

```
echo/
├── ib_lite/
│   ├── __init__.py
│   ├── ib_lite.py          # Main entry point: init, read, write, end_session
│   ├── db.py               # SQLite connection management, schema init
│   ├── embedder.py         # all-MiniLM-L6-v2 wrapper, encode() → bytes
│   ├── significance.py     # Significance gate LLM prompt + parse
│   ├── schema.py           # Validation gate: validate_write(type, payload)
│   ├── retrieval.py        # Hybrid retrieval: fact_search(), episodic_search()
│   └── ib_lite_schema.sql  # Schema file (copy from handoff)
├── ib_lite_cli.py          # Optional: inspect/edit memory from terminal
└── echo.db                 # SQLite database (created on first run)
```

---

## SQL Schema

Full schema is in `ib_lite_schema.sql`. Key points:

```sql
-- Five typed tables:
core_memory       (key TEXT PK, content TEXT, updated_at)
preference_memory (key TEXT PK, value TEXT, confidence, source_session, timestamps)
fact_memory       (id TEXT PK, entity, attribute, value, confidence, source_session,
                   embedding BLOB, timestamps,
                   UNIQUE(entity, attribute) ON CONFLICT REPLACE)
episodic_memory   (id TEXT PK, session_id, summary, key_topics JSON, mood_signal,
                   turn_count, embedding BLOB, created_at)
policy_memory     (key TEXT PK, rule, priority 1-10, active 0|1, source_session, created_at)

-- FTS5 virtual tables (keyword search):
fact_fts          USING fts5(entity, attribute, value, content='fact_memory')
episodic_fts      USING fts5(summary, key_topics, content='episodic_memory')

-- FTS5 sync triggers on INSERT/UPDATE/DELETE for both fact and episodic
```

---

## Code Stubs

### db.py
```python
import sqlite3
from pathlib import Path

DB_PATH = Path("echo.db")
VEC_EXTENSION = Path("vec0.dll")  # Windows; adjust path if needed

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.enable_load_extension(True)
    conn.load_extension(str(VEC_EXTENSION))
    conn.enable_load_extension(False)
    return conn

def init_schema(conn: sqlite3.Connection) -> None:
    schema = Path("ib_lite/ib_lite_schema.sql").read_text()
    conn.executescript(schema)
    conn.commit()
```

### embedder.py
```python
from sentence_transformers import SentenceTransformer
import numpy as np

_model = None

def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
    return _model

def encode(text: str) -> bytes:
    vec = _get_model().encode(text, normalize_embeddings=True)
    return vec.astype(np.float32).tobytes()

def decode(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)
```

### significance.py
```python
import json
import httpx

GATE_SYSTEM = """You are Echo's memory gate. After each conversation turn, decide
if anything is worth saving to long-term memory.

Respond ONLY with valid JSON. No explanation, no markdown, no preamble.

If nothing is worth saving:
{"save": false}

If saving a fact about Michael or his world:
{"save": true, "type": "fact", "entity": "<entity>", "attribute": "<attribute>", "value": "<value>"}

If saving a personal preference:
{"save": true, "type": "preference", "key": "<key>", "value": "<value>"}

If saving a behavioral rule for Echo:
{"save": true, "type": "policy", "key": "<key>", "rule": "<rule>", "priority": <1-10>}

Rules:
- Only save new information not already known
- Do not save things Echo should already know from core memory
- Be specific: "Jeep needs new shocks" not "car stuff"
- If uncertain, save: false
"""

def run_gate(turn_text: str, lm_base: str = "http://127.0.0.1:1234") -> dict:
    resp = httpx.post(
        f"{lm_base}/v1/chat/completions",
        json={
            "model": "gemma-4-12b",  # adjust to actual LM Studio model name
            "messages": [
                {"role": "system", "content": GATE_SYSTEM},
                {"role": "user", "content": f"Turn transcript:\n{turn_text}"}
            ],
            "temperature": 0.1,
            "max_tokens": 150,
        },
        timeout=10,
    )
    raw = resp.json()["choices"][0]["message"]["content"].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"save": False, "_raw": raw, "_error": "json_parse_failed"}
```

### schema.py (validation gate)
```python
REQUIRED_FIELDS = {
    "fact":       {"entity", "attribute", "value"},
    "preference": {"key", "value"},
    "policy":     {"key", "rule", "priority"},
}

def validate_write(payload: dict) -> tuple[bool, str]:
    """Returns (valid, error_message). On failure, caller retries once."""
    ptype = payload.get("type")
    if ptype not in REQUIRED_FIELDS:
        return False, f"Unknown type: {ptype}"
    missing = REQUIRED_FIELDS[ptype] - set(payload.keys())
    if missing:
        return False, f"Missing fields: {missing}"
    if ptype == "policy":
        p = payload.get("priority")
        if not isinstance(p, int) or not (1 <= p <= 10):
            return False, f"priority must be int 1-10, got: {p}"
    return True, ""
```

### retrieval.py
```python
import sqlite3
from embedder import encode

RETRIEVAL_WEIGHTS = {"fts": 0.5, "vec": 0.3, "recency": 0.2}
TOP_K = 5
MIN_SCORE = 0.4

def fact_search(conn: sqlite3.Connection, query: str) -> list[dict]:
    query_embedding = encode(query)
    sql = """
        WITH fts AS (
            SELECT rowid, bm25(fact_fts) * -1 AS fts_score
            FROM fact_fts WHERE fact_fts MATCH ?
        ),
        vec AS (
            SELECT rowid,
                1.0 - vec_distance_cosine(embedding, ?) AS vec_score
            FROM fact_memory WHERE embedding IS NOT NULL
        ),
        rec AS (
            SELECT id, rowid,
                1.0 / (1.0 + (julianday('now') - julianday(updated_at))) AS recency
            FROM fact_memory
        )
        SELECT f.id, f.entity, f.attribute, f.value, f.confidence,
            ROUND(
                (? * COALESCE(fts.fts_score, 0))
                + (? * COALESCE(vec.vec_score, 0))
                + (? * rec.recency), 4
            ) AS score
        FROM fact_memory f
        JOIN rec ON rec.rowid = f.rowid
        LEFT JOIN fts ON fts.rowid = f.rowid
        LEFT JOIN vec ON vec.rowid = f.rowid
        WHERE (fts.fts_score IS NOT NULL OR vec.vec_score IS NOT NULL)
          AND score >= ?
        ORDER BY score DESC LIMIT ?
    """
    rows = conn.execute(sql, (
        query, query_embedding,
        RETRIEVAL_WEIGHTS["fts"],
        RETRIEVAL_WEIGHTS["vec"],
        RETRIEVAL_WEIGHTS["recency"],
        MIN_SCORE, TOP_K
    )).fetchall()
    return [dict(r) for r in rows]
```

### ib_lite.py (main interface)
```python
"""
Three functions the Echo pipeline calls:

  read_memory(query)   → str  (formatted memory block for system prompt)
  write_memory(turn)   → None (async-safe: fire significance gate, write if needed)
  end_session(summary) → None (write episodic row, close session)
"""

def build_context_block(conn) -> str:
    """Load Core + Policy for system prompt injection at session start."""
    ...

def read_memory(conn, query: str) -> str:
    """Per-turn: retrieve relevant Facts and Episodic memories."""
    ...

def write_memory(conn, session_id: str, turn_text: str) -> None:
    """After each turn: run significance gate, validate, insert if needed."""
    ...

def end_session(conn, session_id: str, transcript: str) -> None:
    """On sign-off: generate episodic summary, write to episodic_memory."""
    ...
```

---

## Gotchas

**1. `localhost` adds ~2s on Windows — always use `127.0.0.1`**
All HTTP calls to LM Studio must use `127.0.0.1:1234`. This is a known existing
Echo gotcha. Do not reintroduce localhost.

**2. `bm25()` in SQLite FTS5 returns negative values**
`bm25(fact_fts)` returns e.g. `-2.4`. Multiply by `-1` before scoring. Lower
magnitude (closer to 0) = better BM25 match. After flip, higher = better.

**3. sqlite-vec must be loaded per connection**
```python
conn.enable_load_extension(True)
conn.load_extension("vec0")
conn.enable_load_extension(False)
```
Load it after every new connection. The extension is not persistent. Download
`vec0.dll` for Windows from: https://github.com/asg017/sqlite-vec/releases

**4. FTS5 triggers must stay in sync**
Do NOT use `INSERT OR REPLACE` to bypass the FTS5 trigger chain. The schema uses
`UNIQUE(entity, attribute) ON CONFLICT REPLACE` which fires the UPDATE trigger
correctly. Do not manually handle deduplication in Python.

**5. Embeddings are stored as raw bytes**
```python
embedding_bytes = encode(text)         # returns bytes (384 × float32)
# Store: INSERT ... VALUES (..., ?, ...)  with embedding_bytes
# Read:  np.frombuffer(row["embedding"], dtype=np.float32)
```
Never store as JSON array — use raw bytes for vec_distance_cosine to work.

**6. Significance gate is a SEPARATE LLM call**
The gate runs as its own system prompt + user message, completely isolated from
Echo's personality generation. Use `temperature=0.1` for deterministic output.
Do not mix it into Echo's main conversation loop.

**7. Episodic write happens BEFORE session `ended_at`**
Call `end_session()` → write Episodic row → then update `sessions.ended_at`.
Do not close the session before the episodic write.

**8. All-MiniLM-L6-v2 loads once, stays in memory**
Use a module-level `_model = None` singleton. Load on first call to `encode()`.
Don't re-instantiate per turn or per write — it loads in ~1-2s.

**9. Validation gate retries once, then discards**
On second failure, log the raw output and payload to a `memory_failures.log`
file for future tuning. Do not raise exceptions — memory write failure is
non-fatal. Echo continues the conversation.

**10. `UNIQUE(entity, attribute) ON CONFLICT REPLACE` means updates are free**
Writing the same entity/attribute pair twice just updates the row. Don't check
for existence before writing — let SQLite handle it.

---

## Scope Guard

You are building Ib-Lite memory. You are NOT:

- Modifying VAD, STT, TTS, or Kokoro in any way
- Changing the streaming or latency logic in `llm.py` (only update system prompt assembly)
- Implementing Echo's personality layer (Stage 5 Part 2 — separate PRD)
- Adding web search tools (Stage 5 Part 3 — separate PRD)
- Migrating old OpenMemory data — start fresh, no migration script needed
- Adding graph relationships between memories
- Building any cloud sync, telemetry, or external API calls of any kind

If a task isn't in the PRD milestones, it's out of scope.

---

## Build Checklist

- [ ] `ib_lite_schema.sql` copied into `ib_lite/` and verified
- [ ] `vec0.dll` present and loadable on Windows (test with `conn.load_extension("vec0")`)
- [ ] `sentence-transformers` installed: `pip install sentence-transformers --break-system-packages`
- [ ] `db.py` init runs cleanly and all five tables + FTS5 virtual tables exist
- [ ] Core and Policy seed data present after init
- [ ] Significance gate returns valid JSON on a test turn
- [ ] Validation gate catches missing fields and triggers retry
- [ ] Fact write: row appears in `fact_memory` AND `fact_fts` is updated
- [ ] Fact read: hybrid retrieval returns ranked results for a test query
- [ ] Episodic write: session-end summary produces a row in `episodic_memory`
- [ ] Episodic read: per-turn retrieval returns recent session context
- [ ] `memory_reader.py` deleted and no OpenMemory imports remain in codebase
- [ ] `llm.py` system prompt assembly updated to use `build_context_block()`
- [ ] Full session smoke test: boot → Core injected → turn → Fact saved → sign-off → Episodic written
- [ ] First-audio latency verified ≤ 1.3s (memory operations must not add to hot path)

---

## MEMORY

**Hindsight bank:** `echo` (no separate bank for Ib-Lite)
**Ib logging:** Ib-Lite decisions and architecture notes should be retained to Ib
under session IDs in format `ib-YYYY-MM-DD-echo-iblite-*`

Type tags for Hindsight: `ib-lite`, `memory`, `sqlite`, `stage5`

---

## Axly's Customs Standards

- Local-first, no telemetry, no external calls except LM Studio (127.0.0.1)
- No cloud storage, no syncing, no analytics
- Python 3.11+; use stdlib where possible
- SQLite only — no Postgres, no Docker, no external vector DBs
- All LLM calls use LM Studio's OpenAI-compatible API
- EULA and PRIVACY follow Texas law templates (existing Echo copies apply)
