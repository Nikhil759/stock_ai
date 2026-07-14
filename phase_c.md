# Wolf Capital — Phase C Handover Prompt
## Per-Strategy Math Funnels

Paste this whole document into Cursor as the task brief.

---

## Context

Dossiers now contain: Basics (yfinance), Chart reading incl. `chart_shape` (ta library + Stage 2/consolidation/volume/patterns), Ownership, Big trades (bulk/block/insider), Market mood (FII/DII, market-wide), News & events, Order book (Kite).

This phase builds four **independent** math funnels — one per strategy — that each take all 200 dossiers and narrow them down to a shortlist of candidates. Each funnel only looks at the fields relevant to its own strategy. **No LLM calls in this phase** — pure deterministic filtering only. Batch scoring is the next phase.

---

## Goal

Create four funnel modules, each following the same pattern:
1. Take all 200 dossiers as input
2. Apply a sequence of filters specific to that strategy
3. Log the before/after count at each filter step
4. Output a shortlist of surviving stocks (typically 20-30) with the specific numbers that got them through, for the batch-scoring step to use later

---

## Part 1 — Value funnel

**File:** `funnels/value_funnel.py`

Filters, applied in this order:
1. P/E ratio below configurable ceiling (default: 25)
2. Debt-to-equity below configurable ceiling (default: 0.5)
3. Positive earnings over each of the last 3 years (no losses)
4. Price below calculated Graham fair value (`sqrt(22.5 × EPS × Book Value per share)`)
5. Market cap above a liquidity floor (default: ₹5,000 crore — adjust if this excludes too much of the Nifty 200)

All thresholds should be named constants at the top of the file, not hardcoded inline, so they're easy to tune later.

---

## Part 2 — Winners funnel

**File:** `funnels/winners_funnel.py`

Filters, applied in this order:
1. Most recent quarterly earnings growth > 0% year-over-year
2. Price within 15% of its 52-week high (configurable)
3. Relative strength vs Nifty positive over the last 3 months
4. FII/institutional ownership trend is flat or increasing (not declining) over the last 2 available data points
5. `chart_shape.stage == "stage2_uptrend"` (from Phase B) — exclude anything not in a confirmed uptrend

---

## Part 3 — Box funnel

**File:** `funnels/box_funnel.py`

Filters, applied in this order:
1. `chart_shape.stage == "stage2_uptrend"` — same Stage 2 pre-filter as Winners
2. `chart_shape.is_consolidating == true` (consolidation_percentage ≤ 10%, already computed in Phase B)
3. Price has broken above the recent consolidation range (compare current price to the high of the lookback window)
4. `chart_shape.volume_confirmed_breakout == true` (volume_ratio ≥ 2.5x, already computed in Phase B)

Optional (log but don't filter on yet): note whether `"nr4"` is present in `chart_shape.patterns` for any survivor — useful supporting context for the LLM later, not a hard filter for now.

---

## Part 4 — Dip funnel

**File:** `funnels/dip_funnel.py`

Filters, applied in this order:
1. RSI(2) below extreme oversold threshold (default: 10)
2. Price above its 200-day moving average (confirms still in a longer-term uptrend, not a falling knife)

This funnel will likely produce fewer survivors than the others on any given day — that's expected, not a bug. Log this clearly rather than trying to force a minimum count.

---

## Shared requirements across all four funnels

- Each funnel is a standalone function, e.g. `run_value_funnel(dossiers: list[dict]) -> list[dict]`, callable independently — no shared mutable state between strategies.
- Every filter step logs: stock count before → after, and the filter name. E.g.:
  ```
  [MATH FUNNEL] Value: 200 → 142 (P/E ≤ 25)
  [MATH FUNNEL] Value: 142 → 61 (debt/equity ≤ 0.5)
  [MATH FUNNEL] Value: 61 → 25 (Graham fair value)
  ```
- When a filter step removes a stock, log at least 2-3 concrete dropped examples with the actual value that failed, e.g.:
  ```
  [MATH FUNNEL] Value: dropped RELIANCE — P/E 34.2 exceeds ceiling of 25
  ```
- Each funnel's final output should be a list of dicts containing: `symbol`, the full dossier (for the next phase's LLM scoring), and a `funnel_reasons` field listing which specific values got it through — this is what feeds the batch-scoring prompt later, so the LLM doesn't have to re-derive why a stock is even a candidate.
- If a strategy's funnel produces fewer than 5 survivors on a given day, log a clear warning — not an error, just a flag worth noticing.
- Thresholds mentioned above (P/E ceiling, debt/equity ceiling, RSI threshold, etc.) should all be easily tunable constants — expect these to be adjusted after backtesting.

---

## Orchestration

**File:** `cron/morning_ingestion.py` (extend the existing fetch/build steps)

After dossiers are built, run all four funnels independently:
```python
value_candidates = run_value_funnel(dossiers)
winners_candidates = run_winners_funnel(dossiers)
box_candidates = run_box_funnel(dossiers)
dip_candidates = run_dip_funnel(dossiers)
```
Do not chain or share results between strategies — each gets the same full 200 dossiers as input, independently.

**This phase stops here** — do not build the shortlist cache or batch scoring yet. Just get clean, well-logged funnel output for each strategy first. We'll wire caching and LLM scoring in the next phase once these funnel outputs look sane.

---

## Acceptance criteria

- [ ] All four funnels run independently on the same 200 dossiers without errors
- [ ] Console logs clearly show before/after counts at every filter step, per strategy
- [ ] Dropped-stock examples are logged with actual values, not just counts
- [ ] Each funnel's survivor count is reasonable (roughly 15-30 for Value/Winners/Box; Dip may be lower — that's fine)
- [ ] Spot-check: pick one survivor from each strategy and manually confirm its dossier values actually satisfy that strategy's filters
- [ ] No LLM/API calls made in this phase — funnels are pure deterministic filtering
- [ ] All thresholds are named, tunable constants, not hardcoded magic numbers