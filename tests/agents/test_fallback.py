"""AGT-6 fallback template tests: schema validity + faithfulness for every
exception type, using real engine output (no LLM)."""

from __future__ import annotations

import pytest
from freshpilot.agents.fallback import build_fallback_finding
from freshpilot.agents.tools import ToolContext
from freshpilot.guardrails.faithfulness import is_faithful

EXCEPTION_TYPES = [
    "phantom_stock", "shrink_spike", "order_swing", "low_confidence_match", "yield_plan_change", "bake_plan_change",
]


@pytest.mark.parametrize("exception_type", EXCEPTION_TYPES)
def test_fallback_finding_is_schema_valid_and_faithful(tool_context: ToolContext, exception_type: str) -> None:
    product_id = tool_context.products["product_id"].iloc[0]
    finding = build_fallback_finding(tool_context, product_id, exception_type)  # type: ignore[arg-type]

    assert finding.product_id == product_id
    assert finding.exception_type == exception_type

    known_values = [e.value for e in finding.evidence]
    if finding.estimated_impact_usd is not None:
        known_values.append(finding.estimated_impact_usd)
    faithful, unmatched = is_faithful(finding.summary, known_values)
    assert faithful, f"unfaithful numbers {unmatched} in: {finding.summary}"


def test_phantom_stock_finding_has_precondition_and_high_risk(tool_context: ToolContext) -> None:
    product_id = tool_context.products["product_id"].iloc[0]
    finding = build_fallback_finding(tool_context, product_id, "phantom_stock")
    assert finding.precondition is not None
    assert finding.risk_level == "high"
    assert finding.recommended_action == "shelf_check_then_order"


def test_adjust_action_always_carries_a_calc_id(tool_context: ToolContext) -> None:
    product_id = tool_context.products["product_id"].iloc[0]
    finding = build_fallback_finding(tool_context, product_id, "order_swing")
    assert finding.recommended_action == "adjust"
    assert finding.recommended_calc_id is not None
    assert finding.recommended_calc_id.startswith("CALC-")


def test_fallback_findings_valid_across_many_products(tool_context: ToolContext) -> None:
    """"Schema validity 100%" (M6 acceptance) across a real sample, not just one item."""
    sample = tool_context.products["product_id"].sample(30, random_state=42)
    for product_id in sample:
        for exception_type in EXCEPTION_TYPES:
            finding = build_fallback_finding(tool_context, product_id, exception_type)  # type: ignore[arg-type]
            assert finding.product_id == product_id  # pydantic already guarantees schema validity by construction
