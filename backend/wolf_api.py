"""Supabase wolves → legacy UI JSON shape for /api/bots/*."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from db import repository as repo

try:
    from strategies import STRATEGY_NAMES
except ImportError:
    from backend.strategies import STRATEGY_NAMES

IST = ZoneInfo("Asia/Kolkata")

_STRATEGY_TO_UI = {
    "VALUE": "value",
    "WINNERS": "winners",
    "BOX": "box",
    "DIP": "dip",
}

_UI_STATUS = {
    "active": "running",
    "paused": "paused",
    "closed": "terminated",
}


def _guardrails(wolf: dict) -> dict[str, float]:
    g = wolf.get("guardrails") or {}
    return {
        "stop_loss_pct": float(g.get("stop_loss_pct") or 15),
        "max_daily_loss_pct": float(g.get("max_daily_loss_pct") or 5),
        "max_deployed_pct": float(
            g.get("max_capital_deployed_pct") or g.get("max_deployed_pct") or 100
        ),
        "max_per_stock_pct": float(
            g.get("max_per_stock_pct") or g.get("max_position_pct") or 40
        ),
    }


def _fmt_entry_date(ts: Any) -> str:
    if ts is None:
        return ""
    if isinstance(ts, datetime):
        dt = ts
    else:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return dt.astimezone(IST).strftime("%d %b %Y")


def _fetch_ltps(symbols: list[str]) -> dict[str, float]:
    if not symbols:
        return {}
    try:
        from data import fetch_latest_price
    except ImportError:
        from backend.data import fetch_latest_price

    out: dict[str, float] = {}
    for sym in symbols:
        sym = sym.upper()
        price = fetch_latest_price(sym)
        if price and price > 0:
            out[sym] = round(float(price), 2)
    return out


def _portfolio_metrics(
    wolf: dict,
    holdings: list[dict],
    ltps: dict[str, float],
) -> tuple[float, float, float, float]:
    cash = float(wolf.get("budget_available") or 0)
    alloc = float(wolf.get("budget_initial") or 0)
    deployed = 0.0
    holdings_value = 0.0
    for h in holdings:
        if h.get("status") != "open":
            continue
        sym = str(h.get("symbol", "")).upper()
        qty = int(h.get("quantity") or 0)
        entry = float(h.get("avg_buy_price") or 0)
        ltp = ltps.get(sym) or entry
        deployed += qty * entry
        holdings_value += qty * ltp
    portfolio = round(cash + holdings_value, 2)
    return round(cash, 2), round(deployed, 2), portfolio, round(portfolio - alloc, 2)


def wolf_to_bot(
    wolf: dict,
    *,
    holdings: list[dict] | None = None,
    ltps: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Map a wolves row to the SQLite-era bot dict the HTML UI expects."""
    wolf_id = wolf["wolf_id"]
    held = holdings if holdings is not None else repo.list_holdings_for_wolf(
        wolf_id, status="open"
    )
    symbols = [str(h["symbol"]).upper() for h in held if h.get("symbol")]
    prices = ltps if ltps is not None else _fetch_ltps(symbols)
    g = _guardrails(wolf)
    cash, deployed, portfolio, pnl = _portfolio_metrics(wolf, held, prices)

    ui_status = _UI_STATUS.get(wolf.get("status", "active"), "running")
    strategy = _STRATEGY_TO_UI.get(
        str(wolf.get("strategy_code", "")).upper(),
        str(wolf.get("strategy_code", "")).lower(),
    )
    created = wolf.get("created_at")
    terminated = wolf.get("closed_at")

    return {
        "id": wolf_id,
        "name": wolf.get("wolf_name") or wolf_id,
        "strategy": strategy,
        "strategyName": STRATEGY_NAMES.get(strategy, strategy),
        "status": ui_status,
        "paused": ui_status == "paused",
        "running": ui_status == "running",
        "terminated": ui_status == "terminated",
        "allocation": round(float(wolf.get("budget_initial") or 0), 2),
        "availableCash": cash,
        "deployed": deployed,
        "portfolioValue": portfolio,
        "pnl": pnl,
        "mode": "autonomous",
        "level": "C",
        "auto_threshold": 2000,
        "budget": round(float(wolf.get("budget_initial") or 0), 2),
        "max_daily_loss_pct": g["max_daily_loss_pct"],
        "max_deployed_pct": g["max_deployed_pct"],
        "max_per_stock_pct": g["max_per_stock_pct"],
        "stop_loss_pct": g["stop_loss_pct"],
        "deployedAt": _fmt_entry_date(created) if created else "",
        "terminatedAt": _fmt_entry_date(terminated) if terminated else None,
        "breakerTripped": wolf.get("circuit_breaker_tripped_at") is not None,
        "breakerResetMode": "auto",
        "dayStartPortfolioValue": None,
        "dayStartDate": None,
        "wolfId": wolf_id,
    }


def holdings_to_trades(
    wolf_id: str,
    *,
    holdings: list[dict] | None = None,
    ledger: list[dict] | None = None,
    ltps: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Build UI trade rows from open holdings + sell ledger entries."""
    held = holdings if holdings is not None else repo.list_holdings_for_wolf(wolf_id)
    trades_ledger = ledger if ledger is not None else repo.list_trades_for_wolf(wolf_id)

    open_syms = [
        str(h["symbol"]).upper()
        for h in held
        if h.get("status") == "open" and h.get("symbol")
    ]
    closed_syms = {
        str(t["symbol"]).upper()
        for t in trades_ledger
        if t.get("action") == "SELL"
    }
    price_syms = list(dict.fromkeys(open_syms + list(closed_syms)))
    prices = ltps if ltps is not None else _fetch_ltps(price_syms)

    # Latest BUY per symbol for linking open positions
    buy_by_sym: dict[str, dict] = {}
    for t in trades_ledger:
        if t.get("action") != "BUY":
            continue
        sym = str(t.get("symbol", "")).upper()
        buy_by_sym[sym] = t

    out: list[dict[str, Any]] = []

    for h in held:
        if h.get("status") != "open":
            continue
        sym = str(h["symbol"]).upper()
        qty = int(h.get("quantity") or 0)
        entry = float(h.get("avg_buy_price") or 0)
        ltp = prices.get(sym) or entry
        buy = buy_by_sym.get(sym, {})
        trade_id = buy.get("trade_id") or h.get("holding_id")
        out.append(
            {
                "id": trade_id,
                "botId": wolf_id,
                "ticker": sym,
                "name": sym,
                "sector": "—",
                "qty": qty,
                "entry": entry,
                "ltp": ltp,
                "target": float(h.get("sell_target") or 0),
                "stopLoss": float(h.get("stop_loss") or 0),
                "entryDate": _fmt_entry_date(h.get("opened_at") or buy.get("executed_at")),
                "exitPrice": None,
                "exitDate": None,
                "exitReason": None,
                "status": "open",
                "source": "wolf_executor",
                "pickReport": None,
            }
        )

    for t in trades_ledger:
        if t.get("action") != "SELL":
            continue
        sym = str(t.get("symbol", "")).upper()
        buy = buy_by_sym.get(sym, {})
        entry = float(buy.get("price") or 0)
        exit_px = float(t.get("price") or 0)
        out.append(
            {
                "id": t.get("trade_id"),
                "botId": wolf_id,
                "ticker": sym,
                "name": sym,
                "sector": "—",
                "qty": int(t.get("quantity") or 0),
                "entry": entry,
                "ltp": exit_px,
                "target": 0,
                "stopLoss": 0,
                "entryDate": _fmt_entry_date(buy.get("executed_at")),
                "exitPrice": exit_px,
                "exitDate": _fmt_entry_date(t.get("executed_at")),
                "exitReason": "Sold",
                "status": "closed",
                "source": "wolf_executor",
                "pickReport": None,
            }
        )

    return out


def intents_to_action_log(wolf_id: str, limit: int = 30) -> list[dict[str, Any]]:
    rows = repo.list_intents_for_wolf(wolf_id, limit=limit)
    out: list[dict[str, Any]] = []
    for r in rows:
        sym = r.get("symbol")
        rationale = r.get("rationale") or ""
        detail = f"{sym}: {rationale[:120]}" if sym else rationale[:160]
        created = r.get("created_at")
        created_iso = (
            created.isoformat()
            if isinstance(created, datetime)
            else str(created or "")
        )
        out.append(
            {
                "id": r.get("intent_id"),
                "botId": wolf_id,
                "action": r.get("intent_type") or "intent",
                "detail": detail,
                "reasoning": rationale,
                "createdAt": created_iso,
            }
        )
    return out


def list_bots_for_user(
    user_id: UUID,
    *,
    include_terminated: bool = False,
) -> list[dict[str, Any]]:
    wolves = repo.list_wolves_for_user(user_id)
    if not include_terminated:
        wolves = [w for w in wolves if w.get("status") != "closed"]
    symbols: list[str] = []
    all_holdings: dict[str, list[dict]] = {}
    for w in wolves:
        held = repo.list_holdings_for_wolf(w["wolf_id"], status="open")
        all_holdings[w["wolf_id"]] = held
        symbols.extend(str(h["symbol"]).upper() for h in held if h.get("symbol"))
    ltps = _fetch_ltps(list(dict.fromkeys(symbols)))
    return [
        wolf_to_bot(w, holdings=all_holdings.get(w["wolf_id"], []), ltps=ltps)
        for w in wolves
    ]


def get_bot_for_user(user_id: UUID, wolf_id: str) -> dict[str, Any] | None:
    wolf = repo.get_wolf_for_user(wolf_id, user_id)
    if not wolf:
        return None
    holdings = repo.list_holdings_for_wolf(wolf_id, status="open")
    ltps = _fetch_ltps([str(h["symbol"]).upper() for h in holdings])
    return wolf_to_bot(wolf, holdings=holdings, ltps=ltps)
