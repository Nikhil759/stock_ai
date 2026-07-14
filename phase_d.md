# Wolf Capital — Phase D Handover Prompt
## Batch LLM Scoring + Shortlist Cache

Paste this whole document into Cursor as the task brief.

---

## Context

Phase C produced four independent funnel outputs (Value, Winners, Box, Dip), each a list of ~5-55 candidate stocks with their dossiers and `funnel_reasons`. This phase adds the LLM batch-scoring step: each strategy's candidates get scored in batches of 5, on absolute merit, and survivors get saved to a daily shortlist cache. This runs once per day, shared across every bot using that strategy — not per bot.

**No order placement, no bot-specific logic in this phase.** This is still shared daily prep, same as Phases A-C.

---

## Goal

1. Build the batch-scoring LLM call (Gemini, as already integrated) with a strict JSON-only output contract
2. Run it in batches of 5 per strategy, scoring every candidate on absolute merit — never eliminating a stock just because it shares a batch with a stronger one
3. Save buy/watch survivors to a shortlist cache, keyed by strategy and date
4. Fully log every batch's reasoning

---

## Part 1 — Prompt design

**File:** `scoring/batch_scorer.py`

- Use a **shared prompt skeleton** (cacheable) plus a **strategy-specific block** injected per call, exactly as designed earlier — this keeps token cost down since the skeleton doesn't change between calls.
- The strategy-specific block should briefly state that strategy's philosophy (e.g. Value = Graham value investing, fundamental-only) so the LLM's reasoning stays anchored to the right lens for that batch.
- **Include the `funnel_reasons` field from Phase C in the prompt** for each stock — the LLM should see *why* this stock is even a candidate (e.g. "P/E 18.2, debt/equity 0.3, below Graham fair value by 22%"), not just raw dossier data. This avoids the LLM re-deriving what the funnel already established.
- **For Winners specifically**: explicitly flag in the prompt whenever a candidate's `funnel_reasons` shows it passed via the `return_3m` momentum proxy rather than real `earnings_growth_yoy` data (per the Phase C follow-up note) — e.g. `"note": "earnings growth data unavailable; passed on 3-month price momentum only"`. The LLM should factor this into its conviction score — a stock with no real earnings data backing it should generally not score as high as one with confirmed growth, even if both cleared the funnel.
- The LLM must only **reason**, never calculate. All numbers come pre-computed from the dossier — do not ask it to compute ratios, percentages, or scores from raw numbers itself.

---

## Part 2 — Output contract

Strict JSON only, no prose outside the JSON. Per stock in the batch:
```json
{
  "symbol": "ITC",
  "conviction": 78,
  "verdict": "buy",
  "reasoning": "Strong FCF, low debt (0.3 D/E), trading 22% below Graham fair value. Promoter holding stable."
}
```
`verdict` must be one of: `"buy"`, `"watch"`, `"skip"`.
`conviction` is 0-100.

Parse and validate this JSON on the code side — if a response fails to parse or is missing a required field for any stock, log it clearly and retry that batch once before giving up on it (don't silently drop it).

---

## Part 3 — Batching logic

- Split each strategy's funnel survivors into batches of 5.
- Every stock in a batch is scored independently, on its own merit — never framed to the LLM as "pick the best of these 5." The prompt should make clear each stock stands alone.
- If a strategy has fewer than 5 survivors (e.g. Dip on a given day), just run one smaller batch — don't pad it with irrelevant stocks.
- Run batches for each strategy sequentially; strategies themselves can run in any order since they're fully independent.

---

## Part 4 — Shortlist cache

**File:** `cache/shortlist_cache.py`

- Cache key format: `shortlist_{strategy}_{YYYY-MM-DD}` (e.g. `shortlist_value_2026-07-15`)
- Cache contents: every `buy` or `watch` verdict across all batches for that strategy (not just top-of-batch — every survivor, since scoring was on absolute merit)
- Each cached entry includes: symbol, conviction, verdict, reasoning, frozen price (from the dossier at scoring time — this price must NOT be re-fetched later), and the date
- Save/load functions: `save_shortlist(strategy, date, candidates)` and `load_shortlist(strategy, date)`
- If a shortlist for today already exists when the pipeline runs, overwrite it (this run replaces any previous attempt for the same day) rather than appending

---

## Part 5 — Logging

Use `[BATCH SCORING]` and `[SHORTLIST CACHE]` prefixes, e.g.:
```
[BATCH SCORING] Value: batch 1/5 [ITC, HDFCBANK, INFY, TCS, WIPRO]
[BATCH SCORING]   ITC → conviction 78, BUY — "Strong FCF, low debt..."
[BATCH SCORING]   HDFCBANK → conviction 41, SKIP — "Valuation fair but..."
[BATCH SCORING] Winners: batch 3/11 — WIPRO flagged (momentum proxy, no earnings data)
[BATCH SCORING] Value: 25 scored → 7 survivors (buy/watch)
[SHORTLIST CACHE] Saved shortlist_value_2026-07-15 (7 candidates)
```

---

## Orchestration

**File:** `cron/morning_ingestion.py` (extend further)

After Phase C's four funnel outputs are produced:
```python
for strategy, candidates in [("value", value_candidates), ("winners", winners_candidates),
                              ("box", box_candidates), ("dip", dip_candidates)]:
    scored = run_batch_scoring(strategy, candidates)
    save_shortlist(strategy, today, scored)
```

**This phase stops here** — no bot-deploy final-selection call yet (that's Phase G). Just get clean batch scoring and caching working first.

---

## Acceptance criteria

- [ ] Batch scoring runs for all four strategies without crashing, including the case where Dip has very few candidates
- [ ] Output JSON is validated and malformed responses are retried once, not silently dropped
- [ ] Every stock is scored independently — verify by checking a batch where you'd expect two strong stocks together (e.g. two high-conviction Winners candidates in the same batch) and confirming both can score `buy`, not just one
- [ ] Winners candidates that passed via the momentum proxy are visibly flagged in the prompt and their reasoning reflects that caveat
- [ ] Shortlist cache files are created with the correct naming convention and contain frozen prices
- [ ] Full reasoning text is logged for every scored stock, not just the verdict
- [ ] Re-running the pipeline the same day overwrites, rather than duplicates, that day's shortlist