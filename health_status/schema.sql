-- Phase E — daily pipeline health status (Supabase / Postgres)
CREATE TABLE IF NOT EXISTS health_status (
    date            DATE PRIMARY KEY,
    started_at      TIMESTAMPTZ,
    stages          JSONB NOT NULL DEFAULT '{}'::jsonb,
    overall_status  TEXT
);

CREATE INDEX IF NOT EXISTS idx_health_status_started_at
    ON health_status (started_at DESC NULLS LAST);

COMMENT ON TABLE health_status IS
    'Incremental morning-ingestion stage status for ops dashboard';

-- Cron writes use SUPABASE_DATABASE_URL (psycopg2) and bypass RLS.
-- Optional REST upserts: set HEALTH_STATUS_USE_SUPABASE_REST=1 and use a
-- service-role key (or add an explicit write policy for that role).
