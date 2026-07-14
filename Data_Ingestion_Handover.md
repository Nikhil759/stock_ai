# Wolf Capital — Phase A Handover Prompt
## New Data Fetchers + NSE Anti-Bot Fix

Paste this whole document into Cursor as the task brief.

---

## Context

Wolf Capital is an AI-powered paper-trading fund manager for NSE stocks. Each of 200 Nifty 200 stocks gets a "dossier" — a JSON file combining fundamentals, technicals, ownership, news, and events. This phase adds three new data categories to the dossier and fixes a known reliability issue with existing NSE data fetching.

**This is paper trading only.** No live order placement is involved in this phase — do not add any Kite order-placement code.

---

## Goal

1. Fix NSE data fetching reliability (nsepython/nsefin currently get blocked when run from cloud infrastructure)
2. Add three new fetchers: Kite order book/circuit data, big trades (bulk/block/insider), market mood (FII/DII flow)
3. Merge new fields into the existing dossier JSON structure without breaking existing fields

---

## Part 1 — Fix NSE fetch reliability

**Problem:** NSE's website blocks requests it identifies as coming from cloud/datacenter infrastructure. This affects any code using nsepython or nsefin.

**Requirements:**
- Every NSE-facing fetch function must first establish a session by requesting the NSE homepage (`https://www.nseindia.com`) to acquire valid cookies, before calling any API endpoint.
- Set realistic headers on every request: a real browser `User-Agent`, `Accept-Language`, and a `Referer` matching the page the data would normally be viewed from.
- Add rate limiting: minimum 1-2 second delay between consecutive NSE requests. Do not fire requests in a tight loop across 200 stocks.
- Add retry logic with exponential backoff (e.g. 3 attempts, doubling delay) for any NSE request that fails or times out.
- Log every NSE fetch attempt and outcome (success/failure/retried) using the `[FETCH]` prefix convention.
- If a request still fails after retries, log it clearly and continue — one stock's failure must never crash the whole run.

**Test this locally first**, before deploying to Railway, to isolate code bugs from infrastructure/IP issues.

---

## Part 2 — New fetcher: Order book & circuit limits (Kite Connect)

**File:** `ingestion/fetch_orderbook.py`

For each stock, use the Kite Connect quote endpoint to fetch:
- 5-level bid/ask market depth (price, quantity, order count per level, both sides)
- Upper and lower circuit limits
- Current volume and average traded price

**Output shape to add to each stock's dossier:**
```json
"order_book": {
  "depth": {
    "buy": [{"price": 0, "quantity": 0, "orders": 0}, ...5 levels],
    "sell": [{"price": 0, "quantity": 0, "orders": 0}, ...5 levels]
  },
  "circuit_limit_upper": 0,
  "circuit_limit_lower": 0,
  "volume": 0,
  "average_traded_price": 0,
  "fetched_at": "ISO timestamp"
}
```

---

## Part 3 — New fetcher: Big trades (bulk deals, block deals, insider trading)

**File:** `ingestion/fetch_bigmoves.py`

Use nsefin (preferred) with nsepython as fallback if a specific data point isn't available in nsefin. Apply the Part 1 reliability fixes to all calls here.

Fetch, per stock, for the most recent trading day(s) available:
- Bulk deals (quantity, price, buyer/seller side if available)
- Block deals (same fields)
- Insider trading disclosures (transaction type, quantity, date)

**Output shape to add to each stock's dossier:**
```json
"big_trades": {
  "bulk_deals": [{"date": "", "quantity": 0, "price": 0, "side": "buy|sell"}],
  "block_deals": [{"date": "", "quantity": 0, "price": 0}],
  "insider_trades": [{"date": "", "type": "buy|sell", "quantity": 0, "person_category": ""}],
  "fetched_at": "ISO timestamp"
}
```
If no bulk/block/insider activity exists for a stock on a given day, use empty arrays — this is a normal, expected state, not an error.

---

## Part 4 — New fetcher: Market mood (FII/DII daily flow)

**File:** `ingestion/fetch_marketmood.py`

This is **market-wide, not per-stock** — fetch once per day, not once per stock.

Fetch the day's combined FII/FPI and DII net buy/sell activity across NSE/BSE/MSEI.

**Output shape — a single market-context file, not part of individual stock dossiers:**
```json
{
  "date": "2026-07-14",
  "fii_net": 0,
  "dii_net": 0,
  "fetched_at": "ISO timestamp"
}
```
Save this as its own file (e.g. `market_context/mood_{date}.json`) so all 200 stock dossiers and all four strategies can reference the same market-wide figure without duplicating it 200 times.

---

## Part 5 — Merge into dossier builder

**File:** `ingestion/build_dossier.py`

- Update the merge step so `order_book` and `big_trades` are added as new top-level keys in each stock's existing dossier JSON, alongside the existing basics/technicals/ownership/news sections.
- Do not modify or remove any existing dossier fields.
- If any new fetcher fails for a given stock, the dossier should still be produced with the existing fields intact and the failed section explicitly marked, e.g. `"order_book": null` with a logged reason — never silently omit the key.

---

## Acceptance criteria

- [ ] Running the full 200-stock ingestion locally completes without crashing, even if some NSE calls fail
- [ ] Console logs clearly show `[FETCH]` status for every source, per stock, with retry attempts visible
- [ ] A sample dossier JSON contains valid `order_book` and `big_trades` sections with the shapes above
- [ ] `market_context/mood_{date}.json` is created once per run, not per stock
- [ ] Existing dossier fields (basics, technicals, ownership, news) are unchanged and still present
- [ ] No Kite order-placement code is introduced anywhere in this phase