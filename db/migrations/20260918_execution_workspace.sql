-- Paper / Live execution workspace (user preference + per-wolf mode stamp)

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS execution_workspace TEXT NOT NULL DEFAULT 'paper';

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_execution_workspace_check;
ALTER TABLE users ADD CONSTRAINT users_execution_workspace_check
    CHECK (execution_workspace IN ('paper', 'live'));

-- Allow same wolf name in paper vs live pools
ALTER TABLE wolves DROP CONSTRAINT IF EXISTS uq_wolves_user_name;
ALTER TABLE wolves ADD CONSTRAINT uq_wolves_user_name_mode
    UNIQUE (user_id, wolf_name, mode);
