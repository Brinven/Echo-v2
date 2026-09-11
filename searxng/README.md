# Echo — SearXNG (web-search backend)

Echo's web search (Stage 5 Part 3) uses **SearXNG**, a keyless, self-hosted metasearch
proxy. Queries reach upstream engines *through* SearXNG, which never identifies Echo or
Michael — the one deliberate, minimized exception to Echo's local-first spine.

## Active backend — the existing `Searxng` host container (port 26)

Echo points at **`http://127.0.0.1:26`** (set in `echo_stage0/echo_search.json`).

This is Michael's pre-existing `Searxng` container (recreated 2026-09-11 on `searxng/searxng:latest`,
was `2026.6.15`), already running before Stage 5 Part 3. **Keep the image fresh** — upstream engines
(DuckDuckGo, Brave, Bing) change their markup/bot checks every few months and a stale image degrades
SILENTLY: the container answers HTTP 200 with zero results (or Bing's first-word-only garbage), so
Echo says "couldn't find it" and it reads as *SearXNG is down* when it is not. Refresh:
`docker pull searxng/searxng:latest`, then re-run the container with the same `-p 26:8080`,
`-v C:/Users/zwolf/searxng:/etc/searxng` and the same `/var/cache/searxng` volume (it is not
compose-managed; `docker inspect Searxng` shows the binds). Diagnose per engine with
`curl "http://127.0.0.1:26/search?q=test&format=json&engines=bing"` and read `unresponsive_engines`. Verified 2026-07-14 — it already meets every PRD §4
requirement:

| Requirement | Status |
|---|---|
| JSON API enabled (`formats: [html, json]`) | ✅ `GET /search?q=...&format=json` → HTTP 200, valid JSON |
| Limiter off (`limiter: false`) | ✅ local programmatic client works, no 403 |
| `public_instance: false` | ✅ bot-detection off for local use |

Quick verify (PowerShell — `curl` is aliased, call the real binary):
```powershell
curl.exe "http://127.0.0.1:26/search?q=test&format=json"
```
Expect JSON with `query`, `results`, `answers`, `suggestions`. A 403 or HTML ⇒ the limiter
turned on or settings aren't mounted (see PRD §4 troubleshooting).

### ⚠ One hardening note (Michael's call, non-blocking)
The existing container is mapped **`0.0.0.0:26`** (LAN-exposed), not localhost-only. Echo's
own traffic stays on loopback (`127.0.0.1:26`), so *Echo* leaks nothing — but other devices
on the LAN can reach this SearXNG. The PRD's spine is localhost-only. To harden, rebind the
existing container's port to `127.0.0.1:26` (a change to *its* run config, so left to Michael).

## Fallback — `docker-compose.yml` (NOT running by default)

`docker-compose.yml` + `config/settings.yml` are a repo-tracked, **localhost-only**,
reproducible recipe to stand up a dedicated Echo SearXNG **if the existing `Searxng`
container ever disappears**. It binds `127.0.0.1:8890` (8888 is taken by a native app; 26 is
the existing container) and ships the JSON-on / limiter-off settings the PRD specifies.

Bring it up only as a replacement (never alongside the existing one):
```
cd searxng && docker compose up -d
curl.exe "http://127.0.0.1:8890/search?q=test&format=json"
```
Then set `searxng_base_url` in `echo_stage0/echo_search.json` to `http://127.0.0.1:8890`.

## Ports on this box (for reference)
- `26`   → existing `Searxng` container (**Echo's active backend**)
- `8888` → a native Windows Python/uvicorn app (unrelated — do not use)
- `8890` → the fallback compose above (only if you bring it up)
