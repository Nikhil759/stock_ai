# Part 0 — Wolf Brain + Executor compatibility audit

Completed before implementing `wolf_brain.py` / `wolf_executor.py`.

## 1. `selection_runs` inserts with old `run_type`

| Finding | Detail |
|---------|--------|
| **No production call sites** | `save_selection_run()` in `db/repository.py` is never invoked from app/cron code. |
| **Only definition + docs** | Referenced in `Wolf_Capital_Handover_phase1.md`. |
| **Seed script** | `scripts/seed_test_data.py` deletes `selection_runs` rows but does not insert with old types. |

**Action taken:** Added `RUN_TYPE_BIRTH` / `RUN_TYPE_DAILY_REVIEW` constants and validation in `save_selection_run()`. Updated schema CHECK to `('birth', 'daily_review')`.

**Not changed (unrelated to DB):** `backend/fund_manager/gates.py` logs ledger event `"morning_deploy"` — this is an activity-log label, not `selection_runs.run_type`.

## 2. Code branching on old `run_type` strings

**Finding:** None. No `if run_type == "morning_deploy"` (or similar) anywhere in Python.

## 3. `wolf_holdings` write paths

| Path | Writes holdings? |
|------|------------------|
| `db/repository.upsert_holding()` | Yes — **only** Supabase path |
| `db/repository.close_holding()` | Updates status only |
| `backend/database.py` `execute_buy()` | SQLite `trades` — **separate** legacy stack |

**Finding:** No scattered Supabase `wolf_holdings` inserts outside `upsert_holding()`.

**Action taken:** Added `stop_loss` column (migration + schema) and `stop_loss` parameter to `upsert_holding()`. Updated `scripts/seed_test_data.py` to pass `stop_loss`.

## 4. Existing DB rows with legacy `run_type`

Cannot verify from repo alone. Before running the migration on Supabase:

```sql
SELECT run_type, COUNT(*) FROM selection_runs GROUP BY run_type;
```

If any `morning_deploy` or `post_close_review` rows exist, `db/migrations/part0_wolf_brain_executor.sql` backfills them to `daily_review` before altering the constraint.

## Migration to apply

Run in Supabase SQL editor:

**`db/migrations/part0_wolf_brain_executor.sql`**

## Dual-stack note (not Part 0 scope)

Live Wolf deploy still uses **SQLite** (`backend/database.py`, `backend/bot.py`, `fund_manager/`). Part 1+ will introduce Supabase-backed brain/executor; this migration prepares the Postgres schema only.

## Ready for Part 1?

Yes — schema, repository contracts, and audit are complete. Apply the migration on Supabase, then proceed to `wolf_brain.py`.
