# Ib-Lite — User Manual

Echo's memory. This is the practical guide: what it is, how it works, the voice
commands, and the CLI for inspecting and fixing what Echo remembers.

---

## What it is

Ib-Lite is a self-contained memory that runs **entirely on your PC**, behind the
local Gemma 4 12B model — no memory server, no cloud, no second model curating
things. It's a single SQLite file (`echo.db`) with keyword search (FTS5) and
semantic search (sqlite-vec). Echo manages most of it itself: after each thing
you say, a quick "significance gate" decides whether anything is worth keeping.

It replaced the old Hindsight/OpenMemory backend. Nothing leaves the machine.

## The five kinds of memory

| Type | What it holds | When Echo reads it | Who writes it |
|------|---------------|--------------------|---------------|
| **Core** | Who Echo is, who you are (persona, your profile) | Every turn, always | You (seed) + Echo |
| **Policy** | Echo's behavioral rules ("never call Michael 'Mike'") | Every turn, always | You (seed) + Echo |
| **Preference** | Stable likes/settings ("call me Michael") | Every turn, in context | The gate |
| **Fact** | Durable facts about you and your world ("the Jeep needs shocks") | Per turn, when relevant | The gate |
| **Episodic** | A short summary of each past conversation | Per turn, when relevant | At sign-off |

**Core and Policy are always in front of Echo.** Facts and Episodes are pulled in
only when they're relevant to what you're talking about.

## How it works (the loop)

1. **Session start** — Echo loads Core + Policy + your Preferences into its head.
2. **Each turn** — it searches Facts and past Episodes for anything relevant to what
   you just said and quietly folds it into context (~13ms, you won't feel it).
3. **After it answers** — a background "significance gate" looks at the turn and
   decides if anything's worth saving (a fact, a preference, a rule). This happens
   *off to the side* so it never slows down Echo's reply. Small talk is ignored.
4. **Sign-off** — when you end the session, Echo writes a short summary of the whole
   conversation as an Episode.

Echo never says "I remember" — it just knows things, the way a friend would.

### How relevance is decided

For Facts and Episodes, each candidate gets a score:

```
score = 0.5·(keyword match) + 0.3·(meaning match) + 0.2·(recency)
```

For Facts, that score is then multiplied by the fact's **confidence** (default 0.85).
Lower a fact's confidence and it ranks lower; drop it below 0.15 and it stops
showing up entirely (without being deleted). Only results above a relevance floor
make it in, capped at the top 5.

---

## Voice commands (while talking to Echo)

| Say... | What happens |
|--------|--------------|
| **"Echo, that's all for now"** | Ends the session and writes the Episode summary. |
| **"Echo, forget that"** | Deletes the **most recent fact** Echo saved this session. Echo confirms aloud what it dropped. |

"Forget that" also responds to *"scratch that"*, *"forget what I just said"*, and
*"don't remember that"*. If there's nothing recent to forget, Echo says so.

---

## The CLI — inspecting and fixing memory

Run from the `echo_stage0/` folder. This is how you see what the gate saved and
correct it (the gate isn't perfect, so this is your steering wheel).

```bash
python ib_lite_cli.py <command>
```

| Command | What it does |
|---------|--------------|
| `list` | Overview: counts per table + the most recent facts and episodes. |
| `facts` | Dump every fact (id, entity, attribute, value, confidence). |
| `prefs` | Show preferences. |
| `core` | Show core memories. |
| `policy` | Show behavioral rules. |
| `episodes` | Show past-session summaries. |
| `sessions` | Show recent sessions. |
| `search "the jeep"` | Search memory the way Echo does, with scores. |
| `confidence <fact_id> <0.0-1.0>` | Dial a fact up or down. Set it low (e.g. `0.1`) to hide a fact without deleting it. |
| `rm <table> <id-or-key>` | Delete a row. `<table>` = `fact` / `pref` / `core` / `policy` / `episodic` / `session`. |

You can also **seed or edit** Core, Policy, and Preferences by hand:

```bash
python ib_lite_cli.py core address_note "Michael goes by Michael, never Mike."
python ib_lite_cli.py policy be_brief "Keep answers short unless asked for detail." 7
python ib_lite_cli.py pref coffee "black, no sugar"
```

(`policy` takes a priority 1–10; 10 = hard rule, 1 = soft preference.)

### Examples

```bash
# What does Echo currently know?
python ib_lite_cli.py list

# Find everything about the Jeep
python ib_lite_cli.py search "jeep"

# The gate saved a wrong fact — find its id, then delete it
python ib_lite_cli.py facts
python ib_lite_cli.py rm fact 20260625004920491413

# Keep a fact but make it rarely surface
python ib_lite_cli.py confidence 20260625004920491413 0.1

# Point the CLI at a different database file
python ib_lite_cli.py --db backup.db list
```

---

## Where things live

| Path | What it is |
|------|------------|
| `echo_stage0/echo.db` | The memory database (created on first run). |
| `echo_stage0/ib_lite/` | The Ib-Lite package (you don't need to touch this). |
| `echo_stage0/ib_lite/ib_lite_schema.sql` | The table definitions + the Core/Policy seed values. Edit the seeds before first run to set Echo's starting persona and rules. |
| `echo_stage0/memory_failures.log` | If the gate ever produces an unparseable write, it's logged here (non-fatal — Echo keeps going). |

## Tuning (for later)

A few knobs, all constants in `ib_lite/retrieval.py`:

- `MIN_SCORE` (0.4) — relevance floor. Raise it if junk surfaces; lower it if Echo
  forgets things it should recall.
- `RETRIEVAL_WEIGHTS` (0.5 / 0.3 / 0.2) — keyword vs meaning vs recency balance.
- `MIN_CONFIDENCE` (0.15) — facts below this are hidden.
- `TOP_K` (5) — max memories pulled per turn.

## Good to know

- **Saving is delayed by ~a second.** The gate runs in the background after Echo
  replies, so a thing you just said isn't searchable for a moment. That's by design
  — it keeps replies fast.
- **Everything is local.** `echo.db` is just a file. Back it up by copying it.
  Delete it and Echo starts fresh (re-seeding Core/Policy from the schema).
- **The gate runs the same model Echo talks with.** If you swap in a different local
  model, structured saving may need re-checking (the gate needs "thinking" disabled
  to return clean output).
