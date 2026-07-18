# Echo — Future Possibilities

A looking-ahead session with Michael, 2026-07-18 (late night). Nothing here is planned or
committed — this is the noodle file. Each section is a memory-jogger; ask CC for the full
reasoning any time.

---

## 1. MCP servers (Camofox first)

**Yes, she can.** MCP servers connect to *host applications*, not models — so "Echo uses MCP"
means her pipeline becomes an MCP client: connect to the server, offer the tools to the 12B,
execute its tool calls, feed results back. She already does this pattern by hand — the web-search
decider and the significance gate ARE single-tool loops (separate JSON reasoning call → pipeline
executes → results injected, CoT stays isolated).

- **Two flavors:** house-style one-capability gates (fast, fits VOICE turns — one round-trip
  behind a filler line) vs. a real agentic tool loop (powerful, slower — belongs on the CHAT
  lane, which was built partly as this substrate).
- **Camofox is the ideal first candidate:** already running locally, read-only, upgrades her
  from SearXNG snippets to actually reading pages. Connect over localhost — no new exposure.
- Small local models chain tools wobblier than they emit one JSON — prove one tool at a time.
- Anything with real-world WRITE access (email etc.) is a different guardrail tier.

## 2. Meshtastic (the radios + Jeep antennas)

**The most on-brand integration possible** — more local-first than Echo herself (no cloud, no
cell, just owned radio waves).

- **Paths:** (a) radio on USB at the PC via the official `meshtastic` Python lib (prove-it
  afternoon); (b) **Pi 4 as a gateway node** — radio + Pi + local MQTT (mosquitto), Pi joins the
  tailnet, Echo subscribes like any local service. Pi can live where the antenna wants to be.
  (c) Jeep endgame: a radio riding with the future Mac-Mini-in-Jeep = comms past cell coverage.
- **Capabilities:** send ("Echo, message Hillary on the mesh"), receive (**her first
  asynchronous input** — the world speaking first; the park-slot machinery already fits),
  node GPS positions ("where's Hillary?" with no internet), telemetry sensors → the dashboard's
  📈 Sensors tile.
- **Constraint:** LoRa is tiny (~200 bytes, seconds-to-minutes latency). She needs a telegram
  register for outbound mesh — terse on the wire, natural when spoken.

## 3. Home Assistant (on the Proxmox cluster / wooden lectern)

**The easiest yes.** Local REST + WebSocket API with long-lived tokens — the exact shape of
every service she already talks to. She IS the assistant; she just needs the lever API
(`POST /api/services/light/turn_on`, `GET /api/states`). HA also ships an official MCP server,
so it fits either integration door from §1.

- **Permissions ride the existing speaker-ID machinery:** known speakers → lights; Michael only →
  locks/garage; unknown → nothing; Kairos structurally can't (ignored-voice drop). Three-line
  policy on live infrastructure.
- **The convergence hub:** HAOS in a VM (light: 2 cores/4GB); Mosquitto add-on ingests
  Meshtastic MQTT (mesh sensors become HA entities → ONE API for Echo); Frigate for the future
  camera pipeline; both dashboard placeholder tiles (📷 Cameras, 📈 Sensors) get tenants
  through this single door.
- **Caveat:** HA itself is a hobby-sized rabbit hole (devices, naming, automations). The Echo
  side is thin; the HA side eats weekends. Cluster = ~30-45 min to resurrect.

## 4. Heartbeats (wake-and-act)

**Mechanically easy; the hard part is manners.**

- **Signal tiers:** (1) timer/cron; (2) events — an HA automation or mesh message POSTing to
  her; (3) full agentic pulse: wake → review the world → act or sleep.
- **The crude version exists TODAY, zero code:** Windows Task Scheduler + `curl -X POST
  /api/chat/turn` — the chat API is also an anything-can-poke-her API.
- **Proper v1 needs:** a scheduler thread parking *system turns* (the single-flight slot already
  prevents trampling live conversation), a system-turn prompt framing (she should know the
  timer woke her — persona wording gate), and an output policy (speak vs. dashboard chip).
- **The etiquette problem is the real work:** an agent that speaks every timer tick is Kairos —
  the clock we taught her to ignore. Quiet hours + a significance bar + "wake ≠ speak."
- **First tasks:** spoken reminders, morning briefing (search already exists), sensor watch
  (once HA), and the long-deferred memory confidence-decay job.

## 5. Alerting Michael remotely (pre-mesh)

**iPhone physics:** no third party gets an always-listening background path — every instant
lock-screen alert transits Apple's push network (APNs). The ladder:

1. **Today, free:** an alert field in the dashboard/remote poll → banner/vibrate while a page
   is open. At home she just says it out loud.
2. **The right pre-HA build: Web Push to the Safari home-screen app** (added 2026-07-18!).
   iOS 16.4+ supports it for home-screen web apps; payloads are E2E-encrypted by spec (Apple
   carries a sealed envelope). Moderate build: service worker + VAPID + pywebpush.
3. **Once HA exists:** the HA companion app's push is one `notify.mobile_app` service call —
   nearly free, and the camera/automation chain lives there anyway.
4. **Endgame: the Meshtastic pocket node** — the only pager that doesn't ask Apple.

- **Blink cameras:** cloud-locked, no local stream (established 2026-07-16) — useless as eyes;
  technically usable as a laggy motion tripwire via `blinkpy` (cloud, 20-30s). One cheap
  RTSP camera (Amcrest/Reolink) at the side gate is the real answer when cameras get real.

## 6. Custom iOS app (Michael has the $99 account + TestFlight experience)

**Not needed for alerts** — a native app obeys the same APNs physics, with more plumbing.
Web push on the home-screen bookmark wins that fight. **Where native genuinely shines (later):**

- **Background audio mode → true walkie-talkie mode with Echo** (a live session that survives
  backgrounding — the browser can never do this).
- **Action button** → instant push-to-talk; **Siri Shortcuts** ("Hey Siri, tell Echo…");
  native widgets; permanent mic permission.
- CarPlay needs Apple-granted entitlements — hard, don't count on it.

## 7. Garmin Instinct 3 (30-day battery > 18-hour battery)

- **Alerts are FREE:** Garmin mirrors iPhone notifications to the wrist — the moment web push
  (§5.2) lands, the Instinct buzzes. The 30-day pager already exists; no Connect IQ app needed.
- **Connect IQ apps:** possible on Instinct 3 (Monkey C), HTTP proxies through the phone,
  background limited to ~5-min polls, and iOS forbids notification *replies* from Garmin —
  receive-only surface. A status glance / canned-message button is feasible but skippable.
- **The sleeper hit: Garmin as a DATA SOURCE.** Sleep score, body battery, stress, HRV via
  Garmin Connect (Python libs exist for your own account). Echo's morning register informed by
  how Michael actually slept. Caveat: that data rides Garmin's cloud regardless (the watch syncs
  there anyway) — a deliberate read-only exception, same category as web search.

---

## Cross-cutting threads

- **The chat lane is the agentic substrate** — long-running tool work belongs there; voice gets
  the quick single-round-trip capabilities.
- **Speaker-ID is the permission layer** for every real-world capability (house control, memory,
  forget rights — and whatever comes next).
- **Local-first exceptions stay deliberate and enumerated:** web search (SearXNG), Apple's push
  relay (E2E-sealed) if/when web push lands, Garmin cloud (own data, read-only) if sleep-aware
  Echo happens. The mesh eventually deletes the alert exception entirely.
- Nothing in this file is committed. It exists to jog memory — ask CC to expand any section
  into a real plan when its day comes.
