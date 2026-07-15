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
    fundamentals_ext.py# Phase 2 — NSE shareholding (via nse_client)
    news.py            # Phase 3 — Marketaux news + sentiment (built)
    events.py          # Phase 3 — NSE board meetings + corporate actions (built)
    nse_client.py      # Phase A — shared NSE session / retry / [FETCH] logs
    orderbook.py       # Phase A — Kite quote depth + circuit limits
    kite_session.py    # Phase A — TOTP token refresh before orderbook
    bigmoves.py        # Phase A — bulk / block / insider trades
    marketmood.py      # Phase A — FII/DII daily flow → market_context/mood_*.json
dossiers/              # output: <TICKER>.json  (created on first run)
market_context/        # output: mood_{date}.json (FII/DII, once per day)
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
- **A** Order book (Kite), big trades, market mood. (built)
- **B** `ta` library indicators + PKScreener-inspired chart_shape signals
  (consolidation %, volume ratio, Stage 2, named patterns). (built)
  Engine is `ta` (pure Python — works on Python 3.14). `compute/indicators.py`
  remains only for small helpers used by market_context / chart_shape.

Each phase leaves the whole thing runnable; empty blocks are valid, not errors.

## Pipeline hardening

- **Dossier merge:** `build.py` loads each ticker's existing dossier before
  updating. Failed fundamentals/technicals/news fetches keep prior data;
  successful fetches still overwrite as usual.
- **News quota:** `try_fetch_news()` reports fetch success separately from
  empty results. Quota failures retain prior news. `--skip-news` on
  `data_layer.build` or `cron.morning_ingestion` skips Marketaux on re-runs.
- **Health shortlists:** `/health` uses `stages.shortlists` per strategy when
  present; missing strategies fall back to local disk then
  `DOSSIER_API_URL/api/shortlists/today` (set on `stock_ai`).

### Local full pipeline

```bash
PYTHONPATH=. python -m cron.morning_ingestion
PYTHONPATH=. python -m cron.morning_ingestion --skip-news   # re-run scoring only
```

Then open `/health` on the dashboard (or query `health_runs` in Supabase).

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
  already checked today. On quota/network failure, `build.py` keeps prior
  dossier news instead of overwriting with empty. Use `--skip-news` to skip
  Marketaux entirely on quick re-runs.
- `fundamentals_ext.py` and `events.py` talk to unofficial nseindia.com JSON
  endpoints via a shared cookie-warmed `requests` session — no `nsepython`
  dependency needed for either.

## Deploying the dossier API on Railway (`data-layer-cron`)

`data-layer-cron` runs a **persistent internal API** (`data_layer.serve`) that:

- Keeps dossiers on its **volume** (unchanged)
- Runs **scheduled builds** via APScheduler (`DOSSIER_BUILD_CRON`, default `30 2 * * 1-5` UTC)
- Exposes `GET /api/dossiers` for `stock_ai` over **private networking**

Start command (`railway.json`):

```
uvicorn data_layer.serve:app --host 0.0.0.0 --port $PORT
```

Set `DOSSIER_API_TOKEN` on both `data-layer-cron` and `stock_ai`. On `stock_ai`:

```
DOSSIER_API_URL=http://data-layer-cron.railway.internal:<PORT>
```

Use the `PORT` variable from the cron service (e.g. `8080`).

## Deploying the daily build (legacy note)

`python -m data_layer.build` still works locally and via `POST /api/build` on the
internal API. The old Railway **cron-only** start command is replaced by the
serve app + in-process scheduler.

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

**Known, accepted limitation on Railway:** `fundamentals_ext.py` and
`events.py` call `nseindia.com`'s unofficial JSON API directly, and NSE
blocks known cloud/datacenter IP ranges (AWS, GCP — and therefore Railway)
at the network level regardless of correct cookies/headers. Expect every
`[fundamentals_ext]` / `[events]` call to log a `403 Forbidden` and degrade
to `None`/empty on Railway — `promoter_holding_pct`, `next_earnings_date`,
`ex_dividend_date`, and `recent_corporate_actions` will stay empty in
production dossiers. This is a deliberate, accepted trade-off (not a bug):
those same calls work fine when run locally (non-datacenter IP). Revisit
only if those fields turn out to matter — the real fix would be routing
just those two fetchers through a proxy/VPN with a non-blocked IP.

Also worth knowing: Marketaux's daily quota is shared per-key regardless of
caller, so running `python -m data_layer.fetch.news TICKER` locally (or any
local full build) on the same day as the Railway cron run eats into the
same quota the production run will see.
