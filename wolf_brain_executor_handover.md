# Wolf Capital — Wolf Brain + Wolf Executor Handover Prompt
## New Deployment Flow + Daily Cron (Autonomous Mode Only)

Paste this whole document into Cursor as the task brief.

---

## Context

The data ingestion pipeline (Phases A-D) already produces a daily, per-strategy
shortlist cache of scored candidates. This phase builds **two new modules** —
`wolf_brain.py` (the LLM judgment layer) and `wolf_executor.py` (the
deterministic execution layer) — and wires them into the deployment flow and a
new daily cron, replacing whatever stock-selection/trade-execution logic
currently exists.

**Before writing any new code, investigate the existing codebase first:**
1. Find whatever code currently handles (a) picking stocks / deciding trades and
   (b) executing trades, regardless of what those files/functions are actually
   named — this project has some earlier version of this logic, but it's
   non-functional and not necessarily named `wolf_brain`/`wolf_executor`.
2. Find how the current new-wolf deployment flow calls into that logic, and any
   existing cron job that runs daily trading logic.
3. **Report back a short summary** of what you found — file names, what they
   currently do, and exactly where they're wired in — before writing any new
   code. Do not assume file names; confirm them from the actual repository.
4. Once confirmed, create the new `wolf_brain.py` and `wolf_executor.py` per the
   contracts below, and rewire the deployment flow + daily cron to call them
   instead of the old logic.
5. **Do not silently delete the old files.** Flag which old files/functions
   become unused once the rewiring is done, and ask before removing them — they
   might still be referenced elsewhere (e.g. tests, other scripts) that aren't
   obvious from a first pass.

The prompt(s) inside the new Wolf Brain are a full, fresh design against the
input/output contracts below — they should be written to match the current
dossier/shortlist/guardrail data shapes, not adapted from whatever prompt
existed in the old code.

**Advisory mode is explicitly out of scope for this phase** — every wolf currently
operates autonomously (Wolf Executor executes whatever Wolf Brain decides,
immediately, no manual confirmation step). Advisory mode is a planned future
addition; do not build any branching for it now.

**This is still paper trading only.** `mode="real"` should exist as a stub in
Wolf Executor (clearly marked, not implemented) — do not wire any actual Zerodha
order placement in this phase.

---

## Part 0 — Compatibility check (do this BEFORE the Supabase migration is run)

Before any new code from this handover is written, search the existing codebase
for:
1. Any existing calls that insert into `selection_runs` using the old
   `run_type` values (`'morning_deploy'` or `'post_close_review'`) — these will
   break once the constraint changes to `('birth', 'daily_review')`. Update any
   such call sites to use the new values, or remove them if they're leftover/
   unused code from an earlier design pass.
2. Any existing code reading `selection_runs.run_type` and branching on the old
   string values (e.g. `if run_type == "morning_deploy"`) — update these too.
3. Confirm there is **no existing code path that writes to `wolf_holdings`
   without going through a shared insert function** — if inserts happen in
   multiple places, `stop_loss` needs to be added consistently to all of them,
   not just the new Wolf Brain/Executor flow.

Report back what was found (even if nothing was found) before proceeding to
Part 1. If existing `selection_runs` rows already exist in the database with old
`run_type` values, flag this explicitly — those rows need a one-time `UPDATE`
before the Supabase constraint migration can run without failing (see the
migration SQL provided separately).

---

## Part 1 — Wolf Brain

**File:** new `wolf_brain.py`, per the investigation step in Context above.

A single function, `run_wolf_brain(wolf_id, mode, ...)`, with two operating modes
that share the same guardrail/budget logic but use different prompts.

### Inputs (both modes)

```json
{
  "wolf_id": "wolf101",
  "mode": "deploy" | "daily_review",
  "trade_strategy": "value",
  "guardrails": {
    "stop_loss_pct": 15.0,
    "max_daily_loss_pct": 5.0,
    "max_capital_deployed_pct": 100.0,
    "max_per_stock_pct": 40.0,
    "min_trade_value": 1000
  },
  "cash_available": 8200,
  "shortlist": "today's cached, scored shortlist for this strategy",
  "market_context": { "nifty_trend": "...", "vix": 13.2, "fii_dii_mood": "..." }
}
```

`daily_review` mode additionally includes:
```json
{
  "current_holdings": [
    {"symbol": "ITC", "quantity": 12, "avg_buy_price": 412.50, "current_price": 438.00,
     "target": 475.00, "stop_loss": 350.63, "unrealized_pl_pct": 6.2, "days_held": 4}
  ],
  "birth_intent": "the wolf's original thesis, fetched from wolves.birth_intent"
}
```

`cash_available` and `current_holdings` are **always computed deterministically
by the caller** (deploy flow or cron), never estimated by the LLM.

### Output — `mode="deploy"`

```json
{
  "birth_intent": "Value strategy wolf born 2026-07-15. Market context: Nifty consolidating...",
  "picks": [
    {"symbol": "ITC", "quantity": 12, "buy_price": 412.50, "target": 475.00,
     "stop_loss": 350.63, "conviction": 78, "reasoning": "..."}
  ]
}
```

### Output — `mode="daily_review"`

```json
{
  "holdings_review": [
    {"symbol": "ITC", "verdict": "hold", "reasoning": "..."},
    {"symbol": "TCS", "verdict": "sell", "reasoning": "..."}
  ],
  "new_picks": [
    {"symbol": "INFY", "quantity": 3, "buy_price": 1840.00, "target": 2050.00,
     "stop_loss": 1564.00, "conviction": 71, "reasoning": "..."}
  ],
  "current_intent": "one-sentence summary of today's overall stance",
  "daily_update": "a fuller paragraph explaining what was reviewed and why"
}
```

**Wolf Brain never outputs `cash_remaining` in either mode** — it only proposes
actions. All cash math happens in Wolf Executor, deterministically, after
execution — this is intentional, it's what prevents a proposal from ever
resulting in a negative balance.

### Prompt requirements

- **Budget constraint, stated explicitly in the prompt**: the total value of all
  proposed buys (`quantity × buy_price` summed across `picks`/`new_picks`) must
  not exceed `cash_available`. This is a first line of defense — Wolf Executor
  independently re-checks it regardless.
- **Minimum trade size, stated explicitly in the prompt**: never propose a
  position below `guardrails.min_trade_value` — skip a candidate entirely rather
  than take a token-sized position in it.
- **`daily_review` mode bias**: since stop-loss is now an automatic, always-on
  guardrail enforced by Wolf Executor on every trade, the LLM's `sell` verdicts
  should be strategic only (target hit, thesis broken, clearly better
  opportunity) — not risk-cutting. Default to `"hold"` unless there's a real,
  stated reason to change a position. The prompt should say this plainly: do not
  take harsh or frequent action just to appear active.
- **`daily_review` mode must reference `birth_intent`** in its reasoning, so
  `current_intent` stays anchored to the wolf's original thesis rather than
  drifting silently day to day.

---

## Part 2 — Wolf Executor

**File:** new `wolf_executor.py`, per the investigation step in Context above.

A single function, `run_wolf_executor(wolf_id, mode, sells, buys)`. **No LLM
calls anywhere in this file** — purely deterministic.

### Input

```json
{
  "wolf_id": "wolf101",
  "mode": "paper" | "real",
  "sells": [{"symbol": "TCS", "quantity": 3, "reason": "Hit target price..."}],
  "buys": [{"symbol": "INFY", "quantity": 3, "buy_price": 1840.00,
            "target": 2050.00, "stop_loss": 1564.00}]
}
```

### Guardrail checks, applied in this order, before executing anything

1. **Round every proposed buy quantity down to a whole share** and recompute its
   actual cost — Indian equity delivery has no fractional shares.
2. **Minimum trade value** — reject (don't execute, log to `actions_rejected`)
   any buy below `guardrails.min_trade_value` after rounding.
3. **Max per stock %** — reject any buy that would make a single position exceed
   `guardrails.max_per_stock_pct` of total portfolio value.
4. **Max capital deployed %** — reject any buy that would push total deployed
   capital above `guardrails.max_capital_deployed_pct`.
5. **Never exceed available cash** — process buys in the order given; if a buy
   would push the running cash balance negative, reject that specific buy (and
   only that one) and continue evaluating the rest. **This is the hard rule that
   prevents the negative-balance scenario — enforce it in code, not just in the
   prompt.**
6. **Max daily loss %** — if today's realized + unrealized loss already exceeds
   `guardrails.max_daily_loss_pct`, reject all new buys for the rest of the run
   (sells/exits still proceed normally).

Sells are generally not blocked by these guardrails (closing a position is
usually safe) — but still validate the symbol is actually held before executing.

### Output

```json
{
  "wolf_id": "wolf101",
  "executed_at": "2026-07-15T09:27:14+05:30",
  "actions_taken": [
    {"action": "SELL", "symbol": "TCS", "quantity": 3, "price": 3620.50,
     "value": 10861.50, "status": "filled"},
    {"action": "BUY", "symbol": "INFY", "quantity": 3, "price": 1838.20,
     "value": 5514.60, "stop_loss_placed": true, "status": "filled"}
  ],
  "actions_rejected": [
    {"symbol": "WIPRO", "reason": "Would exceed max_per_stock guardrail (40% of budget)"}
  ],
  "cash_before": 1800.00,
  "cash_after": 7146.90,
  "portfolio_value_before": 45200.00,
  "portfolio_value_after": 45184.30,
  "guardrail_checks": {"max_per_stock": "pass", "max_capital_deployed": "pass",
                        "max_daily_loss": "pass", "min_trade_value": "pass"},
  "summary": "Sold TCS (3 @ ₹3620.50 = ₹10,861.50). Bought INFY (3 @ ₹1838.20 = ₹5,514.60). Cash: ₹1,800.00 → ₹7,146.90."
}
```

**The `summary` field must always state every number explicitly** (quantities,
prices, values, cash before/after) — this is what feeds the Activity page, so it
needs to be readable and precise on its own, not just a vague description.

For `mode="real"`: add a clearly marked stub function (e.g.
`_place_kite_order(...)`) that raises `NotImplementedError` for now — do not
attempt real Zerodha integration in this phase.

---

## Part 3 — New wolf deployment flow

**File:** `deploy/deploy_wolf.py` (or wherever the existing deploy flow lives —
extend it, don't rewrite from scratch)

1. User selects strategy, budget, guardrails, hits deploy
2. Fetch today's cached shortlist for that strategy + market context
3. Call `run_wolf_brain(wolf_id, mode="deploy", cash_available=budget, ...)`
4. Call `run_wolf_executor(wolf_id, mode="paper", buys=brain_output["picks"], sells=[])`
5. Write `birth_intent` to `wolves.birth_intent` (once, permanent)
6. Write `wolf_holdings` (with `stop_loss` now populated), `trades`,
   `wolf_intents` (`intent_type='birth'`, one row per stock), and
   `selection_runs` (`run_type='birth'`)

---

## Part 4 — Daily cron

**File:** `cron/wolf_daily_cron.py`

Runs once daily, **9:20–9:30 AM IST** (after market open, giving a buffer past
the most volatile opening minutes). No AMO, no event-based triggers, no separate
decide/execute crons — one cron, real orders during live market hours.

```
for each wolf:
    compute cash_available (deterministic, from DB)
    compute current_holdings with fresh prices (deterministic)
    fetch today's cached shortlist for this wolf's strategy
    call run_wolf_brain(wolf_id, mode="daily_review", cash_available=..., current_holdings=..., ...)
    call run_wolf_executor(wolf_id, mode="paper",
                            sells=[from holdings_review where verdict=="sell"],
                            buys=brain_output["new_picks"])
    write wolf_holdings/trades updates from executor's actions_taken
    write wolf_intents: one 'adjustment' row per sell/buy, one 'eod' row with
        current_intent as rationale
    write selection_runs (run_type='daily_review'), storing the full
        daily_update text in gemini_raw_response
```

**Note: do not skip the daily review even when `cash_available` is low.**
Holdings review (hold/sell decisions) must run every day regardless of cash —
that's in fact how cash *becomes* available. Only the `new_picks` portion is
naturally limited by available funds (Wolf Brain simply won't propose new buys
it can't afford, and Wolf Executor backstops this regardless).

---

## Part 5 — Activity page data

For each wolf action (birth or daily), the Activity page should display:
```
[timestamp] current_intent (or birth_intent summary)
            daily_update / birth reasoning (the "why")
            Executor summary: exact quantities, prices, values, cash before → after (the "what")
```
Both halves (Brain's narrative + Executor's numeric summary) should be shown
together, sourced from `wolf_intents.rationale` and that run's
`selection_runs.gemini_raw_response` / executor output respectively.

---

## Acceptance criteria

- [ ] Wolf Brain never outputs `cash_remaining` — only proposes actions
- [ ] Wolf Executor rejects (not crashes on) any action that would exceed
      available cash, and logs the rejection with a clear reason
- [ ] Fractional share quantities are rounded down before any guardrail check
- [ ] A deploy run correctly writes `birth_intent` once and never overwrites it
      on subsequent daily runs
- [ ] A daily run with zero actionable changes still writes one `eod`
      `wolf_intents` row with a sensible `current_intent`/`daily_update`
- [ ] The Executor's `summary` string contains exact numbers for every action,
      not vague language
- [ ] Manually triggering a scenario where a proposed buy would exceed cash
      confirms the buy is rejected, not partially executed or allowed to go
      negative
- [ ] No live Zerodha calls occur anywhere — `mode="real"` remains a stub