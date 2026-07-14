"""Data access functions for Wolf Capital tables. No business logic."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg2.extras import Json, RealDictCursor

from db.connection import get_connection

Row = dict[str, Any]


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
) -> Row:
    """Update an open position for (wolf_id, symbol), or insert one if none exists."""
    avg = Decimal(str(avg_buy_price))
    target = Decimal(str(sell_target)) if sell_target is not None else None

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
                        sell_target = %s
                    WHERE holding_id = %s
                    RETURNING *
                    """,
                    (quantity, avg, target, existing["holding_id"]),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO wolf_holdings (
                        wolf_id, symbol, quantity, avg_buy_price, sell_target, status
                    )
                    VALUES (%s, %s, %s, %s, %s, 'open')
                    RETURNING *
                    """,
                    (wolf_id, symbol, quantity, avg, target),
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


def save_selection_run(
    wolf_id: str,
    run_type: str,
    run_date: date,
    shortlist_json: Any = None,
    final_picks_json: Any = None,
    gemini_raw_response: str | None = None,
    gate_results: Any = None,
) -> Row:
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
