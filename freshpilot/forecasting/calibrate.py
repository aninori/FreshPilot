"""FC-5: split-conformal CQR on the 80% interval (q10/q90), hand-rolled per
docs/progress.md M3 (open question #3) — Romano et al.'s conformalized quantile
regression, no MAPIE dependency.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ConformalMargin:
    margin: float  # add to q90, subtract from q10


def fit_conformal_margin(
    q10_pred: np.ndarray, q90_pred: np.ndarray, actual: np.ndarray, target_coverage: float = 0.80
) -> ConformalMargin:
    """Computed on the calibration split only."""
    alpha = 1 - target_coverage
    conformity_scores = np.maximum(q10_pred - actual, actual - q90_pred)
    margin = float(np.quantile(conformity_scores, 1 - alpha))
    return ConformalMargin(margin=max(margin, 0.0))


def apply_conformal_margin(quantiles: pd.DataFrame, margin: ConformalMargin) -> pd.DataFrame:
    calibrated = quantiles.copy()
    calibrated["q10"] = np.clip(calibrated["q10"] - margin.margin, 0, None)
    calibrated["q90"] = calibrated["q90"] + margin.margin
    return calibrated


def empirical_coverage(q10: np.ndarray, q90: np.ndarray, actual: np.ndarray) -> float:
    return float(np.mean((actual >= q10) & (actual <= q90)))
