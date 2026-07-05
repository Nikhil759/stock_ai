"""Pydantic models for fund manager LLM outputs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RedeployDecision(BaseModel):
    action: Literal["rebuy", "fund_pick", "hold"]
    ticker: str | None = None
    buy_price: float | None = None
    stop_loss: float | None = None
    sell_target: float | None = None
    shares: int = Field(0, ge=0)
    allocation_inr: float = Field(0, ge=0)
    rationale: str = ""
