"""
The dossier schema — ONE neutral fact-file per stock.

This is the single source of truth for the dossier's shape. It is strategy-
NEUTRAL: it holds raw numbers any strategy might read. The strategy lens
(which filters pass, which prompt) is applied later, by the selector — never
stored here. There is exactly one dossier per stock, no matter how many
strategies exist.

Blocks are filled in progressively across build phases; an empty block is a
valid state, not an error.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional
import json

from .config import DOSSIER_VERSION


@dataclass
class Meta:
    ticker: str = ""
    name: str = ""
    sector: str = ""
    as_of: str = ""          # ISO date the dossier was built
    snapshot: str = ""       # "pre_open" | "post_close"
    currency: str = "INR"


@dataclass
class Fundamentals:
    # Phase 0 (yfinance basics)
    price: Optional[float] = None
    market_cap_cr: Optional[float] = None
    pe: Optional[float] = None
    pb: Optional[float] = None
    debt_to_equity: Optional[float] = None
    roe: Optional[float] = None
    current_ratio: Optional[float] = None
    graham_number: Optional[float] = None
    fair_value_estimate: Optional[float] = None
    eps_ttm: Optional[float] = None
    dividend_yield: Optional[float] = None
    # Phase 2 (external source) — stay None until wired up
    revenue_growth_yoy: Optional[float] = None
    earnings_growth_yoy: Optional[float] = None
    net_margin: Optional[float] = None
    operating_margin: Optional[float] = None
    promoter_holding_pct: Optional[float] = None
    promoter_pledge_pct: Optional[float] = None
    fii_holding_pct: Optional[float] = None
    fii_holding_change_qoq: Optional[float] = None


@dataclass
class Technicals:
    dma_50: Optional[float] = None
    dma_200: Optional[float] = None
    above_50dma: Optional[bool] = None
    above_200dma: Optional[bool] = None
    rsi_2: Optional[float] = None
    rsi_14: Optional[float] = None
    pct_from_52w_high: Optional[float] = None
    pct_from_52w_low: Optional[float] = None
    volume_vs_20d_avg: Optional[float] = None
    atr_pct: Optional[float] = None
    rel_strength_vs_nifty_3m: Optional[float] = None
    rel_strength_vs_nifty_6m: Optional[float] = None
    return_1m: Optional[float] = None
    return_3m: Optional[float] = None
    return_6m: Optional[float] = None


@dataclass
class ChartShape:
    trend_50d: Optional[str] = None
    trend_200d: Optional[str] = None
    consolidation: Optional[str] = None
    volume_pattern: Optional[str] = None
    distance_note: Optional[str] = None


@dataclass
class MarketContext:
    nifty_above_200dma: Optional[bool] = None
    nifty_trend: Optional[str] = None
    india_vix: Optional[float] = None
    vix_regime: Optional[str] = None
    sector: Optional[str] = None
    sector_rank_of_11: Optional[int] = None      # Phase 2+ (needs sector indices)
    sector_return_1m: Optional[float] = None      # Phase 2+
    market_breadth_pct_above_200dma: Optional[float] = None


@dataclass
class NewsBlock:
    # Phase 3
    match_score_threshold: Optional[int] = None
    aggregate_sentiment: Optional[str] = None
    sentiment_vs_price: Optional[str] = None
    items: list = field(default_factory=list)


@dataclass
class Events:
    # Phase 3
    next_earnings_date: Optional[str] = None
    days_to_earnings: Optional[int] = None
    ex_dividend_date: Optional[str] = None
    recent_corporate_actions: list = field(default_factory=list)


@dataclass
class Dossier:
    dossier_version: str = DOSSIER_VERSION
    meta: Meta = field(default_factory=Meta)
    fundamentals: Fundamentals = field(default_factory=Fundamentals)
    technicals: Technicals = field(default_factory=Technicals)
    chart_shape: ChartShape = field(default_factory=ChartShape)
    market_context: MarketContext = field(default_factory=MarketContext)
    news: NewsBlock = field(default_factory=NewsBlock)
    events: Events = field(default_factory=Events)

    # ---- serialization ----
    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_dict(cls, d: dict) -> "Dossier":
        return cls(
            dossier_version=d.get("dossier_version", DOSSIER_VERSION),
            meta=Meta(**d.get("meta", {})),
            fundamentals=Fundamentals(**d.get("fundamentals", {})),
            technicals=Technicals(**d.get("technicals", {})),
            chart_shape=ChartShape(**d.get("chart_shape", {})),
            market_context=MarketContext(**d.get("market_context", {})),
            news=NewsBlock(**d.get("news", {})),
            events=Events(**d.get("events", {})),
        )

    @classmethod
    def from_json(cls, s: str) -> "Dossier":
        return cls.from_dict(json.loads(s))
