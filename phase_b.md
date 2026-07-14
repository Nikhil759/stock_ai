# Wolf Capital — Phase B Handover Prompt
## Expanded Technicals + PKScreener-Inspired Signals

Paste this whole document into Cursor as the task brief.

---

## Context

Phase A added order book, big trades, and market mood data to the dossier. This phase upgrades the existing "chart reading" section — currently basic moving averages/RSI — with a proper indicator library and several new signals inspired by PKScreener's scan logic, specifically to support the Box (Darvas) and Winners (CANSLIM/Livermore) strategies later.

**This phase only computes and stores these new fields on each dossier. It does not yet change how any strategy's funnel filters stocks — that's the next phase.** Keep this phase scoped to computation only.

---

## Goal

1. Replace/extend the custom technical calculations in `fetch_technicals.py` with `pandas-ta` (fallback to `TA-Lib` if a specific indicator isn't available in pandas-ta)
2. Add four new PKScreener-inspired signals to the dossier's chart reading section
3. Add named chart pattern detection

---

## Part 1 — pandas-ta integration

**File:** `ingestion/fetch_technicals.py`

- Install and use `pandas-ta` for standard indicators: RSI(14), MACD, ADX, Bollinger Bands, ATR, moving averages (20/50/200-day).
- Keep any existing custom-calculated fields that already work correctly — this is an upgrade/extension, not a rewrite. If a custom field and a pandas-ta equivalent overlap, prefer pandas-ta's version and log the change.
- ATR (Average True Range) is required for Part 2 below — make sure it's computed.

---

## Part 2 — New signal: Consolidation percentage ("is it in a box")

Add a field that measures how tight the stock's recent price range is.

**Definition (PKScreener default, use as starting point):**
- Lookback window: 22 trading days
- `consolidation_percentage = (highest_high - lowest_low) / lowest_low * 100` over that window
- A stock is considered "tightly consolidating" when this value is ≤ 10%

**Add to dossier:**
```json
"chart_shape": {
  "consolidation_percentage": 0.0,
  "is_consolidating": true
}
```

---

## Part 3 — New signal: Breakout volume ratio

Add a field that checks whether a price breakout is backed by real volume, not noise.

**Definition (PKScreener default):**
- `volume_ratio = today's volume / average volume over the last 22 trading days`
- A breakout is considered volume-confirmed when `volume_ratio >= 2.5`

**Add to dossier (same `chart_shape` object):**
```json
"volume_ratio": 0.0,
"volume_confirmed_breakout": true
```

---

## Part 4 — New signal: Stage 2 uptrend flag (Weinstein stage analysis)

This is a pre-filter to avoid treating a "breakout" inside a long-term downtrend as meaningful.

**Simplified Stage 2 definition to implement:**
A stock is in Stage 2 (confirmed uptrend) when ALL of the following are true:
- Price is above its 30-week (150-day) moving average
- The 150-day moving average itself is trending upward (compare its value today vs. 20 trading days ago)
- Price is at least 25% above its 52-week low

**Add to dossier (same `chart_shape` object):**
```json
"stage": "stage2_uptrend | not_stage2"
```

---

## Part 5 — Named chart pattern detection

Add basic detection for a small, high-value set of patterns (don't try to implement all of PKScreener's patterns — just these four to start):

- **Inside Bar**: today's high/low range is fully contained within yesterday's high/low range
- **NR4 (Narrow Range 4)**: today's high-low range is the narrowest of the last 4 trading days
- **52-week high breakout**: today's close is a new 52-week high
- **52-week low breakout**: today's close is a new 52-week low

**Add to dossier (same `chart_shape` object):**
```json
"patterns": ["inside_bar", "nr4", "52w_high_breakout"]
```
(array of whichever patterns are currently true — can be empty)

---

## Full `chart_shape` object shape (combining Parts 2-5)

```json
"chart_shape": {
  "consolidation_percentage": 0.0,
  "is_consolidating": true,
  "volume_ratio": 0.0,
  "volume_confirmed_breakout": true,
  "stage": "stage2_uptrend",
  "patterns": ["inside_bar", "nr4"]
}
```

---

## Logging

Use the `[FETCH]` or a new `[TECHNICALS]` prefix. For each stock, log a one-line summary, e.g.:
```
[TECHNICALS] RELIANCE — consolidation 8.2%, volume_ratio 1.4x, stage2_uptrend, patterns: none
[TECHNICALS] TATASTEEL — consolidation 12.1%, volume_ratio 3.1x, not_stage2, patterns: nr4
```

---

## Acceptance criteria

- [ ] `pandas-ta` is integrated and producing RSI/MACD/ADX/ATR/moving averages for all 200 stocks
- [ ] Every dossier has a `chart_shape` object with all fields from the combined shape above
- [ ] Values are sane (e.g. `consolidation_percentage` and `volume_ratio` are never negative, `stage` is always one of the two allowed strings)
- [ ] A quick manual check on 2-3 known trending stocks and 2-3 known range-bound stocks shows plausible `is_consolidating` / `stage` values
- [ ] No changes made to any strategy funnel logic — this phase only adds fields to the dossier
- [ ] Existing dossier fields from Phase 0/1/2/3 and Phase A remain unchanged