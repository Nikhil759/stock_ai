"""
Phase D — batch LLM scoring (Gemini).

Scores funnel survivors in batches of BATCH_SIZE on absolute merit. Shared
daily prep — not per-bot. Uses a cacheable skeleton + strategy-specific lens.

Call-count controls:
- BATCH_SIZE=8 (more stocks per call than the original 5)
- DEFAULT_LLM_CAPS trims fat funnels (esp. Winners) before scoring
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date
from pathlib import Path
from typing import Any, Literal, NamedTuple

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

from scoring.output_quality import evaluate_batch_output
from scoring.payload_trim import (
    extract_shared_market_context,
    extract_stock_sector_context,
    slim_dossier_for_llm,
)
from selector.llm.client import (
    batch_scoring_cache_instruction,
    batch_scoring_system_base,
    generate_batch_scoring,
    get_or_create_batch_scoring_cache,
)
from selector.llm.usage import LlmUsageCollector, add_tokens, empty_tokens, extract_usage

_REPO = Path(__file__).resolve().parents[1]
load_dotenv(_REPO / ".env")

BATCH_SIZE = 8
# Small pause between batches; raise via BATCH_SCORING_PAUSE_SEC if rate-limited.
BATCH_PAUSE_SEC = float(os.getenv("BATCH_SCORING_PAUSE_SEC", "3") or "3")
# Soft caps before LLM (0 = uncapped). Keeps Winners/Box from exploding call count.
DEFAULT_LLM_CAPS: dict[str, int] = {
    "value": 0,
    "winners": 25,
    "box": 25,
    "dip": 0,
}
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"

log = logging.getLogger(__name__)


class BatchScoringResult(NamedTuple):
    survivors: list[dict]
    usage: dict[str, Any]


class BatchStockScore(BaseModel):
    symbol: str
    conviction: int = Field(ge=0, le=100)
    verdict: Literal["buy", "watch", "skip"]
    reasoning: str


class BatchScoreResponse(BaseModel):
    scores: list[BatchStockScore]


def _chunk(items: list, size: int) -> list[list]:
    if not items:
        return []
    return [items[i : i + size] for i in range(0, len(items), size)]


def _rank_key(strategy: str, row: dict) -> tuple:
    """Best-first sort key when applying DEFAULT_LLM_CAPS."""
    reasons = row.get("funnel_reasons") or {}
    dossier = row.get("dossier") or {}
    tech = dossier.get("technicals") or {}
    cs = dossier.get("chart_shape") or {}

    if strategy == "winners":
        r3 = reasons.get("return_3m")
        if r3 is None:
            r3 = tech.get("return_3m")
        rs = reasons.get("rel_strength_vs_nifty_3m")
        if rs is None:
            rs = tech.get("rel_strength_vs_nifty_3m")
        try:
            return (-(float(r3) if r3 is not None else -1e9), -(float(rs) if rs is not None else -1e9))
        except (TypeError, ValueError):
            return (0.0, 0.0)

    if strategy == "box":
        consol = reasons.get("consolidation_percentage")
        if consol is None:
            consol = cs.get("consolidation_percentage")
        vol = reasons.get("volume_ratio")
        if vol is None:
            vol = cs.get("volume_ratio")
        try:
            c = float(consol) if consol is not None else 99.0
            v = float(vol) if vol is not None else 0.0
            return (c, -v)
        except (TypeError, ValueError):
            return (99.0, 0.0)

    return (0,)


def apply_llm_cap(
    strategy: str,
    candidates: list[dict],
    *,
    hard_cap: int | None = None,
) -> list[dict]:
    """
    Trim candidates before scoring.

    hard_cap: if >0, overrides DEFAULT_LLM_CAPS for this strategy (CLI smoke).
    DEFAULT_LLM_CAPS entry of 0 means uncapped unless hard_cap is set.
    """
    strategy = strategy.lower().strip()
    if hard_cap and hard_cap > 0:
        limit = hard_cap
    else:
        limit = DEFAULT_LLM_CAPS.get(strategy, 0)

    if not limit or len(candidates) <= limit:
        return candidates

    ranked = sorted(candidates, key=lambda r: _rank_key(strategy, r))
    kept = ranked[:limit]
    dropped = [r["symbol"] for r in ranked[limit:]]
    print(
        f"[BATCH SCORING] {strategy.capitalize()}: capping "
        f"{len(candidates)} → {limit} before LLM "
        f"(dropped {len(dropped)}: {', '.join(dropped[:12])}"
        f"{' …' if len(dropped) > 12 else ''})"
    )
    return kept


def _frozen_price(dossier: dict) -> float | None:
    f = dossier.get("fundamentals") or {}
    p = f.get("price")
    try:
        return float(p) if p is not None else None
    except (TypeError, ValueError):
        return None


def _winners_proxy_note(funnel_reasons: dict) -> str | None:
    """Flag momentum-proxy earnings passes for the Winners prompt."""
    if not funnel_reasons:
        return None
    proxy = str(funnel_reasons.get("earnings_proxy") or "")
    if "return_3m" in proxy.lower() or (
        funnel_reasons.get("earnings_growth_yoy") is None
        and funnel_reasons.get("return_3m") is not None
    ):
        return (
            "earnings growth data unavailable; passed on 3-month price momentum only"
        )
    return None


def _payload_json(data: dict[str, Any]) -> str:
    return json.dumps(data, separators=(",", ":"), default=str)


def _payload_breakdown(
    strategy: str,
    batch: list[dict],
    *,
    include_market_context: bool,
    shared_market_context: dict[str, Any],
) -> dict[str, Any]:
    """Character counts per section for drift debugging (matches LLM payload)."""
    section_totals: dict[str, int] = {}
    per_symbol: dict[str, dict[str, int]] = {}

    if include_market_context and shared_market_context:
        mc_chars = len(_payload_json(shared_market_context))
        section_totals["market_context"] = mc_chars

    for row in batch:
        sym = str(row.get("symbol") or "").upper()
        if not sym:
            continue
        sections: dict[str, int] = {}
        d = slim_dossier_for_llm(row.get("dossier") or {})
        for key, val in d.items():
            chars = len(_payload_json({key: val}))
            sections[key] = chars
            section_totals[key] = section_totals.get(key, 0) + chars
        sector = extract_stock_sector_context(row.get("dossier") or {})
        if sector:
            sec_chars = len(_payload_json(sector))
            sections["sector"] = sec_chars
            section_totals["sector"] = section_totals.get("sector", 0) + sec_chars
        if strategy == "winners" and _winners_proxy_note(row.get("funnel_reasons") or {}):
            sections["winners_note"] = sections.get("winners_note", 0) + 120
            section_totals["winners_note"] = section_totals.get("winners_note", 0) + 120
        sym_meta = dict(row.get("funnel_reasons") or {})
        if sym_meta:
            fr_chars = len(_payload_json(sym_meta))
            sections["funnel_reasons"] = fr_chars
            section_totals["funnel_reasons"] = section_totals.get("funnel_reasons", 0) + fr_chars
        per_symbol[sym] = sections
    return {"sections": section_totals, "per_symbol": per_symbol}


def _build_batch_payload(
    strategy: str,
    batch: list[dict],
    *,
    include_market_context: bool,
    shared_market_context: dict[str, Any] | None = None,
) -> str:
    shared_mc = shared_market_context
    if shared_mc is None and batch:
        shared_mc = extract_shared_market_context(batch[0].get("dossier") or {})

    stocks = []
    for row in batch:
        reasons = dict(row.get("funnel_reasons") or {})
        entry: dict[str, Any] = {
            "symbol": row["symbol"],
            "funnel_reasons": reasons,
            "dossier": slim_dossier_for_llm(row.get("dossier") or {}),
        }
        sector = extract_stock_sector_context(row.get("dossier") or {})
        if sector:
            entry["sector"] = sector
        if strategy == "winners":
            note = _winners_proxy_note(reasons)
            if note:
                entry["note"] = note
        stocks.append(entry)

    body: dict[str, Any] = {
        "strategy": strategy,
        "instruction": (
            "Score each stock independently on absolute merit for this strategy. "
            "Return one score object per input symbol."
        ),
        "stocks": stocks,
    }
    if include_market_context and shared_mc:
        body["market_context"] = shared_mc

    return _payload_json(body)


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty model response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise
        return json.loads(m.group(0))


def _validate_batch(
    raw: dict | list,
    expected_symbols: list[str],
) -> list[BatchStockScore]:
    if isinstance(raw, list):
        raw = {"scores": raw}
    parsed = BatchScoreResponse.model_validate(raw)
    by_sym = {s.symbol.strip().upper(): s for s in parsed.scores}
    missing = [s for s in expected_symbols if s not in by_sym]
    if missing:
        raise ValueError(f"response missing symbols: {missing}")
    return [by_sym[s] for s in expected_symbols]


def _call_gemini(
    strategy: str,
    user_content: str,
    *,
    cache_name: str | None = None,
) -> tuple[str, dict[str, int], float]:
    t0 = time.monotonic()
    response = generate_batch_scoring(
        strategy,
        user_content,
        BatchScoreResponse,
        cache_name=cache_name,
    )
    elapsed = time.monotonic() - t0
    tokens = extract_usage(response)
    log.debug(
        "[BATCH SCORING] Gemini %s batch call — %s cached=%s elapsed=%.2fs payload=%d chars",
        strategy,
        f"prompt={tokens['prompt_tokens']} output={tokens['output_tokens']} "
        f"total={tokens['total_tokens']}",
        tokens.get("cached_tokens"),
        elapsed,
        len(user_content),
    )
    return response.text or "", tokens, elapsed


def _system_prompt_chars(
    strategy: str,
    *,
    cache_name: str | None,
    shared_market_context: dict[str, Any] | None,
) -> int:
    if cache_name and shared_market_context:
        return len(batch_scoring_cache_instruction(strategy, shared_market_context))
    return len(batch_scoring_system_base(strategy))


def _retry_sleep_sec(err: Exception, attempt: int) -> float:
    m = re.search(r"retry in ([0-9.]+)", str(err), re.I)
    if m:
        return max(float(m.group(1)) + 0.5, 1.0)
    return min(2 ** attempt * 4, 60)


def _is_rate_limit(err: Exception) -> bool:
    s = str(err)
    return "429" in s or "RESOURCE_EXHAUSTED" in s


def _score_one_batch(
    strategy: str,
    batch_idx: int,
    batch_total: int,
    batch: list[dict],
    *,
    usage: LlmUsageCollector | None = None,
    cache_name: str | None = None,
    shared_market_context: dict[str, Any] | None = None,
) -> list[dict]:
    """Score one batch; retry once on parse errors, more on rate limits."""
    symbols = [r["symbol"].upper() for r in batch]
    batch_label = f"{batch_idx}/{batch_total}"
    label = f"{strategy.capitalize()}: batch {batch_label} [{', '.join(symbols)}]"
    print(f"[BATCH SCORING] {label}")
    log.info("[BATCH SCORING] %s", label)

    for row in batch:
        if strategy == "winners":
            note = _winners_proxy_note(row.get("funnel_reasons") or {})
            if note:
                print(
                    f"[BATCH SCORING] {strategy.capitalize()}: batch {batch_label} "
                    f"— {row['symbol']} flagged (momentum proxy, no earnings data)"
                )

    mc = shared_market_context or extract_shared_market_context(batch[0].get("dossier") or {})
    include_mc_in_user = cache_name is None and bool(mc)
    payload = _build_batch_payload(
        strategy,
        batch,
        include_market_context=include_mc_in_user,
        shared_market_context=mc,
    )
    payload_chars = len(payload)
    payload_breakdown = _payload_breakdown(
        strategy,
        batch,
        include_market_context=include_mc_in_user,
        shared_market_context=mc,
    )
    system_prompt_chars = _system_prompt_chars(
        strategy,
        cache_name=cache_name,
        shared_market_context=mc,
    )
    last_err: Exception | None = None
    scores: list[BatchStockScore] | None = None
    max_attempts = 5
    batch_tokens = empty_tokens()
    batch_elapsed_ms = 0
    attempts_used = 0
    batch_status = "ok"
    last_response_text = ""

    for attempt in range(1, max_attempts + 1):
        attempts_used = attempt
        try:
            text, tokens, elapsed = _call_gemini(
                strategy,
                payload,
                cache_name=cache_name,
            )
            last_response_text = text or ""
            add_tokens(batch_tokens, tokens)
            batch_elapsed_ms += int(elapsed * 1000)
            raw = _extract_json(text)
            scores = _validate_batch(raw, symbols)
            break
        except (ValidationError, ValueError, json.JSONDecodeError) as e:
            last_err = e
            print(
                f"[BATCH SCORING] {strategy.capitalize()}: batch {batch_label} "
                f"parse/validate FAILED attempt={attempt}/2 — {e}"
            )
            if attempt >= 2:
                batch_status = "failed"
                break
        except Exception as e:
            last_err = e
            rate = _is_rate_limit(e)
            print(
                f"[BATCH SCORING] {strategy.capitalize()}: batch {batch_label} "
                f"API FAILED attempt={attempt}/{max_attempts if rate else 2} — {e}"
            )
            if rate and attempt < max_attempts:
                wait = _retry_sleep_sec(e, attempt)
                print(
                    f"[BATCH SCORING] {strategy.capitalize()}: batch {batch_label} "
                    f"rate-limited — sleeping {wait:.1f}s before retry"
                )
                time.sleep(wait)
                continue
            batch_status = "failed"
            if not rate and attempt >= 2:
                break
            if not rate:
                break

    if scores is None:
        print(
            f"[BATCH SCORING] {strategy.capitalize()}: batch {batch_label} "
            f"GAVE UP after retries — {last_err}"
        )
        batch_status = "degraded"
        scores = [
            BatchStockScore(
                symbol=s,
                conviction=0,
                verdict="skip",
                reasoning=f"Batch scoring failed after retry: {last_err}",
            )
            for s in symbols
        ]

    output_quality = evaluate_batch_output(
        strategy,
        batch,
        scores,
        winners_proxy_note_fn=_winners_proxy_note,
    )
    if output_quality.get("flag_count", 0) > 0:
        print(
            f"[BATCH SCORING] {strategy.capitalize()}: batch {batch_label} "
            f"quality {output_quality.get('severity')} — "
            f"{output_quality.get('flag_count')} flag(s), "
            f"{output_quality.get('symbols_flagged')} symbol(s)"
        )

    if usage is not None:
        usage.record_batch(
            strategy=strategy,
            batch_label=batch_label,
            symbols=symbols,
            tokens=batch_tokens,
            elapsed_ms=batch_elapsed_ms,
            attempts=attempts_used,
            status=batch_status,
            payload_chars=payload_chars,
            system_prompt_chars=system_prompt_chars,
            payload_breakdown=payload_breakdown,
            output_quality=output_quality,
            llm_io={
                "user_content": payload,
                "response_text": last_response_text or "",
                "system_prompt_chars": system_prompt_chars,
                "model": GEMINI_MODEL,
                "attempts_used": attempts_used,
                "context_cache": bool(cache_name),
            },
        )

    out: list[dict] = []
    by_row = {r["symbol"].upper(): r for r in batch}
    for sc in scores:
        row = by_row[sc.symbol.upper()]
        price = _frozen_price(row.get("dossier") or {})
        entry = {
            "symbol": sc.symbol.upper(),
            "conviction": sc.conviction,
            "verdict": sc.verdict,
            "reasoning": sc.reasoning,
            "price": price,
            "funnel_reasons": row.get("funnel_reasons") or {},
        }
        print(
            f"[BATCH SCORING]   {entry['symbol']} → conviction {entry['conviction']}, "
            f"{entry['verdict'].upper()} — {entry['reasoning']}"
        )
        out.append(entry)
    return out


def run_batch_scoring(
    strategy: str,
    candidates: list[dict],
    *,
    as_of: date | None = None,
    usage_collector: LlmUsageCollector | None = None,
    shared_market_context: dict[str, Any] | None = None,
) -> BatchScoringResult:
    """
    Score all funnel candidates for one strategy in batches of BATCH_SIZE.

    Returns buy/watch survivors only (absolute-merit survivors for the shortlist),
    each with symbol, conviction, verdict, reasoning, price (frozen), plus usage stats.
    """
    strategy = strategy.lower().strip()
    as_of = as_of or date.today()
    print(
        f"[BATCH SCORING] {strategy.capitalize()}: scoring {len(candidates)} "
        f"candidates (batch_size={BATCH_SIZE}, date={as_of.isoformat()})"
    )
    if not candidates:
        print(f"[BATCH SCORING] {strategy.capitalize()}: nothing to score")
        return BatchScoringResult(survivors=[], usage=empty_tokens())

    mc = shared_market_context
    if mc is None and candidates:
        mc = extract_shared_market_context(candidates[0].get("dossier") or {})

    cache_name = get_or_create_batch_scoring_cache(strategy, mc)
    if cache_name:
        print(
            f"[BATCH SCORING] {strategy.capitalize()}: using Gemini context cache "
            f"({cache_name})"
        )
    elif mc:
        print(
            f"[BATCH SCORING] {strategy.capitalize()}: context cache unavailable — "
            f"market_context included in user payload"
        )

    batches = _chunk(candidates, BATCH_SIZE)
    all_scored: list[dict] = []
    for i, batch in enumerate(batches, 1):
        if i > 1 and BATCH_PAUSE_SEC > 0:
            print(
                f"[BATCH SCORING] {strategy.capitalize()}: pausing "
                f"{BATCH_PAUSE_SEC:.0f}s between batches (rate-limit guard)"
            )
            time.sleep(BATCH_PAUSE_SEC)
        all_scored.extend(
            _score_one_batch(
                strategy,
                i,
                len(batches),
                batch,
                usage=usage_collector,
                cache_name=cache_name,
                shared_market_context=mc,
            )
        )

    survivors = [s for s in all_scored if s["verdict"] in ("buy", "watch")]
    failed = sum(
        1
        for s in all_scored
        if str(s.get("reasoning", "")).startswith("Batch scoring failed")
    )
    print(
        f"[BATCH SCORING] {strategy.capitalize()}: {len(all_scored)} scored → "
        f"{len(survivors)} survivors (buy/watch)"
    )
    if failed and failed == len(all_scored):
        print(
            f"[BATCH SCORING] {strategy.capitalize()}: WARNING — every batch failed "
            f"(API/parse). Shortlist will be empty; check GEMINI_API_KEY / quota."
        )

    strategy_usage = empty_tokens()
    if usage_collector is not None:
        strat_block = usage_collector.by_strategy.get(strategy) or {}
        strategy_usage = {k: int(strat_block.get(k, 0) or 0) for k in empty_tokens()}
    return BatchScoringResult(survivors=survivors, usage=strategy_usage)
