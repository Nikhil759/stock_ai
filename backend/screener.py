"""Strategy screener — dossier funnel + selector LLM pipeline."""

from dossier_screen import screen
from strategies import STRATEGY_NAMES, VALID_STRATEGIES

__all__ = ["screen", "VALID_STRATEGIES", "STRATEGY_NAMES"]
