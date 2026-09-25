"""G1: WAPE vs a seasonal-naive baseline (lag-7 — last week's same weekday), the
same baseline policy EVAL-2 uses elsewhere in the BRD.
"""

from __future__ import annotations

import numpy as np


def wape(actual: np.ndarray, forecast: np.ndarray) -> float:
    denom = np.sum(np.abs(actual))
    if denom == 0:
        return 0.0
    return float(np.sum(np.abs(actual - forecast)) / denom)


def relative_improvement(baseline_wape: float, model_wape: float) -> float:
    """Fraction by which model_wape beats baseline_wape (BRD G1: target >= 0.25)."""
    if baseline_wape == 0:
        return 0.0
    return (baseline_wape - model_wape) / baseline_wape
