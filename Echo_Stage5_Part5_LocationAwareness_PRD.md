# PRD: Echo — Stage 5 Part 5 — Location / Context Awareness

**Project:** Echo
**Author:** Michael (Axly's Customs) — drafted by CC 2026-07-14
**Date:** 2026-07-14
**Status:** Draft for Michael's review
**Depends on:** Stage 5 Part 2 (Personality Layer) — complete.

---

## 1. Overview

Echo should know *where she is* and let it shape how she talks. On Michael's home
network she is **in the house** (his desk, downtime); off it she is **in the Jeep**
(driving companion mode). The point is naturalness: asking about oil level or fuel
range while sitting on a desk is odd; on a drive it's exactly right.

This is a **persona-context** feature, not a telemetry feature. It adds a *location
context block* to the system prompt — the same mechanism the snark level and the mood
opener already use — plus a small local network probe to resolve the location and a
voice override to force it. No OBD-II, no GPS, no hardware sensors: location is the
**flag** those future features will gate on, delivered first and on its own.

It is fully local-first: the probe reads Michael's own network (default-gateway MAC,
a known-host reachability check). Nothing leaves the machine — consistent with Echo's
spine (unlike web search, this needs no exception at all).

### Reality of the current deployment
Echo runs on a **stationary desktop**, so auto-detection will always resolve **home**.
The immediate value is therefore twofold: (1) Echo stops doing Jeep-companion things at
the desk *today* (more natural now), and (2) a **voice override** ("Echo, we're in the
Jeep") lets Michael drive-test companion mode without moving. True network auto-switch
becomes load-bearing when there is Jeep hardware to run on.

---

## 2. Goals

### Must-Have
- A local `location.py` that resolves `home` / `jeep` / `unknown` from a configured
  home-network fingerprint, fail-soft, with a test seam (mirrors `daily_state.py`).
- A `LOCATION_CONTEXTS` block in `persona.py`, injected every turn via
  `build_system_prompt`, part of the never-trimmed persona region.
- Session state (`session.location`) resolved once at session start (like daily snark
  and the mood opener).
- Voice override — "Echo, we're in the Jeep" / "Echo, we're home" — session-scoped,
  handled as a fast-path (not gated, does not advance the exchange counter), exactly
  like Maximum Snark Mode.
- `home` context suppresses unsolicited Jeep telemetry talk; `jeep` context turns the
  drive/route/fuel/Jeep-protectiveness on.

### Nice-to-Have
- Periodic re-check (every N minutes) so a mid-session network change (engine off →
  hotspot drops) flips context. Relevant to Jeep hardware; off by default on the desktop.
- Location-tagging of episodic memories (jeep events vs home) — future; noted only.
- WiFi-SSID signal for the Jeep's hotspot (the signal that matters once the Jeep unit
  runs WiFi rather than ethernet).

### Non-Goals
- OBD-II / vehicle telemetry, GPS, or any real sensor integration (future stages).
- Continuous location tracking or history/logging beyond the current session's flag.
- Multi-location modeling beyond home/jeep/unknown (no "office", "friend's house", etc.
  in v1 — see §4 assumption).
- Any cloud/geolocation API — detection is local network only.

---

## 3. Architecture (mirrors the snark pattern)

Location reuses the exact skeleton snark already proves:

```
Snark:     daily_state.py       → session.daily_snark → SNARK_CONTEXTS block   → main.py (resolve@start + S-key/"maximum snark" override)
Location:  location.py          → session.location    → LOCATION_CONTEXTS block → main.py (resolve@start + "we're in the Jeep" override)
```

**System-prompt assembly** (extends Part 2 §4). Location is identity-level context, so
it sits right after the persona block, alongside the snark context and mood opener:

```
PERSONA BLOCK (+ snark context)
MOOD OPENER            (exchange 1 only)
LOCATION CONTEXT       (every turn)          ← new
CORE MEMORY / POLICY / PREFS   (Ib-Lite)
RETRIEVED MEMORIES     (per turn)
ANTI-DRIFT ANCHOR      (every 8 exchanges)
```

`build_system_prompt` gains a `location: str = ""` argument; the block is looked up
from `LOCATION_CONTEXTS` and inserted. `unknown` → empty string (no nudge; neutral).
The block is small and never trimmed (persona region).

---

## 4. Detection (`echo_stage0/location.py`)

Local network fingerprint, resolved at session start. **No new dependencies** — Windows
-native probes via `subprocess` (PowerShell `Get-NetRoute` + `Get-NetNeighbor`, with
`route print` / `arp -a` as fallback). `psutil` noted as an optional cleaner alternative
if Michael ever wants it flagged and added.

### Signals (in priority order)
1. **Default-gateway MAC** — get the IPv4 default gateway, resolve its MAC, compare to
   configured `home_gateway_mac`. Stable per-router and specific; the right primary
   signal for the ethernet desktop (no WiFi SSID to read).
2. **Known-host reachable** (backup) — a quick reachability check to a configured home
   device (`home_known_host`, e.g. the router or a NAS). Confirms "home" if the MAC read
   fails (VPN/adapter quirks).
3. **WiFi SSID** (future) — for the Jeep's hotspot, once the Jeep unit runs WiFi.

### Resolution logic (v1)
```
if detection disabled            → "unknown"   (neutral)
elif gateway MAC == home_gateway_mac
     or home_known_host reachable → "home"
elif probe succeeded but no match → "jeep"
else (probe error / no network)  → "unknown"   (fail-soft → neutral, NOT jeep)
```

> **v1 assumption (Michael's model):** *on the home network = house, off it = Jeep.*
> This is exact for the PoC because the **only** non-home deployment is the Jeep, and on
> the stationary desktop the "else → jeep" branch never fires. The honest edge: at some
> *other* network (a friend's house), "no match → jeep" would misfire. Mitigations:
> the **voice override** corrects any session instantly, and the per-deployment
> refinement is to give the Jeep unit its *own* fingerprint (SSID/host) so `jeep` is
> matched positively rather than inferred by elimination. Flagged, not built, in v1.

`unknown` deliberately fails to **neutral/home behavior**, never Jeep — because doing
Jeep-telemetry talk when location is uncertain is precisely the awkwardness this feature
removes.

### Timing & fail-soft
- Resolved **once at session start** (`main.py`, beside `get_daily_snark_level()`), with
  a hard probe timeout (~500ms) so a slow `arp`/ping never stalls startup.
- Any error → `unknown` → logged, no crash. Detection is best-effort; the override is
  always available.

---

## 5. Location Context Strings (`persona.py` — Michael approves, persona content)

Draft — treat like the persona block (Part 2 §2f): CC drafts, Michael signs off.

```python
LOCATION_CONTEXTS = {
    "home": (
        "You are with Michael at home — his desk, the house. This is downtime, not a "
        "drive. Don't raise the Jeep's fuel, oil, route, or anything vehicular unless "
        "Michael brings it up first. Home conversation."
    ),
    "jeep": (
        "You are in the Jeep with Michael. Driving companion mode: the route, fuel, the "
        "Jeep's condition, and the road are all fair game, and your protectiveness of "
        "the Jeep is warranted here. Read the drive."
    ),
    "unknown": "",   # neutral — no location nudge; relaxed home-style conversation
}
```

This makes an existing persona trait location-aware: Part 2's *"protective of Michael
and the Jeep"* now knows *when the Jeep half is live.*

---

## 6. Voice Override (`session.py` + `main.py`)

Exactly the Maximum Snark Mode pattern (Part 2; `is_max_snark`):

- `session.py`: `is_location_override(transcript) -> str | None` — parses:
  - → `"jeep"`: "we're in the jeep", "we're in the jeep now", "get in the jeep", "jeep mode"
  - → `"home"`: "we're home", "we're at home", "we're in the house", "back home", "home mode"
  - "Echo" prefix expected (like sign-off/max-snark); partial/variant tolerant.
- `main.py`: handled before the normal turn (like forget / max-snark): print the turn,
  `add_user_turn`, set `session.location`, speak a short in-character confirm, **not
  gated, does not advance the exchange counter**. Session-scoped; next launch re-resolves
  from the network.
- Confirm lines (in Echo's voice, snark-neutral), e.g. jeep → "Buckle up, Michael." ;
  home → "Home it is, Michael."

---

## 7. Config (`config.json` keys, or `echo_location.json`)

```json
{
  "location_detection_enabled": true,
  "home_gateway_mac": "AA:BB:CC:DD:EE:FF",
  "home_known_host": "192.168.1.1",
  "recheck_interval_min": 0
}
```

Fail-soft to defaults (detection off / `unknown`) if missing or malformed, mirroring
`echo_sampler.json`. **Michael fills in his home gateway MAC once** — find it with:
```powershell
Get-NetRoute -DestinationPrefix 0.0.0.0/0 | Select-Object NextHop     # gateway IP
Get-NetNeighbor -IPAddress <that IP>       | Select-Object LinkLayerAddress   # its MAC
```

---

## 8. Logging

Add `location` to `logger.log_run` (the resolved/active location for the turn) so
sessions record context. No separate location log, no tracking history (Non-Goal).

---

## 9. MVP Milestones

| # | Milestone | Deliverable | Done When |
|---|-----------|-------------|-----------|
| 1 | Detection module | `location.py::resolve_location` — gateway-MAC + known-host, ~500ms cap, fail-soft, test seam | Returns `home` on Michael's desk; `unknown` on probe error; forced values via seam |
| 2 | Config | location keys + fail-soft load; MAC-lookup one-liner documented | Missing/bad config → detection off → `unknown`, no crash |
| 3 | Context block | `LOCATION_CONTEXTS` + `location` arg in `build_system_prompt`; never trimmed | Correct block injected for home/jeep; `unknown` injects nothing; order matches §3 |
| 4 | Session wiring | `session.location` resolved at start; `is_location_override()` parser | Location set once at start; override phrases parse to jeep/home, others don't |
| 5 | Voice override | `main.py` fast-path: set location, confirm aloud, not gated, no counter advance | "Echo, we're in the Jeep" flips context mid-session; counter/anchor unaffected |
| 6 | Behavior proof | Live: home suppresses unsolicited Jeep talk; jeep turns it on | At `home` she doesn't volunteer fuel/oil; after override she treats the drive as live |
| 7 | Logging | `location` field in JSONL | Each turn logs the active location |
| 8 | Re-check (NTH) | Optional periodic re-probe (`recheck_interval_min`) | Off by default; when on, a simulated network change flips context |

---

## 10. Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| "Not-home → jeep" misfires at a non-home network | Low (desktop never hits it) | Voice override; per-deployment jeep fingerprint later (§4); `unknown` fails to neutral, not jeep |
| Gateway-MAC probe flaky (VPN/Tailscale/multi-adapter) | Medium | Known-host backup; fail-soft to `unknown`/neutral; log the probe; override always available |
| Probe stalls session start | Low | Hard ~500ms timeout; resolve fail-soft; never blocks the loop |
| Location block clashes with snark/mood tone | Low | Additive context, not contradictory; verified in assembly test (§3 order) |
| Home context over-suppresses (she won't discuss the Jeep even when asked) | Low-Med | Block says *don't raise it unsolicited* — explicitly allows Jeep talk when Michael brings it up |
| Detection feels like surveillance | Low | Purely local network read, session-scoped flag, no history/tracking (Non-Goals); nothing leaves the box |
| New dependency creep for network probe | Low | Stdlib `subprocess` + Windows-native cmdlets only; `psutil` flagged, not added |

---

## 11. Test / Verification

- **Offline (no model, no network):** `resolve_location` via test seam — match→home,
  no-match→jeep, error→unknown; `is_location_override` parsing (jeep/home/none);
  `build_system_prompt` injects the right block, `unknown`→nothing, order correct, block
  never trimmed even over-budget.
- **Live (LM Studio up, on the desk):** session resolves `home`; a home-context prompt
  ("how's it going") gets no unsolicited Jeep telemetry; "Echo, we're in the Jeep" flips
  the confirm + subsequent turns treat the drive as live; "Echo, we're home" flips back.

---

## 12. Memory

**Hindsight bank:** `echo`
**Tags:** `stage5`, `location-awareness`, `context`, `persona`
**axly-infra:** the Windows default-gateway-MAC detection recipe (`Get-NetRoute` +
`Get-NetNeighbor`) is a reusable infra snippet — retain there.
**Ib:** retain that Echo gained home/Jeep context awareness (the naturalness feature
Michael carried over from an earlier Echo iteration).

---

## Axly's Customs Standards
- Local-first — network probe reads only Michael's own network; nothing leaves the box.
- Inference-only; identity/persona content (`LOCATION_CONTEXTS`) is Michael's to approve.
- Reuses the snark/mood context-block pattern — no new architecture, no new runtime deps.
- Location is the **flag** future telemetry (OBD/GPS) gates on — delivered standalone first.
