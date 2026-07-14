---
id: wolf-capital
name: Wolf Capital
slug: wolf-capital
file: faq
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
  - architecture.md
updated_at: 2026-07-09
---

# Wolf Capital — FAQ

## Frequently Asked Questions

### What is Wolf Capital?

Wolf Capital is an AI fund manager for NSE stocks. You deploy **Wolves** — bots tied to a single investing philosophy and capital pool. Each Wolf researches the market daily, picks stocks with Gemini, and manages your money: entries, exits, stop-losses, and rotating freed cash. It is built for people who want returns without doing the research and chart-watching themselves.

### Is this just paper trading?

No. Paper mode is the current **beta** while end-to-end flows are validated. The production path is **Zerodha Kite Connect** for live quotes and order execution. The fund manager, selector, and guardrails are built for real capital management — paper trades are a staging step, not the product goal.

### What problem does it solve?

NSE investing requires deep knowledge, daily attention, and comfort with fundamentals, technicals, and position management. Most people want their money working, not another hobby. Wolf Capital handles the research, stock selection, and ongoing fund management so users do not have to live inside screeners and charts.

### What stocks does it cover?

The universe is **Nifty 200** — roughly 200 large-cap NSE stocks. The list is stored in `nifty200.json` and refreshable from official NSE index constituent data.

### What are the four investing philosophies?

**Buy cheap quality** (Graham-style fundamentals, long-term), **Buy the winners** (CANSLIM + breakout, weeks–months), **Buy the box breakout** (Darvas box pattern, days–weeks), and **Buy the dip** (RSI pullback in uptrend, few days to ~2 weeks). All four work on end-of-day data — no intraday streaming required.

### How does the AI part work?

A 3-phase **selector** pipeline: (1) deterministic math funnel filters ~200 stocks down to ≤30 survivors per philosophy; (2) Gemini scores each survivor individually (buy/watch/skip, conviction, prices, thesis); (3) one final Gemini call picks 1–3 stocks and allocates budget. Code re-validates arithmetic and clamps invalid allocations.

### What's a "Wolf"?

**Wolf Capital** is the platform. A **Wolf** is one deployed bot — e.g. Wolf 1 on value with ₹50k, Wolf 2 on box breakout with ₹25k. Each runs independently with its own fund pool, activity log, pause/terminate controls, and guardrails.

### What autonomy levels exist?

**Advisory:** Wolf suggests picks; you decide. **Autonomous A:** Wolf proposes trades and waits for approval. **Autonomous B:** trades under a rupee threshold auto-execute; larger ones need approval. **Autonomous C:** full auto within guardrails. Pause freezes all automated activity.

### What guardrails protect capital?

Every trade requires a stop-loss (default 15% below entry). Additional limits: max daily loss % (circuit breaker auto-pauses the Wolf), max capital deployed at once, and max per-stock % of budget. Strategy-lock prevents a Wolf from acting outside its chosen philosophy.

### When does the Wolf make decisions?

After market close, on end-of-day data. Decisions use that day's closing prices; execution targets the next session open via Zerodha. This avoids intraday data dependencies and matches all four philosophies' design.

### How does Zerodha fit in?

Zerodha Kite Connect provides live LTP for fill prices and is the intended order execution layer. `backend/fund_manager/kite_auth.py` handles auth. Beta validates the full fund-manager loop with paper positions while Kite supplies real market prices.

### What's the tech stack?

FastAPI + Python backend on Railway, SQLite persistence, yfinance + Marketaux + NSE data for dossiers, Gemini for LLM scoring, Zerodha Kite Connect for broker integration, Vercel-hosted PWA frontend. Two Railway services: web API (`stock_ai`) and dossier cron/API (`data-layer-cron`).

### How is this different from a robo-advisor or broker app?

It is a personal AI fund manager built around named classical strategies (Graham, O'Neil, Darvas) with full transparency — action log, reasoning per pick, configurable autonomy. Users choose the philosophy; the Wolf handles research and rotation. Not a regulated product; personal project in beta.

### What did Nikhil build?

Solo-built: dossier pipeline, four screeners, 3-phase Gemini selector, fund manager with scheduled jobs, multi-Wolf bot model, Zerodha Kite integration, FastAPI API, and the Wolf Capital PWA UI.

### Can I try it?

The UI is an installable PWA. A public demo URL will be added when ready. Backend requires Railway deployment with `GEMINI_API_KEY`, dossier service, and Zerodha credentials for live execution.
