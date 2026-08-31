"""
Post-scoring output quality checks — grounding, rule consistency, outliers.

No second LLM pass: deterministic checks against dossier facts and strategy rules.
"""
from __future__ import annotations

import re
import statistics
from typing import Any, Literal

Severity = Literal["ok", "warn", "alert"]

_SEV_RANK = {"ok": 0, "warn": 1, "alert": 2}

# conviction bands from batch_skeleton.txt
_VERDICT_MIN = {"buy": 60, "watch": 40, "skip": 0}
_VERDICT_MAX = {"buy": 100, "watch": 59, "skip": 39}

_METRIC_PATTERNS: list[tuple[str, str, str]] = [
    (r"rsi\s*\(\s*2\s*\)\s*(?:of|at|=|:)?\s*(\d+(?:\.\d+)?)", "technicals", "rsi_2"),
    (r"rsi\s*\(\s*2\s*\)\s*(?:reading|value)?\s*(?:of|at|=|:)?\s*(\d+(?:\.\d+)?)", "technicals", "rsi_2"),
    (r"rsi\s*\(\s*14\s*\)\s*(?:of|at|=|:)?\s*(\d+(?:\.\d+)?)", "technicals", "rsi_14"),
    (r"rsi\s*\(\s*14\s*\)\s*(?:reading|value)?\s*(?:of|at|=|:)?\s*(\d+(?:\.\d+)?)", "technicals", "rsi_14"),
    (r"p\s*/\s*e\s*(?:of|at|=|:)?\s*(\d+(?:\.\d+)?)", "fundamentals", "pe"),
    (r"p/e\s*(?:of|at|=|:)?\s*(\d+(?:\.\d+)?)", "fundamentals", "pe"),
    (r"debt\s*/\s*equity\s*(?:of|at|=|:)?\s*(\d+(?:\.\d+)?)", "fundamentals", "debt_to_equity"),
    (r"roe\s*(?:of|at|=|:)?\s*(\d+(?:\.\d+)?)", "fundamentals", "roe"),
]

_FLOAT_RE = re.compile(r"(?<!\d)(-?\d+(?:\.\d+)?)(?!\d)")


def _max_severity(*levels: Severity) -> Severity:
    best: Severity = "ok"
    for lv in levels:
        if _SEV_RANK.get(lv, 0) > _SEV_RANK.get(best, 0):
            best = lv
    return best


def _flag(
    code: str,
    message: str,
    *,
    severity: Severity = "warn",
) -> dict[str, str]:
    return {"code": code, "message": message, "severity": severity}


def _nested_get(dossier: dict[str, Any], section: str, key: str) -> float | None:
    block = dossier.get(section) if isinstance(dossier.get(section), dict) else {}
    val = block.get(key)
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _truth_numbers(dossier: dict[str, Any], funnel_reasons: dict[str, Any]) -> dict[str, float]:
    """Flatten dossier + funnel numbers for grounding comparisons."""
    out: dict[str, float] = {}
    d = dossier or {}
    fr = funnel_reasons or {}

    for section, keys in (
        ("fundamentals", ("price", "pe", "pb", "debt_to_equity", "roe", "graham_number", "fair_value_estimate")),
        ("technicals", ("rsi_2", "rsi_14", "dma_50", "dma_200", "pct_from_52w_high", "pct_from_52w_low")),
    ):
        for key in keys:
            v = _nested_get(d, section, key)
            if v is not None:
                out[f"{section}.{key}"] = v
                out[key] = v

    for key, val in fr.items():
        if key in ("earnings_proxy", "note"):
            continue
        try:
            out[f"funnel.{key}"] = float(val)
        except (TypeError, ValueError):
            continue
    return out


def _match_tolerance(key: str, truth: float, cited: float) -> bool:
    if key in ("rsi_2", "rsi_14", "technicals.rsi_2", "technicals.rsi_14"):
        return abs(truth - cited) <= 1.5
    if "pct" in key or key.startswith("return_"):
        return abs(truth - cited) <= 2.0
    if truth == 0:
        return abs(cited) < 0.5
    return abs(truth - cited) / max(abs(truth), 1e-9) <= 0.08


def check_metric_citations(
    reasoning: str,
    dossier: dict[str, Any],
    funnel_reasons: dict[str, Any] | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Match explicit metric mentions (RSI, P/E, etc.) to dossier values."""
    flags: list[dict[str, str]] = []
    text = reasoning or ""
    truth = _truth_numbers(dossier, funnel_reasons or {})
    cited = 0
    matched = 0
    details: list[dict[str, Any]] = []

    for pattern, section, key in _METRIC_PATTERNS:
        for m in re.finditer(pattern, text, re.I):
            cited += 1
            try:
                val = float(m.group(1))
            except (TypeError, ValueError):
                continue
            truth_val = _nested_get(dossier, section, key)
            if truth_val is None:
                truth_val = truth.get(key)
            ok = truth_val is not None and _match_tolerance(key, truth_val, val)
            if ok:
                matched += 1
                details.append({"metric": key, "cited": val, "truth": truth_val, "match": True})
            else:
                details.append({"metric": key, "cited": val, "truth": truth_val, "match": False})
                flags.append(
                    _flag(
                        "grounding_metric_mismatch",
                        f"Cited {key}={val} but dossier has {truth_val!r}",
                        severity="warn",
                    )
                )

    # Orphan floats: numbers in text that match no dossier value (ignore small ints / years)
    orphans: list[float] = []
    truth_vals = list(truth.values())
    for m in _FLOAT_RE.finditer(text):
        try:
            val = float(m.group(1))
        except ValueError:
            continue
        if val == int(val) and abs(val) in (50, 200) and re.search(r"\b\d+\s*-?\s*day", text, re.I):
            continue
        if val == int(val) and 1990 <= val <= 2035:
            continue
        if abs(val) < 3 and val == int(val):
            continue
        if any(_match_tolerance("generic", tv, val) for tv in truth_vals):
            continue
        orphans.append(val)

    if orphans and cited == 0:
        flags.append(
            _flag(
                "grounding_unmatched_numbers",
                f"Reasoning cites numbers {orphans[:5]} with no dossier match",
                severity="warn",
            )
        )

    summary = {
        "metrics_cited": cited,
        "metrics_matched": matched,
        "orphan_numbers": orphans[:8],
        "details": details[:12],
    }
    return flags, summary


def check_directional_claims(
    reasoning: str,
    dossier: dict[str, Any],
) -> list[dict[str, str]]:
    """Check above/below 200 DMA claims against dossier flags."""
    flags: list[dict[str, str]] = []
    text = (reasoning or "").lower()
    tech = dossier.get("technicals") if isinstance(dossier.get("technicals"), dict) else {}
    above = tech.get("above_200dma")
    if above is None:
        return flags

    claims_below = bool(
        re.search(r"below\s+(?:its\s+)?200[\s-]*(?:day\s+)?(?:moving\s+average|ma|dma)", text)
        or re.search(r"under\s+(?:its\s+)?200[\s-]*(?:day\s+)?(?:moving\s+average|ma|dma)", text)
        or re.search(r"uptrend\s+is\s+broken", text)
        or re.search(r"below\s+200\s+dma", text)
    )
    claims_above = bool(
        re.search(r"above\s+(?:its\s+)?200[\s-]*(?:day\s+)?(?:moving\s+average|ma|dma)", text)
        or re.search(r"intact\s+uptrend", text)
        or re.search(r"above\s+200\s+dma", text)
    )

    if claims_below and above is True:
        flags.append(
            _flag(
                "grounding_dma_contradiction",
                "Reasoning says below/broken 200 DMA but dossier above_200dma=True",
                severity="alert",
            )
        )
    if claims_above and above is False:
        flags.append(
            _flag(
                "grounding_dma_contradiction",
                "Reasoning says above 200 DMA but dossier above_200dma=False",
                severity="alert",
            )
        )
    return flags


def check_verdict_bands(conviction: int, verdict: str) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    v = (verdict or "").lower().strip()
    if v not in _VERDICT_MIN:
        flags.append(_flag("invalid_verdict", f"Unknown verdict {verdict!r}", severity="alert"))
        return flags
    c = int(conviction)
    lo, hi = _VERDICT_MIN[v], _VERDICT_MAX[v]
    if c < lo or c > hi:
        flags.append(
            _flag(
                "verdict_band_mismatch",
                f"Verdict {v} with conviction {c} outside band [{lo}, {hi}]",
                severity="alert",
            )
        )
    return flags


def check_strategy_rules(
    strategy: str,
    *,
    conviction: int,
    verdict: str,
    dossier: dict[str, Any],
    funnel_reasons: dict[str, Any] | None,
    winners_note: str | None = None,
) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    strategy = strategy.lower().strip()
    v = (verdict or "").lower()
    tech = dossier.get("technicals") if isinstance(dossier.get("technicals"), dict) else {}

    if strategy == "dip" and v in ("buy", "watch"):
        if tech.get("above_200dma") is False:
            flags.append(
                _flag(
                    "dip_below_200dma",
                    f"Dip {v} but price is below 200 DMA (hard skip rule)",
                    severity="alert",
                )
            )

    if strategy == "winners" and winners_note and conviction >= 80 and v == "buy":
        flags.append(
            _flag(
                "winners_proxy_high_conviction",
                "Buy conviction ≥80 with momentum-only earnings proxy (policy: prefer lower)",
                severity="warn",
            )
        )

    if v == "skip" and conviction >= 50:
        flags.append(
            _flag(
                "skip_high_conviction",
                f"Skip verdict with relatively high conviction ({conviction})",
                severity="warn",
            )
        )

    return flags


def evaluate_stock_output(
    strategy: str,
    *,
    symbol: str,
    conviction: int,
    verdict: str,
    reasoning: str,
    dossier: dict[str, Any],
    funnel_reasons: dict[str, Any] | None = None,
    winners_note: str | None = None,
) -> dict[str, Any]:
    flags: list[dict[str, str]] = []
    flags.extend(check_verdict_bands(conviction, verdict))
    flags.extend(
        check_strategy_rules(
            strategy,
            conviction=conviction,
            verdict=verdict,
            dossier=dossier,
            funnel_reasons=funnel_reasons,
            winners_note=winners_note,
        )
    )
    metric_flags, grounding = check_metric_citations(reasoning, dossier, funnel_reasons)
    flags.extend(metric_flags)
    flags.extend(check_directional_claims(reasoning, dossier))

    severity = _max_severity(*(f.get("severity", "ok") for f in flags), "ok")
    return {
        "symbol": symbol.upper(),
        "conviction": conviction,
        "verdict": verdict,
        "severity": severity,
        "flag_count": len(flags),
        "flags": flags,
        "grounding": grounding,
    }


def evaluate_batch_output(
    strategy: str,
    batch_rows: list[dict[str, Any]],
    scores: list[Any],
    *,
    winners_proxy_note_fn: Any | None = None,
) -> dict[str, Any]:
    """Evaluate all scores in one batch; add batch-level pattern flags."""
    stock_reports: list[dict[str, Any]] = []
    by_row = {str(r["symbol"]).upper(): r for r in batch_rows}

    for sc in scores:
        sym = sc.symbol.upper() if hasattr(sc, "symbol") else str(sc.get("symbol", "")).upper()
        row = by_row.get(sym, {})
        dossier = row.get("dossier") or {}
        fr = row.get("funnel_reasons") or {}
        note = None
        if winners_proxy_note_fn and strategy == "winners":
            note = winners_proxy_note_fn(fr)
        conviction = sc.conviction if hasattr(sc, "conviction") else int(sc.get("conviction", 0))
        verdict = sc.verdict if hasattr(sc, "verdict") else sc.get("verdict", "skip")
        reasoning = sc.reasoning if hasattr(sc, "reasoning") else sc.get("reasoning", "")
        stock_reports.append(
            evaluate_stock_output(
                strategy,
                symbol=sym,
                conviction=conviction,
                verdict=verdict,
                reasoning=reasoning,
                dossier=dossier,
                funnel_reasons=fr,
                winners_note=note,
            )
        )

    batch_flags: list[dict[str, str]] = []
    n = len(stock_reports)
    if n >= 4:
        buys = sum(1 for s in stock_reports if s.get("verdict") == "buy")
        if buys == n:
            batch_flags.append(
                _flag("batch_all_buy", f"All {n} symbols scored buy (unusual)", severity="warn")
            )
        skips = sum(1 for s in stock_reports if s.get("verdict") == "skip")
        if skips == n:
            batch_flags.append(
                _flag("batch_all_skip", f"All {n} symbols scored skip (unusual)", severity="warn")
            )

    convictions = [int(s.get("conviction", 0)) for s in stock_reports]
    if convictions and max(convictions) - min(convictions) <= 3 and n >= 4:
        batch_flags.append(
            _flag(
                "batch_flat_conviction",
                f"Conviction nearly identical across batch ({min(convictions)}–{max(convictions)})",
                severity="warn",
            )
        )

    flagged = [s for s in stock_reports if s.get("flag_count", 0) > 0]
    total_flags = sum(s.get("flag_count", 0) for s in stock_reports) + len(batch_flags)
    severity = _max_severity(
        *(s.get("severity", "ok") for s in stock_reports),
        *(f.get("severity", "ok") for f in batch_flags),
        "ok",
    )

    return {
        "severity": severity,
        "flag_count": total_flags,
        "symbols_flagged": len(flagged),
        "batch_flags": batch_flags,
        "stocks": stock_reports,
        "summary": {
            "buy": sum(1 for s in stock_reports if s.get("verdict") == "buy"),
            "watch": sum(1 for s in stock_reports if s.get("verdict") == "watch"),
            "skip": sum(1 for s in stock_reports if s.get("verdict") == "skip"),
            "mean_conviction": round(statistics.mean(convictions), 1) if convictions else None,
        },
    }


def build_quality_baselines(
    recent_runs: list[dict[str, Any]],
    *,
    exclude_run_id: str | None = None,
    window: int = 14,
) -> dict[str, Any]:
    """Historical baselines for batch mean conviction and buy rate per strategy."""
    by_strategy: dict[str, dict[str, list[float]]] = {}
    runs_used = 0

    for run in recent_runs:
        if exclude_run_id and run.get("id") == exclude_run_id:
            continue
        stages = run.get("stages") or {}
        llm = stages.get("llm_usage") if isinstance(stages.get("llm_usage"), dict) else {}
        for batch in llm.get("batches") or []:
            if not isinstance(batch, dict):
                continue
            q = batch.get("output_quality") if isinstance(batch.get("output_quality"), dict) else {}
            summary = q.get("summary") if isinstance(q.get("summary"), dict) else {}
            strat = str(batch.get("strategy") or "")
            if not strat:
                continue
            sym_n = int(batch.get("symbol_count", 0) or 0) or len(batch.get("symbols") or [])
            if sym_n <= 0:
                continue
            bucket = by_strategy.setdefault(strat, {"mean_conviction": [], "buy_rate": []})
            mc = summary.get("mean_conviction")
            if mc is not None:
                try:
                    bucket["mean_conviction"].append(float(mc))
                except (TypeError, ValueError):
                    pass
            buys = summary.get("buy")
            if buys is not None:
                try:
                    bucket["buy_rate"].append(float(buys) / float(sym_n))
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
        runs_used += 1
        if runs_used >= window:
            break

    out: dict[str, Any] = {"sample_size": runs_used, "by_strategy": {}}
    for strat, metrics in by_strategy.items():
        out["by_strategy"][strat] = {}
        for name, vals in metrics.items():
            if vals:
                out["by_strategy"][strat][name] = {
                    "median": statistics.median(vals),
                    "n": len(vals),
                }
    return out


def enrich_batch_quality_outliers(
    quality: dict[str, Any],
    strategy: str,
    baselines: dict[str, Any] | None,
) -> dict[str, Any]:
    """Add historical outlier notes to an existing output_quality block."""
    out = dict(quality)
    outliers: list[dict[str, str]] = []
    if not baselines:
        out["outliers"] = outliers
        return out

    strat_base = (baselines.get("by_strategy") or {}).get(strategy) or {}
    summary = out.get("summary") if isinstance(out.get("summary"), dict) else {}

    mc = summary.get("mean_conviction")
    mc_base = strat_base.get("mean_conviction") if isinstance(strat_base.get("mean_conviction"), dict) else {}
    if mc is not None and mc_base.get("median") is not None:
        med = float(mc_base["median"])
        if med > 0:
            delta = (float(mc) - med) / med * 100
            if abs(delta) >= 25:
                outliers.append(
                    _flag(
                        "conviction_vs_baseline",
                        f"Batch mean conviction {mc} vs baseline median {med:.1f} ({delta:+.0f}%)",
                        severity="warn" if abs(delta) < 40 else "alert",
                    )
                )

    buys = summary.get("buy")
    sym_total = (summary.get("buy") or 0) + (summary.get("watch") or 0) + (summary.get("skip") or 0)
    br_base = strat_base.get("buy_rate") if isinstance(strat_base.get("buy_rate"), dict) else {}
    if buys is not None and sym_total > 0 and br_base.get("median") is not None:
        rate = float(buys) / float(sym_total)
        med = float(br_base["median"])
        if med >= 0 and abs(rate - med) >= 0.35:
            outliers.append(
                _flag(
                    "buy_rate_vs_baseline",
                    f"Batch buy rate {rate:.0%} vs baseline median {med:.0%}",
                    severity="warn",
                )
            )

    out["outliers"] = outliers
    if outliers:
        out["severity"] = _max_severity(out.get("severity", "ok"), *(o.get("severity", "ok") for o in outliers))
    return out


_PATTERN_CODES = frozenset(
    {
        "batch_all_buy",
        "batch_all_skip",
        "batch_flat_conviction",
        "conviction_vs_baseline",
        "buy_rate_vs_baseline",
    }
)


def _categorize_flag(code: str) -> str:
    if code.startswith("grounding_"):
        return "facts"
    if code in _PATTERN_CODES:
        return "patterns"
    return "rules"


def _empty_category_counts() -> dict[str, int]:
    return {"facts": 0, "rules": 0, "patterns": 0}


def _count_flags_in_quality(q: dict[str, Any]) -> dict[str, int]:
    counts = _empty_category_counts()
    for f in (q.get("batch_flags") or []) + (q.get("outliers") or []):
        if isinstance(f, dict):
            cat = _categorize_flag(str(f.get("code") or ""))
            counts[cat] += 1
    for stock in q.get("stocks") or []:
        if not isinstance(stock, dict):
            continue
        for f in stock.get("flags") or []:
            if isinstance(f, dict):
                cat = _categorize_flag(str(f.get("code") or ""))
                counts[cat] += 1
    return counts


def _batch_symbol_list(batch: dict[str, Any]) -> list[str]:
    raw = batch.get("symbol_list")
    if isinstance(raw, list) and raw:
        return [str(s).upper() for s in raw if s]
    raw = batch.get("symbols")
    if isinstance(raw, list):
        return [str(s).upper() for s in raw if s]
    if isinstance(raw, str) and raw.strip():
        return [s.strip().upper() for s in raw.split(",") if s.strip()]
    return []


def build_quality_summary(batches: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll up per-batch output_quality blocks into run-level (or period-level) metrics."""
    total_symbols = 0
    symbols_flagged = 0
    total_batches = 0
    batches_with_flags = 0
    flag_count = 0
    by_category = _empty_category_counts()
    metrics_cited = 0
    metrics_matched = 0
    max_severity: Severity = "ok"
    by_strategy: dict[str, dict[str, Any]] = {}
    run_ids: set[str] = set()
    unique_symbols: set[str] = set()

    for batch in batches or []:
        if not isinstance(batch, dict):
            continue
        q = batch.get("output_quality")
        if not isinstance(q, dict) or not q:
            continue

        sym_n = int(batch.get("symbol_count", 0) or 0) or len(batch.get("symbols") or [])
        if sym_n <= 0:
            stocks = q.get("stocks") or []
            sym_n = len(stocks) if isinstance(stocks, list) else 0
        if sym_n <= 0:
            continue

        total_batches += 1
        total_symbols += sym_n

        run_id = str(batch.get("run_id") or "").strip()
        if run_id:
            run_ids.add(run_id)
        for sym in _batch_symbol_list(batch):
            unique_symbols.add(sym)
        if not _batch_symbol_list(batch):
            for stock in q.get("stocks") or []:
                if isinstance(stock, dict) and stock.get("symbol"):
                    unique_symbols.add(str(stock["symbol"]).upper())

        batch_symbols_flagged = int(q.get("symbols_flagged", 0) or 0)
        symbols_flagged += batch_symbols_flagged

        batch_flag_count = int(q.get("flag_count", 0) or 0)
        flag_count += batch_flag_count
        if batch_flag_count > 0:
            batches_with_flags += 1

        max_severity = _max_severity(max_severity, q.get("severity", "ok"))

        cats = _count_flags_in_quality(q)
        for key, val in cats.items():
            by_category[key] += val

        for stock in q.get("stocks") or []:
            if not isinstance(stock, dict):
                continue
            grounding = stock.get("grounding") if isinstance(stock.get("grounding"), dict) else {}
            metrics_cited += int(grounding.get("metrics_cited", 0) or 0)
            metrics_matched += int(grounding.get("metrics_matched", 0) or 0)

        strat = str(batch.get("strategy") or "")
        if strat:
            sb = by_strategy.setdefault(
                strat,
                {
                    "total_symbols": 0,
                    "symbols_flagged": 0,
                    "total_batches": 0,
                    "batches_with_flags": 0,
                    "by_category": _empty_category_counts(),
                    "metrics_cited": 0,
                    "metrics_matched": 0,
                },
            )
            sb["total_symbols"] += sym_n
            sb["symbols_flagged"] += batch_symbols_flagged
            sb["total_batches"] += 1
            if batch_flag_count > 0:
                sb["batches_with_flags"] += 1
            for key, val in cats.items():
                sb["by_category"][key] += val
            for stock in q.get("stocks") or []:
                if not isinstance(stock, dict):
                    continue
                grounding = stock.get("grounding") if isinstance(stock.get("grounding"), dict) else {}
                sb["metrics_cited"] += int(grounding.get("metrics_cited", 0) or 0)
                sb["metrics_matched"] += int(grounding.get("metrics_matched", 0) or 0)

    symbols_clean = max(0, total_symbols - symbols_flagged)
    clean_pct = round(symbols_clean / total_symbols * 100, 1) if total_symbols else None
    batches_clean = max(0, total_batches - batches_with_flags)
    batches_clean_pct = round(batches_clean / total_batches * 100, 1) if total_batches else None
    match_pct = round(metrics_matched / metrics_cited * 100, 1) if metrics_cited else None

    strategy_rows: dict[str, Any] = {}
    for strat, sb in by_strategy.items():
        sym_total = int(sb["total_symbols"])
        sym_flagged = int(sb["symbols_flagged"])
        sym_clean = max(0, sym_total - sym_flagged)
        strat_cited = int(sb.get("metrics_cited", 0) or 0)
        strat_matched = int(sb.get("metrics_matched", 0) or 0)
        strategy_rows[strat] = {
            **sb,
            "symbols_clean": sym_clean,
            "clean_pct": round(sym_clean / sym_total * 100, 1) if sym_total else None,
            "batches_clean": max(0, int(sb["total_batches"]) - int(sb["batches_with_flags"])),
            "grounding": {
                "metrics_cited": strat_cited,
                "metrics_matched": strat_matched,
                "match_pct": round(strat_matched / strat_cited * 100, 1) if strat_cited else None,
            },
        }

    return {
        "has_data": total_batches > 0,
        "run_count": len(run_ids),
        "unique_symbols": len(unique_symbols),
        "total_symbols": total_symbols,
        "symbols_flagged": symbols_flagged,
        "symbols_clean": symbols_clean,
        "clean_pct": clean_pct,
        "total_batches": total_batches,
        "batches_with_flags": batches_with_flags,
        "batches_clean": batches_clean,
        "batches_clean_pct": batches_clean_pct,
        "flag_count": flag_count,
        "severity": max_severity,
        "by_category": by_category,
        "grounding": {
            "metrics_cited": metrics_cited,
            "metrics_matched": metrics_matched,
            "match_pct": match_pct,
        },
        "by_strategy": strategy_rows,
    }
