"""Daily fund-manager note — human-readable summary of today's moves."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

import database as db
from fund_manager.ledger import BotLedger

IST = ZoneInfo("Asia/Kolkata")


@dataclass
class NoteLine:
    kind: str
    text: str
    reasoning: str = ""


@dataclass
class NoteSection:
    phase: str
    title: str
    lines: list[NoteLine] = field(default_factory=list)


@dataclass
class DayJournal:
    bot_id: int
    note_date: str
    sections: list[NoteSection] = field(default_factory=list)

    @classmethod
    def load(cls, bot_id: int, note_date: str | None = None) -> DayJournal:
        d = note_date or date.today().isoformat()
        journal = cls(bot_id=bot_id, note_date=d)
        existing = db.get_daily_note(bot_id, d)
        if existing and existing.get("sectionsJson"):
            journal.sections = _sections_from_json(existing["sectionsJson"])
        return journal

    def _find_or_add_section(self, phase: str, title: str) -> NoteSection:
        for s in self.sections:
            if s.phase == phase:
                return s
        section = NoteSection(phase=phase, title=title)
        self.sections.append(section)
        return section

    def add_line(self, phase: str, title: str, kind: str, text: str, reasoning: str = "") -> None:
        section = self._find_or_add_section(phase, title)
        section.lines.append(NoteLine(kind=kind, text=text, reasoning=reasoning))

    def add_morning(self, summary) -> None:
        from fund_manager.deploy import DeploySummary

        if not isinstance(summary, DeploySummary):
            return
        phase, title = "morning", "Morning deploy"
        if summary.halted:
            self.add_line(phase, title, "halted", f"HALTED — {summary.halt_reason}")
            self.publish()
            return
        for r in summary.bought:
            o = r.order
            if not o:
                continue
            trim = " (trimmed to fit per-stock cap)" if o.trimmed else ""
            self.add_line(
                phase, title, "bought",
                f"Bought {o.shares} × {o.ticker} @ ₹{o.fill_price:,.2f} = ₹{o.cost:,.0f}{trim}",
                o.rationale or r.reason,
            )
        for r in summary.pending:
            o = r.order
            if not o:
                continue
            self.add_line(
                phase, title, "pending",
                f"Awaiting approval: {o.shares} × {o.ticker} @ ₹{o.fill_price:,.2f} = ₹{o.cost:,.0f}",
                o.rationale or r.reason,
            )
        for ticker, reason in summary.skipped:
            label = ticker or "—"
            self.add_line(phase, title, "skipped", f"Skipped {label}: {reason}")
        if not summary.bought and not summary.pending and not summary.skipped:
            self.add_line(phase, title, "none", "No trades placed.")
        self.add_line(
            phase, title, "cash",
            f"Cash after morning deploy: ₹{summary.cash_remaining:,.2f}",
        )
        self.publish()

    def add_redeploy(self, closed_ticker: str, freed_amount: float, outcome: dict) -> None:
        phase, title = "midday", "Cash redeploy"
        action = outcome.get("action", "unknown")
        decision = outcome.get("decision") or {}
        rationale = decision.get("rationale", "") if isinstance(decision, dict) else ""

        if action == "hold":
            self.add_line(
                phase, title, "hold",
                f"Held ₹{freed_amount:,.0f} freed from {closed_ticker} close",
                rationale or outcome.get("reason", ""),
            )
        elif action == "execute":
            order = outcome.get("order") or {}
            self.add_line(
                phase, title, "bought",
                f"Redeployed into {order.get('ticker', '?')}: "
                f"{order.get('shares', '?')} shares for ₹{order.get('cost', 0):,.0f}",
                rationale or outcome.get("reason", ""),
            )
        elif action == "pending":
            order = outcome.get("order") or {}
            self.add_line(
                phase, title, "pending",
                f"Redeploy pending approval: {order.get('ticker', '?')}",
                rationale or outcome.get("reason", ""),
            )
        elif action == "halted":
            self.add_line(phase, title, "halted", outcome.get("reason", "Circuit breaker — cash held."))
        elif action == "error":
            self.add_line(phase, title, "error", f"Redeploy failed: {outcome.get('reason', 'unknown')}")
        else:
            self.add_line(
                phase, title, action,
                f"Redeploy after {closed_ticker}: {outcome.get('reason', action)}",
                rationale,
            )
        self.publish()

    def add_evening(self, summary) -> None:
        from fund_manager.evening import EveningSummary

        if not isinstance(summary, EveningSummary):
            return
        phase, title = "evening", "Evening wrap-up"
        refresh = summary.refresh or {}
        for c in refresh.get("closed") or []:
            reason = "target hit" if c.get("reason") == "target" else "stop-loss hit"
            self.add_line(
                phase, title, "closed",
                f"Sold {c.get('ticker')} @ ₹{c.get('price', 0):,.2f} ({reason}) "
                f"→ ₹{c.get('proceeds', 0):,.0f} back to cash",
            )
        checked = refresh.get("checked", 0)
        updated = len(refresh.get("updated") or [])
        if checked and not refresh.get("closed"):
            self.add_line(
                phase, title, "refresh",
                f"Refreshed {checked} position(s); {updated} price update(s), no exits.",
            )
        for i, rd in enumerate(summary.redeploys):
            closed = (refresh.get("closed") or [])
            c = closed[i] if i < len(closed) else {}
            self._append_redeploy_line(
                phase, title,
                c.get("ticker", "?"),
                rd.get("freedAmount", c.get("proceeds", 0)),
                rd,
            )
        self.add_line(
            phase, title, "pnl",
            f"Daily loss vs allocation: {summary.daily_loss_pct:.1f}%",
        )
        if summary.breaker_tripped:
            self.add_line(
                phase, title, "breaker",
                "Circuit breaker TRIPPED — all new buys halted until cleared.",
            )
        else:
            self.add_line(phase, title, "breaker", "Circuit breaker: OK")
        ledger = BotLedger(self.bot_id)
        bot = ledger.bot()
        self.add_line(
            phase, title, "snapshot",
            f"End of day — Cash ₹{bot['availableCash']:,.0f} · "
            f"Portfolio ₹{bot['portfolioValue']:,.0f} · P&L {ledger.pnl():+,.0f}",
        )
        self.publish()

    def _append_redeploy_line(
        self, phase: str, title: str, closed_ticker: str, freed: float, outcome: dict,
    ) -> None:
        action = outcome.get("action", "")
        decision = outcome.get("decision") or {}
        rationale = decision.get("rationale", "") if isinstance(decision, dict) else ""
        if action == "hold":
            self.add_line(
                phase, title, "hold",
                f"Held ₹{freed:,.0f} freed from {closed_ticker}",
                rationale,
            )
        elif action in ("execute", "pending", "skip", "halted"):
            self.add_line(
                phase, title, f"redeploy_{action}",
                outcome.get("reason", f"Redeploy: {action}"),
                rationale,
            )

    def render(self) -> str:
        ledger = BotLedger(self.bot_id)
        bot = ledger.bot()
        now = datetime.now(IST).strftime("%I:%M %p IST").lstrip("0")
        header = (
            f"Fund Manager Daily Note — {bot['name']} · {bot['strategy']} · {self.note_date}\n"
            f"Last updated {now}\n"
        )
        if not self.sections:
            return header + "\nNo fund manager activity recorded yet today.\n"

        parts = [header]
        for section in self.sections:
            parts.append(f"\n## {section.title}\n")
            for line in section.lines:
                parts.append(f"• {line.text}")
                if line.reasoning:
                    parts.append(f"  → {line.reasoning}")
            parts.append("")
        return "\n".join(parts).strip() + "\n"

    def publish(self) -> dict:
        text = self.render()
        sections_json = json.dumps([
            {
                "phase": s.phase,
                "title": s.title,
                "lines": [
                    {"kind": l.kind, "text": l.text, "reasoning": l.reasoning}
                    for l in s.lines
                ],
            }
            for s in self.sections
        ])
        result = db.upsert_daily_note(self.bot_id, self.note_date, text, sections_json)
        db.log_action(
            self.bot_id,
            "fund_manager_daily_note",
            _one_line_summary(self.sections),
            text,
        )
        return result


def _one_line_summary(sections: list[NoteSection]) -> str:
    counts = {"bought": 0, "closed": 0, "skipped": 0, "hold": 0, "pending": 0}
    for s in sections:
        for line in s.lines:
            if line.kind in counts:
                counts[line.kind] += 1
            elif line.kind.startswith("redeploy"):
                counts["hold"] += 1
    bits = []
    if counts["bought"]:
        bits.append(f"{counts['bought']} buy(s)")
    if counts["closed"]:
        bits.append(f"{counts['closed']} sell(s)")
    if counts["pending"]:
        bits.append(f"{counts['pending']} pending")
    if counts["skipped"]:
        bits.append(f"{counts['skipped']} skipped")
    if counts["hold"]:
        bits.append("cash held")
    return "Today's moves: " + (", ".join(bits) if bits else "no trades")


def _sections_from_json(raw: str) -> list[NoteSection]:
    data = json.loads(raw)
    sections = []
    for s in data:
        lines = [NoteLine(**l) for l in s.get("lines", [])]
        sections.append(NoteSection(phase=s["phase"], title=s["title"], lines=lines))
    return sections


def get_today_note(bot_id: int) -> dict | None:
    return db.get_daily_note(bot_id, date.today().isoformat())
