"""M3 acceptance test (BRD §12 M3 row): WAPE beats baseline >=25%, coverage
78-85%, non-crossing quantiles.
"""

from __future__ import annotations

import numpy as np
import pytest
from freshpilot.data.generate import generate, write_duckdb
from freshpilot.forecasting import run as forecasting_run
from freshpilot.forecasting.explain import top_drivers
from freshpilot.forecasting.model import QUANTILE_COLUMNS

WAPE_IMPROVEMENT_TARGET = 0.25
COVERAGE_RANGE = (0.78, 0.85)


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    tmp_dir = tmp_path_factory.mktemp("forecasting_db")
    db_path = tmp_dir / "freshpilot.duckdb"
    write_duckdb(generate(42), db_path)
    return forecasting_run.run(db_path=db_path, seed=42)


def test_wape_beats_baseline_by_at_least_25pct(result: dict[str, object]) -> None:
    assert result["improvement"] >= WAPE_IMPROVEMENT_TARGET, result


def test_80pct_interval_coverage_in_target_band(result: dict[str, object]) -> None:
    assert COVERAGE_RANGE[0] <= result["coverage"] <= COVERAGE_RANGE[1], result["coverage"]


def test_quantiles_are_non_crossing(result: dict[str, object]) -> None:
    assert result["non_crossing_ok"]


def test_wape_by_department_all_positive_improvement(result: dict[str, object]) -> None:
    """Every department should at least beat the naive baseline, even if the
    overall (gating) figure is what BRD's M3 acceptance check requires."""
    for dept_id, metrics in result["wape_by_dept"].items():
        assert metrics["improvement"] > 0, (dept_id, metrics)


def test_shap_drivers_for_one_item(result: dict[str, object]) -> None:
    models = result["models"]
    test_df = result["test_df"]
    item_row = test_df.iloc[[0]]
    drivers = top_drivers(models[0.5], item_row, n=3)
    assert 1 <= len(drivers) <= 3
    assert all(isinstance(d, str) and d for d in drivers)


def test_quantile_columns_present_and_sorted(result: dict[str, object]) -> None:
    test_df = result["test_df"]
    from freshpilot.forecasting.model import predict_quantiles

    preds = predict_quantiles(result["models"], test_df.head(50))
    values = preds[QUANTILE_COLUMNS].to_numpy()
    assert np.all(np.diff(values, axis=1) >= 0)
