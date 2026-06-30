# NSE Value Bot — Prototype

Paper-trading bot for NSE stocks. Scans **Nifty 200** via throttled yfinance, runs all **4 strategies**, and ranks picks with **Gemini** using strategy markdown + EOD data.

## Quick start

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Open **http://localhost:8000/app** in your browser.

## How to use

1. **Setup** — Select **Buy cheap quality**, set your budget (default ₹10,000), click **Run screen**.
2. **Shortlist** — Wait ~20–40s while Nifty 50 fundamentals load. Review candidates and filter badges.
3. **Log paper trade** — Click on a passing candidate to add it to your paper portfolio.
4. **Tracker** — View invested amount, mock P&L, and open positions (saved in `localStorage`).

## Bot modes

**Setup → Step 3** configures how the bot behaves:

| Mode | Behavior |
|------|----------|
| **Advisory** | Bot suggests picks; you log paper trades manually |
| **Autonomous A** | Bot proposes trades; you approve/reject each one |
| **Autonomous B** | Trades under ₹ ceiling auto-execute; larger ones ask first |
| **Autonomous C** | Bot auto-executes within guardrails |

Guardrails (always on): 15% stop-loss, max daily loss %, max capital deployed %, max per stock %.

Use the **pause button** in the header to freeze all bot activity. Check **Activity** tab for the full action log.

## API

- `GET /api/health` — health check
## Strategies

Four EOD-compatible strategies — each has a reference doc used as the bot's knowledge base:

| # | ID | Name | Reference file |
|---|-----|------|----------------|
| 1 | `value` | Buy cheap quality | `Strategy-1-Buy-Cheap-Quality-Companies.md` |
| 2 | `winners` | Buy the winners | `Strategy-2-Buy-the-Winners.md` |
| 3 | `box` | Buy the box breakout | `Strategy-3-Buy-the-Box-Breakout.md` |
| 4 | `dip` | Buy the dip | `Strategy-4-Buy-the-Dip.md` |

Only **Buy cheap quality** is implemented for screening. The bot runs after market close using end-of-day data.

- `GET /api/strategies` — list all strategies
- `GET /api/strategies/{id}` — full strategy markdown (for LLM / reference)
- `POST /api/screen` — `{ "strategy": "value", "budget": 10000 }`

## Stack

- **UI:** `Trading Bot.dc.html` + `support.js` (DC runtime)
- **Backend:** FastAPI + yfinance
- **Data:** Nifty 50, end-of-day fundamentals

Not financial advice — personal experimentation only.
# stock_ai
