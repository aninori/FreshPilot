"""Integration test: the full LangGraph investigator (triage -> investigate ->
validate -> route -> narrate) in fallback mode, on a real phantom-stock case —
BRD §13's demo script is the strawberries item; any planted phantom_stock event
plays the same role here.
"""

from __future__ import annotations

from freshpilot.agents.graph import run_investigator
from freshpilot.agents.tools import ToolContext
from freshpilot.config import Settings
from freshpilot.guardrails.faithfulness import is_faithful

FALLBACK_SETTINGS = Settings(
    seed=42, openai_api_key=None, openai_model="gpt-4o-mini", llm_mode="off",
    max_run_usd=1.0, demo_date=None, langfuse_public_key=None, langfuse_secret_key=None, langfuse_host=None,
)


def test_investigator_on_phantom_stock_case(tool_context: ToolContext, phantom_product_id: str) -> None:
    result = run_investigator(tool_context, FALLBACK_SETTINGS, phantom_product_id, "phantom_stock")

    finding = result["finding"]
    assert finding is not None
    assert finding.product_id == phantom_product_id
    assert finding.exception_type == "phantom_stock"
    assert finding.risk_level == "high"

    assert result["route"] == "escalate"
    assert result["source"] == "fallback"
    assert result["validation_errors"] == []

    known_values = [e.value for e in finding.evidence]
    faithful, unmatched = is_faithful(finding.summary, known_values)
    assert faithful, f"unfaithful numbers: {unmatched}"


def test_investigator_trace_records_every_step(tool_context: ToolContext, phantom_product_id: str) -> None:
    result = run_investigator(tool_context, FALLBACK_SETTINGS, phantom_product_id, "phantom_stock")
    step_names = [step["name"] for step in result["trace"]]
    assert step_names == ["triage", "investigate", "validate", "route", "narrate"]
    for step in result["trace"]:
        assert step["duration_s"] >= 0


def test_investigator_routine_order_auto_approves(tool_context: ToolContext) -> None:
    product_id = tool_context.products["product_id"].iloc[5]
    result = run_investigator(tool_context, FALLBACK_SETTINGS, product_id, "order_swing")
    assert result["route"] == "auto_approve"
