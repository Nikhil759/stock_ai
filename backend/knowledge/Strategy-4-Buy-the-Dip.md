# Strategy 4: Buy the Dip

*Connors RSI-2 mean-reversion pullback, adapted for NSE — pure technical, short swing*

| | |
|---|---|
| **Time horizon** | A few days to ~2 weeks |
| **Analysis type** | Technical only — no fundamentals involved |
| **Risk level** | Medium |
| **Trade frequency** | Moderate (signals reviewed end-of-day, clustered during pullbacks) |
| **Origin** | Larry Connors & Cesar Alvarez — *Short Term Trading Strategies That Work* |

## 1. Core philosophy

Within a stock that is already in a well-established long-term uptrend, short, sharp pullbacks — a handful of red days in a row — are treated as temporary overreactions rather than the start of a genuine reversal. This strategy buys into that short-term panic, betting that the stock reverts back toward its recent short-term average price, and then exits quickly once that reversion happens — often within days, not weeks.

This is deliberately the mirror image of Strategy 3: where the Box Breakout strategy buys strength and rides momentum, this strategy buys weakness within a broader uptrend and takes small, fast profits. Running both side by side tends to smooth out a portfolio's overall equity curve, since they tend to perform well in different kinds of market conditions.

> **In plain terms:** Only look at stocks that are already climbing steadily over the long run. When one of them has a short, sharp dip — a few red days — buy it, betting on a bounce back toward its recent average price. Sell quickly once it recovers, even if the gain is small. Don't hold out for a huge profit.

## 2. Where this comes from

This strategy is based on the 2-period RSI (Relative Strength Index) mean-reversion system documented by Larry Connors and Cesar Alvarez in *Short Term Trading Strategies That Work* (2008) and their related *Street Smarts* material. Their research, originally built and backtested on US index and stock data, found that very short-term RSI readings (using just a 2-day lookback, rather than the more commonly used 14-day RSI) were unusually effective at identifying short-term overreaction points when combined with a longer-term trend filter.

## 3. The indicators used

- **200-day moving average** — the trend filter — the stock must be trading above this level for a signal to be considered at all. This ensures the strategy only buys dips within stocks that are, in the bigger picture, still going up.
- **2-period RSI — RSI(2)** — the entry trigger. Unlike the standard 14-period RSI used in most technical analysis, this much shorter lookback reacts very quickly to recent price action, making it sensitive to short, sharp pullbacks rather than longer-term trend shifts.
- **5-day moving average** — used as the exit signal — once the price recovers back above this short-term average, the reversion this strategy is betting on is considered complete.

## 4. Entry rules

- Trend filter: the stock's price must be above its 200-day moving average. No exceptions — this strategy does not buy dips in stocks that are in a long-term downtrend.
- Trigger: RSI(2) drops below 10, signalling a sharp short-term oversold condition. A more aggressive, higher-conviction version of this signal uses a threshold below 5 — Connors and Alvarez's own backtests found that the lower the RSI(2) reading at entry, the stronger the subsequent average bounce tended to be.
- Optional additional confirmation: requiring the price to also be trading below its 5-day moving average at the time of the signal, or requiring 3 consecutive down days beforehand, can be used to filter for higher-conviction setups, at the cost of fewer signals overall.
- Because RSI-2 signals cluster heavily during broad market corrections (many stocks become oversold together), cap total exposure to this strategy at roughly 10% of the overall portfolio at any one time, to avoid taking on many correlated positions that all move together in a worse-than-expected downturn.

## 5. Exit rules

### 5.1 Profit target — the reversion exit

Exit once the price closes back above its 5-day moving average, or once RSI(2) recovers back above roughly 60–70 — either is treated as confirmation that the short-term oversold condition has resolved. This strategy is designed around quick, small wins rather than large profit targets; the original research backing it found a high win rate paired with modest individual gains, not the other way around.

### 5.2 Stop-loss

This is the one place where this strategy deliberately departs from its original source material. Connors and Alvarez's own backtests found that adding a stop-loss to the raw RSI-2 system actually reduced overall returns on the US data they tested — and so their published system runs without one. For a personal bot trading real capital, running completely without a protective stop is judged too risky: an oversold dip can, on occasion, turn into the start of a genuine trend reversal rather than a bounce, and without a stop that scenario has no defined floor.

This strategy's adaptation therefore mandates a protective stop on every trade — either a hard stop roughly 1.5–2× the stock's recent Average True Range (14-day) below the entry price, or a simpler fixed percentage stop (around 8%), or an exit on a confirmed close below the 200-day moving average (signalling the longer-term uptrend itself has broken, invalidating the entire premise of the trade). This is a conscious, documented trade-off: slightly lower theoretical returns in exchange for a defined worst case on every position.

## 6. Position sizing

Standard risk-based sizing: risk roughly 1% of portfolio equity per trade, calculated from the entry price to the stop-loss level. Given that RSI-2 signals cluster during market pullbacks, also enforce a hard cap of roughly 5–6 concurrent positions under this strategy at any one time, on top of the 10% aggregate-exposure cap mentioned in Section 4.

## 7. Risk classification and what to expect

| | |
|---|---|
| **Risk level** | Medium |
| **Why** | The high win-rate, small-individual-loss character of this strategy is its main strength, but it carries fat-tail risk: an oversold signal occasionally precedes a larger trend break rather than a bounce. The mandatory stop-loss (Section 5.2) and the 200-day trend filter both exist specifically to contain this risk. |
| **Expected win/loss shape** | Connors and Alvarez's published backtests on US index data found win rates in the rough range of 70–85%, with small average gains per trade — the classic high-win-rate, small-edge profile of a mean-reversion system. Expect a meaningfully lower win rate on individual NSE stocks specifically, since single-stock price behaviour is noisier and less liquid than the broad US indices the original research was built on. |

A note on shorting: Connors' original research also covers a mirrored short-side version of this system (selling into short-term overbought spikes within a downtrend). This adaptation deliberately omits the short side — shorting individual stocks overnight isn't straightforward in Indian cash-equity markets without moving into derivatives, which is explicitly out of scope for this project. This strategy is run long-only.

## 8. NSE-specific considerations

- Universe: liquid Nifty 100–200 large-cap names are the safest fit — mean-reversion strategies depend on liquidity and are more vulnerable to distortion or manipulation in thinly-traded small-cap stocks.
- This strategy is delivery-based (CNC) and typically held for several days, so while T2T/BE-series stocks are technically tradeable under this holding style, they should generally still be avoided for the same liquidity-on-exit reasons noted in the other strategies. Avoid ESM/GSM-flagged names entirely.
- This approach also works well applied to broad index ETFs (for example, a Nifty-tracking ETF) rather than individual stocks, since it reduces single-company risk while still capturing short-term index-level pullbacks.
- Fully compatible with the project's end-of-day operating rhythm: RSI(2), the 200-day and 5-day moving averages, and the ATR-based stop are all calculated from daily closing price data — no live intraday feed is required to run this strategy.

## 9. Summary checklist for the bot

| Check | Rule |
|---|---|
| Trend filter | Price above 200-day moving average |
| Entry trigger | RSI(2) < 10 (more aggressive: < 5) |
| Optional confirmation | Price below 5-day MA, or 3+ consecutive down days |
| Aggregate exposure cap | ~10% of portfolio in this strategy at once |
| Profit exit | Close above 5-day MA, or RSI(2) > ~60–70 |
| Stop-loss | ~1.5–2× ATR(14) below entry, or ~8% fixed, or close below 200-day MA |
| Position risk per trade | ~1% of portfolio equity |
| Concurrent positions cap | ~5–6 |
| Direction | Long only — no short selling |

---

*This document is a strategy specification for personal, educational, and automation-testing purposes. It is not investment advice. Win-rate figures cited are from Connors & Alvarez's published backtests on US market data and are illustrative of the strategy's character, not a guarantee of results on NSE stocks.*
