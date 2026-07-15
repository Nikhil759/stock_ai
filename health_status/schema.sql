-- Phase E — pipeline run history (Supabase / Postgres)
-- Each pipeline invocation is one row; multiple runs per calendar day are allowed.

CREATE TABLE IF NOT EXISTS health_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_date        DATE NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    stages          JSONB NOT NULL DEFAULT '{}'::jsonb,
    overall_status  TEXT
);

CREATE INDEX IF NOT EXISTS idx_health_runs_started_at
    ON health_runs (started_at DESC);

CREATE INDEX IF NOT EXISTS idx_health_runs_run_date_started
    ON health_runs (run_date, started_at DESC);

COMMENT ON TABLE health_runs IS
    'Incremental morning-ingestion stage status per pipeline run (ops dashboard)';

-- Legacy one-row-per-day table (deprecated — kept for backfill only)
CREATE TABLE IF NOT EXISTS health_status (
    date            DATE PRIMARY KEY,
    started_at      TIMESTAMPTZ,
    stages          JSONB NOT NULL DEFAULT '{}'::jsonb,
    overall_status  TEXT
);

-- One-time backfill from legacy rows (safe to re-run)
INSERT INTO health_runs (run_date, started_at, finished_at, stages, overall_status)
SELECT
    date,
    COALESCE(started_at, date::timestamptz),
    COALESCE(started_at, date::timestamptz),
    stages,
    overall_status
FROM health_status hs
WHERE NOT EXISTS (
    SELECT 1
    FROM health_runs hr
    WHERE hr.run_date = hs.date
      AND hr.started_at = COALESCE(hs.started_at, hs.date::timestamptz)
);

-- Cron writes use SUPABASE_DATABASE_URL (psycopg2) and bypass RLS.
-- Optional REST upserts: set HEALTH_STATUS_USE_SUPABASE_REST=1 and use a
-- service-role key (or add an explicit write policy for that role).
