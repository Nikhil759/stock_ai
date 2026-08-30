"""Trim and dedupe dossier fields for batch LLM user payloads."""
from __future__ import annotations

import os
from typing import Any

SHARED_MARKET_CONTEXT_KEYS = (
    "nifty_above_200dma",
    "nifty_trend",
    "india_vix",
    "vix_regime",
    "market_breadth_pct_above_200dma",
)

STOCK_MARKET_CONTEXT_KEYS = (
    "sector",
    "sector_rank_of_11",
    "sector_return_1m",
)

DOSSIER_LLM_KEYS = (
    "meta",
    "fundamentals",
    "technicals",
    "chart_shape",
    "events",
    "order_book",
    "big_trades",
)

LLM_NEWS_MAX_ITEMS = max(0, int(os.getenv("LLM_NEWS_MAX_ITEMS", "3") or 3))
LLM_NEWS_SUMMARY_MAX_CHARS = max(40, int(os.getenv("LLM_NEWS_SUMMARY_MAX_CHARS", "120") or 120))
LLM_TRIM_NEWS = os.getenv("LLM_TRIM_NEWS", "1").strip().lower() not in ("0", "false", "no")


def _short_text(text: str | None, max_len: int) -> str:
    if not text:
        return ""
    cleaned = " ".join(str(text).split())
    if len(cleaned) <= max_len:
        return cleaned
    trimmed = cleaned[:max_len].rsplit(" ", 1)[0]
    return (trimmed or cleaned[:max_len]).rstrip() + "…"


def trim_news_for_llm(news: dict | None) -> dict[str, Any]:
    """Compact news block for LLM input — full dossiers on disk stay unchanged."""
    if not isinstance(news, dict):
        return {}

    if not LLM_TRIM_NEWS:
        return dict(news)

    out: dict[str, Any] = {}
    for key in ("aggregate_sentiment", "sentiment_vs_price"):
        if news.get(key) is not None:
            out[key] = news.get(key)

    items = []
    for item in (news.get("items") or [])[:LLM_NEWS_MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        trimmed = {
            "date": item.get("date"),
            "headline": item.get("headline"),
            "summary": _short_text(item.get("summary"), LLM_NEWS_SUMMARY_MAX_CHARS),
        }
        if item.get("sentiment_score") is not None:
            trimmed["sentiment_score"] = item.get("sentiment_score")
        items.append(trimmed)
    if items:
        out["items"] = items
    return out


def extract_shared_market_context(dossier: dict | None) -> dict[str, Any]:
    mc = (dossier or {}).get("market_context") or {}
    if not isinstance(mc, dict):
        return {}
    return {k: mc.get(k) for k in SHARED_MARKET_CONTEXT_KEYS if mc.get(k) is not None}


def extract_stock_sector_context(dossier: dict | None) -> dict[str, Any]:
    mc = (dossier or {}).get("market_context") or {}
    if not isinstance(mc, dict):
        return {}
    sector = {k: mc.get(k) for k in STOCK_MARKET_CONTEXT_KEYS if mc.get(k) is not None}
    if not sector and isinstance((dossier or {}).get("meta"), dict):
        meta_sector = (dossier or {}).get("meta", {}).get("sector")
        if meta_sector:
            sector["sector"] = meta_sector
    return sector


def slim_dossier_for_llm(dossier: dict | None) -> dict[str, Any]:
    """Dossier sections sent per stock — excludes duplicated market_context."""
    d = dossier or {}
    out = {k: d.get(k) for k in DOSSIER_LLM_KEYS if k in d}
    news = d.get("news")
    if news is not None:
        out["news"] = trim_news_for_llm(news if isinstance(news, dict) else {})
    return out
