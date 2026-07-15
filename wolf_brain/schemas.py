"""Pydantic models for Wolf Brain structured Gemini outputs."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BrainPick(BaseModel):
    symbol: str
    quantity: int = Field(ge=0)
    buy_price: float
    target: float
    stop_loss: float
    conviction: int = Field(ge=0, le=100)
    reasoning: str


class DeployBrainOutput(BaseModel):
    birth_intent: str
    picks: list[BrainPick] = Field(default_factory=list)


class HoldingReview(BaseModel):
    symbol: str
    verdict: Literal["hold", "sell"]
    reasoning: str


class DailyReviewBrainOutput(BaseModel):
    holdings_review: list[HoldingReview] = Field(default_factory=list)
    new_picks: list[BrainPick] = Field(default_factory=list)
    current_intent: str
    daily_update: str
