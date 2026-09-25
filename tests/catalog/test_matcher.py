"""M2 acceptance test (BRD §12 M2 row): Match F1 >= 0.93 on the held-out test split.

Uses freshpilot.data.generate.generate() directly (not data/freshpilot.duckdb) so
this test doesn't depend on `make data` having run first — `make test` must pass
standalone.
"""

from __future__ import annotations

import pytest
from freshpilot.catalog.matcher import (
    best_candidate_per_listing,
    decide,
    evaluate_f1,
    fit,
)
from freshpilot.data.generate import generate

F1_TARGET = 0.93


@pytest.fixture(scope="module")
def match_result():
    tables = generate(42)
    return fit(tables["supplier_listings"], tables["products"], tables["answer_key"], seed=42)


def test_match_f1_meets_brd_target(match_result) -> None:
    metrics = evaluate_f1(match_result)
    assert metrics["f1"] >= F1_TARGET, metrics


def test_decisions_cover_all_three_cat5_bands(match_result) -> None:
    decisions = best_candidate_per_listing(match_result)["decision"]
    assert set(decisions.unique()) == {"auto_merge", "needs_review", "auto_separate"}


@pytest.mark.parametrize(
    "probability,expected",
    [
        (0.95, "auto_merge"),
        (0.90, "auto_merge"),
        (0.75, "needs_review"),
        (0.60, "needs_review"),
        (0.59, "auto_separate"),
        (0.1, "auto_separate"),
    ],
)
def test_decide_thresholds_match_cat5(probability: float, expected: str) -> None:
    assert decide(probability) == expected


def test_every_listing_gets_exactly_one_decision(match_result) -> None:
    best = best_candidate_per_listing(match_result)
    assert len(best) == len(best["listing_id"].unique())
