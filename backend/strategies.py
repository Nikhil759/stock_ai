"""Strategy definitions and knowledge-base (markdown reference docs)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STRATEGIES = {
    "value": {
        "id": "value",
        "number": 1,
        "name": "Buy cheap quality",
        "shortName": "Value",
        "horizon": "Long-term",
        "analysisType": "Fundamental only",
        "implemented": True,
        "knowledgeFile": "Strategy-1-Buy-Cheap-Quality-Companies.md",
    },
    "winners": {
        "id": "winners",
        "number": 2,
        "name": "Buy the winners",
        "shortName": "Winners",
        "horizon": "Weeks–months",
        "analysisType": "Hybrid (fundamentals + technicals)",
        "implemented": True,
        "knowledgeFile": "Strategy-2-Buy-the-Winners.md",
    },
    "box": {
        "id": "box",
        "number": 3,
        "name": "Buy the box breakout",
        "shortName": "Box breakout",
        "horizon": "Days–weeks",
        "analysisType": "Technical only (Darvas Box)",
        "implemented": True,
        "knowledgeFile": "Strategy-3-Buy-the-Box-Breakout.md",
    },
    "dip": {
        "id": "dip",
        "number": 4,
        "name": "Buy the dip",
        "shortName": "Buy the dip",
        "horizon": "Few days–2wks",
        "analysisType": "Technical only (RSI-2)",
        "implemented": True,
        "knowledgeFile": "Strategy-4-Buy-the-Dip.md",
    },
}

STRATEGY_NAMES = {k: v["name"] for k, v in STRATEGIES.items()}

VALID_STRATEGIES = frozenset(STRATEGIES.keys())


def get_strategy(strategy_id: str) -> dict | None:
    meta = STRATEGIES.get(strategy_id)
    if not meta:
        return None
    return {**meta, "knowledge": load_knowledge(strategy_id)}


def load_knowledge(strategy_id: str) -> str:
    meta = STRATEGIES.get(strategy_id)
    if not meta:
        return ""
    path = ROOT / meta["knowledgeFile"]
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def list_strategies() -> list[dict]:
    return [
        {
            "id": s["id"],
            "number": s["number"],
            "name": s["name"],
            "horizon": s["horizon"],
            "analysisType": s["analysisType"],
            "implemented": s["implemented"],
            "knowledgeFile": s["knowledgeFile"],
        }
        for s in STRATEGIES.values()
    ]
