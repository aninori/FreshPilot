"""Entry point for `make train`'s forecasting half: fit quantile models, calibrate,
evaluate against BRD §12 M3's acceptance check, log to MLflow, and persist
`forecasts` for the test window.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import duckdb
import mlflow
import numpy as np
import pandas as pd

from freshpilot.config import SEED
from freshpilot.forecasting.baseline import relative_improvement, wape
from freshpilot.forecasting.calibrate import apply_conformal_margin, empirical_coverage, fit_conformal_margin
from freshpilot.forecasting.explain import _describe_feature
from freshpilot.forecasting.features import FEATURE_COLUMNS, build_feature_frame
from freshpilot.forecasting.model import QUANTILE_COLUMNS, predict_quantiles, train_quantile_models

DB_PATH = Path("data") / "freshpilot.duckdb"
TEST_DAYS = 28
CALIBRATE_DAYS = 28
TARGET_COVERAGE = 0.80


def _split_by_date(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dates = sorted(df["date"].unique())
    test_start = dates[-TEST_DAYS]
    calibrate_start = dates[-(TEST_DAYS + CALIBRATE_DAYS)]
    train = df[df["date"] < calibrate_start]
    calibrate = df[(df["date"] >= calibrate_start) & (df["date"] < test_start)]
    test = df[df["date"] >= test_start]
    return train, calibrate, test


def run(db_path: Path = DB_PATH, seed: int = SEED) -> dict[str, Any]:
    con = duckdb.connect(str(db_path))
    try:
        sales_daily = con.execute("SELECT * FROM sales_daily").df()
        calendar = con.execute("SELECT * FROM calendar").df()
        weather = con.execute("SELECT * FROM weather").df()
        promotions = con.execute("SELECT * FROM promotions").df()
        products = con.execute("SELECT * FROM products").df()

        features = build_feature_frame(sales_daily, calendar, weather, promotions, products)
        train_df, calibrate_df, test_df = _split_by_date(features)
        train_df = train_df.dropna(subset=["lag_28"])  # needs full lag history; calibrate/test keep all rows

        # MLflow 3.x deprecated the plain "mlruns/" file store for a DB backend;
        # opt back in so logs land in "mlruns/" as CLAUDE.md's Makefile documents.
        os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
        mlflow.set_tracking_uri("file:./mlruns")
        mlflow.set_experiment("freshpilot-forecasting")
        with mlflow.start_run():
            mlflow.log_param("seed", seed)
            mlflow.log_param("n_train_rows", len(train_df))

            models = train_quantile_models(train_df, seed=seed)

            calibrate_preds = predict_quantiles(models, calibrate_df)
            margin = fit_conformal_margin(
                calibrate_preds["q10"].to_numpy(),
                calibrate_preds["q90"].to_numpy(),
                calibrate_df["qty_sold"].to_numpy(),
                target_coverage=TARGET_COVERAGE,
            )
            mlflow.log_param("conformal_margin", margin.margin)

            test_preds = predict_quantiles(models, test_df)
            test_preds_cal = apply_conformal_margin(test_preds, margin)

            actual = test_df["qty_sold"].to_numpy()
            model_wape = wape(actual, test_preds["q50"].to_numpy())
            baseline_forecast = test_df["lag_7"].fillna(0).to_numpy()
            baseline_wape_value = wape(actual, baseline_forecast)
            improvement = relative_improvement(baseline_wape_value, model_wape)
            coverage = empirical_coverage(
                test_preds_cal["q10"].to_numpy(), test_preds_cal["q90"].to_numpy(), actual
            )

            dept_by_product = products.set_index("product_id")["dept_id"]
            test_dept = test_df["product_id"].map(dept_by_product)
            wape_by_dept = {}
            for dept_id in sorted(test_dept.unique()):
                mask = (test_dept == dept_id).to_numpy()
                dept_model_wape = wape(actual[mask], test_preds["q50"].to_numpy()[mask])
                dept_baseline_wape = wape(actual[mask], baseline_forecast[mask])
                wape_by_dept[dept_id] = {
                    "model_wape": dept_model_wape,
                    "baseline_wape": dept_baseline_wape,
                    "improvement": relative_improvement(dept_baseline_wape, dept_model_wape),
                }

            non_crossing_ok = bool((np.diff(test_preds[QUANTILE_COLUMNS].to_numpy(), axis=1) >= 0).all())

            mlflow.log_metric("wape_model", model_wape)
            mlflow.log_metric("wape_baseline", baseline_wape_value)
            mlflow.log_metric("wape_improvement", improvement)
            mlflow.log_metric("coverage_80pct", coverage)
            artifact_dir = db_path.parent / "models"
            artifact_dir.mkdir(exist_ok=True)
            for quantile, model in models.items():
                model.save_model(str(artifact_dir / f"q{int(quantile * 100)}.txt"))
            (artifact_dir / "metadata.json").write_text(json.dumps({
                "seed": seed, "margin": margin.margin,
                "train_end": str(train_df["date"].max()),
                "calibration_end": str(calibrate_df["date"].max()),
            }), encoding="utf-8")
            mlflow.log_artifacts(str(artifact_dir), artifact_path="models")

        forecasts_out = test_preds.copy()
        forecasts_out["date"] = test_df["date"].to_numpy()
        forecasts_out["product_id"] = test_df["product_id"].to_numpy()
        forecasts_out["q10_cal"] = test_preds_cal["q10"].to_numpy()
        forecasts_out["q90_cal"] = test_preds_cal["q90"].to_numpy()
        forecasts_out = forecasts_out[
            ["date", "product_id", "q10", "q25", "q50", "q60", "q70", "q75", "q85", "q90", "q10_cal", "q90_cal"]
        ]
        con.execute("DELETE FROM forecasts")
        con.append("forecasts", forecasts_out)
        # LightGBM's contribution mode returns exact TreeSHAP values plus a bias
        # column. Persist the daily drivers so UI reads never rerun the model.
        contributions = np.asarray(models[0.5].predict(
            test_df[FEATURE_COLUMNS], pred_contrib=True, num_threads=4,
        ))[:, :-1]
        driver_rows = []
        for position, (_, feature_row) in enumerate(test_df.iterrows()):
            top = np.argsort(-np.abs(contributions[position]))[:3]
            drivers = [{"label": _describe_feature(FEATURE_COLUMNS[i], feature_row),
                        "feature": FEATURE_COLUMNS[i], "value": float(contributions[position, i])} for i in top]
            driver_rows.append({"date": feature_row["date"], "product_id": feature_row["product_id"],
                                "drivers": json.dumps(drivers)})
        con.execute("CREATE TABLE IF NOT EXISTS forecast_drivers (date DATE, product_id VARCHAR, drivers VARCHAR)")
        con.execute("DELETE FROM forecast_drivers")
        con.append("forecast_drivers", pd.DataFrame(driver_rows))

        return {
            "model_wape": model_wape,
            "baseline_wape": baseline_wape_value,
            "improvement": improvement,
            "coverage": coverage,
            "non_crossing_ok": non_crossing_ok,
            "wape_by_dept": wape_by_dept,
            "models": models,
            "test_df": test_df,
        }
    finally:
        con.close()


def main() -> None:
    result = run()
    print("Forecasting (FC-1..FC-8)")
    print(
        f"  WAPE model={result['model_wape']:.4f} baseline={result['baseline_wape']:.4f} "
        f"improvement={result['improvement']:.1%} (target >= 25%)"
    )
    print(f"  80% interval coverage: {result['coverage']:.1%} (target 78-85%)")
    print(f"  Non-crossing quantiles: {'OK' if result['non_crossing_ok'] else 'FAILED'}")
    print("  WAPE by department:")
    for dept_id, m in sorted(result["wape_by_dept"].items()):
        print(
            f"    {dept_id:15s} model={m['model_wape']:.4f} "
            f"baseline={m['baseline_wape']:.4f} improvement={m['improvement']:.1%}"
        )


if __name__ == "__main__":
    main()
