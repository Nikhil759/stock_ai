"""Data access functions for Wolf Capital tables. No business logic."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg2.extras import Json, RealDictCursor

from db.connection import get_connection

Row = dict[str, Any]

# selection_runs.run_type — must match db/schema.sql CHECK constraint
RUN_TYPE_BIRTH = "birth"
RUN_TYPE_DAILY_REVIEW = "daily_review"
VALID_RUN_TYPES = frozenset({RUN_TYPE_BIRTH, RUN_TYPE_DAILY_REVIEW})


def _row(result: Any) -> Row | None:
    return dict(result) if result is not None else None


def _rows(results: Any) -> list[Row]:
    return [dict(r) for r in results]


def _to_roman(n: int) -> str:
    """Convert a small positive integer to Roman numerals (for name suffixes)."""
    if n <= 0:
        raise ValueError("roman numeral requires n >= 1")
    mapping = (
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    )
    out: list[str] = []
    remaining = n
    for value, numeral in mapping:
        while remaining >= value:
            out.append(numeral)
            remaining -= value
    return "".join(out)


def get_user(user_id: UUID) -> Row | None:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (str(user_id),))
            return _row(cur.fetchone())


def get_user_by_email(email: str) -> Row | None:
    """Resolve public.users row from auth.users email."""
    normalized = email.strip().lower()
    if not normalized:
        return None
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT u.*
                FROM users u
                JOIN auth.users au ON au.id = u.id
                WHERE lower(au.email) = %s
                """,
                (normalized,),
            )
            return _row(cur.fetchone())


def ensure_user_from_auth_email(email: str) -> Row | None:
    """Create public.users from auth.users if the signup trigger did not run."""
    normalized = email.strip().lower()
    if not normalized:
        return None
    existing = get_user_by_email(normalized)
    if existing:
        return existing
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO users (id, name)
                SELECT
                    au.id,
                    COALESCE(
                        au.raw_user_meta_data->>'full_name',
                        split_part(au.email, '@', 1)
                    )
                FROM auth.users au
                WHERE lower(au.email) = %s
                ON CONFLICT (id) DO NOTHING
                RETURNING *
                """,
                (normalized,),
            )
            row = cur.fetchone()
            if row:
                return dict(row)
    return get_user_by_email(normalized)


def create_wolf(
    user_id: UUID,
    wolf_id: str,
    wolf_name: str,
    strategy_code: str,
    budget_initial: Decimal | float | int,
    guardrails: dict,
) -> Row:
    budget = Decimal(str(budget_initial))
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO wolves (
                    wolf_id, user_id, wolf_name, strategy_code,
                    budget_initial, budget_available, mode, status, guardrails
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'paper', 'active', %s)
                RETURNING *
                """,
                (
                    wolf_id,
                    str(user_id),
                    wolf_name,
                    strategy_code,
                    budget,
                    budget,
                    Json(guardrails),
                ),
            )
            row = cur.fetchone()
            assert row is not None
            return dict(row)


def get_wolf(wolf_id: str) -> Row | None:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM wolves WHERE wolf_id = %s", (wolf_id,))
            return _row(cur.fetchone())


def get_wolf_for_user(wolf_id: str, user_id: UUID) -> Row | None:
    wolf = get_wolf(wolf_id)
    if wolf is None or str(wolf.get("user_id")) != str(user_id):
        return None
    return wolf


def set_wolf_status(wolf_id: str, status: str) -> Row:
    if status not in ("active", "paused", "closed"):
        raise ValueError(f"invalid wolf status {status!r}")
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if status == "closed":
                cur.execute(
                    """
                    UPDATE wolves
                    SET status = %s, closed_at = now()
                    WHERE wolf_id = %s
                    RETURNING *
                    """,
                    (status, wolf_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE wolves
                    SET status = %s, closed_at = NULL
                    WHERE wolf_id = %s
                    RETURNING *
                    """,
                    (status, wolf_id),
                )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"wolf not found: {wolf_id}")
            return dict(row)


def list_intents_for_wolf(wolf_id: str, limit: int = 50) -> list[Row]:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM wolf_intents
                WHERE wolf_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (wolf_id, limit),
            )
            return _rows(cur.fetchall())


def list_holdings_for_wolf(
    wolf_id: str,
    *,
    status: str | None = None,
) -> list[Row]:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if status:
                cur.execute(
                    """
                    SELECT * FROM wolf_holdings
                    WHERE wolf_id = %s AND status = %s
                    ORDER BY opened_at
                    """,
                    (wolf_id, status),
                )
            else:
                cur.execute(
                    """
                    SELECT * FROM wolf_holdings
                    WHERE wolf_id = %s
                    ORDER BY opened_at
                    """,
                    (wolf_id,),
                )
            return _rows(cur.fetchall())


def list_trades_for_wolf(wolf_id: str) -> list[Row]:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM trades
                WHERE wolf_id = %s
                ORDER BY executed_at, trade_id
                """,
                (wolf_id,),
            )
            return _rows(cur.fetchall())


def list_wolves_for_user(user_id: UUID) -> list[Row]:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM wolves
                WHERE user_id = %s
                ORDER BY created_at
                """,
                (str(user_id),),
            )
            return _rows(cur.fetchall())


def list_active_wolves() -> list[Row]:
    """Wolves eligible for evening auto-exit (active only; paused/closed skipped)."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM wolves
                WHERE status = 'active'
                ORDER BY created_at
                """
            )
            return _rows(cur.fetchall())


def assign_default_wolf_name(user_id: UUID) -> str:
    """Next unused name from wolf_name_pool by sort_order; wrap with Roman suffix past pool size."""
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM wolves WHERE user_id = %s",
                (str(user_id),),
            )
            count = int(cur.fetchone()["n"])
            cur.execute(
                "SELECT name FROM wolf_name_pool ORDER BY sort_order ASC"
            )
            names = [r["name"] for r in cur.fetchall()]

    if not names:
        raise RuntimeError("wolf_name_pool is empty")

    pool_size = len(names)
    base = names[count % pool_size]
    cycle = count // pool_size  # 0 = first pass through the pool
    if cycle == 0:
        return base
    # Second pass -> "Alpha II", third -> "Alpha III", etc.
    return f"{base} {_to_roman(cycle + 1)}"


def record_trade(
    wolf_id: str,
    symbol: str,
    action: str,
    quantity: int,
    price: Decimal | float | int,
    mode: str = "paper",
    kite_order_id: str | None = None,
    linked_run_id: int | None = None,
) -> Row:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO trades (
                    wolf_id, symbol, action, quantity, price,
                    mode, kite_order_id, linked_run_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    wolf_id,
                    symbol,
                    action,
                    quantity,
                    Decimal(str(price)),
                    mode,
                    kite_order_id,
                    linked_run_id,
                ),
            )
            row = cur.fetchone()
            assert row is not None
            return dict(row)


def upsert_holding(
    wolf_id: str,
    symbol: str,
    quantity: int,
    avg_buy_price: Decimal | float | int,
    sell_target: Decimal | float | int | None = None,
    stop_loss: Decimal | float | int | None = None,
) -> Row:
    """Update an open position for (wolf_id, symbol), or insert one if none exists."""
    avg = Decimal(str(avg_buy_price))
    target = Decimal(str(sell_target)) if sell_target is not None else None
    stop = Decimal(str(stop_loss)) if stop_loss is not None else None

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT holding_id FROM wolf_holdings
                WHERE wolf_id = %s AND symbol = %s AND status = 'open'
                """,
                (wolf_id, symbol),
            )
            existing = cur.fetchone()
            if existing:
                cur.execute(
                    """
                    UPDATE wolf_holdings
                    SET quantity = %s,
                        avg_buy_price = %s,
                        sell_target = %s,
                        stop_loss = COALESCE(%s, stop_loss)
                    WHERE holding_id = %s
                    RETURNING *
                    """,
                    (quantity, avg, target, stop, existing["holding_id"]),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO wolf_holdings (
                        wolf_id, symbol, quantity, avg_buy_price,
                        sell_target, stop_loss, status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, 'open')
                    RETURNING *
                    """,
                    (wolf_id, symbol, quantity, avg, target, stop),
                )
            row = cur.fetchone()
            assert row is not None
            return dict(row)


def close_holding(wolf_id: str, symbol: str) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE wolf_holdings
                SET status = 'closed', closed_at = now()
                WHERE wolf_id = %s AND symbol = %s AND status = 'open'
                """,
                (wolf_id, symbol),
            )


def list_open_holdings(wolf_id: str) -> list[Row]:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM wolf_holdings
                WHERE wolf_id = %s AND status = 'open'
                ORDER BY symbol
                """,
                (wolf_id,),
            )
            return _rows(cur.fetchall())


def set_budget_available(wolf_id: str, amount: Decimal | float | int) -> Row:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE wolves
                SET budget_available = %s
                WHERE wolf_id = %s
                RETURNING *
                """,
                (Decimal(str(amount)), wolf_id),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"wolf not found: {wolf_id}")
            return dict(row)


def reduce_holding_quantity(wolf_id: str, symbol: str, sell_qty: int) -> Row | None:
    """Decrease open quantity; close row when quantity reaches zero."""
    if sell_qty <= 0:
        raise ValueError("sell_qty must be positive")
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM wolf_holdings
                WHERE wolf_id = %s AND symbol = %s AND status = 'open'
                """,
                (wolf_id, symbol),
            )
            row = cur.fetchone()
            if not row:
                return None
            remaining = int(row["quantity"]) - sell_qty
            if remaining <= 0:
                cur.execute(
                    """
                    UPDATE wolf_holdings
                    SET status = 'closed', closed_at = now(), quantity = 0
                    WHERE holding_id = %s
                    RETURNING *
                    """,
                    (row["holding_id"],),
                )
            else:
                cur.execute(
                    """
                    UPDATE wolf_holdings
                    SET quantity = %s
                    WHERE holding_id = %s
                    RETURNING *
                    """,
                    (remaining, row["holding_id"]),
                )
            updated = cur.fetchone()
            return dict(updated) if updated else None


def log_intent(
    wolf_id: str,
    intent_date: date,
    intent_type: str,
    symbol: str | None = None,
    conviction_score: Decimal | float | int | None = None,
    target_allocation: Decimal | float | int | None = None,
    rationale: str | None = None,
) -> Row:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO wolf_intents (
                    wolf_id, intent_date, intent_type, symbol,
                    conviction_score, target_allocation, rationale
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    wolf_id,
                    intent_date,
                    intent_type,
                    symbol,
                    (
                        Decimal(str(conviction_score))
                        if conviction_score is not None
                        else None
                    ),
                    (
                        Decimal(str(target_allocation))
                        if target_allocation is not None
                        else None
                    ),
                    rationale,
                ),
            )
            row = cur.fetchone()
            assert row is not None
            return dict(row)


def save_snapshot(
    wolf_id: str,
    snapshot_date: date,
    cash_balance: Decimal | float | int,
    holdings_value: Decimal | float | int,
    total_value: Decimal | float | int,
    daily_pl: Decimal | float | int | None = None,
    cumulative_pl: Decimal | float | int | None = None,
) -> Row:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO portfolio_snapshots (
                    wolf_id, snapshot_date, cash_balance, holdings_value,
                    total_value, daily_pl, cumulative_pl
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    wolf_id,
                    snapshot_date,
                    Decimal(str(cash_balance)),
                    Decimal(str(holdings_value)),
                    Decimal(str(total_value)),
                    Decimal(str(daily_pl)) if daily_pl is not None else None,
                    (
                        Decimal(str(cumulative_pl))
                        if cumulative_pl is not None
                        else None
                    ),
                ),
            )
            row = cur.fetchone()
            assert row is not None
            return dict(row)


def allocate_wolf_id() -> str:
    """Next sequential wolf_id (W0001, W0002, …)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT wolf_id FROM wolves
                WHERE wolf_id ~ '^W[0-9]+$'
                ORDER BY CAST(SUBSTRING(wolf_id FROM 2) AS INTEGER) DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()
    if row:
        n = int(row[0][1:]) + 1
    else:
        n = 1
    return f"W{n:04d}"


def set_birth_intent_once(wolf_id: str, intent_text: str) -> Row:
    """Persist birth_intent JSON once — never overwrite an existing value."""
    payload = Json({"text": intent_text})
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE wolves
                SET birth_intent = %s
                WHERE wolf_id = %s AND birth_intent IS NULL
                RETURNING *
                """,
                (payload, wolf_id),
            )
            row = cur.fetchone()
            if row:
                return dict(row)
            cur.execute("SELECT * FROM wolves WHERE wolf_id = %s", (wolf_id,))
            existing = cur.fetchone()
            if not existing:
                raise ValueError(f"wolf not found: {wolf_id}")
            return dict(existing)


def patch_selection_run_gate_results(run_id: int, gate_results: Any) -> Row:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE selection_runs
                SET gate_results = %s
                WHERE run_id = %s
                RETURNING *
                """,
                (Json(gate_results), run_id),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"selection_run not found: {run_id}")
            return dict(row)


def save_selection_run(
    wolf_id: str,
    run_type: str,
    run_date: date,
    shortlist_json: Any = None,
    final_picks_json: Any = None,
    gemini_raw_response: str | None = None,
    gate_results: Any = None,
) -> Row:
    if run_type not in VALID_RUN_TYPES:
        raise ValueError(
            f"invalid selection_runs.run_type {run_type!r}; "
            f"expected one of {sorted(VALID_RUN_TYPES)}"
        )
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO selection_runs (
                    wolf_id, run_type, run_date,
                    shortlist_json, final_picks_json,
                    gemini_raw_response, gate_results
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    wolf_id,
                    run_type,
                    run_date,
                    Json(shortlist_json) if shortlist_json is not None else None,
                    Json(final_picks_json) if final_picks_json is not None else None,
                    gemini_raw_response,
                    Json(gate_results) if gate_results is not None else None,
                ),
            )
            row = cur.fetchone()
            assert row is not None
            return dict(row)
