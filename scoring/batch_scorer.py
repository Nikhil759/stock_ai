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
import os
import re
import time
from datetime import date
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

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
PROMPTS_DIR = _REPO / "selector" / "prompts"
SKELETON_PATH = Path(__file__).resolve().parent / "batch_skeleton.txt"

_client: genai.Client | None = None
_skeleton: str | None = None
_strategy_blocks: dict[str, str] = {}


class BatchStockScore(BaseModel):
    symbol: str
    conviction: int = Field(ge=0, le=100)
    verdict: Literal["buy", "watch", "skip"]
    reasoning: str


class BatchScoreResponse(BaseModel):
    scores: list[BatchStockScore]


def _client_get() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set")
        _client = genai.Client(api_key=api_key)
    return _client


def _load_skeleton() -> str:
    global _skeleton
    if _skeleton is None:
        _skeleton = SKELETON_PATH.read_text(encoding="utf-8")
    return _skeleton


def _load_strategy_block(strategy: str) -> str:
    if strategy not in _strategy_blocks:
        path = PROMPTS_DIR / f"strategy_{strategy}.txt"
        if not path.exists():
            raise FileNotFoundError(f"Missing strategy prompt: {path}")
        _strategy_blocks[strategy] = path.read_text(encoding="utf-8")
    return _strategy_blocks[strategy]


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
        # Prefer stronger 3m momentum / RS when trimming the fat list
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
        # Prefer tighter ranges / stronger volume confirmation
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

    # value / dip — keep funnel order
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


def _slim_dossier(dossier: dict) -> dict:
    """Keep the LLM payload focused — full blocks, drop huge raw blobs if any."""
    keep = (
        "meta",
        "fundamentals",
        "technicals",
        "chart_shape",
        "market_context",
        "news",
        "events",
        "order_book",
        "big_trades",
    )
    return {k: dossier.get(k) for k in keep if k in dossier}


def _build_batch_payload(strategy: str, batch: list[dict]) -> str:
    stocks = []
    for row in batch:
        reasons = dict(row.get("funnel_reasons") or {})
        entry: dict[str, Any] = {
            "symbol": row["symbol"],
            "funnel_reasons": reasons,
            "dossier": _slim_dossier(row.get("dossier") or {}),
        }
        if strategy == "winners":
            note = _winners_proxy_note(reasons)
            if note:
                entry["note"] = note
        stocks.append(entry)

    return json.dumps(
        {
            "strategy": strategy,
            "instruction": (
                "Score each stock independently on absolute merit for this strategy. "
                "Return one score object per input symbol."
            ),
            "stocks": stocks,
        },
        indent=2,
        default=str,
    )


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
    # Keep only expected symbols, in input order
    return [by_sym[s] for s in expected_symbols]


def _call_gemini(strategy: str, user_content: str) -> str:
    client = _client_get()
    system = _load_skeleton() + "\n\n" + _load_strategy_block(strategy)
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=BatchScoreResponse,
    )
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_content,
        config=config,
    )
    return response.text or ""


def _retry_sleep_sec(err: Exception, attempt: int) -> float:
    """Parse Gemini's 'Please retry in Xs' hint; else exponential backoff."""
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
) -> list[dict]:
    """Score one batch; retry once on parse errors, more on rate limits."""
    symbols = [r["symbol"].upper() for r in batch]
    label = f"{strategy.capitalize()}: batch {batch_idx}/{batch_total} [{', '.join(symbols)}]"
    print(f"[BATCH SCORING] {label}")

    for row in batch:
        if strategy == "winners":
            note = _winners_proxy_note(row.get("funnel_reasons") or {})
            if note:
                print(
                    f"[BATCH SCORING] {strategy.capitalize()}: batch {batch_idx}/{batch_total} "
                    f"— {row['symbol']} flagged (momentum proxy, no earnings data)"
                )

    payload = _build_batch_payload(strategy, batch)
    last_err: Exception | None = None
    scores: list[BatchStockScore] | None = None
    max_attempts = 5  # parse fails stop earlier; 429 can use the full budget

    for attempt in range(1, max_attempts + 1):
        try:
            text = _call_gemini(strategy, payload)
            raw = _extract_json(text)
            scores = _validate_batch(raw, symbols)
            break
        except (ValidationError, ValueError, json.JSONDecodeError) as e:
            last_err = e
            print(
                f"[BATCH SCORING] {strategy.capitalize()}: batch {batch_idx}/{batch_total} "
                f"parse/validate FAILED attempt={attempt}/2 — {e}"
            )
            if attempt >= 2:
                break
        except Exception as e:
            last_err = e
            rate = _is_rate_limit(e)
            print(
                f"[BATCH SCORING] {strategy.capitalize()}: batch {batch_idx}/{batch_total} "
                f"API FAILED attempt={attempt}/{max_attempts if rate else 2} — {e}"
            )
            if rate and attempt < max_attempts:
                wait = _retry_sleep_sec(e, attempt)
                print(
                    f"[BATCH SCORING] {strategy.capitalize()}: batch {batch_idx}/{batch_total} "
                    f"rate-limited — sleeping {wait:.1f}s before retry"
                )
                time.sleep(wait)
                continue
            if not rate and attempt >= 2:
                break
            if not rate:
                break

    if scores is None:
        print(
            f"[BATCH SCORING] {strategy.capitalize()}: batch {batch_idx}/{batch_total} "
            f"GAVE UP after retries — {last_err}"
        )
        # Degrade to skip so the pipeline continues
        scores = [
            BatchStockScore(
                symbol=s,
                conviction=0,
                verdict="skip",
                reasoning=f"Batch scoring failed after retry: {last_err}",
            )
            for s in symbols
        ]

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
) -> list[dict]:
    """
    Score all funnel candidates for one strategy in batches of BATCH_SIZE.

    Returns buy/watch survivors only (absolute-merit survivors for the shortlist),
    each with symbol, conviction, verdict, reasoning, price (frozen).
    """
    strategy = strategy.lower().strip()
    as_of = as_of or date.today()
    print(
        f"[BATCH SCORING] {strategy.capitalize()}: scoring {len(candidates)} "
        f"candidates (batch_size={BATCH_SIZE}, date={as_of.isoformat()})"
    )
    if not candidates:
        print(f"[BATCH SCORING] {strategy.capitalize()}: nothing to score")
        return []

    batches = _chunk(candidates, BATCH_SIZE)
    all_scored: list[dict] = []
    for i, batch in enumerate(batches, 1):
        if i > 1 and BATCH_PAUSE_SEC > 0:
            print(
                f"[BATCH SCORING] {strategy.capitalize()}: pausing "
                f"{BATCH_PAUSE_SEC:.0f}s between batches (rate-limit guard)"
            )
            time.sleep(BATCH_PAUSE_SEC)
        all_scored.extend(_score_one_batch(strategy, i, len(batches), batch))

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
    return survivors
