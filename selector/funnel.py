"""
Phase 1 — the math funnel. Deterministic, no LLM, no cost.

Loads every dossier, applies one strategy's coarse pass/fail filter, and
returns the survivors capped at FUNNEL_MAX_SURVIVORS (strongest first, by
number of checks passed). This is a funnel, not the final decision -- the
strict judgment happens later in the LLM (Phase 2).

Run:  python -m selector.funnel value
"""
from __future__ import annotations

import argparse
import logging

from data_layer.storage import load_all_dossiers
from data_layer.dossier import Dossier

from .config import FUNNEL_MAX_SURVIVORS
from .reasoning_log import ReasoningLog
from .strategies import STRATEGIES, RANK_KEYS

log = logging.getLogger(__name__)


def run_funnel(strategy: str, reasoning: ReasoningLog | None = None) -> list[Dossier]:
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown strategy {strategy!r}. Choose from {sorted(STRATEGIES)}.")
    passes_fn = STRATEGIES[strategy]

    dossiers = load_all_dossiers()
    log.info("loaded %d dossiers from %s", len(dossiers), "data_layer")

    scored = []
    rejected = 0
    for d in dossiers:
        survived, checks = passes_fn(d)
        passed_names = [k for k, v in checks.items() if v]
        failed_names = [k for k, v in checks.items() if not v]
        if survived:
            score = sum(1 for v in checks.values() if v)
            rank_fn = RANK_KEYS.get(strategy)
            rank = rank_fn(d) if rank_fn else ()
            scored.append((score, rank, d, checks))
            log.debug("PASS %-12s score=%d/%d  passed=%s  failed=%s",
                      d.meta.ticker, score, len(checks), passed_names, failed_names)
        else:
            rejected += 1
            log.debug("FAIL %-12s passed=%s  failed=%s", d.meta.ticker, passed_names, failed_names)

    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    survivors = scored[:FUNNEL_MAX_SURVIVORS]
    dropped_by_cap = len(scored) - len(survivors)

    # stash the checks on each dossier's meta for the caller/CLI to inspect
    # without changing the dossier schema itself.
    for _, _, d, checks in survivors:
        d._funnel_checks = checks  # type: ignore[attr-defined]

    log.info(
        "%s funnel: %d/%d dossiers passed their checks, %d rejected outright"
        "%s -> %d survivor(s) after FUNNEL_MAX_SURVIVORS=%d cap",
        strategy, len(scored), len(dossiers), rejected,
        f", {dropped_by_cap} more dropped by the cap" if dropped_by_cap else "",
        len(survivors), FUNNEL_MAX_SURVIVORS,
    )
    for score, _, d, checks in survivors:
        fired = ", ".join(k for k, v in checks.items() if v)
        log.info("  SURVIVOR %-12s score=%d  checks=[%s]", d.meta.ticker, score, fired)

    if reasoning is not None:
        top = ", ".join(d.meta.ticker for _, _, d, _ in survivors[:8])
        extra = f" (+{len(survivors) - 8} more)" if len(survivors) > 8 else ""
        reasoning.add(
            "funnel",
            f"{len(scored)}/{len(dossiers)} passed {strategy} math checks → {len(survivors)} sent to LLM"
            + (f" (top: {top}{extra})" if top else ""),
            passed=len(scored),
            rejected=rejected,
            survivors=len(survivors),
            topTickers=[d.meta.ticker for _, _, d, _ in survivors[:12]],
        )

    return [d for _, _, d, _ in survivors]


if __name__ == "__main__":
    from .log_setup import setup_logging

    ap = argparse.ArgumentParser()
    ap.add_argument("strategy", nargs="?", default="value")
    ap.add_argument("--verbose", "-v", action="store_true", help="show per-ticker pass/fail detail")
    args = ap.parse_args()

    setup_logging(strategy=args.strategy, verbose=args.verbose)
    run_funnel(args.strategy)
