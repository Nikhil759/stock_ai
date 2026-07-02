"""Pydantic models for the LLM's structured outputs (Phase 2 scoring, Phase 3 final)."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class StockVerdict(BaseModel):
    ticker: str
    decision: Literal["buy", "watch", "skip"]
    conviction: int = Field(ge=0, le=100)
    buy_price: float
    stop_loss: float
    sell_target: float
    thesis: str
    risks: list[str] = Field(default_factory=list)
    key_signals: list[str] = Field(default_factory=list)


class Pick(BaseModel):
    ticker: str
    buy_price: float
    stop_loss: float
    sell_target: float
    allocation_inr: float
    shares: int
    conviction: int = Field(ge=0, le=100)
    rationale: str


class SkippedEntry(BaseModel):
    ticker: str
    reason: str


class FinalPicks(BaseModel):
    picks: list[Pick] = Field(default_factory=list)
    skipped: list[SkippedEntry] = Field(default_factory=list)
    cash_held_inr: float
    portfolio_note: str
