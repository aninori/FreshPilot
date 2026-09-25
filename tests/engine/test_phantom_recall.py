"""M4 acceptance test (BRD §12 M4 row): phantom-stock recall >= 0.90 against the
12 planted `phantom_stock` answer_key events.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from freshpilot.data.generate import generate, write_duckdb
from freshpilot.engine.inventory import consecutive_zero_sales_days, estimate_inventory, trailing_waste_rate
from freshpilot.forecasting import run as forecasting_run
from freshpilot.forecasting.model import predict_quantiles

PHANTOM_RECALL_TARGET = 0.90


@pytest.fixture(scope="module")
def phantom_eval_data(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, pd.DataFrame], dict[str, object]]:
    tmp_dir = tmp_path_factory.mktemp("engine_db")
    db_path: Path = tmp_dir / "freshpilot.duckdb"
    tables = generate(42)
    write_duckdb(tables, db_path)
    fc_result = forecasting_run.run(db_path=db_path, seed=42)
    return tables, fc_result


def test_phantom_stock_recall_at_least_90pct(
    phantom_eval_data: tuple[dict[str, pd.DataFrame], dict[str, object]],
) -> None:
    tables, fc_result = phantom_eval_data
    answer_key = tables["answer_key"]
    products = tables["products"].set_index("product_id")

    inventory_book = tables["inventory_book"].copy()
    inventory_book["date"] = pd.to_datetime(inventory_book["date"])
    waste_log = tables["waste_log"].copy()
    waste_log["date"] = pd.to_datetime(waste_log["date"])
    sales_daily = tables["sales_daily"].copy()
    sales_daily["date"] = pd.to_datetime(sales_daily["date"])

    models = fc_result["models"]
    test_df = fc_result["test_df"]

    phantom_events = answer_key[answer_key["type"] == "phantom_stock"]
    assert len(phantom_events) > 0

    detected = 0
    for row in phantom_events.itertuples(index=False):
        product_id = row.product_id
        end_date = pd.Timestamp(row.end_date)

        book_rows = inventory_book[(inventory_book["product_id"] == product_id) & (inventory_book["date"] == end_date)]
        book_on_hand = float(book_rows.iloc[0]["book_on_hand"]) if len(book_rows) else 0.0

        waste_rate = trailing_waste_rate(waste_log, inventory_book, product_id, end_date)
        zeros = consecutive_zero_sales_days(sales_daily, product_id, end_date)

        feature_row = test_df[(test_df["product_id"] == product_id) & (test_df["date"] == end_date)]
        assert len(feature_row) == 1, "phantom_stock events are planted in the forecasting test window"
        daily_mean = float(predict_quantiles(models, feature_row).iloc[0]["q50"])

        shelf_life = int(products.loc[product_id, "shelf_life_days"])
        estimate = estimate_inventory(shelf_life, book_on_hand, daily_mean, waste_rate, zeros)
        if estimate.is_phantom_stock:
            detected += 1

    recall = detected / len(phantom_events)
    assert recall >= PHANTOM_RECALL_TARGET, f"recall={recall:.2f} ({detected}/{len(phantom_events)})"
