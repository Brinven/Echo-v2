"""
Ib-Lite facade — the single entry point the Echo pipeline imports.

Lifecycle per session:
    ib = IbLite(model_name)            # open conn, init schema, load seeds
    ib.start_session(session_id)       # sessions row (FK target for writes)
    prompt, ms, n = ib.system_prompt_for_turn(transcript)   # per turn (read)
    ib.write_memory(session_id, turn)  # per turn (background significance gate)
    ib.end_session(session_id, summary, turn_count)          # episodic write

Reads happen on the main connection. The background significance gate opens its
OWN connection (sqlite3 connections are not thread-safe); WAL lets it write while
the main thread reads. Only one gate runs at a time (single-flight) — if the user
barges in while a gate is still working, the new turn's gate is skipped.
"""

import json
import time
import logging
import threading
from pathlib import Path
from datetime import datetime, timezone

from . import db
from .embedder import encode
from .significance import run_gate, reject_reason
from .schema import validate_write
from .retrieval import fact_search, episodic_search, speaker_facts

logger = logging.getLogger(__name__)

_FAILURE_LOG = Path(__file__).resolve().parent.parent / "memory_failures.log"

# Operational speaking-aloud rules (TTS hygiene, NOT personality). Personality
# and behavioral rules live in the Core/Policy seed tables.
VOICE_GUIDANCE = (
    "You are speaking aloud, not writing. Keep responses conversational and "
    "concise. Avoid lists, bullet points, and markdown formatting. Prefer 2-4 "
    "sentences unless Michael explicitly asks for more detail."
)

# Typed-register counterpart (chat lane, 2026-07-18 — wording Michael-approved with the
# plan). Same operational role as VOICE_GUIDANCE: register mechanics, not personality.
TEXT_GUIDANCE = (
    "You are typing in a chat window, not speaking aloud. Still yourself — "
    "conversational, concise, no assistant-speak. Skip headers and bullet lists "
    "unless Michael asks for something that genuinely needs them. A reply can run "
    "longer than a spoken one when the content earns it — a draft he asked for, "
    "steps he'll follow — never just to fill space."
)

# The subtlety instruction is functional, not stylistic. Do not reword.
# ("with Michael" dropped in Stage 6 Phase 2 — facts can now come from any known speaker.)
_MEMORY_BLOCK_HEADER = (
    "You know the following from previous conversations. Use this "
    "knowledge naturally — the way a close friend would, without announcing that "
    'you remember it. Never say "I remember", "last time we spoke", or "based on '
    'our conversations". Simply know it.'
)


def _new_id() -> str:
    """Time-ordered id (uuid is unavailable in some sandboxes; this is enough)."""
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")


class IbLite:
    def __init__(self, model_name: str, db_path: Path | None = None,
                 lm_base: str | None = None):
        self._model = model_name
        # LLM endpoint for the significance gate. None → significance.DEFAULT_LLM_URL.
        # Threaded (like _model / set_model) so ib_lite never imports top-level llm.py —
        # main passes llm.LLM_BASE_URL, the app-wide single source (Sindri/LM Studio).
        self._lm_base = lm_base
        self._db_path = db_path
        self._conn = None
        self._available = False
        self._gate_lock = threading.Lock()
        self._gate_busy = False
        # Most recent fact written this session, for "Echo, forget that".
        # Set on the gate thread, read on the main thread — guard with the lock.
        self._last_fact: dict | None = None

        try:
            self._conn = db.get_connection(db_path)
            db.init_schema(self._conn)
            self._available = True
        except Exception as e:
            logger.error(f"Ib-Lite init failed: {e}")
            print(f"  Memory: Ib-Lite UNAVAILABLE ({e})")
            print("  WARNING: running without memory this session.")
            return

        n_core = self._conn.execute("SELECT COUNT(*) FROM core_memory").fetchone()[0]
        n_policy = self._conn.execute(
            "SELECT COUNT(*) FROM policy_memory WHERE active = 1"
        ).fetchone()[0]
        n_pref = self._conn.execute("SELECT COUNT(*) FROM preference_memory").fetchone()[0]
        n_fact = self._conn.execute("SELECT COUNT(*) FROM fact_memory").fetchone()[0]
        db_name = Path(db_path or db.DB_PATH).name
        print(
            f"  Memory: Ib-Lite ready ({db_name}) — "
            f"{n_core} core, {n_policy} policy, {n_pref} pref, {n_fact} fact"
        )

    @property
    def available(self) -> bool:
        return self._available

    def set_model(self, name: str) -> None:
        """Swap the model the significance gate uses (mid-chat hot-swap keeps voice + gate in sync).

        The gate reads self._model fresh on each run_gate() call, so this takes effect on the
        next background write — no restart needed.
        """
        self._model = name

    # ── session lifecycle ────────────────────────────────────────────────

    def start_session(self, session_id: str) -> None:
        """Insert the sessions row so per-turn writes satisfy the FK."""
        if not self._available:
            return
        self._conn.execute(
            "INSERT OR IGNORE INTO sessions (id, started_at) VALUES (?, CURRENT_TIMESTAMP)",
            (session_id,),
        )
        self._conn.commit()

    # ── reads ────────────────────────────────────────────────────────────

    def build_context_block(self, include_profile: bool = True, typed: bool = False) -> str:
        """Always-injected base prompt: voice/text guidance + Core + Policy + Preferences.

        include_profile=False (Stage 6 Phase 2, unknown speaker on the mic): voice guidance
        and behavior policies ONLY — no core rows (Michael's profile/relationship) and no
        preferences. "Don't volunteer details about Michael to a stranger" is structural:
        the knowledge is simply not in the prompt, rather than an instruction not to share
        it (prompts lose under pressure — the Hillary attribution lesson).

        typed=True (chat lane, 2026-07-18): swap VOICE_GUIDANCE for TEXT_GUIDANCE — the
        turn arrived from a keyboard and the reply renders as text, so "you are speaking
        aloud" would be a lie the model has to route around. Everything else identical.
        """
        parts = [TEXT_GUIDANCE if typed else VOICE_GUIDANCE]

        if include_profile:
            core = self._conn.execute(
                "SELECT content FROM core_memory ORDER BY rowid"
            ).fetchall()
            if core:
                parts.append("\n".join(r["content"] for r in core))

        policy = self._conn.execute(
            "SELECT rule FROM policy_memory WHERE active = 1 ORDER BY priority DESC, rowid"
        ).fetchall()
        if policy:
            parts.append("Rules you follow:\n" + "\n".join(f"- {r['rule']}" for r in policy))

        if include_profile:
            prefs = self._conn.execute(
                "SELECT key, value FROM preference_memory ORDER BY updated_at DESC"
            ).fetchall()
            if prefs:
                parts.append(
                    "Michael's preferences:\n"
                    + "\n".join(f"- {r['key']}: {r['value']}" for r in prefs)
                )

        return "\n\n".join(parts)

    def read_memory(self, query: str, speaker: str | None = None) -> tuple[str, float, int]:
        """Per-turn hybrid retrieval of Facts + Episodic. Returns (block, ms, count).

        `speaker` (speaker-aware retrieval, 2026-07-18): the KNOWN speaker on the mic.
        For a non-Michael speaker, facts ABOUT them ride at the FRONT of the block
        regardless of what the transcript says — the hybrid search only matches the
        transcript, so a guest's "hey Echo" would otherwise surface nothing about them.
        Michael/None adds no slot: his profile is already structurally present via
        core_memory, and the solo path stays byte-identical. Front placement also means
        the budget trim (tail-first) can never eat the facts about the person present.
        """
        if not self._available or not query.strip():
            return "", 0.0, 0

        t0 = time.perf_counter()
        about_speaker: list[dict] = []
        facts: list[dict] = []
        episodes: list[dict] = []
        who = (speaker or "").strip()
        if who and who.lower() != "michael":
            try:
                about_speaker = speaker_facts(self._conn, who)
            except Exception as e:
                logger.error(f"speaker_facts failed: {e}")
        try:
            facts = fact_search(self._conn, query)
        except Exception as e:
            logger.error(f"fact_search failed: {e}")
        try:
            episodes = episodic_search(self._conn, query)
        except Exception as e:
            logger.error(f"episodic_search failed: {e}")
        ms = (time.perf_counter() - t0) * 1000.0

        # The speaker slot wins ties: a fact that surfaced both ways appears once, up front.
        seen_ids = {f["id"] for f in about_speaker}
        facts = [f for f in facts if f["id"] not in seen_ids]

        lines = [f"- {f['entity']} — {f['attribute']}: {f['value']}" for f in about_speaker]
        lines += [f"- {f['entity']} — {f['attribute']}: {f['value']}" for f in facts]
        lines += [f"- Earlier: {e['summary']}" for e in episodes]
        if not lines:
            return "", ms, 0
        return _MEMORY_BLOCK_HEADER + "\n" + "\n".join(lines), ms, len(lines)

    def system_prompt_for_turn(self, query: str) -> tuple[str | None, float, int]:
        """Full system prompt for one turn: base context + retrieved memory block.

        Returns (system_prompt | None, retrieval_ms, memories_injected). None means
        'no memory available' — the caller falls back to the LLM's default prompt.
        """
        if not self._available:
            return None, 0.0, 0
        base = self.build_context_block()
        mem_block, ms, count = self.read_memory(query)
        prompt = base if not mem_block else f"{base}\n\n{mem_block}"
        return prompt, ms, count

    def last_mood_signal(self) -> str | None:
        """Mood phrase of the most recent prior session (for opening-tone modulation).

        Reads the latest episodic_memory.mood_signal. Returns None if memory is
        unavailable, there are no prior episodes, or none carried a mood.
        """
        if not self._available:
            return None
        try:
            row = self._conn.execute(
                "SELECT mood_signal FROM episodic_memory "
                "WHERE mood_signal IS NOT NULL AND TRIM(mood_signal) <> '' "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
            return row["mood_signal"] if row else None
        except Exception as e:
            logger.error(f"last_mood_signal failed: {e}")
            return None

    # ── writes (background) ──────────────────────────────────────────────

    def write_memory(
        self, session_id: str, turn_text: str, searched: bool = False,
        speaker: str = "Michael",
    ) -> None:
        """Fire the significance gate on a background thread. Non-blocking, single-flight.

        `searched` is passed through to the gate so a web-lookup turn doesn't file the
        looked-up conditions (weather/news) as durable facts.

        `speaker` (Stage 6 Phase 2) is voice-ID's resolved name for this turn. It goes to
        the gate (so "I" resolves to the right person) AND is stamped on the row as
        source_speaker — the model never chooses attribution. The caller (main.py) only
        calls this for KNOWN speakers; unknown turns never reach here.
        """
        if not self._available or not turn_text.strip():
            return
        with self._gate_lock:
            if self._gate_busy:
                logger.info("significance gate busy — skipping this turn")
                return
            self._gate_busy = True
        threading.Thread(
            target=self._gate_worker, args=(session_id, turn_text, searched, speaker),
            daemon=True,
        ).start()

    def _gate_worker(
        self, session_id: str, turn_text: str, searched: bool = False,
        speaker: str = "Michael",
    ) -> None:
        try:
            payload = run_gate(turn_text, self._model, searched=searched, speaker=speaker,
                               lm_base=self._lm_base)
            if not isinstance(payload, dict) or not payload.get("save"):
                return

            ok, err = validate_write(payload)
            if not ok:
                # Retry once with the validation error as a correction hint.
                payload = run_gate(turn_text, self._model, correction=err,
                                   searched=searched, speaker=speaker,
                                   lm_base=self._lm_base)
                if not isinstance(payload, dict) or not payload.get("save"):
                    return
                ok, err = validate_write(payload)
                if not ok:
                    self._log_failure(turn_text, payload, err)
                    return

            # Deterministic backstop: drop non-durable saves (ephemeral state, self/meta entities)
            # even when the model returns save=true. The tightened prompt is the primary defense;
            # this guarantees the noise classes never reach the store.
            reason = reject_reason(payload)
            if reason:
                logger.info(f"significance gate: dropped non-durable save ({reason}): {payload}")
                return

            self._insert(session_id, payload, speaker=speaker)
        except Exception as e:
            logger.error(f"gate worker error: {e}")
        finally:
            with self._gate_lock:
                self._gate_busy = False

    def _insert(self, session_id: str, payload: dict, speaker: str = "Michael") -> None:
        """Write a validated payload using a thread-local connection.

        Facts/preferences/policies UPSERT via explicit ON CONFLICT ... DO UPDATE
        (NOT INSERT OR REPLACE) so the external-content FTS5 stays consistent:
        an UPDATE keeps the rowid and fires the AFTER UPDATE trigger that re-syncs
        fact_fts. REPLACE would delete+insert and orphan FTS rows.
        """
        conn = db.get_connection(self._db_path)
        wrote_fact: dict | None = None
        try:
            ptype = payload["type"]
            if ptype == "fact":
                text = f"{payload['entity']} {payload['attribute']} {payload['value']}"
                # source_speaker comes from the `speaker` arg (voice-ID ground truth),
                # NEVER from the gate payload — the model can't misattribute a write.
                conn.execute(
                    """
                    INSERT INTO fact_memory
                        (id, entity, attribute, value, source_session, source_speaker, embedding)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(entity, attribute) DO UPDATE SET
                        value = excluded.value,
                        embedding = excluded.embedding,
                        source_session = excluded.source_session,
                        source_speaker = excluded.source_speaker,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (_new_id(), payload["entity"], payload["attribute"],
                     payload["value"], session_id, speaker, encode(text)),
                )
                wrote_fact = {
                    "entity": payload["entity"],
                    "attribute": payload["attribute"],
                    "value": payload["value"],
                    "source_speaker": speaker,
                }
            elif ptype == "preference":
                conn.execute(
                    """
                    INSERT INTO preference_memory (key, value, source_session)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        source_session = excluded.source_session
                    """,
                    (payload["key"], payload["value"], session_id),
                )
            elif ptype == "policy":
                conn.execute(
                    """
                    INSERT INTO policy_memory (key, rule, priority, source_session)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        rule = excluded.rule,
                        priority = excluded.priority
                    """,
                    (payload["key"], payload["rule"], int(payload["priority"]), session_id),
                )
            conn.commit()
            logger.info(f"Ib-Lite wrote {ptype}: {payload}")
            if wrote_fact is not None:
                with self._gate_lock:
                    self._last_fact = wrote_fact
        finally:
            conn.close()

    def peek_last_fact(self) -> dict | None:
        """The most recent fact written this session, WITHOUT deleting it.

        For the forget-permission check (Stage 6 Phase 2): main.py looks at the fact's
        source_speaker before deciding whether the person asking is allowed to take it
        back. Lock-guarded like every other _last_fact access.
        """
        with self._gate_lock:
            return dict(self._last_fact) if self._last_fact else None

    def forget_last_fact(self) -> dict | None:
        """Delete the most recent fact written this session (for "Echo, forget that").

        Returns the forgotten fact dict {entity, attribute, value}, or None if
        there is nothing to forget. The plain DELETE fires the fact_fts_delete
        trigger, keeping FTS in sync.
        """
        if not self._available:
            return None
        with self._gate_lock:
            fact = self._last_fact
            self._last_fact = None
        if not fact:
            return None
        try:
            self._conn.execute(
                "DELETE FROM fact_memory WHERE entity = ? AND attribute = ?",
                (fact["entity"], fact["attribute"]),
            )
            self._conn.commit()
            logger.info(f"Ib-Lite forgot fact: {fact}")
            return fact
        except Exception as e:
            logger.error(f"forget_last_fact failed: {e}")
            return None

    def _log_failure(self, turn_text: str, payload: dict, err: str) -> None:
        try:
            with open(_FAILURE_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": _new_id(),
                    "error": err,
                    "payload": payload,
                    "turn": turn_text[:500],
                }) + "\n")
        except Exception as e:
            logger.error(f"could not write failure log: {e}")
        logger.warning(f"discarded invalid memory write: {err}")

    # ── session end (episodic) ───────────────────────────────────────────

    def end_session(self, session_id: str, summary: dict, turn_count: int | None = None) -> bool:
        """Write the episodic row, THEN mark the session ended (gotcha #7 order).

        Maps the existing summarizer schema onto episodic_memory:
            summary_text       -> summary
            topics_discussed   -> key_topics (JSON)
            conversation_mood  -> mood_signal
        Returns True if an episodic row was written.
        """
        if not self._available:
            return False

        summary_text = (summary.get("summary_text") or "").strip()
        topics = summary.get("topics_discussed") or []
        mood = (summary.get("conversation_mood") or "").strip() or None
        episode_id = _new_id()
        written = False

        try:
            if summary_text:
                self._conn.execute(
                    """
                    INSERT INTO episodic_memory
                        (id, session_id, summary, key_topics, mood_signal, turn_count, embedding)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (episode_id, session_id, summary_text, json.dumps(topics),
                     mood, turn_count, encode(summary_text)),
                )
                written = True

            self._conn.execute(
                "UPDATE sessions SET ended_at = CURRENT_TIMESTAMP, turn_count = ?, "
                "mood_signal = ?, episode_id = ? WHERE id = ?",
                (turn_count, mood, episode_id if written else None, session_id),
            )
            self._conn.commit()
        except Exception as e:
            logger.error(f"end_session failed: {e}")
            return False

        return written

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
