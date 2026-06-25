"""
Ib-Lite CLI — inspect and curate Echo's memory from the terminal.

The significance gate is imprecise, so this is how you SEE what Echo saved and
fix it: list/dump tables, search the way Echo does, delete bad rows, seed Core
and Policy, and dial a fact's confidence up or down (low confidence demotes/hides
it in retrieval without deleting it).

Usage (run from echo_stage0/):
  python ib_lite_cli.py list                      # counts + recent rows
  python ib_lite_cli.py facts                     # dump fact_memory
  python ib_lite_cli.py prefs | core | policy | episodes | sessions
  python ib_lite_cli.py search "the jeep"         # hybrid search, with scores
  python ib_lite_cli.py core <key> "<content>"    # seed/update a core memory
  python ib_lite_cli.py policy <key> "<rule>" <priority>
  python ib_lite_cli.py pref <key> "<value>"
  python ib_lite_cli.py confidence <fact_id> <0.0-1.0>
  python ib_lite_cli.py rm <table> <id-or-key>    # table: fact|pref|core|policy|episodic|session
  python ib_lite_cli.py --db other.db <command>   # point at a different DB
"""

import argparse
import sys

from ib_lite import db
from ib_lite.retrieval import fact_search, episodic_search

# CLI table name -> (real table, key column)
TABLES = {
    "core": ("core_memory", "key"),
    "policy": ("policy_memory", "key"),
    "pref": ("preference_memory", "key"),
    "preference": ("preference_memory", "key"),
    "fact": ("fact_memory", "id"),
    "episodic": ("episodic_memory", "id"),
    "episode": ("episodic_memory", "id"),
    "session": ("sessions", "id"),
}

CYAN = "\033[36m"; DIM = "\033[2m"; YELLOW = "\033[33m"; GREEN = "\033[32m"; RESET = "\033[0m"


def _rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _print_table(title, rows, cols):
    print(f"\n{CYAN}{title}{RESET} ({len(rows)})")
    if not rows:
        print(f"  {DIM}(empty){RESET}")
        return
    for r in rows:
        line = "  " + "  ".join(f"{c}={r.get(c)!r}" for c in cols if c in r)
        print(line)


def cmd_list(conn, _args):
    counts = {
        "core_memory": "core", "policy_memory": "policy", "preference_memory": "pref",
        "fact_memory": "fact", "episodic_memory": "episodic", "sessions": "session",
    }
    dbfile = conn.execute("PRAGMA database_list").fetchone()["file"]
    print(f"{CYAN}Ib-Lite memory ({dbfile}){RESET}")
    for tbl, label in counts.items():
        n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"  {label:10} {n}")
    _print_table("Recent facts", _rows(conn,
        "SELECT entity,attribute,value,confidence FROM fact_memory ORDER BY updated_at DESC LIMIT 10"),
        ["entity", "attribute", "value", "confidence"])
    _print_table("Recent episodes", _rows(conn,
        "SELECT session_id,mood_signal,summary FROM episodic_memory ORDER BY created_at DESC LIMIT 5"),
        ["session_id", "mood_signal", "summary"])


def cmd_facts(conn, _args):
    _print_table("fact_memory", _rows(conn,
        "SELECT id,entity,attribute,value,confidence,updated_at FROM fact_memory ORDER BY updated_at DESC"),
        ["id", "entity", "attribute", "value", "confidence", "updated_at"])


def cmd_prefs(conn, args):
    if args.key is not None and args.value is not None:
        conn.execute(
            "INSERT INTO preference_memory (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (args.key, args.value))
        conn.commit()
        print(f"{GREEN}set pref {args.key!r} = {args.value!r}{RESET}")
    _print_table("preference_memory", _rows(conn,
        "SELECT key,value,confidence,updated_at FROM preference_memory ORDER BY updated_at DESC"),
        ["key", "value", "confidence", "updated_at"])


def cmd_core(conn, args):
    if args.key is not None and args.content is not None:
        conn.execute(
            "INSERT INTO core_memory (key, content) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET content = excluded.content, updated_at = CURRENT_TIMESTAMP",
            (args.key, args.content))
        conn.commit()
        print(f"{GREEN}set core {args.key!r}{RESET}")
    _print_table("core_memory", _rows(conn,
        "SELECT key,content,updated_at FROM core_memory ORDER BY rowid"),
        ["key", "content", "updated_at"])


def cmd_policy(conn, args):
    if args.key is not None and args.rule is not None and args.priority is not None:
        conn.execute(
            "INSERT INTO policy_memory (key, rule, priority) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET rule = excluded.rule, priority = excluded.priority",
            (args.key, args.rule, args.priority))
        conn.commit()
        print(f"{GREEN}set policy {args.key!r} (priority {args.priority}){RESET}")
    _print_table("policy_memory", _rows(conn,
        "SELECT key,rule,priority,active FROM policy_memory ORDER BY priority DESC, rowid"),
        ["key", "rule", "priority", "active"])


def cmd_episodes(conn, _args):
    _print_table("episodic_memory", _rows(conn,
        "SELECT id,session_id,mood_signal,turn_count,key_topics,summary,created_at "
        "FROM episodic_memory ORDER BY created_at DESC"),
        ["id", "session_id", "mood_signal", "turn_count", "key_topics", "summary", "created_at"])


def cmd_sessions(conn, _args):
    _print_table("sessions", _rows(conn,
        "SELECT id,started_at,ended_at,turn_count,mood_signal FROM sessions ORDER BY started_at DESC LIMIT 20"),
        ["id", "started_at", "ended_at", "turn_count", "mood_signal"])


def cmd_search(conn, args):
    facts = fact_search(conn, args.query)
    eps = episodic_search(conn, args.query)
    print(f"\n{CYAN}facts for {args.query!r}{RESET} ({len(facts)})")
    for f in facts:
        print(f"  [{f['score']}] {f['entity']} — {f['attribute']}: {f['value']}  "
              f"{DIM}(conf {f.get('confidence')}){RESET}")
    print(f"\n{CYAN}episodes for {args.query!r}{RESET} ({len(eps)})")
    for e in eps:
        print(f"  [{e['score']}] {e['summary']}")


def cmd_confidence(conn, args):
    if not (0.0 <= args.value <= 1.0):
        print(f"{YELLOW}confidence must be 0.0–1.0{RESET}")
        return
    cur = conn.execute("UPDATE fact_memory SET confidence = ? WHERE id = ?", (args.value, args.fact_id))
    conn.commit()
    if cur.rowcount:
        print(f"{GREEN}set confidence of {args.fact_id} to {args.value}{RESET}")
    else:
        print(f"{YELLOW}no fact with id {args.fact_id}{RESET}")


def cmd_rm(conn, args):
    entry = TABLES.get(args.table)
    if not entry:
        print(f"{YELLOW}unknown table {args.table!r}. one of: {sorted(set(TABLES))}{RESET}")
        return
    table, keycol = entry
    cur = conn.execute(f"DELETE FROM {table} WHERE {keycol} = ?", (args.ident,))
    conn.commit()
    print(f"{GREEN}deleted {cur.rowcount} row(s) from {table} where {keycol}={args.ident!r}{RESET}"
          if cur.rowcount else f"{YELLOW}no match in {table} for {keycol}={args.ident!r}{RESET}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Inspect and curate Ib-Lite memory.")
    p.add_argument("--db", default=None, help="path to a DB file (default: echo_stage0/echo.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list").set_defaults(func=cmd_list)
    sub.add_parser("facts").set_defaults(func=cmd_facts)
    sub.add_parser("episodes").set_defaults(func=cmd_episodes)
    sub.add_parser("sessions").set_defaults(func=cmd_sessions)

    sp = sub.add_parser("prefs"); sp.add_argument("key", nargs="?"); sp.add_argument("value", nargs="?")
    sp.set_defaults(func=cmd_prefs)
    sc = sub.add_parser("core"); sc.add_argument("key", nargs="?"); sc.add_argument("content", nargs="?")
    sc.set_defaults(func=cmd_core)
    spo = sub.add_parser("policy"); spo.add_argument("key", nargs="?"); spo.add_argument("rule", nargs="?")
    spo.add_argument("priority", nargs="?", type=int); spo.set_defaults(func=cmd_policy)

    ss = sub.add_parser("search"); ss.add_argument("query"); ss.set_defaults(func=cmd_search)
    cf = sub.add_parser("confidence"); cf.add_argument("fact_id"); cf.add_argument("value", type=float)
    cf.set_defaults(func=cmd_confidence)
    rm = sub.add_parser("rm"); rm.add_argument("table"); rm.add_argument("ident")
    rm.set_defaults(func=cmd_rm)
    return p


def main() -> int:
    args = build_parser().parse_args()
    db_path = None
    if args.db:
        from pathlib import Path
        db_path = Path(args.db)
    conn = db.get_connection(db_path)
    try:
        args.func(conn, args)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
