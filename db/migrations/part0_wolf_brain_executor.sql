-- Part 0 — Wolf Brain + Executor compatibility migration (Supabase SQL editor)
-- Run once before implementing wolf_brain.py / wolf_executor.py.
--
-- Audit summary (2026-07-15):
--   • No Python code called save_selection_run() with old run_type values.
--   • No code branches on selection_runs.run_type strings.
--   • wolf_holdings writes go only through db/repository.upsert_holding() (seed script).
--   • Live SQLite bots/trades path is separate — unchanged by this migration.
--
-- If selection_runs already has rows with legacy run_type values, backfill first:

UPDATE selection_runs
SET run_type = 'daily_review'
WHERE run_type IN ('morning_deploy', 'post_close_review');

-- Replace run_type CHECK constraint (Postgres names inline checks selection_runs_run_type_check)

ALTER TABLE selection_runs
    DROP CONSTRAINT IF EXISTS selection_runs_run_type_check;

ALTER TABLE selection_runs
    ADD CONSTRAINT selection_runs_run_type_check
    CHECK (run_type IN ('birth', 'daily_review'));

COMMENT ON COLUMN selection_runs.run_type IS
    'birth = initial deploy run; daily_review = scheduled daily brain+executor cycle';

-- Wolf Executor stores stop-loss on each open position (enforced in code, not just LLM text)

ALTER TABLE wolf_holdings
    ADD COLUMN IF NOT EXISTS stop_loss NUMERIC(12,2);

COMMENT ON COLUMN wolf_holdings.stop_loss IS
    'Stop-loss price frozen at buy time; Wolf Executor enforces exits against this level';

-- Document min_trade_value in guardrails JSON (app-level; no column change)

COMMENT ON COLUMN wolves.guardrails IS
    'User-chosen thresholds at deploy, e.g. stop_loss_pct, max_per_stock_pct, '
    'max_capital_deployed_pct, max_daily_loss_pct, min_trade_value';
