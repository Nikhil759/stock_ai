"""
Morning ingestion orchestration — build dossiers (optional), Phase C funnels,
then Phase D batch LLM scoring + shortlist cache.

Usage (from repo root):
    PYTHONPATH=. python -m cron.morning_ingestion
    PYTHONPATH=. python -m cron.morning_ingestion --skip-build
    PYTHONPATH=. python -m cron.morning_ingestion --skip-build --skip-scoring

Phase E: incremental health_status upserts to Supabase while the run progresses.
Phase G (bot deploy / final selection) is NOT here.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from data_layer.storage import load_all_dossiers
from funnels.value_funnel import run_value_funnel
from funnels.winners_funnel import run_winners_funnel
from funnels.box_funnel import run_box_funnel
from funnels.dip_funnel import run_dip_funnel
from scoring.batch_scorer import run_batch_scoring, apply_llm_cap, BATCH_SIZE, DEFAULT_LLM_CAPS
from cache.shortlist_cache import save_shortlist
from health_status import start_run, update_stage, finalize

ALL_STRATEGIES = ("value", "winners", "box", "dip")


def _summarize(name: str, candidates: list[dict]) -> None:
    syms = [c["symbol"] for c in candidates]
    print(f"\n=== {name}: {len(candidates)} candidates ===")
    if syms:
        print("  " + ", ".join(syms[:40]) + (" …" if len(syms) > 40 else ""))


def _record_prep_from_dossiers(dossiers: list, *, skipped_build: bool) -> None:
    """Update fetch / technicals / market_context from loaded dossiers."""
    from funnels.common import as_dict

    dicts = [as_dict(d) for d in dossiers]
    n = len(dicts)
    prefix = "skipped build — existing dossiers" if skipped_build else "built"

    update_stage(
        "fetch",
        {"status": "success", "detail": f"{n}/{n} fetched ({prefix})"},
    )

    with_tech = 0
    engine = "ta"
    for d in dicts:
        t = d.get("technicals") or {}
        if t.get("rsi_14") is not None or t.get("above_200dma") is not None:
            with_tech += 1
    update_stage(
        "technicals",
        {
            "status": "success" if with_tech else "failed",
            "detail": f"{with_tech}/{n} computed, engine={engine}",
        },
    )

    mc = {}
    for d in dicts:
        mc = d.get("market_context") or {}
        if mc:
            break
    nifty = mc.get("nifty_trend")
    vix = mc.get("india_vix")
    detail = f"Nifty trend={nifty}, VIX {vix}" if mc else "market_context missing"
    update_stage(
        "market_context",
        {
            "status": "success" if mc else "failed",
            "detail": detail,
        },
    )


def run_funnels(dossiers: list) -> dict[str, list[dict]]:
    """Run all four funnels independently; upsert health per strategy."""
    print(f"\n[MATH FUNNEL] orchestration: {len(dossiers)} dossiers in")
    n_in = len(dossiers)

    runners = [
        ("value", run_value_funnel),
        ("winners", run_winners_funnel),
        ("box", run_box_funnel),
        ("dip", run_dip_funnel),
    ]
    results: dict[str, list[dict]] = {}
    for name, fn in runners:
        try:
            candidates = fn(dossiers)
            results[name] = candidates
            _summarize(name.capitalize(), candidates)
            update_stage(
                f"funnels.{name}",
                {"status": "success", "in": n_in, "out": len(candidates)},
            )
        except Exception as e:
            print(f"[MATH FUNNEL] {name} FAILED: {e}")
            results[name] = []
            update_stage(
                f"funnels.{name}",
                {"status": "failed", "in": n_in, "out": 0, "detail": str(e)},
            )

    return results


def run_scoring(
    funnel_results: dict[str, list[dict]],
    strategies: list[str],
    as_of: date | None = None,
    max_candidates: int = 0,
) -> dict[str, list[dict]]:
    """Phase D — batch score + shortlist cache; health per strategy."""
    as_of = as_of or date.today()
    print(
        f"\n[BATCH SCORING] batch_size={BATCH_SIZE}, "
        f"default_caps={DEFAULT_LLM_CAPS}"
        + (f", hard_cap={max_candidates}" if max_candidates else "")
    )
    shortlists: dict[str, list[dict]] = {}
    for strategy in strategies:
        candidates = apply_llm_cap(
            strategy,
            list(funnel_results.get(strategy) or []),
            hard_cap=max_candidates or None,
        )
        n_score = len(candidates)
        try:
            scored = run_batch_scoring(strategy, candidates, as_of=as_of)
            save_shortlist(strategy, as_of, scored)
            shortlists[strategy] = scored
            update_stage(
                f"batch_scoring.{strategy}",
                {
                    "status": "success",
                    "candidates_scored": n_score,
                    "survivors": len(scored),
                },
            )
            update_stage("cache_saved", {strategy: True})
        except Exception as e:
            print(f"[BATCH SCORING] {strategy} FAILED: {e}")
            shortlists[strategy] = []
            update_stage(
                f"batch_scoring.{strategy}",
                {
                    "status": "failed",
                    "candidates_scored": n_score,
                    "survivors": 0,
                    "detail": str(e),
                },
            )
            update_stage("cache_saved", {strategy: False})
    return shortlists


def run_pipeline(
    *,
    skip_build: bool = False,
    skip_scoring: bool = False,
    strategies: list[str] | None = None,
    max_candidates: int = 0,
    tickers: list[str] | None = None,
    close: bool = False,
) -> None:
    """Library entrypoint — build (optional) -> funnels -> batch scoring.

    Raises on failure (never calls sys.exit) so it's safe to call from a
    long-running process (e.g. the data-layer-cron scheduler), not just the
    CLI below.
    """
    start_run(date.today())

    try:
        if not skip_build:
            from data_layer.build import run as build_run

            try:
                build_run(
                    snapshot="post_close" if close else "pre_open",
                    tickers=tickers or None,
                )
            except Exception as e:
                update_stage(
                    "fetch",
                    {"status": "failed", "detail": f"build failed: {e}"},
                )
                finalize("failed")
                raise

        dossiers = load_all_dossiers()
        if not dossiers:
            print("[MATH FUNNEL] no dossiers found — run a build first")
            update_stage(
                "fetch",
                {"status": "failed", "detail": "no dossiers found"},
            )
            finalize("failed")
            raise RuntimeError("no dossiers found")

        _record_prep_from_dossiers(dossiers, skipped_build=skip_build)

        results = run_funnels(dossiers)

        summary = {k: [c["symbol"] for c in v] for k, v in results.items()}
        print("\n[MATH FUNNEL] summary JSON:")
        print(
            json.dumps(
                {k: {"count": len(v), "symbols": v} for k, v in summary.items()},
                indent=2,
            )
        )

        if skip_scoring:
            print("\n[BATCH SCORING] skipped (skip_scoring=True)")
            finalize()
            return

        strategies = [s.strip().lower() for s in (strategies or []) if s.strip()] or list(
            ALL_STRATEGIES
        )
        unknown = [s for s in strategies if s not in ALL_STRATEGIES]
        if unknown:
            print(f"[BATCH SCORING] unknown strategies: {unknown}")
            finalize("failed")
            raise ValueError(f"unknown strategies: {unknown}")

        shortlists = run_scoring(
            results,
            strategies,
            max_candidates=max_candidates or 0,
        )
        print("\n[SHORTLIST CACHE] summary:")
        print(
            json.dumps(
                {
                    k: {
                        "count": len(v),
                        "symbols": [c["symbol"] for c in v],
                    }
                    for k, v in shortlists.items()
                },
                indent=2,
            )
        )
        finalize()
    except Exception as e:
        print(f"[HEALTH STATUS] pipeline aborted: {e}")
        finalize("failed")
        raise


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Morning ingestion + Phase C funnels + Phase D batch scoring"
    )
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--skip-scoring", action="store_true")
    ap.add_argument("--strategies", type=str, default="")
    ap.add_argument("--max-candidates", type=int, default=0)
    ap.add_argument("--tickers", type=str, default="")
    ap.add_argument("--close", action="store_true")
    args = ap.parse_args()

    try:
        run_pipeline(
            skip_build=args.skip_build,
            skip_scoring=args.skip_scoring,
            strategies=[s.strip().lower() for s in args.strategies.split(",") if s.strip()],
            max_candidates=args.max_candidates,
            tickers=[t.strip().upper() for t in args.tickers.split(",") if t.strip()],
            close=args.close,
        )
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
