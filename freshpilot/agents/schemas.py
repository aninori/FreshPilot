"""AGT-2/9.6: the Finding and MatchReview schemas agent output is validated against."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ExceptionType = Literal[
    "phantom_stock", "shrink_spike", "order_swing", "low_confidence_match", "yield_plan_change", "bake_plan_change"
]
RecommendedAction = Literal["approve", "shelf_check_then_order", "adjust", "notify_supplier", "review_match"]
RiskLevel = Literal["low", "medium", "high"]
Confidence = Literal["low", "medium", "high"]


class Evidence(BaseModel):
    value: float = Field(allow_inf_nan=False)
    unit: str
    source_tool: str
    source_ref: str


class Finding(BaseModel):
    product_id: str
    exception_type: ExceptionType
    summary: str = Field(max_length=400)
    evidence: list[Evidence]
    recommended_action: RecommendedAction
    recommended_calc_id: str | None = None
    precondition: str | None = None
    risk_level: RiskLevel
    confidence: Confidence
    estimated_impact_usd: float | None = Field(default=None, allow_inf_nan=False)
    reasons: list[str] = Field(default_factory=list, max_length=3)


class MatchReview(BaseModel):
    pair_id: str
    decision: Literal["merge", "keep_separate"]
    reason: str = Field(max_length=300)
    evidence: list[Evidence]
