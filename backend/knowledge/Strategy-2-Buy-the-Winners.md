# Strategy 2: Buy the Winners

*Momentum-leadership investing on NSE — hybrid fundamentals + technicals, positional*

| | |
|---|---|
| **Time horizon** | Weeks to months (positional) |
| **Analysis type** | Hybrid — fundamentals find the company, technicals time the entry |
| **Risk level** | Medium–High |
| **Trade frequency** | Low (quarterly portfolio rotation) |
| **Origin** | William O'Neil — *How to Make Money in Stocks* (CANSLIM); Jesse Livermore |

## 1. Core philosophy

This strategy buys the strongest companies in the market — those with accelerating earnings and growing institutional sponsorship — at the exact moment their stock price breaks out of a quiet consolidation phase into new high ground. The logic is that strength tends to persist: a company already outperforming, with smart money already buying in, is statistically more likely to keep outperforming than an unloved or struggling one is to suddenly turn around.

It is a hybrid by design: fundamentals are used to find genuinely good businesses (so the bot isn't just chasing random price spikes), and technicals — specifically, a breakout from a price base on rising volume — are used to time the entry so the bot buys at the moment conviction is building, not before or after.

> **In plain terms:** Find companies that are already winning — growing profits, more big investors buying in — and buy the stock right as it breaks out of a quiet sideways phase into a new high on strong volume. Ride the trend while it lasts. Cut losses fast if it stops working.

## 2. Where this comes from

The core screening framework is William O'Neil's CANSLIM system, detailed in *How to Make Money in Stocks* (various editions since the 1980s), built from O'Neil's own study of the biggest stock market winners across decades. The entry/exit discipline layers in Jesse Livermore's documented trading philosophy (*Reminiscences of a Stock Operator*; *How to Trade in Stocks*) — specifically his rule of buying strength at "pivotal points" rather than trying to buy at the absolute bottom, and his insistence on cutting losses immediately and letting winners run. Nicolas Darvas's box-breakout method (used independently as Strategy 3 in this set) also informs the consolidation-then-breakout pattern recognition here.

## 3. Screening criteria — the CANSLIM framework

CANSLIM is an acronym — each letter represents one filter a stock should pass. A candidate doesn't need to score perfectly on all seven, but the more it satisfies, the stronger the case.

### C — Current quarterly earnings

Latest-quarter earnings-per-share growth of roughly 25% or more year-over-year. Strong, accelerating recent performance, not stale older results.

### A — Annual earnings growth

Annual EPS growth of roughly 25% or more over the past 3 years, paired with a healthy return on equity. Consistent growth, not a single lucky quarter.

### N — New factor

Something new driving the stock — a new product, new management, a new industry trend, or (most measurably) the stock making a new 52-week high. New highs are often misunderstood as "too expensive to buy" but in this framework, a stock breaking to new highs on strength is exactly the signal being sought.

### S — Supply and demand

Reasonable share supply (not an enormous float that's hard to move) combined with a clear volume surge during the breakout — evidence of real demand stepping in, not a thin, unconvincing move.

### L — Leader, not laggard

The stock's relative strength rank should sit in roughly the top 20% of the universe being screened over the past 12 months — i.e., it should be measurably outperforming most other stocks, not merely "not falling."

### I — Institutional sponsorship

Rising institutional or FII (foreign institutional investor) shareholding over recent quarters — a signal that professional money managers are accumulating the stock, which tends to provide both validation and a source of sustained buying pressure.

### M — Market direction

The overall market should be in a confirmed uptrend before taking new positions — for this strategy, that means the Nifty index trading above its 200-day moving average. Even a great individual stock struggles to perform well against a falling broad market; this filter exists to avoid fighting the tide.

## 4. Technical entry trigger

Fundamentals (the CANSLIM checks above) identify which companies are worth watching. The actual buy decision is triggered by price action:

- The stock should be forming or have recently formed a base — a period of relatively tight, sideways consolidation lasting 7 weeks or more (patterns commonly described as a "cup-with-handle" or a "flat base" in this literature).
- Buy on a volume-confirmed close above the top of that base (the "pivot point") — volume on the breakout day should be at least 1.5× the recent average, evidence that institutional buying is driving the move, not noise.
- The stock should be trading above its rising 50-day and 200-day moving averages, and ideally within about 15% of (or already making) a new 52-week high.
- Require at least 5 of the 7 CANSLIM conditions to be satisfied, alongside the market-direction ("M") filter being positive, before entering.

## 5. Position building — pyramiding

Rather than buying a full position all at once, Livermore's approach (and O'Neil's, independently) favours adding to a position only after it has already moved into profit — confirming the thesis is playing out — rather than averaging down into a loser. Concretely: take an initial position on the breakout, and consider adding a smaller follow-on tranche only if the stock confirms strength shortly afterward. Never add to a position that is moving against the original entry.

## 6. Exit rules

### 6.1 Stop-loss — O'Neil's 7–8% rule

This is one of the most quoted and specific rules in trading literature. O'Neil's own words, from *How to Make Money in Stocks*: "Always, without Exception, Limit Losses to 7% or 8% of Your Cost… 7% to 8% is your absolute loss limit. You must sell without hesitation." This is a hard, mechanical rule — not a guideline to be second-guessed in the moment. Alternatively, exit if price closes below the base low or the 50-day moving average, whichever is hit first.

### 6.2 Profit-taking

O'Neil's general guideline is to take most profits in the 20–25% range. The one notable exception: if a stock surges 20% or more within 1–3 weeks of its breakout, that unusual strength is treated as a signal of a potential much larger winner, and the position is held longer — known as the "8-week hold rule" — with the position trailed using the 50-day moving average or weekly higher-lows rather than sold at the first profit target. This is the practical expression of Livermore's instruction to "let your winners run."

### 6.3 Trend-break exit

Exit on a close below the rising 50-day moving average on heavy volume, or on clear abnormal/reversal price action — signs the institutional sponsorship driving the stock has reversed.

## 7. Position sizing

Equal-weight roughly 8–15 positions (so individually around 7–12% of the portfolio each), or alternatively size each position so that the 7–8% stop-loss represents about 1% of total portfolio equity at risk. Scale the number of concurrent positions with available capital — a diversified 8–12 stock book of this kind realistically needs a starting capital of at least roughly ₹2–3 lakh to avoid awkward odd-lot sizing; with smaller capital, hold fewer names.

## 8. Risk classification and what to expect

| | |
|---|---|
| **Risk level** | Medium–High |
| **Why** | This is a trend-following, momentum-based approach. Indian momentum-strategy backtests have shown maximum drawdowns in the roughly −30% to −35% range under normal conditions, and considerably deeper (around −70%) during severe market stress if quality/fundamental filters are skipped — which is exactly why the CANSLIM fundamental screen is paired with the technical entry here rather than trading breakouts alone. |
| **Expected win/loss shape** | Roughly 35–45% of trades are winners, but average winners tend to be 3× or more the size of average losers — a small number of big winners drive most of the return, while many trades are cut quickly at the 7–8% stop. |

Reference data points from Indian momentum-factor research: long-run backtests of momentum strategies on the Nifty 200 universe have reported figures in the rough range of 14–22% CAGR depending on the exact filters and period studied (for example, one long-run study citing roughly 14% CAGR for a pure momentum approach, rising to nearly 18% with an added quality/anti-speculation filter; another 11-year study citing 22.4% CAGR for a filtered momentum approach versus 13.1% for simple Nifty 200 buy-and-hold over the same period). These are backtest results, not guarantees, and forum claims of 50%+ CAGR for momentum strategies should be treated with real skepticism — they are frequently distorted by survivorship bias in how the underlying index-constituent history was constructed.

## 9. NSE-specific considerations

- Universe: Nifty 500 or Nifty Midcap 150 for a wide-enough pool of candidates, filtered down to stocks with market capitalization of at least roughly ₹1,000 crore and adequate average daily trading volume to avoid illiquidity and ESM-related restrictions.
- Favour stocks with rising promoter and/or FII holding and clean governance histories; avoid recently-flagged T2T or ESM names even if they otherwise pass the screen.
- NSE publishes its own factor indices built on closely related logic — the Nifty 200 Momentum 30 and Nifty Midcap 150 Momentum 50 indices — which rank stocks by volatility-adjusted 6- and 12-month returns. These are a useful sanity-check reference alongside the bot's own screening.
- Because this strategy depends on trend confirmation rather than instant reaction, end-of-day data (rather than live intraday feeds) is sufficient for both screening and execution timing.

## 10. Summary checklist for the bot

| Check | Threshold |
|---|---|
| Quarterly EPS growth (YoY) | ≥ ~25% |
| 3-yr annual EPS growth | ≥ ~25% |
| 52-week high / new driver | Near or at new high |
| Breakout volume | ≥ 1.5× average |
| Relative strength rank | Top ~20% of universe |
| Institutional/FII holding trend | Rising |
| Market filter (Nifty vs 200-DMA) | Nifty above 200-DMA |
| Base length before breakout | ≥ 7 weeks consolidation |
| Stop-loss | 7–8% below entry, hard rule |
| Profit target (standard) | 20–25%, trail if exceptional strength |

---

*This document is a strategy specification for personal, educational, and automation-testing purposes. It is not investment advice. Backtest and performance figures cited are drawn from published trading literature and third-party Indian market research, not a guarantee of future results.*
