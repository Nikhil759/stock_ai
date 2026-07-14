---
id: wolf-capital
name: Wolf Capital
slug: wolf-capital
file: architecture
year: 2026
category: web
tags: [full-stack, ai, fintech, fund-manager, nse, zerodha]
employer: null
role: solo-builder
status: in-progress
one_liner: AI fund manager for NSE stocks — deploy strategy-driven Wolves that research the market daily, pick stocks, and manage your capital via Zerodha.
stack: [FastAPI, Python, Gemini, Zerodha Kite Connect, SQLite, yfinance, Pandas, APScheduler, Railway, Vercel, PWA, Marketaux]
links:
  - label: Live demo
    url: ""
doc_type: project
visibility: public
related_files:
  - index.md
  - faq.md
updated_at: 2026-07-09
---

# Wolf Capital — Architecture

## System Components

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Vercel (PWA)   │────▶│  Railway stock_ai │────▶│  SQLite DB      │
│  Wolf Capital UI│     │  FastAPI backend  │     │  bots/trades/   │
└─────────────────┘     └────────┬─────────┘     │  action_log     │
                                 │                └─────────────────┘
                    dossier sync │                        │
                                 ▼                        ▼
                        ┌──────────────────┐     ┌─────────────────┐
                        │ data-layer-cron  │     │ Zerodha Kite    │
                        │ dossier build +  │     │ quotes + orders │
                        │ internal API     │     │ (production)    │
                        └──────────────────┘     └─────────────────┘
```

**`data_layer/`** — builds ~200 strategy-neutral JSON dossiers (`dossiers/<TICKER>.json`) plus append-only history in SQLite. Fetches prices and fundamentals via yfinance, computes technicals and chart shape locally, pulls news via Marketaux, and corporate events via NSE JSON.

**`selector/`** — reads dossiers, runs strategy funnel + Gemini scoring + final allocation. Outputs daily intentions per Wolf per strategy.

**`backend/`** — FastAPI app (`main.py`, title "Wolf Capital" v0.4.0). Hosts fund manager, bot lifecycle, screening endpoint, workspace isolation. Serves UI in local dev.

**`backend/fund_manager/`** — morning deploy, evening refresh, circuit breaker, redeploy freed cash, ledger, 9-gate approval flow.

**Frontend** — `Trading Bot.dc.html` + `support.js`, built to `public/` for Vercel via `scripts/build-vercel.js`. API base injected from `RAILWAY_PUBLIC_URL`.

## Data Flow — Daily Cycle

1. **~08:00 IST (cron):** `data-layer-cron` runs `data_layer.build` (pre-open snapshot). Dossiers persisted on Railway volume. Internal API serves `GET /api/dossiers` to `stock_ai` over private network.

2. **Before screen:** `stock_ai` syncs dossiers from `DOSSIER_API_URL` into local `dossier_cache/`.

3. **~09:00 IST (selector cron on `stock_ai`):** `run_selector_all_wolves` runs the 3-phase selector for each running Wolf, writes intentions.

4. **~09:15 IST (morning deploy cron):** `run_morning_all_wolves` loads intentions, runs each pick through 9 fund-manager gates (cash, caps, breaker, mode), opens positions or queues for approval. Fill price from Zerodha Kite LTP (`FUND_MANAGER_FILL_PRICE=kite`) or intention price in beta.

5. **~16:00 IST (evening job):** price refresh, auto-exits on stop/target, redeploy freed cash, check daily loss circuit breaker.

6. **User anytime:** deploy new Wolf, pause/resume, approve pending trades, view portfolio and activity log.

## Selector Pipeline

```
All dossiers (Nifty 200)
        │
        ▼
Phase 1: funnel.py + strategies/*.py  (deterministic, no LLM)
        │  cap: FUNNEL_MAX_SURVIVORS = 30
        ▼
Phase 2: llm/scoring.py  (1 Gemini call per survivor, parallel)
        │  StockVerdict: buy | watch | skip
        ▼
Phase 3: llm/final.py  (1 Gemini call per strategy per day)
        │  1–3 picks, budget allocation, cash held OK
        ▼
intentions/<strategy>_<date>.json
```

Prompts live in `selector/prompts/` (`scoring_skeleton.txt`, `strategy_*.txt`, `final_selection.txt`, `daily_wolf_selection.txt`). Model: `gemini-2.5-flash`.

## Dossier Schema

One JSON file per stock. Blocks: `meta`, `fundamentals`, `technicals`, `chart_shape`, `market_context`, `news`, `events`. Strategy logic is never stored in the dossier — the selector applies the lens at read time. Defined in `data_layer/dossier.py`.

## Bot Modes & Gates

**Modes:** Advisory | Autonomous (A = approval gate, B = auto under ₹ threshold, C = full auto)

**Morning deploy gates (9):** include paused/terminated checks, circuit breaker, cash availability, per-stock cap, max deployed %, mode-specific approval routing.

**Guardrails always on:** stop-loss %, max daily loss %, max deployed %, max per stock %, strategy-lock.

## Zerodha Integration

Zerodha Kite Connect (`kiteconnect` SDK) is wired into the fund manager for live LTP quotes at fill time. `backend/fund_manager/kite_auth.py` handles authentication. Order placement is the production execution path; beta currently validates the full loop with paper positions while Kite provides real market prices.

## Deployment Topology

| Service | Platform | Config | Role |
|---------|----------|--------|------|
| Frontend | Vercel | `vercel.json` | Static PWA, rewrites `/app` → `index.html` |
| `stock_ai` | Railway | `railway.web.toml` | FastAPI, fund scheduler, dossier sync, selector |
| `data-layer-cron` | Railway | `railway.json` | Dossier build cron + internal dossier API on volume |

Both Railway services use **repo root** as root directory so `selector/` and `data_layer/` are in the build context.

Key env vars: `GEMINI_API_KEY`, `DOSSIER_API_URL`, `DOSSIER_API_TOKEN`, `RAILWAY_PUBLIC_URL` (on Vercel build), `FUND_SCHEDULER_ENABLED`, `MARKETAUX_API_KEY`, Zerodha API credentials.

## AI / ML Design

- LLM handles judgment and ranking, not raw data fetching
- Rule-based funnel bounds API cost (max 30 stocks per strategy into LLM)
- Structured output via Pydantic schemas (`StockVerdict`, `FinalPicks`)
- Failed LLM calls default to `skip` — never crash the pipeline
- Live screening uses `selector/prompts/` with full dossier JSON; legacy `backend/knowledge/Strategy-*.md` docs are not used at runtime

## Known Limitations

- Beta phase uses paper positions for portfolio state; live Zerodha order execution is the production target
- NSE unofficial endpoints blocked from Railway datacenter IPs; some dossier fields (promoter holding, events) empty in production builds
- yfinance gaps: no ASM/GSM surveillance flags, limited multi-year EPS, no FII flow — planned for future data sources (Screener.in, NSE feeds)
- Post-close dossier snapshot not yet separate from pre-open cache
