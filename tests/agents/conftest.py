"""Shared fixtures for agent tests: a real ToolContext, with real trained forecasts
(needed for INV-2/3's Poisson test to be meaningful -- empty/zero forecasts would
never trip the phantom-stock threshold)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from freshpilot.agents.tools import ToolContext
from freshpilot.data.generate import generate, write_duckdb
from freshpilot.forecasting.run import run as run_forecasting


@pytest.fixture(scope="session")
def generated_tables() -> dict[str, pd.DataFrame]:
    return generate(42)


@pytest.fixture(scope="session")
def forecasting_result(
    tmp_path_factory: pytest.TempPathFactory, generated_tables: dict[str, pd.DataFrame]
) -> dict[str, object]:
    tmp_dir = tmp_path_factory.mktemp("agents_db")
    db_path: Path = tmp_dir / "freshpilot.duckdb"
    write_duckdb(generated_tables, db_path)
    return run_forecasting(db_path=db_path, seed=42)


@pytest.fixture(scope="session")
def tool_context(
    generated_tables: dict[str, pd.DataFrame], forecasting_result: dict[str, object]
) -> ToolContext:
    tables = generated_tables
    demo_date = pd.to_datetime(tables["sales_daily"]["date"]).max()

    # forecasting.run() only persists the 28-day test window; that's exactly the
    # window phantom_stock events are planted in, so it covers what we need here.
    test_df = forecasting_result["test_df"]
    preds = forecasting_result["models"]
    from freshpilot.forecasting.model import predict_quantiles

    quantiles = predict_quantiles(preds, test_df)
    forecasts = pd.concat(
        [test_df[["date", "product_id"]].reset_index(drop=True), quantiles.reset_index(drop=True)], axis=1
    )
    forecasts["q10_cal"] = forecasts["q10"]
    forecasts["q90_cal"] = forecasts["q90"]

    return ToolContext(
        products=tables["products"],
        sales_daily=tables["sales_daily"].assign(date=pd.to_datetime(tables["sales_daily"]["date"])),
        inventory_book=tables["inventory_book"].assign(date=pd.to_datetime(tables["inventory_book"]["date"])),
        waste_log=tables["waste_log"].assign(date=pd.to_datetime(tables["waste_log"]["date"])),
        forecasts=forecasts,
        promotions=tables["promotions"],
        weather=tables["weather"].assign(date=pd.to_datetime(tables["weather"]["date"])),
        calendar=tables["calendar"].assign(date=pd.to_datetime(tables["calendar"]["date"])),
        product_listing_map=tables["product_listing_map"],
        match_candidates=tables["match_candidates"],
        demo_date=demo_date,
        p50_model=preds[0.5],
    )


@pytest.fixture(scope="session")
def phantom_product_id(tool_context: ToolContext, generated_tables: dict[str, pd.DataFrame]) -> str:
    answer_key = generated_tables["answer_key"]
    phantom = answer_key[answer_key["type"] == "phantom_stock"]
    for row in phantom.itertuples(index=False):
        if pd.Timestamp(row.end_date) == tool_context.demo_date:
            return str(row.product_id)
    return str(phantom.iloc[0]["product_id"])
