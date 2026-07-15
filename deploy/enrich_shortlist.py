"""Attach slim dossier facts to deploy shortlists for Wolf Brain."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_DOSSIER_BLOCKS = (
    "meta",
    "fundamentals",
    "technicals",
    "chart_shape",
    "news",
    "events",
)
_MAX_NEWS_ITEMS = 3


def _trim_news_block(news: Any) -> dict:
    if not isinstance(news, dict):
        return {}
    items = news.get("items") or []
    if not isinstance(items, list):
        items = []
    trimmed = items[:_MAX_NEWS_ITEMS]
    return {
        "aggregate_sentiment": news.get("aggregate_sentiment"),
        "sentiment_vs_price": news.get("sentiment_vs_price"),
        "items": [
            {
                "date": it.get("date"),
                "headline": it.get("headline"),
                "sentiment_score": it.get("sentiment_score"),
            }
            for it in trimmed
            if isinstance(it, dict)
        ],
    }


def slim_dossier_for_deploy(dossier: dict) -> dict:
    """Strategy-neutral facts Wolf Brain needs for targets — compact for tokens."""
    out: dict[str, Any] = {}
    for key in _DOSSIER_BLOCKS:
        if key not in dossier:
            continue
        if key == "news":
            block = _trim_news_block(dossier.get("news"))
            if block.get("items") or block.get("aggregate_sentiment"):
                out["news"] = block
        else:
            out[key] = dossier[key]
    return out


def _load_dossier_index() -> dict[str, dict]:
    from data_layer.storage import load_all_dossiers

    index: dict[str, dict] = {}
    for d in load_all_dossiers():
        ticker = (d.meta.ticker or "").strip().upper()
        if ticker:
            index[ticker] = d.to_dict()
    return index


def enrich_shortlist_with_dossiers(
    shortlist: list[dict],
    *,
    dossier_index: dict[str, dict] | None = None,
) -> list[dict]:
    """Merge synced dossier slices into each shortlist candidate."""
    if not shortlist:
        return shortlist

    index = dossier_index if dossier_index is not None else _load_dossier_index()
    if not index:
        log.warning("[DEPLOY] no dossiers on disk — shortlist sent without enrich")
        return [dict(c) for c in shortlist]

    enriched = 0
    out: list[dict] = []
    for cand in shortlist:
        row = dict(cand)
        sym = str(row.get("symbol", "")).strip().upper()
        dossier = index.get(sym)
        if dossier:
            row["dossier"] = slim_dossier_for_deploy(dossier)
            enriched += 1
        else:
            log.warning("[DEPLOY] no dossier for shortlist symbol %s", sym)
        out.append(row)

    log.info(
        "[DEPLOY] enriched %d/%d shortlist candidate(s) with dossier data",
        enriched,
        len(shortlist),
    )
    return out
