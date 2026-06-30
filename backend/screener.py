"""Strategy screener router — Nifty 200 + LLM enrichment."""

from llm import enrich_with_llm
from pick_report import attach_pick_reports
from screeners.box import screen as screen_box
from screeners.dip import screen as screen_dip
from screeners.value import screen as screen_value
from screeners.winners import screen as screen_winners
from strategies import STRATEGY_NAMES, VALID_STRATEGIES

SCREENERS = {
    "value": screen_value,
    "winners": screen_winners,
    "box": screen_box,
    "dip": screen_dip,
}


def screen(strategy: str, budget: int, bot_context: dict | None = None, use_llm: bool = True) -> dict:
    if strategy not in VALID_STRATEGIES:
        return {"strategy": strategy, "supported": False, "message": "Unknown strategy."}

    fn = SCREENERS.get(strategy)
    if not fn:
        return {
            "strategy": strategy,
            "strategyName": STRATEGY_NAMES.get(strategy, strategy),
            "supported": False,
            "message": f"{STRATEGY_NAMES.get(strategy, strategy)} is not implemented yet.",
        }

    result = fn(budget)
    result["supported"] = True

    if use_llm and result.get("candidates"):
        enriched = enrich_with_llm(strategy, budget, result["candidates"], bot_context)
        result["candidates"] = enriched["candidates"]
        result["llm"] = enriched["llm"]
        if enriched["llm"].get("summary"):
            result["llmSummary"] = enriched["llm"]["summary"]
    elif result.get("candidates"):
        attach_pick_reports(result["candidates"], strategy)

    return result
