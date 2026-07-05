"""Evening job — refresh, exits, circuit breaker, redeploy freed cash."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import eod
from fund_manager.breaker import check_and_trip_breaker, ensure_day_baseline
from fund_manager.redeploy import handle_freed_cash

import database as db

log = logging.getLogger(__name__)


@dataclass
class EveningSummary:
    bot_id: int
    refresh: dict = field(default_factory=dict)
    redeploys: list[dict] = field(default_factory=list)
    breaker_tripped: bool = False
    daily_loss_pct: float = 0.0

    def to_dict(self) -> dict:
        return {
            "botId": self.bot_id,
            "refresh": self.refresh,
            "redeploys": self.redeploys,
            "breakerTripped": self.breaker_tripped,
            "dailyLossPct": self.daily_loss_pct,
        }


def run_evening_job(
    bot_id: int,
    *,
    run_date: date | str | None = None,
    dry_run: bool = False,
    skip_redeploy: bool = False,
) -> EveningSummary:
    """4pm-style job: price refresh + auto-exits, redeploy freed cash, check breaker."""
    summary = EveningSummary(bot_id=bot_id)
    ensure_day_baseline(bot_id)

    refresh = eod.run_eod(bot_id)
    summary.refresh = refresh

    if refresh.get("error") or refresh.get("skipped"):
        return summary

    if not skip_redeploy:
        for closed in refresh.get("closed") or []:
            try:
                outcome = handle_freed_cash(
                    bot_id,
                    closed["proceeds"],
                    closed["ticker"],
                    run_date=run_date,
                    dry_run=dry_run,
                )
                summary.redeploys.append(outcome)
            except Exception:
                log.exception("handle_freed_cash failed for %s", closed.get("ticker"))

    summary.breaker_tripped = check_and_trip_breaker(bot_id)
    from fund_manager.breaker import daily_loss_pct
    summary.daily_loss_pct = daily_loss_pct(bot_id)

    if not dry_run:
        from fund_manager.daily_note import DayJournal
        DayJournal.load(bot_id).add_evening(summary)

    return summary


def print_evening_summary(summary: EveningSummary) -> None:
    print(f"\n=== Evening job — Wolf {summary.bot_id} ===")
    r = summary.refresh
    print(f"  Positions checked: {r.get('checked', 0)}")
    print(f"  Closed: {len(r.get('closed') or [])}")
    for c in r.get("closed") or []:
        print(f"    {c['ticker']} @ ₹{c['price']:,.2f} ({c['reason']}) → ₹{c['proceeds']:,.0f}")
    print(f"  Cash: ₹{r.get('availableCash', 0):,.2f}")
    for rd in summary.redeploys:
        print(f"  Redeploy {rd.get('action')}: {rd.get('reason', rd.get('decision', {}))}")
    print(f"  Daily loss: {summary.daily_loss_pct:.1f}%")
    if summary.breaker_tripped:
        print("  CIRCUIT BREAKER TRIPPED — no new buys until cleared")
