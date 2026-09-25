"""FC-6: per-item SHAP values from the P50 model, grouped into human-readable drivers."""

from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd
import shap

from freshpilot.forecasting.features import FEATURE_COLUMNS

_DOW_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_DOW_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def shap_values_for(model: lgb.Booster, df: pd.DataFrame) -> np.ndarray:
    explainer = shap.TreeExplainer(model)
    return np.asarray(explainer.shap_values(df[FEATURE_COLUMNS]))


def _describe_feature(feature: str, row: pd.Series) -> str:
    if feature == "on_promo" and row["on_promo"]:
        return "On promo"
    if feature == "discount_pct" and row["discount_pct"] > 0:
        return f"{row['discount_pct']:.0f}% off"
    if feature == "days_to_promo" and row["days_to_promo"] <= 3:
        target_dow = int((row["dow"] + row["days_to_promo"]) % 7)
        return f"Promo starts {_DOW_SHORT[target_dow]}"
    if feature == "dow" and int(row["dow"]) in (4, 5, 6):
        return "Weekend"
    if feature == "is_holiday" and row["is_holiday"]:
        return "Holiday"
    if feature == "precip_prob" and row["precip_prob"] > 0.5:
        return f"Rain {_DOW_LONG[int(row['dow'])]}"
    if feature == "temp_f" and row["temp_f"] > 80:
        return "Warm weather"
    if feature in ("lag_1", "lag_7", "lag_14", "lag_28", "roll_mean_7", "roll_mean_28"):
        return "Recent sales trend"
    return feature.replace("_", " ").title()


def top_drivers(model: lgb.Booster, item_row: pd.DataFrame, n: int = 3) -> list[str]:
    """Human-readable drivers for one item's P50 forecast, e.g. "Promo starts Fri",
    "Weekend", "Rain Saturday" (BRD §9.2 FC-6 examples)."""
    shap_vals = shap_values_for(model, item_row)[0]
    row = item_row.iloc[0]
    order = np.argsort(-np.abs(shap_vals))
    seen: set[str] = set()
    drivers: list[str] = []
    for idx in order:
        label = _describe_feature(FEATURE_COLUMNS[idx], row)
        if label not in seen:
            seen.add(label)
            drivers.append(label)
        if len(drivers) >= n:
            break
    return drivers
