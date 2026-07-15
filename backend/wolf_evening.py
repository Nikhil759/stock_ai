"""Price refresh + target/stop auto-exit for Supabase wolves (paper mode)."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Literal

from db import repository as repo
import wolf_api
from wolf_executor import run_wolf_executor

log = logging.getLogger(__name__)


def check_exit_reason(
    ltp: float,
    sell_target: float,
    stop_loss: float,
) -> str | None:
    """Return ``target`` or ``stop_loss`` when an exit is triggered."""
    if sell_target > 0 and ltp >= sell_target:
        return "target"
    if stop_loss > 0 and ltp <= stop_loss:
        return "stop_loss"
    return None


def _portfolio_snapshot(wolf_id: str) -> tuple[float, float]:
    wolf = repo.get_wolf(wolf_id)
    if not wolf:
        return 0.0, 0.0
    holdings = repo.list_holdings_for_wolf(wolf_id, status="open")
    symbols = [str(h["symbol"]).upper() for h in holdings if h.get("symbol")]
    ltps = wolf_api._fetch_ltps(symbols)
    cash, _, portfolio, _ = wolf_api._portfolio_metrics(wolf, holdings, ltps)
    return cash, portfolio


def _log_eod_intent(
    wolf_id: str,
    closed: list[dict[str, Any]],
    updated: list[dict[str, Any]],
    failed: list[str],
) -> None:
    parts: list[str] = []
    if closed:
        sold = ", ".join(f"{c['ticker']} ({c['reason']})" for c in closed)
        parts.append(f"{len(closed)} sold: {sold}")
    if updated:
        parts.append(f"{len(updated)} held")
    if failed:
        parts.append(f"{len(failed)} price fetch failed ({', '.join(failed)})")
    if not parts:
        rationale = "EOD: no open positions."
    elif closed:
        rationale = "EOD auto-exit: " + "; ".join(parts) + "."
    else:
        rationale = "EOD check: " + "; ".join(parts) + "."

    try:
        repo.log_intent(
            wolf_id,
            date.today(),
            "eod",
            rationale=rationale,
        )
    except Exception:
        log.exception("failed to log eod intent for %s", wolf_id)


def _log_refresh_exits(wolf_id: str, closed: list[dict[str, Any]]) -> None:
    for c in closed:
        sym = c.get("ticker", "")
        reason = c.get("reason", "exit")
        price = c.get("price", 0)
        try:
            repo.log_intent(
                wolf_id,
                date.today(),
                "adjustment",
                symbol=sym or None,
                rationale=f"Intraday auto-exit ({reason}) @ ₹{price:.2f}",
            )
        except Exception:
            log.exception("failed to log refresh exit for %s %s", wolf_id, sym)


def run_wolf_exit_check(
    wolf_id: str,
    *,
    dry_run: bool = False,
    log_profile: Literal["eod", "refresh"] = "eod",
) -> dict[str, Any]:
    """Fetch LTPs, auto-sell on target/stop, return refresh summary."""
    wolf = repo.get_wolf(wolf_id)
    if wolf is None:
        return {"error": "Wolf not found"}

    if wolf.get("status") == "closed":
        return {
            "skipped": True,
            "reason": "closed",
            "wolf_id": wolf_id,
            "message": "Wolf is closed.",
        }

    if wolf.get("status") == "paused":
        return {
            "skipped": True,
            "reason": "paused",
            "wolf_id": wolf_id,
            "message": "Paused — no price checks or exits.",
        }

    holdings = repo.list_open_holdings(wolf_id)
    if not holdings:
        if not dry_run and log_profile == "eod":
            _log_eod_intent(wolf_id, [], [], [])
        return {
            "botId": wolf_id,
            "checked": 0,
            "closed": [],
            "updated": [],
            "failed": [],
        }

    symbols = [str(h["symbol"]).upper() for h in holdings if h.get("symbol")]
    ltps = wolf_api._fetch_ltps(symbols)

    sells: list[dict[str, Any]] = []
    closed: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []
    failed: list[str] = []
    fill_prices: dict[str, float] = {}

    for h in holdings:
        sym = str(h["symbol"]).upper()
        qty = int(h.get("quantity") or 0)
        if qty <= 0:
            continue

        ltp = ltps.get(sym)
        if ltp is None or ltp <= 0:
            failed.append(sym)
            continue

        target = float(h.get("sell_target") or 0)
        stop = float(h.get("stop_loss") or 0)
        reason = check_exit_reason(ltp, target, stop)

        if reason:
            sells.append({"symbol": sym, "quantity": qty})
            fill_prices[sym] = ltp
            closed.append(
                {
                    "ticker": sym,
                    "price": ltp,
                    "reason": reason,
                    "proceeds": round(qty * ltp, 2),
                }
            )
        else:
            updated.append({"ticker": sym, "ltp": ltp})

    mode = str(wolf.get("mode") or "paper")
    executor_result: dict[str, Any] | None = None
    if sells:
        executor_result = run_wolf_executor(
            wolf_id,
            mode,
            sells=sells,
            buys=[],
            fill_prices=fill_prices,
            dry_run=dry_run,
        )

    if not dry_run:
        if log_profile == "eod":
            _log_eod_intent(wolf_id, closed, updated, failed)
        elif closed:
            _log_refresh_exits(wolf_id, closed)

    cash, portfolio = _portfolio_snapshot(wolf_id)
    return {
        "botId": wolf_id,
        "checked": len(holdings),
        "closed": closed,
        "updated": updated,
        "failed": failed,
        "availableCash": cash,
        "portfolioValue": portfolio,
        "executor": executor_result,
        "dry_run": dry_run,
    }


def run_wolf_evening(
    wolf_id: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """End-of-day price check + auto-exit (logs eod intent)."""
    return run_wolf_exit_check(wolf_id, dry_run=dry_run, log_profile="eod")


def run_wolf_price_refresh(
    wolf_id: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Intraday refresh + auto-exit (logs adjustment only when sells occur)."""
    return run_wolf_exit_check(wolf_id, dry_run=dry_run, log_profile="refresh")


def run_evening_all_wolves(*, dry_run: bool = False) -> list[dict[str, Any]]:
    """Evening job for every active Supabase wolf."""
    wolves = repo.list_active_wolves()
    if not wolves:
        log.info("no active wolves for evening job")
        return []

    results: list[dict[str, Any]] = []
    for wolf in wolves:
        wolf_id = wolf["wolf_id"]
        log.info("evening job starting for %s", wolf_id)
        try:
            result = run_wolf_evening(wolf_id, dry_run=dry_run)
        except Exception:
            log.exception("evening job failed for %s", wolf_id)
            result = {"botId": wolf_id, "error": "evening job failed"}
        results.append(result)
    return results


def print_evening_summary(result: dict[str, Any]) -> None:
    wolf_id = result.get("botId") or result.get("wolf_id") or "?"
    print(f"\n=== Evening — Wolf {wolf_id} ===")
    if result.get("skipped"):
        print(f"Skipped: {result.get('reason')} — {result.get('message', '')}")
        return
    if result.get("error"):
        print(f"Error: {result['error']}")
        return
    print(
        f"Checked {result.get('checked', 0)} · "
        f"sold {len(result.get('closed') or [])} · "
        f"updated {len(result.get('updated') or [])} · "
        f"failed {len(result.get('failed') or [])}"
    )
    for c in result.get("closed") or []:
        print(
            f"  SELL {c['ticker']} @ {c['price']} ({c['reason']}) "
            f"→ ₹{c['proceeds']:,.0f}"
        )
