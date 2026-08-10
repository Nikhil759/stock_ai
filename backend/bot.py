"""Bot response helpers for Supabase-backed wolves."""


def _fmt_inr(n: float) -> str:
    return "₹" + f"{round(n):,}"


def bot_config(bot: dict) -> dict:
    """Normalize bot row for guardrail helpers."""
    return {
        **bot,
        "budget": bot["allocation"],
        "paused": bot["status"] == "paused",
        "available_cash": bot["availableCash"],
    }


def behavior_summary(cfg: dict) -> str:
    if cfg.get("status") == "terminated":
        return "This Wolf has been terminated. History is kept for reference."

    if cfg.get("paused") or cfg.get("status") == "paused":
        return "Wolf is paused — no buys, no sells, no automated actions until you resume."

    if cfg["mode"] == "advisory":
        return "Advisory mode — the Wolf suggests picks and you decide whether to log each paper trade yourself."

    level = cfg["level"]
    if level == "A":
        return "Autonomous (approval gate) — the Wolf finds trades and waits for your OK before executing anything."
    if level == "B":
        th = _fmt_inr(cfg["auto_threshold"])
        return f"Autonomous (auto under {th}) — trades below {th} execute immediately; larger ones ask first."
    return "Autonomous (full auto) — the Wolf executes trades within your guardrails and notifies you after each action."
