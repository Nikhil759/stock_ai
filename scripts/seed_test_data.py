"""
Throwaway validation script for the Phase 1 database layer.

Proves schema + repository end-to-end against Supabase Postgres.
Run from repo root:

    PYTHONPATH=. python scripts/seed_test_data.py
"""

from __future__ import annotations

import pprint
import sys
from datetime import date
from pathlib import Path
from uuid import UUID

from psycopg2 import errors
from psycopg2.extras import RealDictCursor

# Allow `python scripts/seed_test_data.py` from repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from db.connection import get_connection
from db import repository as repo

TEST_USER_ID = UUID("11111111-1111-1111-1111-111111111111")
TEST_USER_EMAIL = "seed-test@wolfcapital.local"
TEST_WOLF_ID = "WSEED01"


def ensure_test_user() -> dict:
    """Insert into auth.users if missing; trigger creates public.users."""
    existing = repo.get_user(TEST_USER_ID)
    if existing:
        print(f"Test user already exists: {existing['id']}")
        return existing

    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO auth.users (
                    instance_id,
                    id,
                    aud,
                    role,
                    email,
                    encrypted_password,
                    email_confirmed_at,
                    raw_app_meta_data,
                    raw_user_meta_data,
                    created_at,
                    updated_at,
                    confirmation_token,
                    recovery_token,
                    email_change_token_new,
                    email_change
                )
                VALUES (
                    '00000000-0000-0000-0000-000000000000',
                    %s,
                    'authenticated',
                    'authenticated',
                    %s,
                    '',
                    now(),
                    '{"provider":"email","providers":["email"]}'::jsonb,
                    '{"full_name":"Wolf Capital Seed Test"}'::jsonb,
                    now(),
                    now(),
                    '',
                    '',
                    '',
                    ''
                )
                ON CONFLICT (id) DO NOTHING
                """,
                (str(TEST_USER_ID), TEST_USER_EMAIL),
            )

    user = repo.get_user(TEST_USER_ID)
    if user is None:
        raise RuntimeError(
            "Failed to create test user — check auth.users insert / trigger"
        )
    print(f"Created test user: {user['id']} ({user.get('name')})")
    return user


def main() -> None:
    print("=== Wolf Capital Phase 1 seed / validation ===\n")

    # 1. Test user (bypass real Supabase Auth signup)
    user = ensure_test_user()

    # Clean prior seed wolf so re-runs are idempotent enough for wolf_id
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM portfolio_snapshots WHERE wolf_id = %s",
                (TEST_WOLF_ID,),
            )
            cur.execute(
                "DELETE FROM wolf_intents WHERE wolf_id = %s", (TEST_WOLF_ID,)
            )
            cur.execute(
                "DELETE FROM trades WHERE wolf_id = %s", (TEST_WOLF_ID,)
            )
            cur.execute(
                "DELETE FROM wolf_holdings WHERE wolf_id = %s", (TEST_WOLF_ID,)
            )
            cur.execute(
                "DELETE FROM selection_runs WHERE wolf_id = %s", (TEST_WOLF_ID,)
            )
            cur.execute("DELETE FROM wolves WHERE wolf_id = %s", (TEST_WOLF_ID,))

    # 2. Create wolf with default name, DIP, ₹10,000
    wolf_name = repo.assign_default_wolf_name(TEST_USER_ID)
    print(f"Assigned default wolf name: {wolf_name}")

    wolf = repo.create_wolf(
        user_id=TEST_USER_ID,
        wolf_id=TEST_WOLF_ID,
        wolf_name=wolf_name,
        strategy_code="DIP",
        budget_initial=10_000,
        guardrails={
            "max_position_pct": 20,
            "stop_loss_pct": 8,
            "circuit_breaker": {"trigger_pct": 5, "reset_mode": "manual"},
        },
    )
    print(f"Created wolf: {wolf['wolf_id']} / {wolf['wolf_name']}")

    # 3. Fake BUY trade
    trade = repo.record_trade(
        wolf_id=TEST_WOLF_ID,
        symbol="RELIANCE",
        action="BUY",
        quantity=2,
        price=1400.00,
        mode="paper",
    )
    print(f"Recorded trade id={trade['trade_id']}")

    # 4. Holdings reflecting that trade
    holding = repo.upsert_holding(
        wolf_id=TEST_WOLF_ID,
        symbol="RELIANCE",
        quantity=2,
        avg_buy_price=1400.00,
        sell_target=1540.00,
        stop_loss=1288.00,
    )
    print(f"Upserted holding id={holding['holding_id']}")

    # 5. Birth intent
    intent = repo.log_intent(
        wolf_id=TEST_WOLF_ID,
        intent_date=date.today(),
        intent_type="birth",
        symbol=None,
        conviction_score=75,
        target_allocation=2800,
        rationale="Seed script birth intent for Phase 1 validation.",
    )
    print(f"Logged intent id={intent['intent_id']}")

    # 6. Snapshot
    cash = 10_000 - (2 * 1400)
    holdings_value = 2 * 1400
    snapshot = repo.save_snapshot(
        wolf_id=TEST_WOLF_ID,
        snapshot_date=date.today(),
        cash_balance=cash,
        holdings_value=holdings_value,
        total_value=cash + holdings_value,
        daily_pl=0,
        cumulative_pl=0,
    )
    print(f"Saved snapshot id={snapshot['snapshot_id']}")

    # 7. Print everything created
    print("\n--- Created rows (check against Supabase table editor) ---\n")
    print("USER:")
    pprint.pp(user)
    print("\nWOLF:")
    pprint.pp(repo.get_wolf(TEST_WOLF_ID))
    print("\nTRADE:")
    pprint.pp(trade)
    print("\nHOLDING:")
    pprint.pp(holding)
    print("\nINTENT:")
    pprint.pp(intent)
    print("\nSNAPSHOT:")
    pprint.pp(snapshot)

    # 8. Strategy-lock trigger
    print("\n--- Strategy-lock check ---")
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE wolves
                    SET strategy_code = 'VALUE'
                    WHERE wolf_id = %s
                    """,
                    (TEST_WOLF_ID,),
                )
        print("FAIL: strategy change was allowed (trigger missing?)")
        sys.exit(1)
    except errors.RaiseException as exc:
        print(f"OK: strategy change blocked — {exc.pgerror.strip()}")
    except Exception as exc:
        # Some drivers surface as generic DatabaseError
        msg = str(exc)
        if "strategy_code cannot be changed" in msg:
            print(f"OK: strategy change blocked — {msg}")
        else:
            raise

    # 9. Per-user wolf_name uniqueness
    print("\n--- Duplicate wolf_name check ---")
    try:
        repo.create_wolf(
            user_id=TEST_USER_ID,
            wolf_id="WSEED02",
            wolf_name=wolf_name,
            strategy_code="DIP",
            budget_initial=10_000,
            guardrails={},
        )
        print("FAIL: duplicate wolf_name was allowed")
        sys.exit(1)
    except errors.UniqueViolation as exc:
        print(f"OK: duplicate name blocked — {exc.pgerror.strip()}")
    except Exception as exc:
        msg = str(exc)
        if "uq_wolves_user_name" in msg or "duplicate key" in msg.lower():
            print(f"OK: duplicate name blocked — {msg}")
        else:
            raise

    print("\n=== All validation steps passed ===")


if __name__ == "__main__":
    main()
