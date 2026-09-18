"""Map DB wolves.mode (paper/live) to wolf_executor mode (paper/real)."""
from __future__ import annotations

VALID_WOLF_MODES = frozenset({"paper", "live"})


def normalize_wolf_mode(value: str | None) -> str:
    mode = (value or "paper").strip().lower()
    return mode if mode in VALID_WOLF_MODES else "paper"


def executor_mode_from_wolf(wolf_mode: str | None) -> str:
    """Return 'paper' or 'real' for run_wolf_executor."""
    return "real" if normalize_wolf_mode(wolf_mode) == "live" else "paper"
