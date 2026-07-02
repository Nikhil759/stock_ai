"""
Value — "Buy cheap quality companies" (Graham-style fundamentals).

Coarse funnel, loosened from the old backend/screeners/value.py strict gates
(PE<=15, PB<=1.5, D/E<=0.5, Curr>=2, ROE>=12, Graham<=22.5, needing 4/6) so
borderline names survive for the LLM to judge. "Graham<=22.5" (PE*PB<=22.5,
Graham's own rule of thumb) is loosened here to PE*PB<=30.
"""
from __future__ import annotations

CHECKS = {
    "pe_le_18": lambda f: f.pe is not None and f.pe <= 18,
    "pb_le_2": lambda f: f.pb is not None and f.pb <= 2.0,
    "roe_ge_10": lambda f: f.roe is not None and f.roe >= 10,
    "de_le_1": lambda f: f.debt_to_equity is not None and f.debt_to_equity <= 1.0,
    "current_ratio_ge_1_5": lambda f: f.current_ratio is not None and f.current_ratio >= 1.5,
    "pe_x_pb_le_30": lambda f: f.pe is not None and f.pb is not None and (f.pe * f.pb) <= 30,
}
MIN_PASSES = 3


def passes(dossier) -> tuple[bool, dict]:
    f = dossier.fundamentals
    checks = {name: bool(fn(f)) for name, fn in CHECKS.items()}
    survived = sum(checks.values()) >= MIN_PASSES
    return survived, checks
