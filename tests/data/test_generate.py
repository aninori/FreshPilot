"""M1 acceptance tests (BRD §12 M1 row): row counts vs §8.1, answer key vs §8.4,
seeded reproducibility."""

from __future__ import annotations

import pandas as pd
import pytest
from freshpilot.data import names
from freshpilot.data.generate import HISTORY_DAYS, TEST_WINDOW_DAYS, generate

EXPECTED_TYPES = {
    "duplicate_listing",
    "unit_mixup",
    "hard_negative",
    "phantom_stock",
    "shrink_spike",
    "yield_item",
    "weather_event",
}


def within_pct(value: float, target: float, pct: float = 0.05) -> bool:
    return abs(value - target) <= pct * target


@pytest.fixture(scope="module")
def tables() -> dict[str, pd.DataFrame]:
    return generate(42)


def test_scale_row_counts_within_5pct_of_brd_8_1(tables: dict[str, pd.DataFrame]) -> None:
    assert len(tables["stores"]) == 1
    assert len(tables["departments"]) == 5
    assert within_pct(len(tables["suppliers"]), 6)
    assert within_pct(len(tables["products"]), 1280)
    assert within_pct(len(tables["supplier_listings"]), 2340)


def test_products_per_department_within_5pct_of_brd_8_1(tables: dict[str, pd.DataFrame]) -> None:
    counts = tables["products"]["dept_id"].value_counts().to_dict()
    for dept_id, target in names.DEPT_PRODUCT_TARGET.items():
        assert within_pct(counts.get(dept_id, 0), target), f"{dept_id}: {counts.get(dept_id)} vs {target}"


def test_answer_key_has_all_brd_8_4_types(tables: dict[str, pd.DataFrame]) -> None:
    assert EXPECTED_TYPES <= set(tables["answer_key"]["type"])


def test_phantom_stock_events_fall_in_test_window(tables: dict[str, pd.DataFrame]) -> None:
    """INV-3/EXC-1 phantom_stock cases must land in the last 28 days (FC-3's test split)."""
    ak = tables["answer_key"]
    from freshpilot.data.generate import START_DATE

    test_window_start = START_DATE + pd.Timedelta(days=HISTORY_DAYS - TEST_WINDOW_DAYS)
    phantom = ak[ak["type"] == "phantom_stock"]
    assert len(phantom) > 0
    for start in phantom["start_date"]:
        assert pd.Timestamp(start) >= pd.Timestamp(test_window_start)


def test_reproducibility_same_seed_gives_identical_tables() -> None:
    """NFR-2: identical outputs across runs in fallback mode, for a fixed SEED."""
    t1 = generate(42)
    t2 = generate(42)
    assert t1.keys() == t2.keys()
    for name, df1 in t1.items():
        pd.testing.assert_frame_equal(
            df1.reset_index(drop=True), t2[name].reset_index(drop=True), check_like=False
        )


def test_different_seed_changes_output(tables: dict[str, pd.DataFrame]) -> None:
    other = generate(43)
    assert not tables["products"]["unit_cost"].equals(other["products"]["unit_cost"])


def test_sales_and_inventory_are_non_negative(tables: dict[str, pd.DataFrame]) -> None:
    assert (tables["sales_daily"]["qty_sold"] >= 0).all()
    assert (tables["inventory_book"]["book_on_hand"] >= 0).all()
    assert (tables["waste_log"]["qty_wasted"] >= 0).all()


def test_supplier_listings_carry_no_product_id(tables: dict[str, pd.DataFrame]) -> None:
    """Design decision in docs/progress.md: listings must not leak the match answer."""
    assert "product_id" not in tables["supplier_listings"].columns
