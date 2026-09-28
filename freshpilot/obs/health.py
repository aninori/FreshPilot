"""Measured weekly forecast performance and feature drift, with explicit windows."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def psi(reference: np.ndarray, current: np.ndarray) -> float:
    edges = np.unique(np.quantile(reference, np.linspace(0, 1, 11)))
    if len(edges) < 2:
        return 0.0 if np.all(current == reference[0]) else float("nan")
    edges[0], edges[-1] = -np.inf, np.inf
    p = np.maximum(np.histogram(reference, edges)[0] / len(reference), 1e-6)
    q = np.maximum(np.histogram(current, edges)[0] / len(current), 1e-6)
    return float(np.sum((q - p) * np.log(q / p)))


def model_health(tables: dict[str, pd.DataFrame]) -> dict[str, Any]:
    sales = tables["sales_daily"].sort_values(["product_id", "date"]).copy()
    sales["baseline"] = sales.groupby("product_id")["qty_sold"].shift(7)
    joined = sales.merge(tables["forecasts"], on=["date", "product_id"])
    start = joined["date"].min()
    joined["week"] = ((joined["date"] - start).dt.days // 7).astype(int)
    weeks = []
    for week, frame in joined.groupby("week"):
        actual = frame["qty_sold"].to_numpy()
        denom = max(float(actual.sum()), 1e-9)
        weeks.append({"week": int(week) + 1, "date": str(frame["date"].min().date()),
                      "wape": float(abs(actual - frame["q50"]).sum() / denom),
                      "baseline_wape": float(abs(actual - frame["baseline"]).sum() / denom),
                      "coverage": float(((actual >= frame["q10_cal"]) & (actual <= frame["q90_cal"])).mean())})
    drift = []
    for name, frame, columns in [("sales", sales, ["qty_sold", "price"]),
                                  ("weather", tables["weather"], ["temp_f", "precip_prob"])]:
        ref = frame[frame["date"] < start - pd.Timedelta(days=28)]
        now = frame[frame["date"] >= start]
        for column in columns:
            value = psi(ref[column].to_numpy(), now[column].to_numpy())
            drift.append({"group": name, "feature": column, "psi": value if np.isfinite(value) else None})
    return {"weeks": weeks, "drift": drift, "test_start": str(start.date()),
            "reference": "Training window, excluding calibration and test", "coverage_target": [0.78, 0.85]}
