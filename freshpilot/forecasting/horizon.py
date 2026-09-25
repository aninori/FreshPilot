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


def cycle_days_for(dept_id: str) -> int:
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
