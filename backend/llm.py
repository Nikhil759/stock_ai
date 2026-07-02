"""LLM layer — Gemini ranks screener shortlists using strategy knowledge."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from pick_report import attach_pick_reports
from strategies import get_strategy, load_knowledge

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

MAX_KNOWLEDGE_CHARS = 12000
MAX_CANDIDATES = 12


def _api_key() -> str | None:
    return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")


def _build_prompt(strategy_id: str, budget: int, candidates: list[dict], bot_context: dict | None) -> str:
    meta = get_strategy(strategy_id) or {}
    knowledge = load_knowledge(strategy_id)[:MAX_KNOWLEDGE_CHARS]

    slim = []
    for c in candidates[:MAX_CANDIDATES]:
        slim.append({
            "ticker": c["ticker"],
            "name": c.get("name"),
            "sector": c.get("sector"),
            "buyPrice": c.get("buyPrice"),
            "sellPrice": c.get("sellPrice"),
            "passCount": c.get("passCount"),
            "recLabel": c.get("recLabel"),
            "recNote": c.get("recNote"),
            "signal": c.get("signal"),
            "canAfford": c.get("canLog"),
            "sharesAtBudget": c.get("shares"),
        })

    ctx = ""
    if bot_context:
        ctx = f"""
Bot context:
- Available cash: ₹{bot_context.get('availableCash', budget):,.0f}
- Allocation pool: ₹{bot_context.get('allocation', budget):,.0f}
- Mode: {bot_context.get('mode', 'advisory')} / level {bot_context.get('level', 'A')}
"""

    return f"""You are an NSE paper-trading assistant. The user runs a bot AFTER market close (~4 PM IST).
Trades are PLANNED today and executed at the NEXT morning open. Use end-of-day prices only.

STRATEGY: {meta.get('name', strategy_id)}
Horizon: {meta.get('horizon', '')}
Analysis: {meta.get('analysisType', '')}

--- STRATEGY RULES (reference) ---
{knowledge}
--- END RULES ---

Budget / cash for sizing: ₹{budget:,}
{ctx}

RULE-BASED SHORTLIST (from yfinance EOD data — may have gaps vs full spec):
{json.dumps(slim, indent=2)}

TASK:
1. Rank the best 1–3 picks for this strategy and budget.
2. For each pick: confirm or adjust buyPrice (use EOD close), sellPrice target, stopLoss, and plain-English reasoning.
3. Reject candidates that aren't worth trading:
   - Skip anything where `sharesAtBudget * buyPrice` would be a tiny trade (well under ₹2,000) —
     brokerage, STT, and slippage eat a disproportionate share of small trades.
   - Skip anything where the sellPrice target is less than ~3% above buyPrice — a gain that thin
     can be wiped out entirely by round-trip costs, so it's not a real edge.
   - If adjusting buyPrice/sellPrice yourself, make sure the adjusted pick still clears both bars.
4. Note any data limitations (yfinance may lack EPS history, FII, NSE surveillance flags).
5. If no pick is good enough, say so.

Respond with ONLY valid JSON (no markdown fences):
{{
  "summary": "one paragraph overview",
  "picks": [
    {{
      "ticker": "SYMBOL",
      "rank": 1,
      "buyPrice": 0.0,
      "sellPrice": 0.0,
      "stopLoss": 0.0,
      "confidence": "high|medium|low",
      "reasoning": "why this fits the strategy"
    }}
  ],
  "warnings": ["optional data caveats"]
}}"""


def _parse_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                return None
    return None


def enrich_with_llm(
    strategy_id: str,
    budget: int,
    candidates: list[dict],
    bot_context: dict | None = None,
) -> dict:
    """Call Gemini to rank picks. Returns {{llm, candidates}} — falls back on error."""
    if not candidates:
        return {"llm": {"summary": "No candidates to analyze.", "picks": [], "warnings": []}, "candidates": []}

    key = _api_key()
    if not key:
        return {
            "llm": {"summary": "LLM skipped — GEMINI_API_KEY not set.", "picks": [], "warnings": []},
            "candidates": candidates,
        }

    try:
        import google.generativeai as genai

        genai.configure(api_key=key)
        model = genai.GenerativeModel(
            os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            generation_config={"response_mime_type": "application/json", "temperature": 0.3},
        )
        prompt = _build_prompt(strategy_id, budget, candidates, bot_context)
        response = model.generate_content(prompt)
        parsed = _parse_json(response.text or "")
        if not parsed:
            return {
                "llm": {"summary": "LLM returned unparseable response — using rule-based list.", "picks": [], "warnings": []},
                "candidates": candidates,
            }
        merged = _merge_picks(candidates, parsed)
        attach_pick_reports(merged, strategy_id)
        return {"llm": parsed, "candidates": merged}
    except Exception as exc:
        return {
            "llm": {"summary": f"LLM unavailable ({exc}) — using rule-based shortlist.", "picks": [], "warnings": [str(exc)]},
            "candidates": candidates,
        }


def _merge_picks(candidates: list[dict], llm: dict) -> list[dict]:
    by_ticker = {c["ticker"]: dict(c) for c in candidates}
    picks = llm.get("picks") or []
    ranked = []
    for p in sorted(picks, key=lambda x: x.get("rank", 99)):
        t = p.get("ticker")
        if not t or t not in by_ticker:
            continue
        c = by_ticker[t]
        if p.get("buyPrice"):
            c["buyPrice"] = float(p["buyPrice"])
            c["buyFmt"] = f"₹{c['buyPrice']:,.2f}"
        if p.get("sellPrice"):
            c["sellPrice"] = float(p["sellPrice"])
            c["sellFmt"] = f"₹{c['sellPrice']:,.0f}"
        if p.get("stopLoss"):
            c["stopLoss"] = float(p["stopLoss"])
        reason = p.get("reasoning") or ""
        conf = p.get("confidence", "")
        c["recNote"] = f"{reason} ({conf} confidence)".strip() if reason else c.get("recNote", "")
        c["recLabel"] = f"AI pick #{p.get('rank', 1)}"
        c["recGood"] = conf in ("high", "medium")
        c["llmRank"] = p.get("rank")
        ranked.append(c)

    seen = {c["ticker"] for c in ranked}
    for c in candidates:
        if c["ticker"] not in seen:
            ranked.append(c)
    return ranked
