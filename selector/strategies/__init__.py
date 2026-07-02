"""
One module per strategy, each exposing:

    def passes(dossier) -> tuple[bool, dict[str, bool]]

`dossier` is a data_layer.dossier.Dossier. Every check must be None-safe: a
missing field means that check does not fire (never a crash). These are
COARSE, loosened filters -- a funnel, not the final decision. The strict
judgment happens later in the LLM (Phase 2).
"""
from . import value, winners, box, dip

STRATEGIES = {
    "value": value.passes,
    "winners": winners.passes,
    "box": box.passes,
    "dip": dip.passes,
}
