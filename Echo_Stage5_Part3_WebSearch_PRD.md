# PRD: Echo — Stage 5 Part 3 — Web Search

**Project:** Echo
**Author:** Michael (Axly's Customs) — drafted by CC 2026-07-13
**Date:** 2026-07-13
**Status:** Draft for Michael's review
**Depends on:** Stage 5 Part 2 (Personality Layer) — complete. Stage 3 memory schema
(anticipates `facts_general[source=web_search]`).

---

## 1. Overview

Echo runs local-first: no data leaves the machine. Web search is the **first
deliberate exception** to that spine, so the design minimizes exposure rather than
treating it as free. The backend is **SearXNG, self-hosted in Docker on Michael's
box** — a keyless metasearch proxy that binds to `127.0.0.1` only. Queries reach
upstream engines *through* SearXNG (the upstream never sees Echo or Michael directly),
SearXNG requires no account/API key, and it does not log or track by default.

The mechanism is the **separate-reasoning-call pattern** Part 2 §6 explicitly reserved
for this: a dedicated reasoning call decides whether a turn needs the web and builds
the query; SearXNG returns results; the results are injected as context into Echo's
character pass. **Echo's generation pass sees the answer, not the reasoning chain** —
CoT isolation preserved, so searching never pushes her off-character.

Because a search turn breaks the <3s latency budget, Echo speaks a brief in-character
line ("Let me look that up, Michael.") the instant she decides to search — which
doubles as the transparency cue that she's going online.

---

## 2. Goals

### Must-Have
- SearXNG stood up locally (Docker), JSON API enabled, bound to localhost only,
  verified returning JSON.
- A **provider-abstracted** `search.py` (`SearchProvider` interface, `SearXNGProvider`
  first impl) so the backend can be swapped without touching the pipeline.
- A **search-decision** step: cheap keyword pre-filter → separate reasoning call that
  returns `{search: false}` or `{search: true, query: "..."}`. Reasoning off; never
  raises; never inline with the character pass.
- Results injected as a compact `search_block` into the character pass; Echo answers
  in her voice — synthesizes, never reads URLs or lists aloud.
- Latency cover: an in-character "looking it up" line spoken before the search runs.
- Graceful failure: SearXNG down/slow/empty → Echo says she couldn't find it, in
  character, no crash.
- A `web_search_enabled` toggle (default **on** — Michael wants it) and transparency
  by design (she announces going online).

### Nice-to-Have
- Voice off-switch: "Echo, stay offline" / "go back online" (session-scoped).
- Provenance tag on any web-sourced fact the memory gate chooses to keep.
- A second provider impl (e.g. DuckDuckGo `ddgs`) behind the same interface, as a
  keyless fallback if SearXNG is down.

### Non-Goals
- Cloud search APIs with keys/accounts (Tavily, Brave API, etc.) — rejected in favor
  of the self-hosted proxy. (The interface allows one later if Michael changes his mind.)
- Autonomous multi-step web browsing / agentic research (Camofox-style page crawling).
- Search results shown as text/UI — Echo is voice; results are spoken synthesis only.
- Persisting every web answer to memory — ephemeral facts must not pollute Ib-Lite (§9).

---

## 3. Architecture

```
STT transcript
   │
   ├─ sign-off / forget / max-snark short-circuits (unchanged)
   │
   ▼
[keyword pre-filter]  ──no──►  normal turn (persona + memory → character pass)
   │ maybe
   ▼
[SEARCH-DECISION call]  (separate reasoning call, reasoning_effort="none")
   │ {search:false} ─────►  normal turn
   │ {search:true, query}
   ▼
speak filler  ("Let me look that up, Michael.")   ← covers latency + transparency
   │
   ▼
[SearXNGProvider.search(query)]  → top-K results  (5s timeout, graceful on fail)
   │
   ▼
build search_block  → inject into character pass system prompt
   │
   ▼
llm.stream_sentences(...)  → Echo answers in voice, grounded in results
   │
   ▼
significance gate (unchanged) sees the turn; may keep a durable fact (§9)
```

Integration point: `run_streaming_pipeline` in `main.py`, immediately **after** the
transcript is accepted (main.py:171) and **before** the persona/memory system-prompt
assembly (main.py:178–193). The `search_block` becomes a new optional argument to
`build_system_prompt`, slotted like the existing `memory_block`.

---

## 4. SearXNG Deployment

*Verified against official SearXNG docs (docs.searxng.org), build 2026.7.x. SearXNG is
a **rolling release, no semver** — pin a dated tag for reproducibility.*

**Path:** Docker Desktop (WSL2 backend) + a single `docker-compose.yml`. A native
Windows install is not supported — do not attempt one. No Caddy/Valkey/reverse-proxy
(the heavyweight `searxng-docker` repo) is needed for a localhost-only instance.

**Layout:** `H:\AxlyGitHub_H\Echo\searxng\` → `docker-compose.yml` + `config/settings.yml`.
Plus a `start-searxng.bat` (Axly launcher convention; `docker compose up -d`), mirroring
`start-kokoro.bat`.

**`docker-compose.yml`:**
```yaml
services:
  searxng:
    image: searxng/searxng:latest      # pin a dated tag for reproducibility
    container_name: echo-searxng
    ports:
      - "127.0.0.1:8888:8080"          # localhost-only; 8888 avoids old OpenMemory :8080
    volumes:
      - ./config:/etc/searxng:rw
    environment:
      - SEARXNG_BASE_URL=http://localhost:8888/
    restart: unless-stopped
```

**`config/settings.yml`** (overrides only what Echo needs):
```yaml
use_default_settings: true
server:
  secret_key: "<python -c \"import secrets; print(secrets.token_hex(32))\">"
  limiter: false           # default; allows a local programmatic client (no Valkey)
  public_instance: false   # default; keep bot-detection off for localhost use
search:
  formats:
    - html
    - json                 # enables the JSON API (HTML-only by default)
```

**Critical gotchas (from research — do not repeat these mistakes):**
- **Localhost binding is enforced by the Docker `127.0.0.1:` port prefix, NOT by
  settings.yml.** `server.bind_address`/`server.port` are **no-ops under Docker**
  (Granian binds `0.0.0.0:8080` inside the container). Get the `ports:` mapping right.
- With `limiter: false` there is **no rate limiting** — safe *only* because the port is
  localhost-bound and only Echo talks to it. Never expose it to the LAN.
- **`403 Forbidden` on `format=json`** ⇒ limiter/bot-detection is on OR settings.yml
  wasn't mounted. First thing to check in troubleshooting.

**Verify (PowerShell — `curl` is aliased, call the real binary):**
```powershell
curl.exe "http://127.0.0.1:8888/search?q=test&format=json"
```
Expect JSON with `query`, `results`, `answers`, `suggestions`. HTML or 403 ⇒ recheck above.

---

## 5. Search Module (`echo_stage0/search.py`)

Provider-abstracted so the backend is swappable (Non-Goal exception clause).

```python
@dataclass
class SearchResult:
    title: str
    url: str
    content: str        # snippet; may be empty — defensive .get()
    engine: str
    score: float

class SearchProvider(ABC):
    @abstractmethod
    def search(self, query: str, *, top_k: int = 5) -> list[SearchResult]: ...
    @abstractmethod
    def healthy(self) -> bool: ...

class SearXNGProvider(SearchProvider):
    # GET {base_url}/search?q=...&format=json&categories=general&language=en&pageno=1
    # optional &engines=duckduckgo,brave for stability
    # timeout ~5s; parse results[] with .get() (heterogeneous by category);
    # rely only on url/title/content/engine/score; sort is already by score desc.
    ...
```

**Parsing notes (from research):**
- Use only `url`, `title`, `content`, `engine`, `score`. Treat `content`,
  `publishedDate`, `thumbnail` as possibly-missing.
- `number_of_results` is **unreliable** across aggregated engines — count `results`
  locally, don't page on it.
- Check `unresponsive_engines` in the response; pick a stable engine subset
  (`engines=duckduckgo,brave`) to reduce empty first-runs.
- `answers[]` items may be strings **or** objects (`{answer, url, engine}`) depending on
  build — handle both if consumed.

---

## 6. Search-Decision Call

Mirrors `ib_lite/significance.py:run_gate` — same reliability shape.

**Stage A — keyword pre-filter (cheap, protects latency):** skip the LLM decision on
obviously-conversational turns. Trigger the decision call only when the transcript
carries lookup signals: interrogatives + freshness/factual markers ("latest", "current",
"today", "right now", "news", "look up", "search", "who is", "when did", "how much",
"price", "weather", "score", "what is the", a year/date, an unfamiliar proper noun).
Recall-biased: when unsure, fall through to Stage B (the decision call is cheap; a missed
search is worse than a spent ~1s).

> Tradeoff (flagged): the pre-filter can miss a needed search phrased without obvious
> markers. Acceptable v1 cost; tune the keyword set from real logs. Alternative —
> always run Stage B — is simpler but taxes **every** turn ~1s. Recommend the pre-filter.

**Stage B — decision call (hot path, must finish before answering):**
```
System: You decide whether answering the user needs fresh or external web
information, and if so write the best search query. Respond ONLY with JSON.
No web needed:            {"search": false}
Web needed:               {"search": true, "query": "<concise search query>"}
Rules: prefer false for opinions, feelings, personal topics, and things a companion
already knows. Prefer true for current events, prices, weather, facts you're unsure
of, or anything time-sensitive.
```
`temperature=0.1`, `max_tokens≈60`, **`reasoning_effort="none"`**, best-effort JSON
parse, `{"search": false}` on any failure (never raises). Empty-content guard (same
Gemma-QAT thinking-model gotcha as the gate).

---

## 7. Result Injection & Character Pass

Format the top-K results into a compact `search_block`, injected into the character
pass system prompt (new optional arg to `build_system_prompt`, slotted beside
`memory_block`):

```
[web results — you looked these up just now]
1. <title> — <snippet>  (source: <engine>)
2. ...
Answer Michael in your own voice. Synthesize what matters; do not read URLs, do not
list results, do not say "according to". You looked it up — just tell him.
```

Echo's pass (`llm.stream_sentences`) streams the spoken answer grounded in the block.
CoT isolation holds: the query construction happened in Stage B; the character pass
only sees results. The Part 2 sampler + `reasoning_effort="none"` are unchanged.

---

## 8. Latency Handling

A search turn = decision call + SearXNG round-trip + character pass — it **will** exceed
<3s. Cover it:

- The instant Stage B returns `search: true`, synthesize + enqueue a short in-character
  filler ("Let me look that up, Michael." / "One moment.") and transition to SPEAKING.
- Run the search + answer behind the filler. The filler is also the **transparency cue**
  that Echo is going online (satisfies the privacy-announce principle).
- Filler lines live in config (a small rotating set, in Echo's voice, snark-neutral).
- Log the decision/search/answer timings separately (§10) so search-turn latency is
  measured but never conflated with the normal <3s budget (search turns are exempt).

---

## 9. Memory Integration

Stage 3's schema already reserved `facts_general[source=web_search]`. In the Ib-Lite
world, the significance gate already sees every turn (`ib.write_memory`, main.py:237),
including the searched answer — **no special write path in v1.**

- **Default:** let the gate decide, as it does now. It's conservative and typically
  ignores ephemeral answers ("weather today"), while durable facts ("X is the capital of
  Y") may be kept — which is correct.
- **Risk:** the gate could occasionally persist a transient web fact. Mitigation
  (nice-to-have): tag web-influenced turns and either (a) attach `source=web_search`
  provenance to any fact kept, or (b) exclude flagged turns from gating if pollution
  shows up in practice. Start simple; revisit only if real logs show junk facts.

---

## 10. Logging (new JSONL fields)

Added to `logger.log_run`: `web_search_triggered` (bool), `search_prefilter_hit` (bool),
`search_decision_ms`, `search_query`, `search_provider`, `search_latency_ms`,
`results_count`, `search_engines_used`. Search turns are marked so they're excluded from
the <3s PASS/FAIL metric.

---

## 11. Config (`echo_search.json` or `config.json` keys)

```json
{
  "web_search_enabled": true,
  "provider": "searxng",
  "searxng_base_url": "http://127.0.0.1:8888",
  "categories": "general",
  "engines": "duckduckgo,brave",
  "top_k": 5,
  "timeout_s": 5,
  "filler_lines": ["Let me look that up, Michael.", "One moment."]
}
```
Loaded once (fail-soft to defaults), mirroring `echo_sampler.json`.

---

## 12. MVP Milestones

| # | Milestone | Deliverable | Done When |
|---|-----------|-------------|-----------|
| 1 | SearXNG up | `searxng/` compose + settings.yml + `start-searxng.bat`; JSON verified | `curl.exe .../search?q=test&format=json` returns JSON, localhost-bound |
| 2 | Search module | `search.py`: `SearchProvider`, `SearXNGProvider`, `SearchResult`, `healthy()` | Live query returns parsed results; defensive on missing fields/empty/timeout |
| 3 | Decision call | Keyword pre-filter + Stage B reasoning call (reasoning off, JSON, never raises) | Conversational turns → no search; "what's the weather in Houston" → `{search:true, query}` |
| 4 | Result injection | `search_block` + `build_system_prompt` arg; character-pass instruction | Echo answers from results in-voice; no URLs/lists/"according to" spoken |
| 5 | Latency filler | In-character filler spoken on search decision; timings logged separately | Filler plays immediately; search-turn latency excluded from <3s metric |
| 6 | Toggle + transparency | `web_search_enabled`; graceful SearXNG-down fallback; (opt) voice off-switch | Toggle off ⇒ never searches; SearXNG stopped ⇒ Echo declines in character, no crash |
| 7 | Logging | New JSONL fields wired | A search turn logs query, provider, latencies, count |
| 8 | End-to-end | Live: current-info Q ⇒ search+answer; personal Q ⇒ no search | Correct routing on a 10-prompt mixed sweep; answers stay in Echo's voice |
| 9 | Memory (NTH) | Provenance/exclusion only if logs show junk facts | Decision recorded; no memory pollution observed |

---

## 13. Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Search turn latency feels slow | High (inherent) | In-character filler covers it; search turns exempt from <3s budget; keep top_k small |
| Pre-filter misses a needed search | Medium | Recall-biased keywords; cheap Stage B; tune from logs; option to always-run Stage B |
| Pre-filter over-fires ⇒ needless ~1s decisions | Low-Med | Markers are lookup-specific; measure hit-rate in logs and tighten |
| SearXNG down/slow/empty first-run | Medium | `healthy()` check at startup (warn, don't block — search is optional); 5s timeout; stable engine subset; graceful in-character decline |
| Search results wrong/junk | Medium | Echo synthesizes + hedges naturally (in character); never over-asserts; pick stable engines |
| Reasoning leaks into character pass | Low | Decision is a separate call; character pass sees results only (Part 2 §6) |
| Memory pollution from ephemeral web facts | Medium | Gate is conservative; provenance/exclusion as NTH if logs show junk (§9) |
| Privacy carve-out broader than intended | Low | Self-hosted keyless proxy, localhost-only, no logging; Echo announces going online; toggle + off-switch |
| SearXNG rolling-release drift breaks settings | Low-Med | Pin a dated image tag; settings.yml minimal (`use_default_settings: true`) |
| `403`/HTML instead of JSON | Low | Documented cause (limiter on / settings not mounted) + fix in §4 |
| Decision call empty content (thinking model) | Low | `reasoning_effort="none"` + empty-content guard, same as the gate |

---

## 14. Memory

**Hindsight bank:** `echo`
**Tags:** `stage5`, `web-search`, `searxng`, `cot-isolation`
**axly-infra:** the SearXNG-on-Windows deployment recipe (localhost port-prefix gotcha,
JSON-enable, limiter-off) is a reusable infra fact — retain there too.
**Ib:** retain the decision to go self-hosted SearXNG (privacy-first) over a cloud
search API as a project decision.

---

## Axly's Customs Standards
- Local-first; web search is the one deliberate, minimized exception via a self-hosted,
  keyless, localhost-only proxy — no cloud search API, no key, no account.
- Inference-only; all LLM calls via LM Studio at `127.0.0.1`.
- CoT isolation preserved — decision/query in a separate reasoning call; character pass
  sees only results.
- Provider-abstracted so the backend is never load-bearing on one vendor.
- `start-searxng.bat` ships with the repo (Windows launcher convention).
