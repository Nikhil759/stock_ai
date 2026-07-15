"""Supabase wolves → legacy UI JSON shape for /api/bots/*."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from db import repository as repo
from db.repository import RUN_TYPE_DAILY_REVIEW

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


_kite_auth_mod = None
_kite_auth_load_failed = False


def _load_kite_auth_module():
    """Load fund_manager/kite_auth.py directly by file path.

    Deliberately bypasses `import fund_manager...`, which would run
    fund_manager/__init__.py and pull in Gemini/legacy-db deps that have
    nothing to do with a live price lookup. kite_auth.py has no intra-package
    imports so it's safe to load standalone (same trick data_layer/fetch/
    kite_session.py uses).
    """
    global _kite_auth_mod, _kite_auth_load_failed
    if _kite_auth_mod is not None:
        return _kite_auth_mod
    if _kite_auth_load_failed:
        return None
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent / "fund_manager" / "kite_auth.py"
    try:
        spec = importlib.util.spec_from_file_location("_wolf_api_kite_auth", path)
        if spec is None or spec.loader is None:
            raise ImportError("could not load kite_auth spec")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception:
        _kite_auth_load_failed = True
        return None
    _kite_auth_mod = mod
    return mod


def _fetch_kite_ltps(symbols: list[str]) -> dict[str, float]:
    """Best-effort live LTPs from Zerodha — never blocks or raises.

    Only uses an already-verified session for today (refreshed earlier by
    the daily `refresh_kite_token` cron/TOTP auto-login); never triggers an
    interactive login from a live web request. Returns {} on any failure so
    callers fall back to yfinance.
    """
    kite_auth = _load_kite_auth_module()
    if kite_auth is None:
        return {}
    try:
        return kite_auth.get_ltp_nonblocking(symbols)
    except Exception:
        return {}


def _fetch_ltps(symbols: list[str]) -> dict[str, float]:
    """Live prices for `symbols` — Zerodha Kite first, yfinance for the rest.

    Zerodha is preferred (real intraday LTP); yfinance silently covers any
    symbol Kite couldn't resolve (no session yet, delisted/renamed, etc.) so
    the UI never shows a blank price.
    """
    if not symbols:
        return {}
    try:
        from data import fetch_latest_price
    except ImportError:
        from backend.data import fetch_latest_price

    syms = [s.upper() for s in symbols]
    out: dict[str, float] = dict(_fetch_kite_ltps(syms))

    for sym in syms:
        if sym in out:
            continue
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
        intent_type = r.get("intent_type") or "intent"
        action = _intent_action_label(intent_type, sym, rationale)
        if action == "daily_review":
            detail = rationale.split("\n\n")[0][:200] if rationale else "Daily review"
            reasoning = rationale
        elif sym:
            detail = f"{sym}: {rationale[:120]}"
            reasoning = rationale
        else:
            detail = rationale[:160]
            reasoning = rationale
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
                "action": action,
                "detail": detail,
                "reasoning": reasoning,
                "createdAt": created_iso,
            }
        )
    return out


def _intent_action_label(
    intent_type: str,
    symbol: str | None,
    rationale: str,
) -> str:
    if intent_type == "birth":
        return "birth"
    if intent_type == "adjustment":
        return "adjustment"
    if intent_type == "eod":
        if not symbol and (
            "Executor:" in rationale
            or len(rationale) > 100
        ):
            return "daily_review"
        return "eod"
    return intent_type or "intent"


def get_daily_note_for_wolf(
    wolf_id: str,
    note_date: date | None = None,
) -> dict[str, Any]:
    """Today's fund manager summary from daily_review selection run or eod intent."""
    d = note_date or date.today()
    run = repo.get_latest_selection_run(wolf_id, d, RUN_TYPE_DAILY_REVIEW)
    if run:
        brain: dict[str, Any] = {}
        raw = run.get("gemini_raw_response")
        if raw:
            try:
                brain = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except (json.JSONDecodeError, TypeError):
                brain = {}
        gate = run.get("gate_results")
        if isinstance(gate, str):
            try:
                gate = json.loads(gate)
            except json.JSONDecodeError:
                gate = {}
        summary = ""
        if isinstance(gate, dict):
            summary = str(gate.get("summary") or "").strip()
        current = str(brain.get("current_intent") or "").strip()
        daily = str(brain.get("daily_update") or "").strip()
        parts = [p for p in (current, daily) if p]
        if summary:
            parts.append(summary)
        note = "\n\n".join(parts) if parts else None
        created = run.get("created_at")
        updated_at = (
            created.isoformat()
            if isinstance(created, datetime)
            else str(created) if created else d.isoformat()
        )
        return {
            "note": note,
            "updatedAt": updated_at,
            "source": "daily_review",
        }

    for r in repo.list_intents_for_wolf(wolf_id, limit=30):
        if r.get("intent_type") != "eod" or r.get("symbol"):
            continue
        intent_date = r.get("intent_date")
        day_s = (
            intent_date.isoformat()
            if hasattr(intent_date, "isoformat")
            else str(intent_date)
        )
        if day_s != d.isoformat():
            continue
        created = r.get("created_at")
        return {
            "note": r.get("rationale"),
            "updatedAt": (
                created.isoformat()
                if isinstance(created, datetime)
                else str(created or "")
            ),
            "source": "eod_intent",
        }

    return {"note": None, "updatedAt": None, "source": None}


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
