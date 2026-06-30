# Strategy 3: Buy the Box Breakout

*The Darvas Box method, adapted for NSE — pure technical, end-of-day compatible*

| | |
|---|---|
| **Time horizon** | Days to a few weeks |
| **Analysis type** | Technical only — no fundamentals involved |
| **Risk level** | Medium–High |
| **Trade frequency** | Moderate |
| **Origin** | Nicolas Darvas — *How I Made $2,000,000 in the Stock Market* |

## 1. Core philosophy

A stock often trades sideways for a stretch of time, bouncing between a fairly consistent high and low — a "box." This strategy treats that box as a coiled spring: when the price finally closes above the top of the box on a clear increase in volume, it signals the stock has gathered enough buying pressure to break into a new upward move. The bot buys at that moment and rides the trend, protecting the position with a stop placed just below the box it broke out of.

Unlike Strategy 1 (which only cares about company fundamentals) or Strategy 2 (which blends fundamentals with price action), this strategy is purely about price behaviour. It doesn't matter what the company does, what its earnings look like, or who owns the stock — only that its price pattern shows a clean, well-defined consolidation followed by a confirmed breakout.

> **In plain terms:** Watch for a stock that's been trading sideways in a tight range for a few weeks — like it's stuck in a box. When it finally breaks above the top of that box with strong buying volume, buy it. Set your safety exit just below the box. Ride the move up, and raise that safety exit as new, higher boxes form.

## 2. Where this comes from

This method was developed by Nicolas Darvas, a professional ballroom dancer who traded stocks on the side in the 1950s while touring internationally — documented in his memoir *How I Made $2,000,000 in the Stock Market*. What makes this strategy particularly relevant to a bot that only operates outside trading hours is that Darvas developed and traded this system using delayed information: he received stock prices via telegram, often a day or more old, while travelling between cities with no access to live quotes. He proved a price-pattern-based system could work using only end-of-day-equivalent information — which is precisely the operating constraint this bot has been designed around.

## 3. Defining the box

A "box" is a price range a stock holds within for a meaningful stretch of time — not just one or two days, but typically several weeks. The mechanics, adapted for an end-of-day system:

- Identify a period (commonly 3 weeks or more) during which the stock's daily closing prices stay within a relatively tight high–low range, without breaking decisively in either direction.
- The top of this range becomes the box ceiling; the bottom becomes the box floor.
- A tighter, cleaner box (less daily noise, a narrower high–low spread) is a stronger setup than a wide, choppy range — it suggests more conviction and less indecision among traders.
- Boxes often form in sequence as a stock climbs — each new, higher box becomes the next support level once price has broken above it.

## 4. Entry rules

- Buy when the stock's daily close breaks above the established box ceiling.
- Require trading volume on the breakout day to be meaningfully above the recent average (this is the volume-confirmation principle shared with Strategies 2 and 4 — a breakout without volume support is treated with more suspicion, as it's more likely to be a false move that reverses).
- Because the bot operates only after market close, the breakout is identified using that day's closing data, and the buy order is placed for execution at the next market open — consistent with the EOD-only operating rhythm used throughout this project.
- Avoid entries where the box has formed during an overall declining trend in the broader market — breakouts have a higher tendency to fail when fighting the broader market direction (the same market-filter logic used in Strategy 2).

## 5. Exit rules

### 5.1 Stop-loss — Darvas's signature discipline

The defining feature of this method is the stop-loss placement: set it just below the floor of the box that was broken out of. This is a tight, well-defined level — if the stock falls back into or below its old box after supposedly breaking out, the thesis has failed, and the position is exited without hesitation. This is conceptually similar to O'Neil's hard stop-loss discipline in Strategy 2, but here the exact level is defined by the chart pattern itself rather than a fixed percentage.

### 5.2 Trailing the stop as new boxes form

As the stock continues rising, it will often pause and consolidate again, forming a new, higher box. Once that happens, the stop-loss is moved up to just below the floor of this newest box — progressively locking in gains while still giving the position room to keep running. The position is held as long as each successive box holds; it is exited only when price closes back below the most recent box's floor.

### 5.3 No separate fixed profit target

Unlike Strategy 1's explicit profit-taking range, this method doesn't use a fixed percentage profit target. Exits are driven entirely by the trailing box-floor stop — the position is allowed to run for as long as the stock keeps forming higher boxes, and is cut as soon as that pattern breaks.

## 6. Position sizing

Risk-based position sizing: for each trade, the rupee distance between the entry price and the box-floor stop-loss defines the risk per share. Size the position so that this distance, multiplied by the number of shares bought, equals a fixed, modest fraction of total portfolio equity (a common default is around 1%). This keeps any single failed breakout from causing significant portfolio damage, regardless of how many shares that translates to at any given stock's price.

- Cap the number of concurrent open positions under this strategy (a reasonable starting point is 4–6) to avoid overexposure to multiple breakouts that may be correlated with the same broad market move.

## 7. Risk classification and what to expect

| | |
|---|---|
| **Risk level** | Medium–High |
| **Why** | Breakout strategies are vulnerable to false breakouts — a price that pokes briefly above the box ceiling and then falls back, triggering an entry that's quickly stopped out. This is mitigated, but not eliminated, by requiring volume confirmation and a positive broad-market backdrop before entering. |
| **Expected win/loss shape** | Similar character to Strategy 2 — a meaningful share of trades will be quick, small losses at the stop, with profitability driven by the minority of breakouts that turn into genuine, multi-box sustained trends. |

## 8. NSE-specific considerations

- Universe: liquid Nifty 100–200 names are the safest starting point — breakout patterns are more reliable in stocks with consistent daily trading volume, where a price move is more likely to reflect genuine demand rather than a single large order skewing a thin order book.
- Avoid T2T/BE-series and ASM/GSM/ESM-flagged stocks for this strategy specifically — their artificially tightened price bands and delivery-only restrictions distort how a normal breakout pattern would otherwise behave, making the box pattern less reliable.
- Be mindful of NSE's circuit bands: a non-F&O stock's daily move can be capped at 2%, 5%, 10%, or 20% depending on its assigned band. A stock can be frozen at its circuit limit, which can prevent a clean breakout close from forming, or trap a position if the stock gaps sharply against the position the next session.
- Because this strategy is end-of-day driven (decide after close, execute at next open), it does not require live intraday data or a broker WebSocket connection — consistent with the project's current operating rhythm. yfinance daily OHLC data is sufficient to identify boxes and breakouts.

## 9. Summary checklist for the bot

| Check | Rule |
|---|---|
| Box definition | Tight consolidation range held ≥ ~3 weeks |
| Entry trigger | Daily close above box ceiling |
| Volume confirmation | Breakout-day volume meaningfully above recent average |
| Market filter | Avoid entries against a declining broad market |
| Stop-loss | Just below the floor of the box broken out of |
| Stop trailing | Move up to newest box floor as higher boxes form |
| Profit target | None fixed — ride until the trailing stop is hit |
| Position risk per trade | ~1% of portfolio equity (entry-to-stop distance basis) |
| Concurrent positions cap | ~4–6 |

---

*This document is a strategy specification for personal, educational, and automation-testing purposes. It is not investment advice. Win-rate and risk characteristics described are drawn from general trading literature on breakout/trend-following systems, not a guarantee of future results on NSE.*
