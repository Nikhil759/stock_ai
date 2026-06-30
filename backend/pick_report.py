"""Structured pick reports — plain-language reasoning for UI."""

from __future__ import annotations

from strategies import STRATEGY_NAMES

_SIGNAL_LABELS = {
    "trend": "Trend",
    "nearHigh": "52-week high",
    "rs": "Relative strength",
    "breakout": "Breakout",
    "base": "Price base",
    "boxHigh": "Box top",
    "boxLow": "Box bottom",
    "widthPct": "Box width",
    "rsi2": "RSI (2-day)",
    "ma200": "200-day average",
    "stop": "Stop-loss level",
}

_STRATEGY_WHY = {
    "value": "This stock passed our Graham-style value screen on Nifty 200 — we look for quality companies trading below fair value.",
    "winners": "This stock scored well on our momentum + quality screen — strong trend, leadership, and optional breakout volume.",
    "box": "Price broke out above a tight Darvas-style box with volume confirmation — a classic momentum entry pattern.",
    "dip": "Short-term oversold in a longer-term uptrend — RSI(2) dip with price still above the 200-day average.",
}


def _fmt_inr(n: float) -> str:
    return "₹" + f"{round(n):,}"


def _signal_bullets(signal: dict) -> list[dict]:
    bullets = []
    for key, val in (signal or {}).items():
        if val is None or val == "":
            continue
        label = _SIGNAL_LABELS.get(key, key.replace("_", " ").title())
        if isinstance(val, float):
            detail = f"{val:,.2f}" if val > 100 else str(val)
        else:
            detail = str(val)
        bullets.append({"label": label, "detail": detail})
    return bullets


def _value_bullets(candidate: dict) -> list[dict]:
    bullets = []
    mapping = [
        ("pe", "P/E ratio", lambda v: f"{v}"),
        ("pb", "P/B ratio", lambda v: f"{v}"),
        ("roe", "Return on equity", lambda v: f"{v}%"),
        ("de", "Debt / equity", lambda v: f"{v}"),
        ("curr", "Current ratio", lambda v: f"{v}"),
        ("graham", "Graham score (P/E × P/B)", lambda v: f"{v}"),
    ]
    for key, label, fmt in mapping:
        val = candidate.get(key)
        if val is not None:
            bullets.append({"label": label, "detail": fmt(val)})
    pc = candidate.get("passCount")
    if pc is not None:
        bullets.append({
            "label": "Filters passed",
            "detail": f"{pc}/6 value checks" + (" — all clear" if candidate.get("passAll") else ""),
        })
    return bullets


def build_pick_report(candidate: dict, strategy: str) -> dict:
    """Build a presentable pick report dict for API + UI."""
    signal = candidate.get("signal") or {}
    bullets = _signal_bullets(signal)
    if strategy == "value" and not bullets:
        bullets = _value_bullets(candidate)

    buy = candidate.get("buyPrice") or 0
    sell = candidate.get("sellPrice") or 0
    stop = candidate.get("stopLoss")
    upside = candidate.get("upside") or ""

    trade_plan = [
        {"label": "Buy at", "value": candidate.get("buyFmt") or _fmt_inr(buy), "hint": "EOD close used for paper trade"},
        {"label": "Sell target", "value": candidate.get("sellFmt") or _fmt_inr(sell), "hint": candidate.get("gainNote") or upside},
        {"label": "Position size", "value": f"{candidate.get('shares', 0)} shares · {candidate.get('costFmt', '—')}", "hint": "Based on your allocation pool"},
    ]
    if stop:
        trade_plan.append({"label": "Stop-loss", "value": f"₹{stop:,.2f}", "hint": "Automatic exit if price falls here"})

    headline = candidate.get("recLabel") or "Recommended pick"
    summary = candidate.get("recNote") or _STRATEGY_WHY.get(strategy, "")
    strategy_intro = _STRATEGY_WHY.get(strategy, "")

    sections = []
    if strategy_intro:
        sections.append({"title": "Strategy fit", "body": strategy_intro})
    if summary and summary != strategy_intro:
        sections.append({"title": "Why this stock", "body": summary})

    return {
        "ticker": candidate.get("ticker"),
        "name": candidate.get("name"),
        "sector": candidate.get("sector"),
        "strategy": strategy,
        "strategyName": STRATEGY_NAMES.get(strategy, strategy),
        "headline": headline,
        "summary": summary,
        "sections": sections,
        "signals": bullets,
        "tradePlan": trade_plan,
        "passCount": candidate.get("passCount"),
        "passAll": candidate.get("passAll"),
        "llmRank": candidate.get("llmRank"),
        "isAiPick": candidate.get("llmRank") is not None,
        "upside": upside,
    }


def attach_pick_reports(candidates: list[dict], strategy: str) -> list[dict]:
    for c in candidates:
        c["pickReport"] = build_pick_report(c, strategy)
    return candidates


def _reanalyze_single(ticker: str, strategy: str) -> dict | None:
    """Re-run strategy analysis for one symbol (uses cached EOD data)."""
    from data import fetch_history, fetch_nifty_index_history, fetch_stock_fundamentals, fetch_stock_full

    if strategy == "box":
        from screeners.box import _analyze as analyze
        stock = fetch_history(ticker)
        return analyze(stock) if stock else None

    if strategy == "dip":
        from screeners.dip import _analyze as analyze
        stock = fetch_history(ticker)
        hit = analyze(stock) if stock else None
        if hit:
            hit["recLabel"] = "Strong dip" if hit.get("passAll") else "Dip signal"
            hit["recNote"] = hit.get("_note", "")
            hit["sellPrice"] = hit.get("_sell")
            hit["stopLoss"] = hit.get("signal", {}).get("stop")
        return hit

    if strategy == "winners":
        from screeners.winners import _score_stock
        stock = fetch_stock_full(ticker)
        if not stock:
            return None
        idx = fetch_nifty_index_history() or {"closes": []}
        score, signals = _score_stock(stock, idx["closes"])
        note = " · ".join(signals.values()) if signals else "Momentum + quality screen match."
        return {
            **stock,
            "passCount": score,
            "passAll": score >= 6,
            "signal": signals,
            "buyPrice": stock["price"],
            "sellPrice": round(stock["price"] * 1.22, 2),
            "recLabel": "Breakout watch" if signals.get("breakout") else "Winner candidate",
            "recNote": note,
        }

    if strategy == "value":
        from screeners.value import FILTERS, _score
        stock = fetch_stock_fundamentals(ticker)
        if not stock:
            return None
        pass_count, pass_all = _score(stock)
        if pass_all:
            label, note = "Recommended", "Passes Graham-style value filters — solid long-term candidate."
        elif pass_count >= 4:
            label, note = "Worth a look", f"Passes {pass_count}/6 value filters."
        else:
            label, note = "Value holding", f"Passes {pass_count}/6 value filters at time of review."
        return {
            **stock,
            "passCount": pass_count,
            "passAll": pass_all,
            "buyPrice": stock["price"],
            "sellPrice": stock.get("fair", stock["price"]),
            "recLabel": label,
            "recNote": note,
            "signal": {},
        }

    return None


def _find_entry_reasoning(bot_id: int, ticker: str) -> str:
    import database as db
    for entry in db.get_action_log(bot_id, limit=100):
        if ticker not in (entry.get("detail") or ""):
            continue
        if entry["action"] in ("auto_executed", "trade_executed", "approval_requested"):
            reason = (entry.get("reasoning") or "").strip()
            if reason:
                return reason
    return ""


def rebuild_pick_report_for_trade(trade: dict, bot: dict) -> dict:
    """Build or rebuild a full pick report for an open/closed trade."""
    stored = trade.get("pickReport")
    if stored and stored.get("signals") and stored.get("headline") not in (None, "Your holding"):
        report = dict(stored)
    else:
        strategy = bot["strategy"]
        log_reason = _find_entry_reasoning(bot["id"], trade["ticker"])
        analyzed = _reanalyze_single(trade["ticker"], strategy)

        if analyzed:
            upside = round((trade["target"] - trade["entry"]) / trade["entry"] * 100) if trade["entry"] else 0
            candidate = {
                **analyzed,
                "ticker": trade["ticker"],
                "name": trade.get("name") or analyzed.get("name"),
                "sector": trade.get("sector") or analyzed.get("sector"),
                "buyPrice": trade["entry"],
                "sellPrice": trade["target"],
                "stopLoss": trade["stopLoss"],
                "buyFmt": f"₹{trade['entry']:,.2f}",
                "sellFmt": f"₹{trade['target']:,.0f}",
                "shares": trade["qty"],
                "costFmt": _fmt_inr(trade["qty"] * trade["entry"]),
                "upside": f"+{max(upside, 0)}%",
                "gainNote": f"~{upside}% to sell target",
            }
            if log_reason:
                candidate["recNote"] = log_reason
            report = build_pick_report(candidate, strategy)
        else:
            strategy = bot["strategy"]
            intro = _STRATEGY_WHY.get(strategy, "")
            log_reason = _find_entry_reasoning(bot["id"], trade["ticker"])
            report = {
                "ticker": trade["ticker"],
                "name": trade.get("name"),
                "sector": trade.get("sector"),
                "strategy": strategy,
                "strategyName": STRATEGY_NAMES.get(strategy, strategy),
                "headline": "Your holding",
                "summary": log_reason or intro,
                "sections": [],
                "signals": [],
                "isAiPick": False,
            }
            if intro:
                report["sections"].append({"title": "Strategy fit", "body": intro})
            if log_reason:
                report["sections"].append({"title": "Why this stock", "body": log_reason})

    report["tradePlan"] = [
        {"label": "Entry price", "value": f"₹{trade['entry']:,.2f}", "hint": f"Bought {trade.get('entryDate', '')}"},
        {"label": "Current price", "value": f"₹{trade['ltp']:,.2f}", "hint": "Latest EOD mark"},
        {"label": "Sell target", "value": f"₹{trade['target']:,.0f}", "hint": "Auto-exit when reached"},
        {"label": "Stop-loss", "value": f"₹{trade['stopLoss']:,.2f}", "hint": "Risk limit"},
    ]
    report["headline"] = report.get("headline") or "Why we bought this"
    if report.get("headline") == "Your holding" and report.get("summary"):
        report["headline"] = "Why we bought this"
    return report
