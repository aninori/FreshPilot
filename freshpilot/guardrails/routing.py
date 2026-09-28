"""GRD-5/GRD-6: routing (escalate vs auto-approve) and the manager-override band."""

from __future__ import annotations

from typing import Literal

from freshpilot.agents.schemas import Finding

Route = Literal["auto_approve", "escalate"]

_ESCALATE_ACTIONS = {"shelf_check_then_order", "notify_supplier"}

OVERRIDE_BAND_LOW = 0.6
OVERRIDE_BAND_HIGH = 1.5


def route(finding: Finding, any_guardrail_fired: bool) -> Route:
    """GRD-5: escalate if risk is high, confidence is low, the action needs a
    precondition (shelf check / supplier notice), or any guardrail fired."""
    if finding.risk_level == "high":
        return "escalate"
    if finding.confidence == "low":
        return "escalate"
    if finding.recommended_action in _ESCALATE_ACTIONS:
        return "escalate"
    if any_guardrail_fired:
        return "escalate"
    return "auto_approve"


def needs_category_manager_approval(override_qty: float, recommended_qty: float) -> bool:
    """GRD-6: a manager override outside [0.6x, 1.5x] of the recommendation needs a reason."""
    if recommended_qty <= 0:
        return override_qty > 0
    ratio = override_qty / recommended_qty
    return not (OVERRIDE_BAND_LOW <= ratio <= OVERRIDE_BAND_HIGH)
