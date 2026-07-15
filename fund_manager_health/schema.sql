-- Daily fund manager run history (one row per wolf per cron invocation).

CREATE TABLE IF NOT EXISTS fund_manager_runs (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_date          DATE NOT NULL,
    wolf_id           TEXT NOT NULL REFERENCES wolves(wolf_id),
    run_type          TEXT NOT NULL DEFAULT 'daily_review'
                      CHECK (run_type IN ('daily_review')),
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    stages            JSONB NOT NULL DEFAULT '{}'::jsonb,
    overall_status    TEXT,
    error_detail      TEXT,
    selection_run_id  INTEGER REFERENCES selection_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_fm_runs_wolf_date
    ON fund_manager_runs (wolf_id, run_date DESC, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_fm_runs_date_started
    ON fund_manager_runs (run_date, started_at DESC);

COMMENT ON TABLE fund_manager_runs IS
    'Per-wolf daily review (Wolf Brain + Executor) status for ops health dashboard';
