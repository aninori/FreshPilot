"""AGT-6: deterministic fallback — fills the Finding schema without an LLM so the
full pipeline/UI works with FRESHPILOT_LLM=off (CLAUDE.md rule 4). Numbers still
come from simulate_order, never invented — "no LLM" doesn't mean "no engine."
"""

from __future__ import annotations

from typing import Any

from freshpilot.agents.schemas import Confidence, Evidence, ExceptionType, Finding, RecommendedAction, RiskLevel
from freshpilot.agents.tools import ToolContext, call_tool

_TEMPLATES: dict[ExceptionType, tuple[str, RecommendedAction, str | None, RiskLevel, Confidence]] = {
    "phantom_stock": (
        "Sales patterns conflict with book inventory — the shelf may be empty.",
        "shelf_check_then_order",
        "Do a shelf check first",
        "high",
        "high",
    ),
    "shrink_spike": (
        "Waste over the last week is well above the usual rate for this item.",
        "notify_supplier",
        "Verify the lot before reordering",
        "medium",
        "medium",
    ),
    "low_confidence_match": (
        "This item's supplier listing match is below the confidence bar and needs review.",
        "review_match",
        None,
        "medium",
        "low",
    ),
    "order_swing": (
        "Today's recommended order differs from the usual amount by more than expected, with no known driver.",
        "adjust",
        None,
        "medium",
        "medium",
    ),
    "bake_plan_change": (
        "Today's bake quantity differs from the usual amount by more than expected.",
        "adjust",
        None,
        "medium",
        "medium",
    ),
    "yield_plan_change": (
        "Today's yield plan differs from the usual amount by more than expected.",
        "adjust",
        None,
        "medium",
        "medium",
    ),
}


def build_fallback_finding(ctx: ToolContext, product_id: str, exception_type: ExceptionType) -> Finding:
    order = call_tool(ctx, "simulate_order", {"product_id": product_id})

    summary, action, precondition, risk, confidence = _TEMPLATES.get(
        exception_type, ("Routine order, no issues detected.", "approve", None, "low", "high")
    )

    evidence = [
        Evidence(
            value=order["estimated_on_hand"], unit="units", source_tool="simulate_order", source_ref=order["calc_id"]
        ),
        Evidence(value=order["target_qty"], unit="units", source_tool="simulate_order", source_ref=order["calc_id"]),
    ]

    impact_usd: float | None = None
    if action == "adjust":
        breakdown: dict[str, Any] = order["breakdown"]
        trailing = breakdown.get("trailing_avg_target") or breakdown.get("trailing_avg")
        if trailing:
            # Only publish a separately recorded impact calculation, never an
            # untracked estimate made by the narrator.
            impact_usd = breakdown.get("estimated_impact_usd")

    return Finding(
        product_id=product_id,
        exception_type=exception_type,
        summary=summary,
        evidence=evidence,
        recommended_action=action,
        recommended_calc_id=order["calc_id"],
        precondition=precondition,
        risk_level=risk,
        confidence=confidence,
        estimated_impact_usd=impact_usd,
        reasons=[
            "The exception needs a closer look.",
            f"The demand target is {order['target_qty']:.6g} units.",
            f"The calculated quantity is {order['order_cases']} {order['quantity_unit']}.",
        ],
    )
