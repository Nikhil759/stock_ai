"""SQLite persistence — multi-bot instances with allocation pools."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from data_paths import get_db_path
from strategies import STRATEGY_NAMES
from workspace import LEGACY_WORKSPACE_ID

DB_PATH = get_db_path()

BOT_DEFAULTS = {
    "mode": "advisory",
    "level": "A",
    "auto_threshold": 2000,
    "max_daily_loss_pct": 5,
    "max_deployed_pct": 100,
    "max_per_stock_pct": 40,
    "stop_loss_pct": 15,
}


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _today():
    return datetime.now().strftime("%d %b %Y")


def init_db():
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS bots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                strategy TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                allocation REAL NOT NULL,
                available_cash REAL NOT NULL,
                mode TEXT NOT NULL DEFAULT 'advisory',
                level TEXT NOT NULL DEFAULT 'A',
                auto_threshold INTEGER NOT NULL DEFAULT 2000,
                max_daily_loss_pct REAL NOT NULL DEFAULT 5,
                max_deployed_pct REAL NOT NULL DEFAULT 100,
                max_per_stock_pct REAL NOT NULL DEFAULT 40,
                stop_loss_pct REAL NOT NULL DEFAULT 15,
                deployed_at TEXT NOT NULL,
                terminated_at TEXT,
                created_at TEXT NOT NULL,
                workspace_id TEXT NOT NULL DEFAULT 'ws-default-legacy'
            );

            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                name TEXT,
                sector TEXT,
                qty INTEGER NOT NULL,
                entry REAL NOT NULL,
                ltp REAL NOT NULL,
                target REAL NOT NULL,
                stop_loss REAL NOT NULL,
                entry_date TEXT NOT NULL,
                exit_price REAL,
                exit_date TEXT,
                exit_reason TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                source TEXT NOT NULL DEFAULT 'manual',
                created_at TEXT NOT NULL,
                FOREIGN KEY (bot_id) REFERENCES bots(id)
            );

            CREATE TABLE IF NOT EXISTS pending_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                name TEXT,
                sector TEXT,
                qty INTEGER NOT NULL,
                buy_price REAL NOT NULL,
                sell_price REAL NOT NULL,
                stop_loss REAL NOT NULL,
                cost REAL NOT NULL,
                reason TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                FOREIGN KEY (bot_id) REFERENCES bots(id)
            );

            CREATE TABLE IF NOT EXISTS action_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id INTEGER,
                action TEXT NOT NULL,
                detail TEXT,
                reasoning TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trade_modifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER NOT NULL,
                bot_id INTEGER NOT NULL,
                field TEXT NOT NULL,
                old_value REAL NOT NULL,
                new_value REAL NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (trade_id) REFERENCES trades(id),
                FOREIGN KEY (bot_id) REFERENCES bots(id)
            );
        """)
        _migrate_legacy(conn)
        _migrate_bot_names(conn)
        _migrate_pick_report(conn)
        _migrate_workspace(conn)
        _migrate_breaker(conn)
        _migrate_daily_notes(conn)
        _migrate_birth_intention(conn)


def _migrate_birth_intention(conn):
    cols = {c[1] for c in conn.execute("PRAGMA table_info(bots)").fetchall()}
    if "birth_intention_json" not in cols:
        conn.execute("ALTER TABLE bots ADD COLUMN birth_intention_json TEXT")


def _migrate_daily_notes(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id INTEGER NOT NULL,
            note_date TEXT NOT NULL,
            note_text TEXT NOT NULL,
            sections_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(bot_id, note_date),
            FOREIGN KEY (bot_id) REFERENCES bots(id)
        )
    """)


def _migrate_breaker(conn):
    cols = {c[1] for c in conn.execute("PRAGMA table_info(bots)").fetchall()}
    if "breaker_tripped" not in cols:
        conn.execute(
            "ALTER TABLE bots ADD COLUMN breaker_tripped INTEGER NOT NULL DEFAULT 0"
        )
    if "breaker_reset_mode" not in cols:
        conn.execute(
            "ALTER TABLE bots ADD COLUMN breaker_reset_mode TEXT NOT NULL DEFAULT 'auto'"
        )
    if "day_start_portfolio_value" not in cols:
        conn.execute("ALTER TABLE bots ADD COLUMN day_start_portfolio_value REAL")
    if "day_start_date" not in cols:
        conn.execute("ALTER TABLE bots ADD COLUMN day_start_date TEXT")


def _bot_name(bot_id: int) -> str:
    return f"Wolf {bot_id}"


def _migrate_workspace(conn):
    cols = {c[1] for c in conn.execute("PRAGMA table_info(bots)").fetchall()}
    if "workspace_id" not in cols:
        conn.execute(
            f"ALTER TABLE bots ADD COLUMN workspace_id TEXT NOT NULL DEFAULT '{LEGACY_WORKSPACE_ID}'"
        )
        conn.execute(
            "UPDATE bots SET workspace_id = ? WHERE workspace_id IS NULL OR workspace_id = ''",
            (LEGACY_WORKSPACE_ID,),
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_bots_workspace ON bots(workspace_id)"
    )


def _migrate_pick_report(conn):
    cols = {c[1] for c in conn.execute("PRAGMA table_info(trades)").fetchall()}
    if "pick_report" not in cols:
        conn.execute("ALTER TABLE trades ADD COLUMN pick_report TEXT")


def _migrate_bot_names(conn):
    """Normalize Wolf display names to Wolf 1, Wolf 2, … by id."""
    rows = conn.execute("SELECT id FROM bots ORDER BY id").fetchall()
    for row in rows:
        conn.execute(
            "UPDATE bots SET name = ? WHERE id = ?",
            (_bot_name(row["id"]), row["id"]),
        )


def _migrate_legacy(conn):
    """Migrate single-bot prototype tables if present."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    if "bot_config" in tables and conn.execute("SELECT COUNT(*) FROM bots").fetchone()[0] == 0:
        row = conn.execute("SELECT * FROM bot_config WHERE id = 1").fetchone()
        if row:
            ts = _now()
            strat = row["strategy"] or "value"
            alloc = float(row["budget"] or 10000)
            cur = conn.execute(
                """INSERT INTO bots
                   (name, strategy, status, allocation, available_cash, mode, level,
                    auto_threshold, max_daily_loss_pct, max_deployed_pct, max_per_stock_pct,
                    stop_loss_pct, deployed_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "Bot",  # renamed after insert
                    strat,
                    "paused" if row["paused"] else "running",
                    alloc,
                    alloc,
                    row["mode"],
                    row["level"],
                    row["auto_threshold"],
                    row["max_daily_loss_pct"],
                    row["max_deployed_pct"],
                    row["max_per_stock_pct"],
                    row["stop_loss_pct"],
                    ts,
                    ts,
                ),
            )
            bot_id = cur.lastrowid
            conn.execute(
                "UPDATE bots SET name = ? WHERE id = ?", (_bot_name(bot_id), bot_id)
            )
            if "trades" in tables:
                cols = {c[1] for c in conn.execute("PRAGMA table_info(trades)").fetchall()}
                if "bot_id" not in cols:
                    conn.execute("ALTER TABLE trades ADD COLUMN bot_id INTEGER")
                    conn.execute("ALTER TABLE trades ADD COLUMN exit_price REAL")
                    conn.execute("ALTER TABLE trades ADD COLUMN exit_date TEXT")
                    conn.execute("ALTER TABLE trades ADD COLUMN exit_reason TEXT")
                conn.execute("UPDATE trades SET bot_id = ? WHERE bot_id IS NULL", (bot_id,))
                deployed = conn.execute(
                    "SELECT COALESCE(SUM(qty * entry), 0) FROM trades WHERE bot_id = ? AND status = 'open'",
                    (bot_id,),
                ).fetchone()[0]
                cash = max(0, alloc - deployed)
                conn.execute(
                    "UPDATE bots SET available_cash = ? WHERE id = ?", (cash, bot_id)
                )

    for table, col in [("trades", "bot_id"), ("pending_trades", "bot_id"), ("action_log", "bot_id")]:
        if table in tables:
            cols = {c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if col not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} INTEGER")
            if table == "trades":
                for extra in ("exit_price", "exit_date", "exit_reason"):
                    if extra not in cols:
                        conn.execute(f"ALTER TABLE trades ADD COLUMN {extra} REAL" if extra == "exit_price" else f"ALTER TABLE trades ADD COLUMN {extra} TEXT")


def _row_bot(r) -> dict:
    deployed = _deployed_for_bot(r["id"])
    cash = float(r["available_cash"])
    alloc = float(r["allocation"])
    value = cash + _market_value_for_bot(r["id"])
    return {
        "id": r["id"],
        "name": r["name"],
        "strategy": r["strategy"],
        "strategyName": STRATEGY_NAMES.get(r["strategy"], r["strategy"]),
        "status": r["status"],
        "paused": r["status"] == "paused",
        "running": r["status"] == "running",
        "terminated": r["status"] == "terminated",
        "allocation": round(alloc, 2),
        "availableCash": round(cash, 2),
        "deployed": round(deployed, 2),
        "portfolioValue": round(value, 2),
        "pnl": round(value - alloc, 2),
        "mode": r["mode"],
        "level": r["level"],
        "auto_threshold": r["auto_threshold"],
        "budget": round(alloc, 2),
        "max_daily_loss_pct": r["max_daily_loss_pct"],
        "max_deployed_pct": r["max_deployed_pct"],
        "max_per_stock_pct": r["max_per_stock_pct"],
        "stop_loss_pct": r["stop_loss_pct"],
        "deployedAt": r["deployed_at"],
        "terminatedAt": r["terminated_at"],
        "breakerTripped": bool(r["breaker_tripped"]) if "breaker_tripped" in r.keys() else False,
        "breakerResetMode": r["breaker_reset_mode"] if "breaker_reset_mode" in r.keys() else "auto",
        "dayStartPortfolioValue": r["day_start_portfolio_value"] if "day_start_portfolio_value" in r.keys() else None,
        "dayStartDate": r["day_start_date"] if "day_start_date" in r.keys() else None,
    }


def _deployed_for_bot(bot_id: int) -> float:
    with _conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(qty * entry), 0) FROM trades WHERE bot_id = ? AND status = 'open'",
            (bot_id,),
        ).fetchone()
    return float(row[0]) if row else 0.0


def _market_value_for_bot(bot_id: int) -> float:
    with _conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(qty * ltp), 0) FROM trades WHERE bot_id = ? AND status = 'open'",
            (bot_id,),
        ).fetchone()
    return float(row[0]) if row else 0.0


def list_running_bots() -> list[dict]:
    """All running (not paused/terminated) bots across workspaces — for cron jobs."""
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM bots WHERE status = 'running' ORDER BY id",
        ).fetchall()
    return [_row_bot(r) for r in rows]


def list_bots(workspace_id: str, include_terminated: bool = False) -> list[dict]:
    init_db()
    with _conn() as conn:
        if include_terminated:
            rows = conn.execute(
                "SELECT * FROM bots WHERE workspace_id = ? ORDER BY id DESC",
                (workspace_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM bots WHERE workspace_id = ? AND status != 'terminated' ORDER BY id DESC",
                (workspace_id,),
            ).fetchall()
    return [_row_bot(r) for r in rows]


def get_bot(bot_id: int, workspace_id: str | None = None) -> dict | None:
    init_db()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
    if not row:
        return None
    if workspace_id and row["workspace_id"] != workspace_id:
        return None
    return _row_bot(row)


def deploy_bot(
    strategy: str,
    allocation: int,
    workspace_id: str,
    mode: str = "advisory",
    level: str = "A",
    auto_threshold: int = 2000,
    max_daily_loss_pct: float = 5,
    max_deployed_pct: float = 100,
    max_per_stock_pct: float = 40,
    stop_loss_pct: float = 15,
    name: str | None = None,
) -> dict:
    init_db()
    ts = _now()
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO bots
               (name, strategy, status, allocation, available_cash, mode, level,
                auto_threshold, max_daily_loss_pct, max_deployed_pct, max_per_stock_pct,
                stop_loss_pct, deployed_at, created_at, workspace_id)
               VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name or "Bot",
                strategy,
                float(allocation),
                float(allocation),
                mode,
                level,
                auto_threshold,
                max_daily_loss_pct,
                max_deployed_pct,
                max_per_stock_pct,
                stop_loss_pct,
                ts,
                ts,
                workspace_id,
            ),
        )
        bot_id = cur.lastrowid
        bot_name = name or _bot_name(bot_id)
        if not name:
            conn.execute("UPDATE bots SET name = ? WHERE id = ?", (bot_name, bot_id))
    log_action(
        bot_id,
        "bot_deployed",
        f"{bot_name} · ₹{allocation:,} · {STRATEGY_NAMES.get(strategy, strategy)}",
        f"Fund allocation pool created with ₹{allocation:,} available cash.",
    )
    return get_bot(bot_id)


def set_bot_status(bot_id: int, status: str) -> dict | None:
    init_db()
    ts = _now() if status == "terminated" else None
    with _conn() as conn:
        if status == "terminated":
            conn.execute(
                "UPDATE bots SET status = ?, terminated_at = ? WHERE id = ?",
                (status, ts, bot_id),
            )
        else:
            conn.execute("UPDATE bots SET status = ? WHERE id = ?", (status, bot_id))
    bot = get_bot(bot_id)
    if bot:
        action = {"running": "bot_resumed", "paused": "bot_paused", "terminated": "bot_terminated"}[status]
        log_action(bot_id, action, bot["name"], f"Status → {status}")
    return bot


def save_birth_intention(bot_id: int, payload: dict) -> None:
    """Persist the selector output from deploy as this Wolf's birth intention."""
    init_db()
    raw = json.dumps(payload, default=str)
    with _conn() as conn:
        conn.execute(
            "UPDATE bots SET birth_intention_json = ? WHERE id = ?",
            (raw, bot_id),
        )
    note = ""
    result = payload.get("result")
    if isinstance(result, dict):
        note = (result.get("portfolio_note") or "")[:500]
    log_action(
        bot_id,
        "birth_intention_saved",
        f"Birth intention recorded ({payload.get('strategy', '?')} deploy)",
        note,
    )


def get_birth_intention(bot_id: int) -> dict | None:
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT birth_intention_json FROM bots WHERE id = ?", (bot_id,)
        ).fetchone()
    if not row or not row["birth_intention_json"]:
        return None
    try:
        return json.loads(row["birth_intention_json"])
    except (json.JSONDecodeError, TypeError):
        return None


def update_bot(bot_id: int, **kwargs) -> dict | None:
    init_db()
    allowed = {
        "name", "mode", "level", "auto_threshold",
        "max_daily_loss_pct", "max_deployed_pct", "max_per_stock_pct",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        return get_bot(bot_id)
    cols = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [bot_id]
    with _conn() as conn:
        conn.execute(f"UPDATE bots SET {cols} WHERE id = ?", vals)
    return get_bot(bot_id)


def adjust_cash(bot_id: int, delta: float) -> float:
    with _conn() as conn:
        conn.execute(
            "UPDATE bots SET available_cash = available_cash + ? WHERE id = ?",
            (delta, bot_id),
        )
        row = conn.execute("SELECT available_cash FROM bots WHERE id = ?", (bot_id,)).fetchone()
    return float(row[0]) if row else 0.0


def log_action(bot_id: int | None, action: str, detail: str = "", reasoning: str = ""):
    init_db()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO action_log (bot_id, action, detail, reasoning, created_at) VALUES (?, ?, ?, ?, ?)",
            (bot_id, action, detail, reasoning, _now()),
        )


def get_action_log(bot_id: int | None = None, limit: int = 50) -> list[dict]:
    init_db()
    with _conn() as conn:
        if bot_id:
            rows = conn.execute(
                "SELECT * FROM action_log WHERE bot_id = ? ORDER BY id DESC LIMIT ?",
                (bot_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM action_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [_row_action(r) for r in rows]


def _row_action(r) -> dict:
    return {
        "id": r["id"],
        "botId": r["bot_id"],
        "action": r["action"],
        "detail": r["detail"] or "",
        "reasoning": r["reasoning"] or "",
        "createdAt": r["created_at"],
    }


def get_trades(bot_id: int, status: str | None = None) -> list[dict]:
    init_db()
    with _conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM trades WHERE bot_id = ? AND status = ? ORDER BY id",
                (bot_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trades WHERE bot_id = ? ORDER BY id", (bot_id,)
            ).fetchall()
    return [_row_trade(r) for r in rows]


def get_trade(trade_id: int, bot_id: int | None = None) -> dict | None:
    init_db()
    with _conn() as conn:
        if bot_id is not None:
            row = conn.execute(
                "SELECT * FROM trades WHERE id = ? AND bot_id = ?", (trade_id, bot_id)
            ).fetchone()
        else:
            row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    return _row_trade(row) if row else None


def update_trade_pick_report(trade_id: int, report: dict) -> None:
    init_db()
    with _conn() as conn:
        conn.execute(
            "UPDATE trades SET pick_report = ? WHERE id = ?",
            (json.dumps(report), trade_id),
        )


def _row_trade(r) -> dict:
    pick_report = None
    raw = r["pick_report"] if "pick_report" in r.keys() else None
    if raw:
        try:
            pick_report = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pick_report = None
    return {
        "id": r["id"],
        "botId": r["bot_id"],
        "ticker": r["ticker"],
        "name": r["name"] or r["ticker"],
        "sector": r["sector"] or "—",
        "qty": r["qty"],
        "entry": r["entry"],
        "ltp": r["ltp"],
        "target": r["target"],
        "stopLoss": r["stop_loss"],
        "entryDate": r["entry_date"],
        "exitPrice": r["exit_price"],
        "exitDate": r["exit_date"],
        "exitReason": r["exit_reason"],
        "status": r["status"],
        "source": r["source"],
        "pickReport": pick_report,
    }


def execute_buy(bot_id: int, trade: dict, cost: float, source: str = "manual") -> dict:
    init_db()
    bot = get_bot(bot_id)
    if not bot:
        raise ValueError("Wolf not found")
    if bot["status"] == "terminated":
        raise ValueError("Wolf is terminated")
    if bot["status"] == "paused":
        raise ValueError("Wolf is paused")
    if bot["availableCash"] < cost - 0.01:
        raise ValueError(f"Insufficient cash (₹{bot['availableCash']:,.0f} available)")

    ts = _now()
    entry_date = trade.get("entryDate") or _today()
    pick_report_json = None
    if trade.get("pickReport"):
        pick_report_json = json.dumps(trade["pickReport"])
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO trades
               (bot_id, ticker, name, sector, qty, entry, ltp, target, stop_loss,
                entry_date, status, source, pick_report, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)""",
            (
                bot_id,
                trade["ticker"],
                trade.get("name", trade["ticker"]),
                trade.get("sector", "—"),
                trade["qty"],
                trade["entry"],
                trade.get("ltp", trade["entry"]),
                trade["target"],
                trade["stopLoss"],
                entry_date,
                source,
                pick_report_json,
                ts,
            ),
        )
        trade_id = cur.lastrowid
        conn.execute(
            "UPDATE bots SET available_cash = available_cash - ? WHERE id = ?",
            (cost, bot_id),
        )
    log_action(
        bot_id,
        "trade_executed",
        f"Bought {trade['qty']} × {trade['ticker']} at ₹{trade['entry']:.2f}",
        trade.get("reasoning", f"Paper buy via {source}."),
    )
    return {**_row_trade(_get_trade_row(trade_id)), "reasoning": trade.get("reasoning")}


def _get_trade_row(trade_id: int):
    with _conn() as conn:
        return conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()


def close_trade(trade_id: int, exit_price: float, reason: str) -> dict | None:
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM trades WHERE id = ? AND status = 'open'", (trade_id,)
        ).fetchone()
        if not row:
            return None
        proceeds = row["qty"] * exit_price
        conn.execute(
            """UPDATE trades SET status = 'closed', ltp = ?, exit_price = ?,
               exit_date = ?, exit_reason = ? WHERE id = ?""",
            (exit_price, exit_price, _today(), reason, trade_id),
        )
        conn.execute(
            "UPDATE bots SET available_cash = available_cash + ? WHERE id = ?",
            (proceeds, row["bot_id"]),
        )
    log_action(
        row["bot_id"],
        "trade_closed",
        f"Sold {row['qty']} × {row['ticker']} at ₹{exit_price:.2f} ({reason})",
        f"Proceeds ₹{proceeds:,.0f} returned to allocation pool.",
    )
    return _row_trade(_get_trade_row(trade_id))


def update_trade_target(trade_id: int, new_target: float, reason: str) -> dict | None:
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM trades WHERE id = ? AND status = 'open'", (trade_id,)
        ).fetchone()
        if not row:
            return None
        old = row["target"]
        if new_target <= row["entry"]:
            raise ValueError("Sell target must be above entry price")
        conn.execute("UPDATE trades SET target = ? WHERE id = ?", (new_target, trade_id))
        conn.execute(
            """INSERT INTO trade_modifications
               (trade_id, bot_id, field, old_value, new_value, reason, created_at)
               VALUES (?, ?, 'target', ?, ?, ?, ?)""",
            (trade_id, row["bot_id"], old, new_target, reason, _now()),
        )
    log_action(
        row["bot_id"],
        "target_updated",
        f"{row['ticker']}: sell ₹{old:.0f} → ₹{new_target:.0f}",
        reason,
    )
    return _row_trade(_get_trade_row(trade_id))


def update_trade_ltp(trade_id: int, ltp: float):
    with _conn() as conn:
        conn.execute("UPDATE trades SET ltp = ? WHERE id = ?", (ltp, trade_id))


def get_pending(bot_id: int) -> list[dict]:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pending_trades WHERE bot_id = ? AND status = 'pending' ORDER BY id DESC",
            (bot_id,),
        ).fetchall()
    return [_row_pending(r) for r in rows]


def _row_pending(r) -> dict:
    return {
        "id": r["id"],
        "botId": r["bot_id"],
        "ticker": r["ticker"],
        "name": r["name"] or r["ticker"],
        "sector": r["sector"] or "—",
        "qty": r["qty"],
        "buyPrice": r["buy_price"],
        "sellPrice": r["sell_price"],
        "stopLoss": r["stop_loss"],
        "cost": r["cost"],
        "buyFmt": f"₹{r['buy_price']:,.2f}",
        "sellFmt": f"₹{r['sell_price']:,.0f}",
        "stopFmt": f"₹{r['stop_loss']:,.2f}",
        "costFmt": f"₹{r['cost']:,.0f}",
        "reason": r["reason"] or "",
        "status": r["status"],
        "createdAt": r["created_at"],
    }


def add_pending(bot_id: int, pending: dict) -> dict:
    init_db()
    ts = _now()
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO pending_trades
               (bot_id, ticker, name, sector, qty, buy_price, sell_price, stop_loss, cost, reason, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                bot_id,
                pending["ticker"],
                pending.get("name"),
                pending.get("sector"),
                pending["qty"],
                pending["buyPrice"],
                pending["sellPrice"],
                pending["stopLoss"],
                pending["cost"],
                pending.get("reason", ""),
                ts,
            ),
        )
        pid = cur.lastrowid
    log_action(
        bot_id,
        "approval_requested",
        f"{pending['ticker']}: buy {pending['qty']} shares for ₹{pending['cost']:,.0f}",
        pending.get("reason", "Awaiting approval."),
    )
    return {**pending, "id": pid, "botId": bot_id}


def resolve_pending(bot_id: int, pending_id: int, approve: bool) -> dict | None:
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM pending_trades WHERE id = ? AND bot_id = ? AND status = 'pending'",
            (pending_id, bot_id),
        ).fetchone()
        if not row:
            return None
        status = "approved" if approve else "rejected"
        conn.execute(
            "UPDATE pending_trades SET status = ? WHERE id = ?", (status, pending_id)
        )

    pending = _row_pending(row)
    if approve:
        trade = execute_buy(
            bot_id,
            {
                "ticker": pending["ticker"],
                "name": pending["name"],
                "sector": pending["sector"],
                "qty": pending["qty"],
                "entry": pending["buyPrice"],
                "ltp": pending["buyPrice"],
                "target": pending["sellPrice"],
                "stopLoss": pending["stopLoss"],
                "reasoning": pending["reason"],
            },
            pending["cost"],
            source="autonomous",
        )
        bot = get_bot(bot_id)
        if bot:
            from pick_report import rebuild_pick_report_for_trade
            report = rebuild_pick_report_for_trade(trade, bot)
            update_trade_pick_report(trade["id"], report)
            trade["pickReport"] = report
        return {"status": "approved", "trade": trade}
    log_action(bot_id, "trade_rejected", f"Rejected {pending['ticker']}", pending["reason"])
    return {"status": "rejected"}


def set_breaker_tripped(bot_id: int, tripped: bool) -> None:
    init_db()
    with _conn() as conn:
        conn.execute(
            "UPDATE bots SET breaker_tripped = ? WHERE id = ?",
            (1 if tripped else 0, bot_id),
        )


def set_day_baseline(bot_id: int, portfolio_value: float, day: str | None = None) -> None:
    init_db()
    day = day or datetime.now().strftime("%Y-%m-%d")
    with _conn() as conn:
        conn.execute(
            "UPDATE bots SET day_start_portfolio_value = ?, day_start_date = ? WHERE id = ?",
            (portfolio_value, day, bot_id),
        )


def clear_breaker_if_auto(bot_id: int) -> bool:
    """Auto-clear breaker at pre-open if mode is 'auto'. Returns True if cleared."""
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT breaker_tripped, breaker_reset_mode FROM bots WHERE id = ?",
            (bot_id,),
        ).fetchone()
        if not row or not row["breaker_tripped"]:
            return False
        if (row["breaker_reset_mode"] or "auto") != "auto":
            return False
        conn.execute(
            "UPDATE bots SET breaker_tripped = 0 WHERE id = ?",
            (bot_id,),
        )
    log_action(bot_id, "breaker_cleared", "Auto-reset at pre-open", "Daily-loss breaker cleared.")
    return True


def upsert_daily_note(
    bot_id: int,
    note_date: str,
    note_text: str,
    sections_json: str | None = None,
) -> dict:
    init_db()
    ts = _now()
    with _conn() as conn:
        existing = conn.execute(
            "SELECT id, created_at FROM daily_notes WHERE bot_id = ? AND note_date = ?",
            (bot_id, note_date),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE daily_notes SET note_text = ?, sections_json = ?, updated_at = ?
                   WHERE bot_id = ? AND note_date = ?""",
                (note_text, sections_json, ts, bot_id, note_date),
            )
            created = existing["created_at"]
        else:
            conn.execute(
                """INSERT INTO daily_notes
                   (bot_id, note_date, note_text, sections_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (bot_id, note_date, note_text, sections_json, ts, ts),
            )
            created = ts
    return {
        "botId": bot_id,
        "date": note_date,
        "note": note_text,
        "createdAt": created,
        "updatedAt": ts,
    }


def get_daily_note(bot_id: int, note_date: str | None = None) -> dict | None:
    init_db()
    note_date = note_date or datetime.now().strftime("%Y-%m-%d")
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM daily_notes WHERE bot_id = ? AND note_date = ?",
            (bot_id, note_date),
        ).fetchone()
    if not row:
        return None
    return {
        "botId": row["bot_id"],
        "date": row["note_date"],
        "note": row["note_text"],
        "sectionsJson": row["sections_json"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


# Legacy compat — returns first active bot or None (requires workspace)
def get_config(workspace_id: str) -> dict:
    bots = list_bots(workspace_id)
    return bots[0] if bots else {**BOT_DEFAULTS, "id": None, "budget": 10000, "paused": False}


def update_config(workspace_id: str, **kwargs):
    bots = list_bots(workspace_id)
    if not bots:
        return get_config(workspace_id)
    bot_id = bots[0]["id"]
    return update_bot(bot_id, **kwargs) or get_bot(bot_id)
