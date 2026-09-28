"""CAT-6 match review agent (fallback path — decision stays threshold-based per
§5 principle 1, regardless of LLM availability)."""

from __future__ import annotations

from freshpilot.agents.match_review import build_fallback_match_review


def test_merges_above_threshold() -> None:
    review = build_fallback_match_review("LST-1", "LB", "CS/12 5LB", "Strawberries, 1 lb", score=0.85)
    assert review.decision == "merge"


def test_keeps_separate_below_threshold() -> None:
    review = build_fallback_match_review("LST-1", "LB", "CS/12 5LB", "Strawberries, 1 lb", score=0.65)
    assert review.decision == "keep_separate"


def test_unit_veto_overrides_high_score() -> None:
    """GRD-4: even a high match score must be vetoed if the unit is internally inconsistent."""
    review = build_fallback_match_review("LST-1", "CT", "CS/12 5LB", "Strawberries, 1 lb", score=0.95)
    assert review.decision == "keep_separate"
    assert "unit" in review.reason.lower()


def test_reason_mentions_product_name() -> None:
    review = build_fallback_match_review("LST-1", "LB", "CS/12", "Green grapes, 2 lb bag", score=0.80)
    assert "Green grapes" in review.reason
