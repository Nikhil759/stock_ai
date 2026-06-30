# Strategy 1: Buy Cheap, Quality Companies

*Graham-style value investing, adapted for NSE — long-term, fundamentals-only*

| | |
|---|---|
| **Time horizon** | Long-term (1–3+ years) |
| **Analysis type** | Fundamental only — no charts or technical indicators |
| **Risk level** | Low–Medium |
| **Trade frequency** | Very low (quarterly review/rebalance) |
| **Origin** | Benjamin Graham — *The Intelligent Investor*; *Security Analysis* (Graham & Dodd) |

## 1. Core philosophy

This strategy buys financially sound companies for materially less than their intrinsic worth, and holds them — sometimes for years — while the market gradually recognizes that value. It deliberately ignores daily price movement, news cycles, and momentum. The only things that matter are the numbers on the company's balance sheet and income statement, and whether the current price offers a meaningful discount against what the business is actually worth.

Graham called this discount the "margin of safety" — the buffer between what you pay and what the company is worth, which protects you if your analysis turns out to be slightly wrong, or if the market stays irrational longer than expected. It is the oldest and most extensively studied approach in this set of four strategies, and the one with the least day-to-day effort required once a position is established.

> **In plain terms:** Find boring, financially healthy companies that the market is currently underpricing. Buy them. Wait. Sell only when the price catches up to what the company is actually worth, or if the business itself starts to deteriorate — never sell just because the price dipped.

## 2. Where this comes from

The strategy is drawn directly from Benjamin Graham's two foundational texts: *The Intelligent Investor* (1949, revised through 1973) and the earlier, more technical *Security Analysis* (1934, with David Dodd), which together created the discipline now called value investing. Graham taught this approach to, among others, Warren Buffett, who called *The Intelligent Investor* "by far the best book on investing ever written."

Graham distinguished between two investor profiles in his book: the "Defensive Investor," who wants a simple, low-maintenance, conservative approach, and the "Enterprising Investor," who is willing to do more analytical work for potentially higher returns. This document focuses primarily on the Defensive criteria, since they are simpler to encode into a screening rule set and better suited to a bot that runs unattended.

## 3. Screening criteria — how candidates are selected

A stock must pass a series of fundamental filters before it is eligible to be bought. None of these depend on price charts or technical indicators — only company financials and valuation ratios.

### 3.1 Financial strength filters

- **Current ratio ≥ 2** — current assets at least twice current liabilities — the company can comfortably cover its short-term obligations.
- **Low debt-to-equity** — long-term debt should be low relative to equity, generally under 0.3–0.5. A heavily indebted company is fragile in a downturn, regardless of how cheap its stock looks.
- **Interest coverage ≥ 4** — operating profit should cover interest payments at least 4 times over, indicating the company isn't financially stretched.

### 3.2 Earnings stability

- **Positive EPS history** — earnings per share should have been positive in each of the last 7–10 years (relaxed to 5+ years where Indian company history is shorter or data is harder to source) — no major loss-making years.
- **Earnings growth** — per-share earnings should have grown by roughly a third or more over the past decade (using 3-year averages at the start and end of the period to smooth out one-off spikes or dips).

### 3.3 Valuation filters

- **P/E ratio ≤ 15** — price relative to earnings should be reasonable, not speculative. Use a 3-year average P/E rather than a single quarter to avoid being thrown off by a temporary earnings blip.
- **P/B ratio ≤ 1.5** — price relative to book value (net assets) should also be reasonable.
- **Graham Number check** — P/E × P/B should be ≤ 22.5 — Graham's combined valuation sanity check. A stock can pass the individual P/E and P/B thresholds but still fail this combined check if both ratios are near their limits simultaneously.

### 3.4 Quality and liquidity overlay

- **Adequate company size** — for the NSE adaptation, require a market capitalization of roughly ₹1,000–5,000 crore or more, to avoid obscure micro-caps with unreliable financials and thin trading volumes.
- **Decent ROE / ROCE** — return on equity and return on capital employed should be healthy relative to the company's sector — a quality overlay borrowed from Buffett's refinement of Graham's pure asset-based approach.
- **Positive operating cash flow** — the company should be generating real cash from operations, not just accounting profit.
- **Avoid governance red flags** — favour companies with high, stable, non-pledged promoter shareholding, and steer clear of stocks flagged under NSE's Additional Surveillance Measure (ASM) or Graded Surveillance Measure (GSM), or those in the trade-to-trade (T2T/BE) segment, which often signal heightened risk or speculative activity.

### 3.5 Optional deep-value sleeve: Net-Net (NCAV)

Graham's most aggressive value approach looks for "net-net" stocks: companies trading below two-thirds of their net current asset value (current assets minus total liabilities, ignoring fixed assets entirely). These are exceptionally rare and usually signal either deep undervaluation or serious underlying problems, so this sleeve should be treated as optional and approached cautiously — useful to flag if found, but not a primary hunting ground for a beginner-run bot.

## 4. Entry rules

- Buy when a stock passes all the Section 3 filters simultaneously, with an estimated margin of safety of at least 33% versus a reasonably conservative estimate of intrinsic value.
- Accumulate in tranches rather than all at once where the budget allows — spreading purchases over a few weeks reduces the risk of buying right before a short-term dip.
- Re-screen the universe quarterly or semi-annually; new candidates may qualify as their financials update or their price moves.
- With a very small starting budget (such as ₹10,000), the bot may only be able to buy 1–3 stocks at first — acceptable for testing, but the full benefit of this strategy comes from eventually holding 15–20+ names.

## 5. Exit rules

### 5.1 Target / profit-taking

Sell when the price reaches the estimated intrinsic value (a fair-value estimate derived from the same fundamentals used to screen the stock), or under Graham's specific net-net rule: exit a net-net position after a 50% gain, or after holding for 2 years, whichever comes first.

### 5.2 Stop-loss / risk control

This is the most distinctive feature of this strategy relative to the other three: Graham did not use price-based stop-losses at all. Protection comes from diversification (spreading across many names so any single failure is small) and from fundamental monitoring, not from reacting to price drops.

- Sell on fundamental deterioration: a sudden spike in debt, earnings turning negative, a serious governance red flag, or the company being moved into an NSE surveillance category.
- Do not sell simply because the price has fallen — a falling price with unchanged fundamentals is, under this philosophy, a buying opportunity, not an exit signal.

## 6. Position sizing

Equal-weight positions, with no single stock exceeding roughly 3.3–5% of the portfolio once enough capital is available to hold the full 20–30 name basket Graham recommended for diversification. With smaller starting capital, hold fewer names but keep them as equal-weighted as practical, and treat full diversification as a goal to grow into rather than a requirement from day one.

This strategy should be run fully funded — no leverage, no margin, delivery-based holding (CNC) only.

## 7. Risk classification and what to expect

| | |
|---|---|
| **Risk level** | Low–Medium |
| **Why** | Diversified, unleveraged, financially healthy companies. Main risks are value traps (cheap for a structural reason, not a temporary one) and long stretches of underperformance while waiting for the market to re-rate the stock. |
| **Expected behaviour** | Not a per-trade win rate — this is a portfolio/group approach. Some names will underperform or stay flat for a long time; the basket as a whole is expected to outperform over a multi-year horizon. |

Historical reference point: in a study of Graham's strict net-net approach on US markets (Henry Oppenheimer, *Financial Analysts Journal*, 1986), net-net stocks returned an average of 29.4% annually between December 1970 and December 1983, versus 11.5% for the broader NYSE-AMEX index over the same period. A later extension of this study (Carlisle, Mohanty & Oxman, 2010) found a 35.3% average annual return for net-nets from December 1983 to December 2008. These figures are from US markets and from the narrower, more aggressive net-net sleeve specifically — not a guarantee for the broader Defensive screen on NSE — but they illustrate why this category of value investing has been studied so extensively over the decades.

## 8. NSE-specific considerations

- Universe: the full NSE main board, filtered down by the liquidity and size thresholds in Section 3.4 — in practice this usually means focusing on Nifty 200 or Nifty 500 constituents rather than the entire exchange.
- Statistically cheap micro-caps are exactly the kind of stock NSE tends to flag under T2T, ASM, or GSM — the strategy's own fundamental filters naturally steer away from most of these, but it's worth an explicit check before buying.
- This strategy is delivery-only by design (no intraday trading), which suits T2T-segment rules even though such stocks should generally be avoided here for liquidity reasons on exit.
- Useful screening resources for NSE-specific data: Screener.in (community-maintained Graham/Buffett-style screens already exist there) is a good cross-reference alongside the bot's own yfinance-based screening.

## 9. Summary checklist for the bot

| Check | Threshold |
|---|---|
| Current ratio | ≥ 2 |
| Debt-to-equity | Low (< 0.3–0.5) |
| Interest coverage | ≥ 4× |
| EPS history | Positive for 7–10 years (min. 5 yrs for NSE) |
| 10-yr EPS growth | ≥ ~33% (3-yr avg basis) |
| P/E (3-yr avg) | ≤ 15 |
| P/B | ≤ 1.5 |
| Graham Number (P/E × P/B) | ≤ 22.5 |
| Market cap | ≥ ₹1,000–5,000 cr (avoid micro-caps) |
| Margin of safety | ≥ 33% below estimated intrinsic value |

---

*This document is a strategy specification for personal, educational, and automation-testing purposes. It is not investment advice. Historical performance figures cited are from published research on US markets and are illustrative of the value-investing philosophy, not a guarantee of future results on NSE.*
