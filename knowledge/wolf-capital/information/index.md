---
id: wolf-capital
name: Wolf Capital
slug: wolf-capital
file: index
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
  - architecture.md
  - faq.md
updated_at: 2026-07-09
---

# Wolf Capital

## Overview

Wolf Capital is an AI-powered fund manager for NSE (Indian equity) stocks. Users deploy **Wolves** — independent bot instances, each bound to one investing philosophy and one capital pool. Every trading day, the system researches the Nifty 200 across prices, fundamentals, technicals, news, and corporate events, ranks candidates with Gemini, and manages the portfolio: entries, exits, stop-losses, and redeploying freed cash.

The intended execution layer is **Zerodha Kite Connect** for live quotes and order placement. The app is currently in **beta**, running paper trades while end-to-end flows are validated before live capital goes in.

Operating rhythm: **decide after market close (~4 PM IST), execute at next session open.** No intraday streaming dependency in v1.

## Problem

Trading on the NSE takes deep knowledge, daily attention, and real experience. Fundamentals live on one portal, charts on another, news somewhere else — and turning any of it into a disciplined trade plan means hours of screening, technical analysis, and position management.

For most people who want their money working rather than another hobby, the learning curve is steep and the work is tedious. They want returns without living inside candlesticks or earnings reports. There is no simple way to hand your capital to a system that researches daily, follows a named investing philosophy, and manages rotation and growth on your behalf.

## Solution

Wolf Capital automates the full loop. Pick a trading philosophy — Graham value, CANSLIM winners, Darvas box breakout, or RSI dip-buying — and deploy a Wolf with your capital allocation. The Wolf researches the market daily from multiple data sources, screens the Nifty 200, uses Gemini to rank survivors with reasoning, and acts as your fund manager: buying, selling, enforcing stop-losses, rotating freed cash, and growing the pool while you stay hands-off.

Choose how much control you want: **Advisory** (Wolf suggests, you approve) or **Autonomous** (Levels A/B/C with approval gates and circuit breakers). Multiple Wolves can run side by side, each on a different strategy and fund pool.

## Key Features

### Four investing philosophies

| ID | Name | Style | Horizon |
|----|------|-------|---------|
| `value` | Buy cheap quality | Graham-style fundamentals | Long-term (1–3+ years) |
| `winners` | Buy the winners | CANSLIM + breakout technicals | Weeks to months |
| `box` | Buy the box breakout | Darvas box pattern + volume | Days to weeks |
| `dip` | Buy the dip | RSI(2) pullback in uptrend | Few days to ~2 weeks |

Each philosophy has rule-based screeners and strategy-specific Gemini prompts grounded in structured dossier data.

### Daily research pipeline

A **data layer** builds one strategy-neutral JSON dossier per Nifty 200 stock every weekday: fundamentals (yfinance), technicals and chart shape (computed), market breadth, news sentiment (Marketaux), and corporate events (NSE). The selector applies the chosen philosophy lens at read time — dossiers are never strategy-specific.

### AI-powered stock selection

Three-phase **selector** pipeline per Wolf per day:

1. **Math funnel** — deterministic filters cap survivors at 30 (no LLM cost).
2. **Per-stock scoring** — one Gemini call per survivor; returns buy/watch/skip with conviction, entry/stop/target, thesis, and risks.
3. **Final allocation** — one Gemini call ranks and allocates 1–3 picks within budget; code re-validates arithmetic.

### Fund manager

Scheduled jobs handle the daily cycle: morning deploy (intentions → positions through 9 gates), evening refresh (price update, auto-exits on stop/target, redeploy freed cash, circuit breaker check). Zerodha Kite Connect provides live LTP for fill prices; order execution is the production path once beta validation completes.

### Multi-Wolf deployments

Each Wolf has its own fund pool, strategy lock, autonomy mode, guardrails, and activity log. Wolves are independently pausable or terminable. Dashboard aggregates across active Wolves.

### Guardrails

- Stop-loss on every trade (default 15% below entry)
- Max daily loss circuit breaker (auto-pause)
- Max capital deployed % and max per-stock %
- Strategy-lock — Wolf cannot act outside its philosophy
- Full timestamped action log with reasoning

## My Contribution

Solo-built end to end: data pipeline and dossier schema, four strategy screeners, 3-phase Gemini selector, FastAPI backend, fund manager with cron scheduling, Zerodha Kite integration, and the Wolf Capital PWA UI.

## Outcomes & Metrics

- Nifty 200 universe with daily dossier rebuild on Railway cron
- All four philosophies implemented with screener + LLM ranking
- Full fund-manager loop: deploy, track, exit, redeploy, circuit breaker
- Currently in beta with paper trades; live Zerodha execution is the production target

## Stack

**Frontend:** HTML/JS (DC runtime), PWA (service worker + web manifest), Vercel static deploy

**Backend:** FastAPI, Python 3, SQLite, APScheduler (in-process fund jobs)

**Data:** yfinance (prices, fundamentals), NSE unofficial JSON (shareholding, events), Marketaux (news sentiment)

**AI:** Google Gemini (`gemini-2.5-flash`) via `google-genai`, structured JSON output, per-strategy prompts

**Broker:** Zerodha Kite Connect — live quotes and intended order execution layer

**Infra:** Railway — two services from one repo (`stock_ai` web API + `data-layer-cron` dossier build/serve); Vercel frontend points at Railway via `RAILWAY_PUBLIC_URL`

## Links & Demos

- Live app URL to be added when public demo is ready
- Not financial advice — personal project in beta

## Documentation Map

- `architecture.md` — system components, data flow, deployment topology, daily schedule
- `faq.md` — common visitor and recruiter questions

## Related Projects

None listed yet.
