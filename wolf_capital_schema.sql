-- ============================================================
-- WOLF CAPITAL — POSTGRES SCHEMA (v1 draft)
-- ============================================================
-- Design principles carried over from architecture notes:
--   - "Brain judges, engine executes" -> keep an audit trail of
--     every Gemini judgment call, separate from executed trades.
--   - Strategy-lock per bot -> strategies is a fixed lookup table.
--   - Fresh data over storage -> only persist state that changes
--     over time (trades, intents, snapshots), not live prices.
--   - Paper trading first -> every trade/wolf carries a mode flag.
-- ============================================================


-- ------------------------------------------------------------
-- 1. USERS
-- ------------------------------------------------------------
-- Login is handled by Supabase Auth (Google OAuth), not by us.
-- Supabase already maintains auth.users (id, email, provider,
-- etc.) — this table just extends that with app-specific fields,
-- keyed on the same id. kite_user_id stays nullable until the
-- user actually links their Zerodha account.
CREATE TABLE users (
    id              UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    kite_user_id    TEXT UNIQUE,              -- Zerodha client ID, set once linked
    name            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active       BOOLEAN NOT NULL DEFAULT true
);

-- Optional but recommended: auto-create a row here whenever someone
-- signs up via Supabase Auth, so you never have to remember to do
-- it manually in app code.
CREATE OR REPLACE FUNCTION handle_new_auth_user() RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO public.users (id, name)
    VALUES (NEW.id, NEW.raw_user_meta_data->>'full_name');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

CREATE TRIGGER trg_new_auth_user
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION handle_new_auth_user();


-- ------------------------------------------------------------
-- 2. STRATEGIES (fixed lookup — Value / Winners / Box / Dip)
-- ------------------------------------------------------------
CREATE TABLE strategies (
    strategy_code   TEXT PRIMARY KEY,          -- 'VALUE','WINNERS','BOX','DIP'
    display_name    TEXT NOT NULL,             -- "Buy Cheap Quality Companies"
    holding_style   TEXT NOT NULL,             -- long-term / positional / swing
    description     TEXT
);

INSERT INTO strategies (strategy_code, display_name, holding_style, description) VALUES
('VALUE',   'Buy Cheap Quality Companies', 'long-term',  'Graham value investing, fundamental-only'),
('WINNERS', 'Buy the Winners',             'positional', 'CANSLIM + Livermore hybrid'),
('BOX',     'Buy the Box Breakout',        'swing',      'Darvas Box, technical-only'),
('DIP',     'Buy the Dip',                 'swing',      'Connors RSI-2 mean reversion');


-- ------------------------------------------------------------
-- 2b. WOLF_NAME_POOL (predefined default names)
-- ------------------------------------------------------------
-- Used by the app to auto-assign a default name when a user
-- deploys a new wolf without picking one themselves. Names are
-- only unique per-user (see UNIQUE constraint on wolves below) —
-- two different users can each have a bot named "Fenrir".
CREATE TABLE wolf_name_pool (
    name_id     SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    sort_order  INTEGER NOT NULL       -- assignment order: 1st default name, 2nd, etc.
);

INSERT INTO wolf_name_pool (name, sort_order) VALUES
('Alpha', 1), ('Fenrir', 2), ('Luna', 3), ('Shadow', 4), ('Storm', 5),
('Blaze', 6), ('Nova', 7), ('Apex', 8), ('Titan', 9), ('Ghost', 10),
('Ranger', 11), ('Onyx', 12), ('Echo', 13), ('Frost', 14), ('Talon', 15),
('Raven', 16), ('Zephyr', 17), ('Orion', 18), ('Maverick', 19), ('Phantom', 20);

-- App logic: when a user deploys a new wolf, look up how many
-- wolves they already have, then assign the next unused name from
-- this pool by sort_order (wrap around or extend the pool once a
-- user passes 20 bots). If the user types their own name instead,
-- skip this lookup entirely and use their input directly.
--
-- Past 20 bots for one user: wrap around the pool and append a
-- suffix, e.g. "Alpha II", "Fenrir II", etc. on the second pass.


-- ------------------------------------------------------------
-- 3. WOLVES (bot instances)
-- ------------------------------------------------------------
CREATE TABLE wolves (
    wolf_id             TEXT PRIMARY KEY,        -- e.g. 'W0101' (your existing format)
    user_id             UUID NOT NULL REFERENCES users(id),
    wolf_name           TEXT NOT NULL,            -- default from wolf_name_pool, or user-chosen
    strategy_code       TEXT NOT NULL REFERENCES strategies(strategy_code),
    budget_initial      NUMERIC(14,2) NOT NULL,   -- 10,000 test budget
    budget_available    NUMERIC(14,2) NOT NULL,   -- live cash balance
    mode                TEXT NOT NULL DEFAULT 'paper' CHECK (mode IN ('paper','live')),
    status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','closed')),
    guardrails          JSONB NOT NULL DEFAULT '{}'::jsonb,  -- per-bot thresholds, chosen by the user at deploy time. e.g.:
                                                              -- {"max_position_pct": 20, "stop_loss_pct": 8,
                                                              --  "circuit_breaker": {"trigger_pct": 5, "reset_mode": "manual"}}
                                                              -- reset_mode: 'auto' (resumes next trading day) /
                                                              --             'manual' (stays paused till user restarts) /
                                                              --             'cooldown' (auto-resumes after N days, see cooldown_days below)
    circuit_breaker_tripped_at TIMESTAMPTZ,       -- null = not tripped; set when the bot's loss trigger fires
    birth_intent        JSONB,                    -- captured once at creation, never overwritten
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at           TIMESTAMPTZ
);

CREATE INDEX idx_wolves_user ON wolves(user_id);

-- Name only needs to be unique within a user's own bots, not globally
ALTER TABLE wolves ADD CONSTRAINT uq_wolves_user_name UNIQUE (user_id, wolf_name);

-- Strategy-lock for life is a confirmed, permanent rule (not just
-- an app-level convention) — enforce it at the database level so
-- no code path can accidentally change a bot's strategy later.
CREATE OR REPLACE FUNCTION prevent_strategy_change() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.strategy_code IS DISTINCT FROM OLD.strategy_code THEN
        RAISE EXCEPTION 'strategy_code cannot be changed after a wolf is created';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_lock_strategy
    BEFORE UPDATE ON wolves
    FOR EACH ROW EXECUTE FUNCTION prevent_strategy_change();


-- ------------------------------------------------------------
-- 4. WOLF_HOLDINGS (current open positions — normalized instead
--    of the JSON blob in your notes, so you can query/aggregate)
-- ------------------------------------------------------------
CREATE TABLE wolf_holdings (
    holding_id      SERIAL PRIMARY KEY,
    wolf_id         TEXT NOT NULL REFERENCES wolves(wolf_id),
    symbol          TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    avg_buy_price   NUMERIC(12,2) NOT NULL,
    sell_target     NUMERIC(12,2),
    stop_loss       NUMERIC(12,2),           -- frozen at buy; enforced by Wolf Executor
    opened_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at       TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
    UNIQUE (wolf_id, symbol, status)   -- prevents duplicate "open" rows per stock
);

CREATE INDEX idx_holdings_wolf ON wolf_holdings(wolf_id) WHERE status = 'open';

-- current P/L and current value are DERIVED (fresh price x quantity
-- minus cost basis) — don't store them, matches your "fresh data
-- over storage" principle.


-- ------------------------------------------------------------
-- 5. TRADES (immutable execution ledger)
-- ------------------------------------------------------------
CREATE TABLE trades (
    trade_id        SERIAL PRIMARY KEY,
    wolf_id         TEXT NOT NULL REFERENCES wolves(wolf_id),
    symbol          TEXT NOT NULL,
    action          TEXT NOT NULL CHECK (action IN ('BUY','SELL')),
    quantity        INTEGER NOT NULL,
    price           NUMERIC(12,2) NOT NULL,
    kite_order_id   TEXT,                     -- null for paper trades
    mode            TEXT NOT NULL DEFAULT 'paper' CHECK (mode IN ('paper','live')),
    executed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    linked_run_id   INTEGER                   -- FK to selection_runs, set below
);

CREATE INDEX idx_trades_wolf ON trades(wolf_id, executed_at);


-- ------------------------------------------------------------
-- 6. WOLF_INTENTS (replaces the W0101.txt/.md log files)
-- ------------------------------------------------------------
-- Every birth intent, EOD intent, and adjustment gets a row.
-- This is your "separate txt file" from the notes, made queryable.
CREATE TABLE wolf_intents (
    intent_id       SERIAL PRIMARY KEY,
    wolf_id         TEXT NOT NULL REFERENCES wolves(wolf_id),
    intent_date     DATE NOT NULL,
    intent_type     TEXT NOT NULL CHECK (intent_type IN ('birth','eod','adjustment')),
    symbol          TEXT,                      -- null for portfolio-level intents
    conviction_score NUMERIC(5,2),              -- Gemini's 0-100 rubric output
    target_allocation NUMERIC(12,2),
    rationale       TEXT,                       -- Gemini's plain-language reasoning
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_intents_wolf_date ON wolf_intents(wolf_id, intent_date);


-- ------------------------------------------------------------
-- 7. PORTFOLIO_SNAPSHOTS (daily NAV / P&L history)
-- ------------------------------------------------------------
CREATE TABLE portfolio_snapshots (
    snapshot_id     SERIAL PRIMARY KEY,
    wolf_id         TEXT NOT NULL REFERENCES wolves(wolf_id),
    snapshot_date   DATE NOT NULL,
    cash_balance    NUMERIC(14,2) NOT NULL,
    holdings_value  NUMERIC(14,2) NOT NULL,
    total_value     NUMERIC(14,2) NOT NULL,
    daily_pl        NUMERIC(14,2),
    cumulative_pl   NUMERIC(14,2),
    UNIQUE (wolf_id, snapshot_date)
);


-- ------------------------------------------------------------
-- 8. SELECTION_RUNS (audit trail for every Gemini judgment call)
-- ------------------------------------------------------------
-- This is the piece your notes don't have yet but you'll want it
-- fast — when a pick looks wrong, you need to see exactly what
-- Gemini was shown and what it said, per run.
CREATE TABLE selection_runs (
    run_id              SERIAL PRIMARY KEY,
    wolf_id             TEXT NOT NULL REFERENCES wolves(wolf_id),
    run_type            TEXT NOT NULL CHECK (run_type IN ('birth','daily_review')),
    run_date            DATE NOT NULL,
    shortlist_json      JSONB,       -- the ~30 stocks after the math funnel
    final_picks_json    JSONB,       -- the 1-3 stocks Gemini selected
    gemini_raw_response TEXT,        -- full response for debugging
    gate_results        JSONB,       -- 9-gate deploy sequence results, e.g.
                                     -- [{"gate": "budget_check", "passed": true},
                                     --  {"gate": "circuit_breaker", "passed": false, "reason": "..."}]
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE trades
    ADD CONSTRAINT fk_trades_run FOREIGN KEY (linked_run_id) REFERENCES selection_runs(run_id);


-- ------------------------------------------------------------
-- 9. KITE_AUTH_TOKENS (daily access tokens per user)
-- ------------------------------------------------------------
CREATE TABLE kite_auth_tokens (
    user_id         UUID PRIMARY KEY REFERENCES users(id),
    access_token    TEXT NOT NULL,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL
);
-- Structural seam for future TOTP automation: just swap out
-- whatever writes this table, schema doesn't change.


-- ------------------------------------------------------------
-- DELIBERATELY OMITTED (for now):
--
--   notifications   — no alerting system or delivery channel
--                      decided yet; add when that's built, it's
--                      a 5-minute addition later.
--
--   stock_dossiers  — dossiers stay as flat JSON files. The math
--                      funnel already does this filtering in
--                      Python, and dossiers are regenerated fresh
--                      each run anyway. Only revisit this if you
--                      want ad-hoc SQL queries across all 200
--                      stocks, or to join dossier fields (like
--                      sector) directly against holdings/trades
--                      in SQL.
--
--   deploy_gate_logs — folded into selection_runs.gate_results
--                      above instead of a separate table.
-- ------------------------------------------------------------