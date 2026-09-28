"""GRD-1, GRD-2, GRD-4, GRD-5, GRD-6 unit tests."""

from __future__ import annotations

from freshpilot.agents.schemas import Evidence, Finding
from freshpilot.guardrails.policy_band import check_policy_band
from freshpilot.guardrails.provenance import check_provenance
from freshpilot.guardrails.routing import needs_category_manager_approval, route
from freshpilot.guardrails.unit_veto import veto_merge_if_unit_incompatible


def _finding(**overrides: object) -> Finding:
    defaults: dict[str, object] = {
        "product_id": "P1",
        "exception_type": "order_swing",
        "summary": "Routine.",
        "evidence": [Evidence(value=10.0, unit="units", source_tool="simulate_order", source_ref="CALC-1")],
        "recommended_action": "approve",
        "recommended_calc_id": None,
        "precondition": None,
        "risk_level": "low",
        "confidence": "high",
        "estimated_impact_usd": None,
    }
    defaults.update(overrides)
    return Finding(**defaults)  # type: ignore[arg-type]


def test_provenance_passes_when_no_quantity_recommended() -> None:
    assert check_provenance(_finding(), known_calc_ids=set())


def test_provenance_fails_when_adjust_has_no_calc_id() -> None:
    finding = _finding(recommended_action="adjust", recommended_calc_id=None)
    assert not check_provenance(finding, known_calc_ids=set())


def test_provenance_fails_when_calc_id_unknown() -> None:
    finding = _finding(recommended_action="adjust", recommended_calc_id="CALC-999")
    assert not check_provenance(finding, known_calc_ids={"CALC-1"})


def test_provenance_passes_when_calc_id_known() -> None:
    finding = _finding(recommended_action="adjust", recommended_calc_id="CALC-1")
    assert check_provenance(finding, known_calc_ids={"CALC-1"})


def test_policy_band_within_range() -> None:
    result = check_policy_band(recommended_qty=12.0, base_order_qty=10.0)
    assert result.within_band


def test_policy_band_outside_range() -> None:
    result = check_policy_band(recommended_qty=20.0, base_order_qty=10.0)
    assert not result.within_band


def test_policy_band_respects_max_shelf_capacity() -> None:
    result = check_policy_band(recommended_qty=11.0, base_order_qty=10.0, max_shelf_capacity=10.5)
    assert not result.within_band


def test_unit_veto_blocks_incompatible_pack() -> None:
    assert veto_merge_if_unit_incompatible("CT", "CS/12 5LB")


def test_unit_veto_allows_compatible_pack() -> None:
    assert not veto_merge_if_unit_incompatible("LB", "CS/12 5LB")


def test_GRD4_unit_veto_checks_target_product() -> None:
    assert veto_merge_if_unit_incompatible("LB", "CS/12 5LB", "ea")
    assert not veto_merge_if_unit_incompatible("LB", "CS/12 5LB", "lb")


def test_route_escalates_high_risk() -> None:
    assert route(_finding(risk_level="high"), any_guardrail_fired=False) == "escalate"


def test_route_escalates_low_confidence() -> None:
    assert route(_finding(confidence="low"), any_guardrail_fired=False) == "escalate"


def test_route_escalates_shelf_check_action() -> None:
    assert route(_finding(recommended_action="shelf_check_then_order"), any_guardrail_fired=False) == "escalate"


def test_route_escalates_when_guardrail_fired() -> None:
    assert route(_finding(), any_guardrail_fired=True) == "escalate"


def test_route_auto_approves_clean_low_risk() -> None:
    assert route(_finding(), any_guardrail_fired=False) == "auto_approve"


def test_manager_override_within_band_ok() -> None:
    assert not needs_category_manager_approval(override_qty=12.0, recommended_qty=10.0)


def test_manager_override_outside_band_needs_approval() -> None:
    assert needs_category_manager_approval(override_qty=20.0, recommended_qty=10.0)
