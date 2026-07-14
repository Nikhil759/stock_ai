# Wolf Capital — Phase 1 Handover: Database Layer (Supabase Postgres)

## Context

Wolf Capital is an AI-powered fund manager app for the Indian stock market (NSE/BSE).
We're rebuilding the persistence layer on Supabase Postgres, replacing the old
SQLite + flat-file (`.txt`/`.md`) setup entirely. This is a clean-slate rebuild —
no migration of old data needed.

This handover covers **only the database layer**: schema setup, a Python data
access module, and a validation script. It does NOT cover dossier ingestion,
stock selection logic, or trade execution — those are separate phases that
will be handed over later, so don't build anything beyond what's listed here.

## What's already decided (don't relitigate these)

- **Auth**: Supabase Auth handles login (Google OAuth). Our `users` table
  extends `auth.users` via a shared UUID `id`, populated automatically by a
  DB trigger on signup. Building the actual login screen is a later task —
  today we're only building the backend data layer.
- **Strategy-lock**: a wolf's `strategy_code` can never change after creation.
  Enforced by a DB trigger — don't add app-level logic to prevent this, it's
  already handled at the database level.
- **Fresh data over storage**: live prices and P/L are never stored — they're
  computed on demand from live data. Don't add caching for these values.
- **Mode flag**: every wolf and trade carries a `mode` of `'paper'` or `'live'`.
  We are paper-trading only right now — default everything to `'paper'`.

## Task 1 — Project structure

Create this structure (or adapt sensibly if a different layout already exists
in the repo):

```
wolf_capital/
├── db/
│   ├── schema.sql              <- provided, see below
│   ├── connection.py           <- Postgres connection handling
│   └── repository.py           <- data access functions
├── scripts/
│   └── seed_test_data.py       <- validation script, see Task 3
├── .env.example
├── .env                        <- gitignored, holds real credentials
└── requirements.txt
```

## Task 2 — Schema setup

The full schema DDL is provided in `db/schema.sql` (attached separately —
copy it in as-is). It defines: `users`, `strategies`, `wolf_name_pool`,
`wolves`, `wolf_holdings`, `trades`, `wolf_intents`, `portfolio_snapshots`,
`selection_runs`, `kite_auth_tokens`, plus triggers for auto-creating user
rows on signup and locking strategy changes.

Steps:
1. Add `DATABASE_URL` to `.env` (the Supabase Postgres connection string,
   user will provide this — never hardcode it or commit it).
2. Write a small script or use `psql`/Supabase SQL editor to apply
   `schema.sql` against the database. Confirm all tables, indexes, triggers,
   and the seed data (strategies, wolf_name_pool) are created without errors.
3. If Cursor has the Supabase MCP connector available, prefer using that to
   apply the schema and confirm the result. Otherwise, apply manually via
   the Supabase SQL editor and report back what was run.

## Task 3 — Python data access layer (`db/repository.py`)

Use `psycopg2` for direct Postgres access (add to `requirements.txt`). Keep
these functions simple and single-purpose — no business logic, just "take
this data, put it in / get it out of this table." Business logic (selection,
guardrail checks, etc.) belongs in later phases, not here.

Required functions, at minimum:

- `get_user(user_id: UUID) -> dict | None`
- `create_wolf(user_id, wolf_id, wolf_name, strategy_code, budget_initial, guardrails: dict) -> dict`
- `get_wolf(wolf_id: str) -> dict | None`
- `list_wolves_for_user(user_id: UUID) -> list[dict]`
- `assign_default_wolf_name(user_id: UUID) -> str` — looks up how many wolves
  the user already has, returns the next unused name from `wolf_name_pool`
  by `sort_order`; if the user has more wolves than names in the pool, wrap
  around and append a suffix (e.g. `"Alpha II"`)
- `record_trade(wolf_id, symbol, action, quantity, price, mode='paper', kite_order_id=None, linked_run_id=None) -> dict`
- `upsert_holding(wolf_id, symbol, quantity, avg_buy_price, sell_target=None) -> dict` —
  update if an open position exists for that symbol, else insert
- `close_holding(wolf_id, symbol) -> None`
- `log_intent(wolf_id, intent_date, intent_type, symbol=None, conviction_score=None, target_allocation=None, rationale=None) -> dict`
- `save_snapshot(wolf_id, snapshot_date, cash_balance, holdings_value, total_value, daily_pl=None, cumulative_pl=None) -> dict`
- `save_selection_run(wolf_id, run_type, run_date, shortlist_json=None, final_picks_json=None, gemini_raw_response=None, gate_results=None) -> dict`

Connection handling (`db/connection.py`): a single function that returns a
psycopg2 connection using `DATABASE_URL` from environment variables. Use a
context manager pattern so connections are always closed properly.

## Task 4 — Validation script (`scripts/seed_test_data.py`)

A throwaway script (not part of the permanent app) that proves the schema
and repository layer work end to end. It should, in order:

1. Look up or create one test user row directly (bypass real Supabase Auth
   signup for now — just insert a row with a fixed test UUID for this
   script's purposes).
2. Create one wolf using `create_wolf`, using `assign_default_wolf_name`
   for the name, strategy `'DIP'`, budget ₹10,000.
3. Record one fake trade (a BUY) via `record_trade`.
4. Update holdings via `upsert_holding` to reflect that trade.
5. Log one intent via `log_intent` (`intent_type='birth'`).
6. Save one snapshot via `save_snapshot`.
7. Print out everything that was created, so it can be visually checked
   against the Supabase table editor.
8. Attempt to change the test wolf's `strategy_code` directly via SQL and
   confirm it raises an error (proves the strategy-lock trigger works).
9. Attempt to create a second wolf for the same user with the same
   `wolf_name` and confirm it raises a uniqueness error.

Do not build anything beyond this validation scope — no CLI, no retry logic,
no error recovery. It's a one-time proof that the foundation works.

## Out of scope for this handover (do not build)

- Dossier ingestion / data pipeline changes
- Wolf brain / Gemini selection logic
- Deployment flow / gate logic implementation
- Cron jobs, event triggers, or Kite Connect API wiring
- The actual login/signup UI screen (Supabase Auth handles the mechanics;
  the screen itself is a separate task)
- Notifications (deferred — no table exists for this yet)

These will be handed over separately as their own phases.