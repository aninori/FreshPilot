"""FC-7: the delivery-cycle horizon forecast (days until the next delivery), on
top of the daily values used for charts.

ORD-2 (§9.4) says the engine's cycle-demand quantile is "interpolated from the
calibrated quantiles" — not a separately trained model — so the horizon
forecast here scales the daily calibrated quantiles rather than training a
second set of models per horizon length.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from freshpilot.data.generate import DEPT_CYCLE_DAYS
from freshpilot.forecasting.model import QUANTILE_COLUMNS

DEFAULT_CYCLE_DAYS = 3


def calibrated_quantiles(row: pd.Series) -> dict[str, float]:
    """ORD-2: use calibrated endpoints, keeping the interior quantiles intact."""
    values = {col: float(row[col]) for col in QUANTILE_COLUMNS}
    values["q10"] = float(row["q10_cal"])
    values["q90"] = float(row["q90_cal"])
    ordered = [values[col] for col in QUANTILE_COLUMNS]
    if not np.isfinite(ordered).all() or min(ordered) < 0 or np.any(np.diff(ordered) < 0):
        raise ValueError("Forecast quantiles must be finite, nonnegative and non-crossing")
    return values


def calibrated_quantile_frame(frame: pd.DataFrame) -> np.ndarray:
    """Vectorized equivalent for backtests, preserving the canonical column order."""
    values = frame[QUANTILE_COLUMNS].to_numpy(dtype=float, copy=True)
    values[:, 0] = frame["q10_cal"].to_numpy(dtype=float)
    values[:, -1] = frame["q90_cal"].to_numpy(dtype=float)
    if not np.isfinite(values).all() or np.any(values < 0) or np.any(np.diff(values, axis=1) < 0):
        raise ValueError("Forecast quantiles must be finite, nonnegative and non-crossing")
    return np.asarray(values, dtype=float)


def cycle_days_for(dept_id: str) -> int:
    if dept_id == "bakery":
        return 1
    return DEPT_CYCLE_DAYS.get(dept_id, DEFAULT_CYCLE_DAYS)


def horizon_quantiles(daily_quantiles: pd.Series, cycle_days: int) -> pd.Series:
    """Scale a single row of daily quantiles up to a `cycle_days`-ahead total:
    the median scales linearly with the number of days, the spread around it
    scales with sqrt(cycle_days) — the standard approximation for summing
    `cycle_days` independent daily demands.
    """
    median = daily_quantiles["q50"]
    scale = np.sqrt(cycle_days)
    return pd.Series(
        {col: max(0.0, median * cycle_days + (daily_quantiles[col] - median) * scale) for col in QUANTILE_COLUMNS}
    )
