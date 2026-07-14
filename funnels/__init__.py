"""
Phase C — per-strategy math funnels (deterministic, no LLM).

Each strategy module exposes `run_*_funnel(dossiers) -> list[dict]` where each
survivor is `{symbol, dossier, funnel_reasons}`.
"""

from .value_funnel import run_value_funnel
from .winners_funnel import run_winners_funnel
from .box_funnel import run_box_funnel
from .dip_funnel import run_dip_funnel

__all__ = [
    "run_value_funnel",
    "run_winners_funnel",
    "run_box_funnel",
    "run_dip_funnel",
]
