# Data Layer

Keeps ~200 **strategy-neutral** dossiers fresh. One dossier per stock. The
strategy lens is applied later by the selector — never stored here.

## Layout
```
data_layer/
  config.py            # paths + tunables — EDIT THIS FIRST
  dossier.py           # the schema (one source of truth for the shape)
  storage.py           # JSON files (current) + SQLite (history)
  build.py             # orchestrator — run this
  compute/             # Phase 1, derived from bars (free)
    bars.py            #   bars dict/DataFrame -> clean DataFrame
    technicals.py      #   RSI, DMA, returns, rel-strength, ATR...
    chart_shape.py     #   plain-language trend/consolidation
    market_context.py  #   shared backdrop + breadth
  fetch/
    prices.py          # Phase 0 — yfinance bars/index (working)
    fundamentals.py    # Phase 0 — yfinance basics (working)
    fundamentals_ext.py# Phase 2 — NSE shareholding (promoter_holding_pct; FII/pledge need XBRL, deferred)
    news.py            # Phase 3 — Marketaux news + sentiment (built)
    events.py          # Phase 3 — NSE board meetings + corporate actions (built)
dossiers/              # output: <TICKER>.json  (created on first run)
data/history.sqlite    # append-only snapshots  (created on first run)
```

## Run
```
python -m data_layer.build          # pre-open full rebuild
python -m data_layer.build --close  # post-close snapshot
```

## Phases
- **0** Foundation — meta + fundamentals persist as JSON + SQLite. (built)
- **1** Technicals + chart_shape + market_context — from bars you already fetch. (built)
- **2** Fundamentals+ — NSE shareholding pattern fills `promoter_holding_pct`.
  `fii_holding_pct` / `promoter_pledge_pct` stay None — only available inside
  per-quarter XBRL filings, not the JSON endpoint; deferred. (built)
- **3** News + Events — `news.py` (Marketaux) and `events.py` (NSE board
  meetings + corporate actions). (built)

Each phase leaves the whole thing runnable; empty blocks are valid, not errors.

## Notes
- Needs `nifty200.json` at the repo root: `["RELIANCE","TCS",...]`.
- Adapt `fetch/prices.py` + `fetch/fundamentals.py` to your existing working
  fetch if the shapes differ — the compute layer only needs the bars dict.
- Requires: `yfinance pandas numpy requests python-dotenv`.
- `news.py` needs `MARKETAUX_API_KEY1` / `MARKETAUX_API_KEY2` (or a single
  `MARKETAUX_API_KEY`) in the repo-root `.env` — free-tier keys are rotated
  across tickers and retired for the run once a key hits its daily quota.
  Results are cached per ticker per day (`data/cache/news/`), so re-running
  the build multiple times in one day won't re-spend quota on tickers
  already checked today.
- `fundamentals_ext.py` and `events.py` talk to unofficial nseindia.com JSON
  endpoints via a shared cookie-warmed `requests` session — no `nsepython`
  dependency needed for either.

## Deploying the daily build as a Railway cron job

`python -m data_layer.build` is written to run-to-completion and exit
cleanly (the `ThreadPoolExecutor` is closed via `with`, sqlite connections
are opened/closed per call) — a good fit for Railway's Cron Job model,
which starts a service on a crontab schedule, runs its start command once,
and expects it to exit.

**1. All runtime state lives under one root, so one Volume covers it.**
`dossiers/`, `data/history.sqlite`, `data/cache/*` (this package) and
`backend/cache/` (the yfinance fetch/fundamentals cache it reuses) all key
off `config.STATE_DIR`, which is:
- `RAILWAY_VOLUME_MOUNT_PATH` if that env var is set (Railway injects this
  automatically the moment a Volume is attached to the service — no manual
  variable to configure), else
- the repo root (today's behavior, unchanged, for local dev).

Railway Volumes are one-per-service with a single mount path, which is why
everything had to be consolidated under one directory rather than three
separate ones — attach a Volume to the cron service at any path (e.g.
`/state`) and history, dossiers, and every fetch cache persist across runs
automatically.

**2. Set up the service.**
- In the same Railway project as `backend`, add another service pointing at
  this same repo (New → GitHub Repo → same repo again). It needs to be a
  *separate* service from `backend`'s always-on web app — a service on a
  cron schedule only runs at trigger time, it's not "always on".
- Set its **Root Directory to the repo root** (`/`), not `data_layer/` —
  `python -m data_layer.build` needs `data_layer` importable as a top-level
  package. The repo-root `requirements.txt` and `railway.json` exist
  specifically so Nixpacks can build a Python env and pick up the start
  command + cron schedule for *this* service without touching how
  `backend` (its own Root Directory, own `requirements.txt`) is deployed.
- Add `MARKETAUX_API_KEY1` / `MARKETAUX_API_KEY2` in that service's
  Variables tab (`GEMINI_API_KEY` / `RAILWAY_URL` aren't needed here).
- Attach a Volume to the service (any small size — current local footprint
  is well under 50MB) at whatever mount path you like; nothing else to
  configure, `STATE_DIR` picks it up automatically.

**3. Schedule.** Railway cron schedules are UTC only. `railway.json` ships
with `30 2 * * 1-5` = 08:00 IST, Mon–Fri (pre-open, matches `build.py`'s
default `snapshot="pre_open"`) — adjust to taste. Minimum interval is 5
minutes, not a concern here.

**Still open / not yet done:** running a second `--close` (post-close)
service on the same day currently wouldn't get fresh EOD prices — `backend
/data.py`'s yfinance cache is keyed per calendar day, so the post-close run
would just reuse whatever the pre-open run already cached that morning.
Fixing that needs a snapshot-aware cache key in `backend/data.py`, deferred
until twice-daily snapshots are actually wanted (also worth weighing against
Marketaux's already-tight free-tier quota across 200 tickers once a day).

Also unresolved: nothing in `backend/` reads `dossiers/` yet. Since the
cron service and the `backend` web service are separate Railway
services with separate filesystems/Volumes, a future selector running
inside `backend` can't just read this service's local files — it'll need
either private-network access to this service, a shared DB/object store, or
folding this build into `backend`'s own process via an in-process scheduler
instead of a separate Railway cron service.
