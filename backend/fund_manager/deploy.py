"""Morning deploy — turn today's intentions into paper positions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from fund_manager.gates import GateAction, GateResult, run_gates
from fund_manager.intentions import load_intentions_for_bot
from fund_manager.ledger import BotLedger
from fund_manager.breaker import pre_open_reset

import database as db


@dataclass
class DeploySummary:
    bot_id: int
    strategy: str
    date: str
    bought: list[GateResult] = field(default_factory=list)
    trimmed: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    pending: list[GateResult] = field(default_factory=list)
    halted: bool = False
    halt_reason: str = ""
    cash_remaining: float = 0.0

    def to_dict(self) -> dict:
        return {
            "botId": self.bot_id,
            "strategy": self.strategy,
            "date": self.date,
            "halted": self.halted,
            "haltReason": self.halt_reason,
            "bought": [
                {"ticker": r.order.ticker, "shares": r.order.shares, "cost": r.order.cost, "reason": r.reason}
                for r in self.bought if r.order
            ],
            "trimmed": self.trimmed,
            "skipped": [{"ticker": t, "reason": r} for t, r in self.skipped],
            "pending": [
                {"ticker": r.order.ticker, "shares": r.order.shares, "cost": r.order.cost, "reason": r.reason}
                for r in self.pending if r.order
            ],
            "cashRemaining": self.cash_remaining,
        }


def morning_deploy(
    bot_id: int,
    run_date: date | str | None = None,
    *,
    dry_run: bool = False,
) -> DeploySummary:
    """Load today's intentions and run each ranked pick through the 9 gates."""
    pre_open_reset(bot_id)
    ledger = BotLedger(bot_id)
    bot = ledger.bot()
    d = run_date or date.today()
    if isinstance(d, date):
        date_str = d.isoformat()
    else:
        date_str = str(d)

    data = load_intentions_for_bot(bot_id, bot["strategy"], d)
    picks = list(data.get("result", {}).get("picks") or [])

    summary = DeploySummary(
        bot_id=bot_id,
        strategy=bot["strategy"],
        date=date_str,
    )

    if not picks:
        summary.cash_remaining = ledger.cash_available()
        summary.skipped.append(("", "No picks in intentions file."))
        return summary

    # Simulate state as we process picks in order
    open_trades = db.get_trades(bot_id, status="open")
    cash = ledger.cash_available()

    for pick in picks:
        pick = {**pick, "_strategy": data.get("strategy", bot["strategy"])}
        result = run_gates(
            bot_id,
            pick,
            open_trades=open_trades,
            cash_override=cash,
            skip_execute=dry_run,
        )

        if result.action == GateAction.HALT:
            summary.halted = True
            summary.halt_reason = result.reason
            break

        ticker = pick.get("ticker", "?").upper()

        if result.action == GateAction.SKIP:
            summary.skipped.append((ticker, result.reason))
            continue

        if result.action == GateAction.PENDING:
            summary.pending.append(result)
            continue

        if result.action == GateAction.EXECUTE:
            summary.bought.append(result)
            if result.order:
                if result.order.trimmed:
                    summary.trimmed.append(ticker)
                if not dry_run:
                    # Refresh simulated state after execute
                    open_trades = db.get_trades(bot_id, status="open")
                    cash = ledger.cash_available()
                else:
                    cash -= result.order.cost
                    open_trades.append({
                        "ticker": result.order.ticker,
                        "qty": result.order.shares,
                        "entry": result.order.fill_price,
                        "status": "open",
                    })

    summary.cash_remaining = cash if dry_run else ledger.cash_available()

    if not dry_run:
        from fund_manager.daily_note import DayJournal
        DayJournal.load(bot_id, date_str).add_morning(summary)

    return summary


def print_deploy_summary(summary: DeploySummary) -> None:
    print(f"\n=== Morning deploy — Wolf {summary.bot_id} ({summary.strategy} {summary.date}) ===")
    if summary.halted:
        print(f"HALTED: {summary.halt_reason}")
    for r in summary.bought:
        o = r.order
        if o:
            tag = " [trimmed]" if o.trimmed else ""
            print(f"  BOUGHT   {o.ticker}: {o.shares} @ ₹{o.fill_price:,.2f} = ₹{o.cost:,.0f}{tag}")
    for r in summary.pending:
        o = r.order
        if o:
            print(f"  PENDING  {o.ticker}: {o.shares} @ ₹{o.fill_price:,.2f} — {r.reason}")
    for ticker, reason in summary.skipped:
        label = ticker or "(none)"
        print(f"  SKIPPED  {label}: {reason}")
    print(f"  Cash left: ₹{summary.cash_remaining:,.2f}")
