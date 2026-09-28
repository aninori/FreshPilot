"""AGT-2 schema validity tests."""

from __future__ import annotations

import pytest
from freshpilot.agents.schemas import Evidence, Finding, MatchReview
from pydantic import ValidationError


def _valid_finding_kwargs() -> dict[str, object]:
    return {
        "product_id": "PRD-1001",
        "exception_type": "phantom_stock",
        "summary": "Shelf likely empty.",
        "evidence": [
            Evidence(value=48.0, unit="lb", source_tool="simulate_order", source_ref="CALC-order-PRD-1001-2025-06-30")
        ],
        "recommended_action": "shelf_check_then_order",
        "recommended_calc_id": None,
        "precondition": "Do a shelf check first",
        "risk_level": "high",
        "confidence": "high",
        "estimated_impact_usd": 120.0,
    }


def test_valid_finding_parses() -> None:
    finding = Finding(**_valid_finding_kwargs())  # type: ignore[arg-type]
    assert finding.product_id == "PRD-1001"


def test_finding_rejects_invalid_exception_type() -> None:
    kwargs = _valid_finding_kwargs()
    kwargs["exception_type"] = "not_a_real_type"
    with pytest.raises(ValidationError):
        Finding(**kwargs)  # type: ignore[arg-type]


def test_finding_rejects_invalid_risk_level() -> None:
    kwargs = _valid_finding_kwargs()
    kwargs["risk_level"] = "extreme"
    with pytest.raises(ValidationError):
        Finding(**kwargs)  # type: ignore[arg-type]


def test_finding_requires_evidence_field() -> None:
    kwargs = _valid_finding_kwargs()
    del kwargs["evidence"]
    with pytest.raises(ValidationError):
        Finding(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_GRD3_evidence_rejects_nonfinite_numbers(value):
    with pytest.raises(ValidationError):
        Evidence(value=value, unit="units", source_tool="simulate_order", source_ref="CALC-1")


def test_match_review_parses() -> None:
    review = MatchReview(
        pair_id="LST-00001",
        decision="merge",
        reason="Name and price line up.",
        evidence=[Evidence(value=0.92, unit="probability", source_tool="catalog_matcher", source_ref="LST-00001")],
    )
    assert review.decision == "merge"


def test_match_review_rejects_invalid_decision() -> None:
    with pytest.raises(ValidationError):
        MatchReview(
            pair_id="LST-00001",
            decision="maybe",  # type: ignore[arg-type]
            reason="Unsure.",
            evidence=[],
        )
