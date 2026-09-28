"""FC-1 (one LightGBM model per quantile) and FC-4 (non-crossing enforcement)."""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd

from freshpilot.forecasting.features import CATEGORICAL_COLUMNS, FEATURE_COLUMNS

QUANTILES = [0.10, 0.25, 0.50, 0.60, 0.70, 0.75, 0.85, 0.90]
QUANTILE_COLUMNS = [f"q{int(q * 100):02d}" for q in QUANTILES]


def train_quantile_models(train_df: pd.DataFrame, seed: int, num_boost_round: int = 200) -> dict[float, lgb.Booster]:
    """FC-1: one global model per quantile, trained across all products."""
    label = train_df["qty_sold"].to_numpy()
    features = train_df[FEATURE_COLUMNS]
    models: dict[float, lgb.Booster] = {}
    for q in QUANTILES:
        params = {
            "objective": "quantile",
            "alpha": q,
            "metric": "quantile",
            "verbosity": -1,
            "seed": seed,
            "deterministic": True,
            "num_leaves": 31,
            "min_data_in_leaf": 50,
            "num_threads": 4,
            "force_col_wise": True,
        }
        dataset = lgb.Dataset(features, label=label, categorical_feature=CATEGORICAL_COLUMNS, free_raw_data=False)
        models[q] = lgb.train(params, dataset, num_boost_round=num_boost_round)
    return models


def predict_quantiles(models: dict[float, lgb.Booster], df: pd.DataFrame) -> pd.DataFrame:
    """Predict every quantile and enforce FC-4 non-crossing by sorting per row."""
    features = df[FEATURE_COLUMNS]
    raw = np.column_stack([models[q].predict(features, num_threads=4) for q in QUANTILES])
    raw = np.clip(raw, 0, None)
    sorted_vals = np.sort(raw, axis=1)  # FC-4: non-crossing quantiles, sorted per row
    return pd.DataFrame(sorted_vals, columns=QUANTILE_COLUMNS, index=df.index)
