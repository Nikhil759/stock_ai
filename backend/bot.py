"""Bot engine — autonomous logic with allocation pools."""

import database as db


def _fmt_inr(n: float) -> str:
    return "₹" + f"{round(n):,}"


def _deployed_capital(trades: list[dict]) -> float:
    return sum(t["qty"] * t["entry"] for t in trades if t.get("status") == "open")


def _stop_loss_price(buy: float, cfg: dict) -> float:
    pct = cfg["stop_loss_pct"] / 100
    return round(buy * (1 - pct), 2)


def bot_config(bot: dict) -> dict:
    """Normalize bot row for guardrail helpers."""
    return {
        **bot,
        "budget": bot["allocation"],
        "paused": bot["status"] == "paused",
        "available_cash": bot["availableCash"],
    }


def check_guardrails(candidate: dict, cfg: dict, trades: list[dict]) -> tuple[bool, str]:
    allocation = cfg["allocation"]
    cash = cfg.get("available_cash") or cfg.get("availableCash") or allocation
    cost = candidate.get("cost") or (candidate.get("shares", 0) * candidate.get("buyPrice", 0))
    shares = candidate.get("shares", 0)

    if shares < 1:
        return False, "Not enough cash to buy even 1 share."

    if cost > cash + 0.01:
        return False, f"Insufficient cash ({_fmt_inr(cash)} available, need {_fmt_inr(cost)})."

    max_per_stock = allocation * cfg["max_per_stock_pct"] / 100
    if cost > max_per_stock:
        return False, f"Trade exceeds max per stock ({cfg['max_per_stock_pct']}% = {_fmt_inr(max_per_stock)})."

    deployed = _deployed_capital(trades)
    max_deploy = allocation * cfg["max_deployed_pct"] / 100
    if deployed + cost > max_deploy:
        return False, f"Would exceed max deployed ({cfg['max_deployed_pct']}% = {_fmt_inr(max_deploy)})."

    return True, ""


def _fit_to_guardrails(candidate: dict, cfg: dict, trades: list[dict]) -> dict | None:
    allocation = cfg["allocation"]
    cash = cfg.get("available_cash") or cfg.get("availableCash") or allocation
    price = candidate.get("buyPrice") or candidate.get("price", 0)
    if price <= 0:
        return None

    max_per_stock = allocation * cfg["max_per_stock_pct"] / 100
    max_deploy = allocation * cfg["max_deployed_pct"] / 100
    deployed = _deployed_capital(trades)
    room = max_deploy - deployed

    shares = min(
        int(max_per_stock // price),
        int(room // price),
        int(cash // price),
    )
    if shares < 1:
        return None

    cost = shares * price
    adjusted = {**candidate, "shares": shares, "cost": cost, "costFmt": _fmt_inr(cost)}
    ok, _ = check_guardrails(adjusted, cfg, trades)
    return adjusted if ok else None


def rank_candidates(candidates: list[dict]) -> list[dict]:
    affordable = [c for c in candidates if c.get("canLog")]
    affordable.sort(key=lambda c: (
        c.get("llmRank") if c.get("llmRank") is not None else 999,
        -int(c.get("passAll", False)),
        -c.get("passCount", 0),
    ))
    return affordable


def pick_best_candidate(candidates: list[dict]) -> dict | None:
    ranked = rank_candidates(candidates)
    return ranked[0] if ranked else None


def _open_tickers(trades: list[dict]) -> set[str]:
    return {t["ticker"] for t in trades if t.get("status") == "open"}


def plan_portfolio_buys(candidates: list[dict], cfg: dict, trades: list[dict]) -> list[dict]:
    """Build multiple positions until deploy limit, per-stock cap, or cash is reached."""
    ranked = rank_candidates(candidates)
    held = _open_tickers(trades)
    planned: list[dict] = []
    simulated_trades = list(trades)
    cash = cfg.get("available_cash") or cfg.get("availableCash") or cfg["allocation"]
    allocation = cfg["allocation"]
    max_deploy = allocation * cfg["max_deployed_pct"] / 100

    for candidate in ranked:
        if candidate["ticker"] in held:
            continue

        deployed = _deployed_capital(simulated_trades)
        if deployed >= max_deploy - 0.01 or cash < 1:
            break

        sim_cfg = {**cfg, "available_cash": cash}
        fitted = _fit_to_guardrails(candidate, sim_cfg, simulated_trades)
        if not fitted:
            continue

        planned.append(fitted)
        held.add(fitted["ticker"])
        cost = fitted["cost"]
        cash -= cost
        simulated_trades.append({
            "ticker": fitted["ticker"],
            "qty": fitted["shares"],
            "entry": fitted["buyPrice"],
            "status": "open",
        })

    return planned


def _proposal_needs_approval(proposal: dict, cfg: dict) -> bool:
    level = cfg["level"]
    return level == "A" or (level == "B" and proposal["cost"] > cfg["auto_threshold"])


def _execute_proposal(bot_id: int, proposal: dict, source: str = "autonomous") -> dict:
    return db.execute_buy(
        bot_id,
        {
            "ticker": proposal["ticker"],
            "name": proposal["name"],
            "sector": proposal["sector"],
            "qty": proposal["qty"],
            "entry": proposal["buyPrice"],
            "ltp": proposal["buyPrice"],
            "target": proposal["sellPrice"],
            "stopLoss": proposal["stopLoss"],
            "reasoning": proposal["reason"],
            "pickReport": proposal.get("pickReport"),
        },
        proposal["cost"],
        source=source,
    )


def build_trade_proposal(candidate: dict, cfg: dict) -> dict:
    buy = candidate["buyPrice"]
    sell = candidate["sellPrice"]
    shares = candidate["shares"]
    cost = candidate["cost"]
    stop = candidate.get("stopLoss") or _stop_loss_price(buy, cfg)
    reason = (
        f"{candidate.get('recLabel', 'Pick')}: {candidate.get('recNote', '')} "
        f"Buy at {_fmt_inr(buy)}, sell target {_fmt_inr(sell)}, "
        f"stop-loss at {_fmt_inr(stop)} ({cfg['stop_loss_pct']}% below entry)."
    ).strip()
    pick_report = candidate.get("pickReport")
    if not pick_report:
        from pick_report import build_pick_report
        pick_report = build_pick_report(
            {**candidate, "buyPrice": buy, "sellPrice": sell, "stopLoss": stop, "shares": shares, "cost": cost},
            cfg.get("strategy", "value"),
        )
    return {
        "ticker": candidate["ticker"],
        "name": candidate.get("name"),
        "sector": candidate.get("sector"),
        "qty": shares,
        "buyPrice": buy,
        "sellPrice": sell,
        "stopLoss": stop,
        "cost": cost,
        "costFmt": _fmt_inr(cost),
        "buyFmt": f"₹{buy:,.2f}",
        "sellFmt": f"₹{sell:,.0f}",
        "stopFmt": f"₹{stop:,.2f}",
        "reason": reason,
        "pickReport": pick_report,
    }


def behavior_summary(cfg: dict) -> str:
    if cfg.get("status") == "terminated":
        return "This bot has been terminated. History is kept for reference."

    if cfg.get("paused") or cfg.get("status") == "paused":
        return "Bot is paused — no buys, no sells, no automated actions until you resume."

    if cfg["mode"] == "advisory":
        return "Advisory mode — the bot suggests picks and you decide whether to log each paper trade yourself."

    level = cfg["level"]
    if level == "A":
        return "Autonomous (approval gate) — the bot finds trades and waits for your OK before executing anything."
    if level == "B":
        th = _fmt_inr(cfg["auto_threshold"])
        return f"Autonomous (auto under {th}) — trades below {th} execute immediately; larger ones ask first."
    return "Autonomous (full auto) — the bot executes trades within your guardrails and notifies you after each action."


def process_screen_results(bot_id: int, candidates: list[dict], bot: dict) -> dict:
    cfg = bot_config(bot)

    if cfg["status"] == "terminated":
        return {"action": "terminated", "message": "Bot is terminated."}

    if cfg["paused"]:
        db.log_action(bot_id, "screen_skipped", "Screen complete but bot is paused", "No trades while paused.")
        return {"action": "paused", "message": "Bot is paused — review picks manually."}

    if cfg["mode"] == "advisory":
        db.log_action(bot_id, "screen_complete", f"Found {len(candidates)} picks", "Advisory — user decides.")
        return {"action": "advisory", "message": "Review picks and log trades yourself."}

    trades = db.get_trades(bot_id, status="open")
    planned = plan_portfolio_buys(candidates, cfg, trades)
    if not planned:
        db.log_action(bot_id, "no_trade", "No affordable candidates", "Nothing to buy.")
        return {"action": "none", "message": "No affordable picks found."}

    executed: list[dict] = []
    pending: list[dict] = []
    for fitted in planned:
        proposal = build_trade_proposal(fitted, cfg)
        if _proposal_needs_approval(proposal, cfg):
            pending.append(db.add_pending(bot_id, proposal))
            continue
        trade = _execute_proposal(bot_id, proposal)
        executed.append(trade)
        db.log_action(
            bot_id,
            "auto_executed",
            f"{proposal['ticker']}: {proposal['qty']} shares",
            proposal["reason"],
        )

    total_cost = sum(p["cost"] for p in planned)
    n = len(planned)
    summary = f"₹{round(total_cost):,} across {n} stock{'s' if n != 1 else ''}"

    if executed and not pending:
        return {
            "action": "executed",
            "message": f"Bought {len(executed)} position{'s' if len(executed) != 1 else ''} ({summary}).",
            "trades": executed,
        }
    if pending and not executed:
        return {
            "action": "pending",
            "message": f"{len(pending)} trade{'s' if len(pending) != 1 else ''} waiting for approval ({summary}).",
            "pending": pending,
        }
    if executed and pending:
        return {
            "action": "partial",
            "message": (
                f"Executed {len(executed)} trade{'s' if len(executed) != 1 else ''}; "
                f"{len(pending)} awaiting approval ({summary})."
            ),
            "trades": executed,
            "pending": pending,
        }
    return {"action": "blocked", "message": "No trades could be placed."}


def manual_log_trade(bot_id: int, candidate: dict, bot: dict) -> dict:
    cfg = bot_config(bot)
    if cfg["status"] == "terminated":
        raise ValueError("Bot is terminated.")
    if cfg["paused"]:
        raise ValueError("Bot is paused — cannot log new trades.")

    trades = db.get_trades(bot_id, status="open")
    fitted = _fit_to_guardrails(candidate, cfg, trades)
    if not fitted:
        _, reason = check_guardrails(candidate, cfg, trades)
        raise ValueError(reason or "Trade does not fit guardrails.")

    proposal = build_trade_proposal(fitted, cfg)
    if not proposal.get("pickReport"):
        from pick_report import build_pick_report
        proposal["pickReport"] = build_pick_report(fitted, bot["strategy"])
    return db.execute_buy(
        bot_id,
        {
            "ticker": proposal["ticker"],
            "name": proposal["name"],
            "sector": proposal["sector"],
            "qty": proposal["qty"],
            "entry": proposal["buyPrice"],
            "ltp": proposal["buyPrice"],
            "target": proposal["sellPrice"],
            "stopLoss": proposal["stopLoss"],
            "reasoning": "Manual paper trade logged by user.",
            "pickReport": proposal.get("pickReport"),
        },
        proposal["cost"],
        source="manual",
    )
