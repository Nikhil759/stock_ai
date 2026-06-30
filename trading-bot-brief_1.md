# NSE Trading Bot — Project Brief

## What this is
A personal trading bot for NSE (Indian stock market) stocks. It uses an LLM
to analyze stocks and suggest trades, based on a chosen strategy and a
user-specified budget. Starting with paper/dummy trades only — no real
money, no live order execution yet.

## Architecture (high level)
1. **Static knowledge base** — trading strategy rules (below), fed into the
   LLM's system prompt.
2. **Live data layer** — stock prices, fundamentals, charts, fetched daily
   (end-of-day for now, real-time later).
3. **LLM reasoning layer** — combines both to generate trade calls (buy
   price, sell price, holding period).
4. **Paper trading tracker** — logs dummy trades for ~1 month to validate
   before going live.

## Operating rhythm — finalized

**The bot only runs and makes decisions outside trading hours (after market
close, ~3:30 PM IST onward).** It does not act intraday or in real-time.

This was a deliberate simplification with real benefits:
- Removes the live-data dependency entirely — 15-20 min delayed yfinance
  data is perfectly fine since the market is closed by the time the bot
  reasons over it anyway.
- No race conditions between "bot decides" and "bot executes."
- Matches the spirit of all 4 strategies below — none of them are meant to
  react to intraday price wiggles.
- No need for Zerodha WebSocket/real-time API at this stage — a single
  scheduled script run per day (after close) is enough.

**Important nuance:** the bot *decides* using the day's closing price, but
can only *execute* at the next market open (NSE cash equity doesn't trade
after hours). So there's always a "decide today, execute next session"
gap — the actual fill price may differ slightly from the decision price.
This is immaterial for all 4 strategies (none are short-term-precision
sensitive), which is exactly why intraday strategies were excluded (see
below).

**Consequence: intraday strategies are out of scope for now.** The
original "Morning breakout" strategy required watching the first 15-30
min of live trading and acting same-session — fundamentally incompatible
with an EOD-only operating rhythm. It has been replaced (see Strategy 3
below) with an end-of-day-compatible alternative. True intraday strategies
are deferred to a clearly later phase, alongside the real-time data
infrastructure they'd require.

## All 4 strategies (user picks one at a time via the UI)

All 4 are end-of-day compatible — no live/intraday data needed for any of
them.

### 1. Buy cheap, quality companies — long-term (SELECTED, build this first)
- **Analysis type:** fundamental only — looks purely at company financials
  (debt, profit, valuation), ignores price charts/technicals entirely.
- **Time horizon:** long-term (1–3+ years)
- **Core idea:** find financially healthy companies trading below their
  actual worth, buy and hold, ignore daily price noise.
- **Screening criteria (simplified):**
  - Low debt (current ratio ≥ 2, low debt-to-equity)
  - Steady/positive earnings over past several years
  - Reasonable valuation: P/E ≤ 15, P/B ≤ 1.5 (Graham number: P/E × P/B ≤ 22.5)
  - Decent ROE / profitability
  - Adequate company size and liquidity (avoid obscure micro-caps for now)
- **Exit rule:** sell when price reaches fair value estimate, or if company
  fundamentals deteriorate (not on short-term price drops).
- **Position sizing:** spread across multiple stocks (ideally 15–20+ for
  full diversification, but user is starting with a small test budget of
  ₹10,000, so 1–3 stocks for now — diversification benefit kicks in once
  budget scales up).
- **Status: this is the strategy being built first, end-to-end.**

### 2. Buy the winners — weeks to months (positional)
- **Analysis type:** hybrid — fundamentals (earnings growth) find good
  companies, technicals (price breakout, volume) time the entry.
- **Time horizon:** weeks to months
- **Core idea:** find companies already doing well (growing profits, more
  big investors buying in) whose stock is breaking out of a quiet sideways
  phase into a new high on strong volume. Buy the breakout, hold while the
  trend lasts, sell when it stops working.
- **Risk:** medium-high — riding strong trends, but trends can reverse.

### 3. Buy the box breakout — days to weeks (Darvas Box)
- **Analysis type:** technical only — pure price pattern, no fundamentals
  involved at all.
- **Time horizon:** days to a few weeks
- **Origin:** Nicolas Darvas's "box" method (*How I Made $2,000,000 in the
  Stock Market*) — notably a strategy originally developed and proven
  using only end-of-day prices (Darvas traded via delayed telegraph
  updates while touring as a dancer), making it a natural fit for an
  EOD-only operating rhythm.
- **Core idea:** a stock trades in a tight "box" range for several
  days/weeks. When it closes above the top of that box on rising volume,
  it signals a new upward move. Buy the breakout, ride the trend.
- **Entry rule:** stock closes above the top of its recent consolidation
  box (e.g. a tight range held for 3+ weeks), with volume above its recent
  average.
- **Exit rule:** stop-loss just below the bottom of the box (signature
  Darvas discipline); trail the stop upward as new boxes form on the way
  up, exit when price closes below the most recent box floor.
- **Risk:** medium-high — fast-moving, trend-following, similar risk
  character to the original Morning Breakout idea but without needing any
  live/intraday data.
- **This strategy replaces the original "Morning breakout" (ORB) strategy,
  which required live intraday data and same-day execution — incompatible
  with the EOD-only operating rhythm decided above.**

### 4. Buy the dip — few days to ~2 weeks (short swing)
- **Analysis type:** technical only — pure price behavior (trend
  direction, short-term oversold dips), no fundamentals involved.
- **Time horizon:** days to ~2 weeks
- **Core idea:** only in stocks already in a long-term uptrend. When the
  stock has a short, sharp pullback (a few red days), buy it — betting on
  a bounce back to its recent average price. Sell quickly once it
  recovers, even on a small gain.
- **Risk:** medium — many small wins, but watch for a "dip" turning into a
  bigger fall.

**Build priority:** Strategy 1 (buy cheap quality) end-to-end first, since
that's selected. Strategies 2, 3, 4 are documented here for later — same
overall architecture (knowledge base + system prompt + live data + LLM
trade calls) but different screening rules and time horizons.

## Current budget for testing
₹10,000 (small test amount — expect only 1–3 stock positions, not full
diversification yet)

## UI — strategy + budget selector
A working HTML/JS mockup of the selection UI was built and approved by the
user. It should be treated as the reference design to follow when building
the real frontend. Key elements:

- **Step 1 — strategy picker:** a 2x2 grid of cards, one per strategy.
  Each card shows: an icon, a small colored tag for time horizon (e.g.
  "Intraday", "Weeks-months", "Long-term", "Few days-2wks"), the strategy
  name, and a one-line plain-English description. Clicking a card selects
  it (highlighted border), deselecting others.
- **Step 2 — budget input:** a ₹ number input paired with a range slider
  (₹10,000 to ₹10,00,000), kept in sync with each other.
- **Summary bar:** appears once a strategy is selected, showing
  "[strategy name] with a budget of ₹[formatted amount]".
- **Confirm button:** disabled until a strategy is selected; on click,
  sends the final selection forward.
- **Style:** flat, minimal, card-based, matches Claude's design system
  (CSS variables for colors/spacing, no gradients/shadows, sentence case
  labels, Tabler outline icons). Cards used:
  - Buy cheap quality — shield-check icon, green "Long-term" tag
  - Buy the winners — trending-up icon, amber "Weeks-months" tag
  - Buy the box breakout — bolt icon, red "Days-weeks" tag (replaces the
    original "Morning breakout" card — same icon/urgency feel, but this
    version is end-of-day compatible, not intraday)
  - Buy the dip — arrow-bear icon, blue "Few days-2wks" tag

When building the real frontend (likely React/Next.js), recreate this same
layout, copy, icons, and interaction pattern faithfully — the user has
already approved this design and wants to follow it exactly.

## Data plan (current phase)
- **No broker account yet** — not needed for data, only needed later for
  live order execution.
- Use **yfinance** (free, no signup) to pull NSE data using ticker format
  `SYMBOL.NS` (e.g. `RELIANCE.NS`, `TCS.NS`).
- Fetch: historical OHLCV prices, P/E, P/B, debt-to-equity, ROE, EPS growth
  where available.
- **End-of-day data is fine for now.** Live/real-time data is a later
  phase requirement (will need a broker API like Upstox or Zerodha Kite
  Connect at that point).

## Immediate next build step
Build a Python script that:
1. Takes a list of NSE stocks (start with Nifty 50 or Nifty 200 universe)
2. Fetches current price + key fundamentals via yfinance
3. Filters/screens them against the value criteria above
4. Outputs a shortlist of candidates that fit within the ₹10,000 budget

## Data approach — finalized

**For now (long-term value strategy): yfinance + occasional Zerodha batched
quotes is sufficient.** No need for real-time WebSocket streaming yet.

- **Live/current price:** fetched on-demand via yfinance when needed (not
  stored — just a live lookup at decision time). If a Zerodha account is
  set up later, `kite.quote()` can batch up to 500 instruments in a single
  call, far more efficient than yfinance's one-ticker-at-a-time model.
- **Historical price data (for charts/trend context):** pulled on-demand
  via `yfinance`'s `.history()` — no need to store this ourselves, fetch
  fresh whenever needed.
- **Fundamentals (P/E, debt-to-equity, ROE, etc.):** via `yfinance`'s
  `.info`, looped per-ticker. For scanning a large universe (e.g. Nifty
  500), this needs to be **parallelized** (Python `concurrent.futures`
  thread pool, ~10-20 concurrent requests with delays/retries) since
  yfinance has no bulk fundamentals call and Yahoo will rate-limit
  aggressive sequential hits.
- **What DOES get stored in our own database (SQLite for now):**
  - Trade log (what the bot bought/sold, when, at what price, why)
  - Screening/shortlist snapshots (for tracking how the bot's picks
    evolved over time)
  - Portfolio state (current holdings, cash remaining)
  - Prices and fundamentals themselves are NOT stored — always fetched
    fresh from yfinance/Zerodha when needed.

**Scanning flow (all strategies):**
```
1. Pull Nifty 200 universe (official NSE CSV → backend/nifty200.json, refreshable)
2. Throttled parallel fetch via yfinance (6 workers, ~0.22s between starts, daily disk cache)
3. Apply strategy-specific rule screen → shortlist
4. Pass shortlist + strategy markdown + EOD metrics to Gemini for ranking/reasoning
5. Bot presents picks; user or autonomous mode executes paper trade next morning
```

**Universe:** Nifty 200 for all four strategies in v1 (not full NSE ~2000+ names).
Refresh list: `universe.refresh_nifty200_from_nse()` pulls NSE archives CSV.

## Data gaps — yfinance only today (future API additions)

The bot runs on yfinance EOD data after market close. The following are **not**
reliably available from yfinance and are planned for later data sources
(Screener.in, NSE surveillance feeds, Zerodha Kite batch quotes):

| Gap | Why it matters | Strategies |
|-----|----------------|------------|
| ASM / GSM / ESM / T2T flags | Avoid risky or restricted names | All |
| Promoter pledge / holding trend | Governance quality | Value, Winners |
| FII / DII institutional flow | CANSLIM “I” | Winners |
| Multi-year quarterly EPS history | EPS stability, CANSLIM C/A | Value, Winners |
| Interest coverage, OCF consistency | Graham financial strength | Value |
| Official index constituent auto-sync | Universe accuracy | All |
| Bulk batched live quotes | Scale + next-open fill price | Execution |
| NSE circuit band metadata | Breakout reliability | Box |

**Planned sources (later phase):**
- **Zerodha Kite Connect** — batched quotes (500/call), eventual order execution
- **Screener.in or similar** — Indian fundamentals, Graham screens
- **NSE published data** — surveillance, bhavcopy, index CSV (already used for Nifty 200 list)

Rule-based screeners work without these; LLM prompts explicitly note data limitations.

## LLM layer — Gemini (implemented)

- API key: `GEMINI_API_KEY` in project `.env` (never commit)
- Model: `gemini-2.5-flash` (override via `GEMINI_MODEL` in `.env`) with JSON output
- Prompt includes: full strategy markdown (truncated), yfinance shortlist metrics, budget/cash context
- LLM ranks top 1–3 picks with buy/sell/stop/reasoning; merges back into UI candidates
- Falls back to rule-based shortlist if API key missing or call fails

## All four strategies — status

| # | Strategy | Screener | LLM |
|---|----------|----------|-----|
| 1 | Buy cheap quality | Graham-style 6 filters on Nifty 200 | Yes |
| 2 | Buy the winners | Simplified CANSLIM + base/breakout | Yes |
| 3 | Box breakout | Darvas box + volume on Nifty 200 | Yes |
| 4 | Buy the dip | RSI(2) + 200 DMA filter | Yes |

Operating rhythm unchanged: **decide after close (~4 PM), execute next morning open.**

**Scanning flow for the value strategy (legacy detail):**
```
1. Pull full stock universe list (e.g. Nifty 500)
2. Parallel-fetch fundamentals via yfinance for all of them
3. Apply the value screen (P/E, P/B, debt, ROE thresholds) → shortlist
4. Fetch live price for shortlisted candidates only (yfinance, or Zerodha
   batched quote if account is set up) to check affordability vs budget
5. Pass shortlist + live prices + strategy rules to the LLM for the final
   call
```

**Why no real-time/WebSocket data yet:** the "buy cheap quality" strategy
is long-term and not price-reactive day-to-day — end-of-day data is
genuinely sufficient and matches how this strategy is meant to work
(Graham-style investing isn't meant to react to daily price noise).
Real-time WebSocket streaming (via Zerodha Kite Connect, ₹2,000/month for
API access) is explicitly deferred to the later phase when building the
"morning breakout" intraday strategy, which actually requires true
real-time prices and an always-on connection during market hours
(9:15–15:30 IST) — mistakes there compound fast since same-day exit is
required, unlike long-term positions which can be re-evaluated the next
day.

**Data source is intentionally swappable:** build the screener so the data
source (yfinance now, Zerodha/broker API later) is a separate, replaceable
layer — not hardcoded into the screening/strategy logic itself.

## Trading modes — manual vs autonomous

Two top-level modes the user can switch between in the UI:

### Mode 1: Advisory (manual)
Bot screens stocks and suggests a shortlist with reasoning (entry price,
sell target, why). User reviews and executes trades manually themselves.
No autonomy — bot never places a trade on its own in this mode.

### Mode 2: Autonomous
Bot manages the user's funds and executes trades on its own, within the
selected strategy and budget. User monitors, and can pause or change
strategy at any time. Within this mode there are 3 trust levels (a dial,
not a single on/off):

- **Level A — Approval gate:** bot finds a trade, sends full reasoning
  (buy price, target, stop-loss, why), and waits for explicit user
  approval before executing anything.
- **Level B — Auto under a threshold:** user sets a rupee ceiling per
  trade (e.g. ₹2,000). Trades under that ceiling execute immediately with
  a notification sent after. Trades above the ceiling require approval
  first, same as Level A.
- **Level C — Full auto:** bot executes all trades within strategy rules
  without asking, notifying the user after every action. Highest trust,
  needs the strongest guardrails (below) since visibility is lowest here.

User can switch between A/B/C at any time.

### Guardrails (apply at every autonomy level, non-negotiable)
These exist specifically to address the user's two biggest fears: losing
money unsupervised, and the bot doing something unintended.

- **Stop-loss required on every trade** — set at entry, not adjustable by
  the bot mid-trade.
- **Max daily loss (circuit breaker)** — if cumulative losses in a day hit
  a user-set % of budget, the bot auto-pauses itself and notifies the
  user. (Open question, not yet decided: does it stay paused until manual
  resume, or auto-resume next trading day?)
- **Max capital deployed at once** — bot never puts more than a user-set %
  of budget into open positions simultaneously, always keeps a cash
  buffer.
- **Max position size per stock** — no single stock can exceed a user-set
  % of total budget (ties into diversification from Strategy 3).
- **Strategy-lock** — bot can only act within the rules of the currently
  selected strategy, can't freelance into a different trading style.
- **Full action log** — every action and the reasoning behind it is
  logged and timestamped, visible to the user regardless of mode/level.

### Pause behavior
When paused: bot freezes completely. No new buys, no exits on open
positions, no automated action of any kind. Open positions stay exactly
as they are until the user manually decides what to do with them. This is
intentional — pause means "do nothing," not "wind down gracefully."

### Open questions (not yet decided, revisit before/during build)
1. When the daily loss circuit breaker trips, does the bot stay paused
   until manual resume, or auto-resume the next trading day?
2. If the user switches strategy while positions are open under the old
   strategy, do those open positions keep being managed by the old
   strategy's exit rules, or does the new strategy take them over?
3. Where should notifications be delivered — in-app only, email, or a
   chat integration (e.g. Telegram/WhatsApp)?

### UI — control panel mockup
A working HTML/JS mockup of the mode/autonomy control panel was built and
approved by the user, in the same design system as the strategy+budget
selector (flat cards, CSS variables, Tabler outline icons, sentence case).
Key elements to recreate faithfully in the real frontend:

- **Status banner** at the top showing running/paused state, with a
  pause/resume button. Green when running, amber when paused, icon and
  label change accordingly (play icon + "Bot is running" / pause icon +
  "Bot is paused — no buys, no sells").
- **Mode picker** — 2 cards side by side: Advisory (eye icon) and
  Autonomous (robot icon), each with a one-line description.
- **Autonomy level list** (only shown when Autonomous mode is selected) —
  3 selectable rows for Levels A/B/C, each with an icon, name, and
  one-line description.
- **Threshold input** (only shown when Level B is selected) — a ₹ number
  input for the auto-execute ceiling, with helper text explaining trades
  above it will ask first.
- **Guardrails section** — always visible, a list of rows: stop-loss
  (fixed/required, no input), max daily loss % (number input), max
  capital deployed % (number input), max per stock % (number input).
- **Summary bar** at the bottom — plain-language sentence describing
  exactly how the bot will behave given the current mode/level selection,
  updates live as choices change.

This panel and the strategy+budget selector together form the full
pre-launch setup flow the user goes through before the bot starts running.

## Future phases (not yet started)
- Add **Zerodha Kite Connect** for batched quotes and live order execution
- Add **Screener.in / NSE surveillance** data for gaps listed above
- Scheduled daily EOD cron (auto screen + refresh + auto-exit) without manual button
- Paper-trading validation over ~1 month before real money
- Optional: expand universe to Nifty 500 with stronger caching

## Important constraints
- No F&O (futures/options) — simple stock buying/selling only
- This is a personal project for paper trading first, not a product yet
  (may consider releasing later if it works well)
- Not financial advice — purely for personal experimentation