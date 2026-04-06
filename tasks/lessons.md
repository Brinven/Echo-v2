# Echo — Lessons Learned

## 2026-04-05: Windows `localhost` adds ~2s to every HTTP request

**Problem**: LLM TTFT was 2.3s in the pipeline but 0.2s in standalone tests.
Traced to `client.chat.completions.create()` blocking for 2.05s before returning.

**Root cause**: On Windows, `localhost` resolves to both `::1` (IPv6) and
`127.0.0.1` (IPv4). The `httpx` library (used by the `openai` Python client)
tries IPv6 first, which times out after ~2s when the server only listens on
IPv4, then falls back. Standalone tests were fast because httpx's connection
pool reused the successful IPv4 connection between rapid calls. In the real
pipeline, connections expired between calls.

**Fix**: Use `127.0.0.1` instead of `localhost` in all HTTP URLs.

**Rule**: On Windows, ALWAYS use `127.0.0.1` for local service URLs, never
`localhost`. This applies to LM Studio, Kokoro-FastAPI, and any other local
API server.

**Impact**: First-audio dropped from 5.3s to 0.8s (6.5x improvement).
