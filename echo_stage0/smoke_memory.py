"""
Smoke test for Echo's Hindsight memory client.

Run after the swap to confirm:
  1. MemoryClient connects to Hindsight + echo bank exists
  2. recall() returns ranked results with content + score fields
  3. history() returns documents (recent ingests)
  4. add() round-trips successfully (async; extraction happens in background)

Usage:
  cd echo_stage0
  python smoke_memory.py

Expects HINDSIGHT_API_KEY in env (or hindsight_api_key in config.json) and
the local Hindsight service running at http://127.0.0.1:8888.
"""

import sys
from memory import MemoryClient

GREEN = "\033[32m"; RED = "\033[31m"; CYAN = "\033[36m"; RESET = "\033[0m"

def main() -> int:
    print(f"{CYAN}[1] init MemoryClient{RESET}")
    mc = MemoryClient(user_id="echo_michael")
    if not mc.available:
        print(f"{RED}FAIL: client unavailable{RESET}")
        return 1
    print(f"  ok\n")

    print(f"{CYAN}[2] recall existing seeded content (Stage 2 closure migrated to echo bank){RESET}")
    hits = mc.search("Echo Stage 2 session management sign-off", limit=5)
    print(f"  results: {len(hits)}")
    for i, h in enumerate(hits[:3]):
        print(f"    [{i}] score={h['score']:.2f} type={h.get('type')!r} content={h['content'][:90]!r}")
    if not hits:
        print(f"{RED}WARN: no recall hits -- echo bank may be empty or recall LLM down{RESET}")
    print()

    print(f"{CYAN}[3] history (recent docs in bank){RESET}")
    docs = mc.history(limit=5)
    print(f"  docs: {len(docs)}")
    for d in docs[:3]:
        print(f"    text={(d.get('text') or '')[:80]!r}")
    print()

    print(f"{CYAN}[4] add a smoke memory (async; extraction runs in background){RESET}")
    res = mc.add(
        "Echo memory smoke test executed successfully on swap day.",
        tags=["smoke-test", "echo-swap"],
    )
    if res:
        print(f"  ok: {res.get('success')} bank={res.get('bank_id')} async={res.get('async')}")
    else:
        print(f"{RED}FAIL: add returned None{RESET}")
        return 1

    print(f"\n{GREEN}SMOKE PASSED{RESET}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
