"""Deterministic trade execution for Wolf Capital (no LLM)."""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from db import repository as repo
from wolf_brain.validate import normalize_guardrails
from wolf_executor.kite_stub import place_kite_order
from wolf_executor.summary import build_summary

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")


def _holding_row(symbol: str, h: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": symbol.upper(),
        "quantity": int(h.get("quantity") or 0),
        "avg_buy_price": float(h.get("avg_buy_price") or 0),
        "current_price": float(
            h.get("current_price") or h.get("avg_buy_price") or 0
        ),
        "sell_target": float(h.get("target") or h.get("sell_target") or 0),
        "stop_loss": float(h.get("stop_loss") or 0),
    }


def _portfolio_value(cash: float, holdings: dict[str, dict]) -> float:
    holdings_val = sum(
        h["quantity"] * h["current_price"] for h in holdings.values()
    )
    return round(cash + holdings_val, 2)


def _deployed_value(holdings: dict[str, dict]) -> float:
    return round(
        sum(h["quantity"] * h["current_price"] for h in holdings.values()),
        2,
    )


def _position_value(holdings: dict[str, dict], symbol: str) -> float:
    h = holdings.get(symbol.upper())
    if not h or h["quantity"] <= 0:
        return 0.0
    return round(h["quantity"] * h["current_price"], 2)


def _sell_fill_price(
    symbol: str,
    holding: dict[str, Any],
    fill_prices: dict[str, float] | None,
) -> float:
    sym = symbol.upper()
    if fill_prices and sym in fill_prices:
        return round(float(fill_prices[sym]), 2)
    cp = float(holding.get("current_price") or 0)
    if cp > 0:
        return round(cp, 2)
    return round(float(holding.get("avg_buy_price") or 0), 2)


def _load_state(
    wolf_id: str,
    cash_available: float | None,
    holdings: list[dict] | None,
    guardrails: dict | None,
) -> tuple[float, dict[str, dict], dict[str, float]]:
    wolf: dict[str, Any] | None = None
    need_wolf = (
        cash_available is None or holdings is None or guardrails is None
    )
    if need_wolf:
        wolf = repo.get_wolf(wolf_id)
        if wolf is None:
            raise ValueError(f"wolf not found: {wolf_id}")

    cash = (
        float(cash_available)
        if cash_available is not None
        else float(wolf["budget_available"])
    )
    if holdings is not None:
        held = {
            _holding_row(h["symbol"], h)["symbol"]: _holding_row(h["symbol"], h)
            for h in holdings
        }
    else:
        held = {
            h["symbol"].upper(): _holding_row(h["symbol"], h)
            for h in repo.list_open_holdings(wolf_id)
        }
    g = normalize_guardrails(
        guardrails if guardrails is not None else (wolf or {}).get("guardrails")
    )
    return cash, held, g


def _persist_sell(
    wolf_id: str,
    symbol: str,
    quantity: int,
    price: float,
    *,
    mode: str,
    linked_run_id: int | None,
) -> None:
    repo.record_trade(
        wolf_id,
        symbol,
        "SELL",
        quantity,
        price,
        mode=mode,
        linked_run_id=linked_run_id,
    )
    repo.reduce_holding_quantity(wolf_id, symbol, quantity)
    wolf = repo.get_wolf(wolf_id)
    if wolf:
        new_cash = float(wolf["budget_available"]) + quantity * price
        repo.set_budget_available(wolf_id, new_cash)


def _persist_buy(
    wolf_id: str,
    symbol: str,
    quantity: int,
    price: float,
    *,
    target: float | None,
    stop_loss: float | None,
    mode: str,
    linked_run_id: int | None,
    holdings: dict[str, dict],
) -> None:
    sym = symbol.upper()
    existing = holdings.get(sym)
    if existing and existing["quantity"] > 0:
        old_qty = existing["quantity"]
        new_qty = old_qty + quantity
        new_avg = round(
            (old_qty * existing["avg_buy_price"] + quantity * price) / new_qty,
            2,
        )
    else:
        new_qty = quantity
        new_avg = price

    repo.record_trade(
        wolf_id,
        symbol,
        "BUY",
        quantity,
        price,
        mode=mode,
        linked_run_id=linked_run_id,
    )
    repo.upsert_holding(
        wolf_id,
        sym,
        new_qty,
        new_avg,
        sell_target=target,
        stop_loss=stop_loss,
    )
    wolf = repo.get_wolf(wolf_id)
    if wolf:
        new_cash = float(wolf["budget_available"]) - quantity * price
        repo.set_budget_available(wolf_id, new_cash)


def run_wolf_executor(
    wolf_id: str,
    mode: str,
    sells: list[dict],
    buys: list[dict],
    *,
    cash_available: float | None = None,
    holdings: list[dict] | None = None,
    guardrails: dict | None = None,
    fill_prices: dict[str, float] | None = None,
    daily_loss_pct: float = 0.0,
    dry_run: bool = False,
    linked_run_id: int | None = None,
) -> dict[str, Any]:
    """Execute sells then buys with deterministic guardrails (paper or real stub)."""
    if mode not in ("paper", "real"):
        raise ValueError(f"mode must be 'paper' or 'real', got {mode!r}")

    cash, holdings_map, g = _load_state(
        wolf_id, cash_available, holdings, guardrails
    )
    cash_before = round(cash, 2)
    portfolio_before = _portfolio_value(cash, holdings_map)

    actions_taken: list[dict] = []
    actions_rejected: list[dict] = []
    guardrail_hits = {
        "min_trade_value": False,
        "max_per_stock": False,
        "max_capital_deployed": False,
        "max_daily_loss": False,
    }

    # --- Sells first (generally not guardrailed) ---
    for sell in sells:
        sym = str(sell.get("symbol", "")).upper()
        if not sym:
            actions_rejected.append({"symbol": "?", "reason": "Missing symbol"})
            continue
        held = holdings_map.get(sym)
        if not held or held["quantity"] <= 0:
            actions_rejected.append(
                {"symbol": sym, "reason": f"Not holding {sym} — sell skipped"}
            )
            continue
        qty = int(sell.get("quantity") or 0)
        if qty <= 0:
            actions_rejected.append(
                {"symbol": sym, "reason": "Sell quantity must be positive"}
            )
            continue
        qty = min(qty, held["quantity"])
        price = _sell_fill_price(sym, held, fill_prices)
        if price <= 0:
            actions_rejected.append(
                {"symbol": sym, "reason": f"No fill price for {sym}"}
            )
            continue
        value = round(qty * price, 2)

        if mode == "real" and not dry_run:
            place_kite_order(
                wolf_id=wolf_id,
                symbol=sym,
                action="SELL",
                quantity=qty,
                price=price,
            )

        if mode == "paper" and not dry_run:
            _persist_sell(
                wolf_id,
                sym,
                qty,
                price,
                mode=mode,
                linked_run_id=linked_run_id,
            )
            cash, holdings_map, _ = _load_state(
                wolf_id, None, None, guardrails
            )
        else:
            cash = round(cash + value, 2)
            remaining = held["quantity"] - qty
            if remaining <= 0:
                holdings_map.pop(sym, None)
            else:
                held["quantity"] = remaining

        actions_taken.append(
            {
                "action": "SELL",
                "symbol": sym,
                "quantity": qty,
                "price": price,
                "value": value,
                "status": "filled",
            }
        )

    daily_loss_halt = daily_loss_pct > g["max_daily_loss_pct"]
    if daily_loss_halt:
        guardrail_hits["max_daily_loss"] = True

    # --- Buys in order ---
    for buy in buys:
        sym = str(buy.get("symbol", "")).upper()
        if not sym:
            actions_rejected.append({"symbol": "?", "reason": "Missing symbol"})
            continue

        if daily_loss_halt:
            actions_rejected.append(
                {
                    "symbol": sym,
                    "reason": (
                        f"Daily loss {daily_loss_pct:.2f}% exceeds "
                        f"max_daily_loss guardrail ({g['max_daily_loss_pct']}%)"
                    ),
                }
            )
            continue

        qty = int(buy.get("quantity") or 0)
        qty = max(0, qty)  # round down to whole shares
        price = round(float(buy.get("buy_price") or 0), 2)
        if qty <= 0 or price <= 0:
            actions_rejected.append(
                {
                    "symbol": sym,
                    "reason": "Invalid quantity or buy_price after rounding",
                }
            )
            continue

        cost = round(qty * price, 2)

        if cost < g["min_trade_value"]:
            guardrail_hits["min_trade_value"] = True
            actions_rejected.append(
                {
                    "symbol": sym,
                    "reason": (
                        f"Trade value {_inr(cost)} below min_trade_value "
                        f"({_inr(g['min_trade_value'])})"
                    ),
                }
            )
            continue

        portfolio_val = _portfolio_value(cash, holdings_map)
        pos_after = _position_value(holdings_map, sym) + cost
        max_pos = portfolio_val * g["max_per_stock_pct"] / 100.0
        if pos_after > max_pos + 0.01:
            guardrail_hits["max_per_stock"] = True
            actions_rejected.append(
                {
                    "symbol": sym,
                    "reason": (
                        f"Would exceed max_per_stock guardrail "
                        f"({g['max_per_stock_pct']}% of portfolio = {_inr(max_pos)})"
                    ),
                }
            )
            continue

        deployed_after = _deployed_value(holdings_map) + cost
        max_deployed = portfolio_val * g["max_capital_deployed_pct"] / 100.0
        if deployed_after > max_deployed + 0.01:
            guardrail_hits["max_capital_deployed"] = True
            actions_rejected.append(
                {
                    "symbol": sym,
                    "reason": (
                        f"Would exceed max_capital_deployed guardrail "
                        f"({g['max_capital_deployed_pct']}% = {_inr(max_deployed)})"
                    ),
                }
            )
            continue

        if cost > cash + 0.01:
            actions_rejected.append(
                {
                    "symbol": sym,
                    "reason": (
                        f"Insufficient cash ({_inr(cash)} < {_inr(cost)})"
                    ),
                }
            )
            continue

        target = buy.get("target")
        stop_loss = buy.get("stop_loss")
        stop_placed = stop_loss is not None and float(stop_loss) > 0

        if mode == "real" and not dry_run:
            place_kite_order(
                wolf_id=wolf_id,
                symbol=sym,
                action="BUY",
                quantity=qty,
                price=price,
            )

        if mode == "paper" and not dry_run:
            _persist_buy(
                wolf_id,
                sym,
                qty,
                price,
                target=float(target) if target is not None else None,
                stop_loss=float(stop_loss) if stop_loss is not None else None,
                mode=mode,
                linked_run_id=linked_run_id,
                holdings=holdings_map,
            )
            cash, holdings_map, _ = _load_state(
                wolf_id, None, None, guardrails
            )
        else:
            cash = round(cash - cost, 2)
            existing = holdings_map.get(sym)
            if existing and existing["quantity"] > 0:
                old_qty = existing["quantity"]
                new_qty = old_qty + qty
                new_avg = round(
                    (old_qty * existing["avg_buy_price"] + qty * price) / new_qty,
                    2,
                )
                existing["quantity"] = new_qty
                existing["avg_buy_price"] = new_avg
                existing["current_price"] = price
            else:
                holdings_map[sym] = {
                    "symbol": sym,
                    "quantity": qty,
                    "avg_buy_price": price,
                    "current_price": price,
                    "sell_target": float(target or 0),
                    "stop_loss": float(stop_loss or 0),
                }

        actions_taken.append(
            {
                "action": "BUY",
                "symbol": sym,
                "quantity": qty,
                "price": price,
                "value": cost,
                "stop_loss_placed": stop_placed,
                "status": "filled",
            }
        )

    cash_after = round(cash, 2)
    portfolio_after = _portfolio_value(cash, holdings_map)

    guardrail_checks = {
        k: "reject" if v else "pass"
        for k, v in guardrail_hits.items()
    }

    return {
        "wolf_id": wolf_id,
        "executed_at": datetime.now(IST).isoformat(timespec="seconds"),
        "actions_taken": actions_taken,
        "actions_rejected": actions_rejected,
        "cash_before": cash_before,
        "cash_after": cash_after,
        "portfolio_value_before": portfolio_before,
        "portfolio_value_after": portfolio_after,
        "guardrail_checks": guardrail_checks,
        "summary": build_summary(
            actions_taken=actions_taken,
            cash_before=cash_before,
            cash_after=cash_after,
        ),
    }


def _inr(n: float) -> str:
    return f"₹{n:,.2f}"
