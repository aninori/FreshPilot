"""FC-2: build the per-(date, product) feature frame forecasting trains and predicts on."""

from __future__ import annotations

import numpy as np
import pandas as pd

LAGS = [1, 7, 14, 28]
ROLLING_WINDOWS = [7, 28]

FEATURE_COLUMNS = [
    "lag_1", "lag_7", "lag_14", "lag_28",
    "roll_mean_7", "roll_mean_28",
    "dow", "is_holiday",
    "on_promo", "discount_pct", "days_to_promo", "days_since_promo",
    "price", "temp_f", "precip_prob",
    "category", "shelf_life_days", "is_random_weight",
]
CATEGORICAL_COLUMNS = ["category"]


def _promo_distances(
    dates: pd.DatetimeIndex, promo_ranges: list[tuple[pd.Timestamp, pd.Timestamp]]
) -> tuple[np.ndarray, np.ndarray]:
    """For each date: days until the next promo starts, and days since the last promo ended (capped at 999)."""
    days_to = np.full(len(dates), 999.0)
    days_since = np.full(len(dates), 999.0)
    for start, end in promo_ranges:
        future = (start - dates).days.to_numpy().astype(float)
        future[future < 0] = 999.0
        days_to = np.minimum(days_to, future)

        past = (dates - end).days.to_numpy().astype(float)
        past[past < 0] = 999.0
        days_since = np.minimum(days_since, past)
    return days_to, days_since


def build_feature_frame(
    sales_daily: pd.DataFrame,
    calendar: pd.DataFrame,
    weather: pd.DataFrame,
    promotions: pd.DataFrame,
    products: pd.DataFrame,
) -> pd.DataFrame:
    """One row per (date, product_id) present in sales_daily, target column `qty_sold`."""
    df = sales_daily[["date", "product_id", "qty_sold", "price", "on_promo"]].copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["product_id", "date"])

    grouped = df.groupby("product_id")["qty_sold"]
    for lag in LAGS:
        df[f"lag_{lag}"] = grouped.shift(lag)
    for window in ROLLING_WINDOWS:
        df[f"roll_mean_{window}"] = grouped.shift(1).rolling(window).mean().reset_index(level=0, drop=True)

    cal = calendar.copy()
    cal["date"] = pd.to_datetime(cal["date"])
    df = df.merge(cal[["date", "dow", "is_holiday"]], on="date", how="left")

    wx = weather.copy()
    wx["date"] = pd.to_datetime(wx["date"])
    df = df.merge(wx[["date", "temp_f", "precip_prob"]], on="date", how="left")

    promo_ranges_by_product: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]] = {}
    for row in promotions.itertuples(index=False):
        promo_ranges_by_product.setdefault(row.product_id, []).append(
            (pd.Timestamp(row.start_date), pd.Timestamp(row.end_date))
        )

    days_to_all = np.full(len(df), 999.0)
    days_since_all = np.full(len(df), 999.0)
    for product_id, idx in df.groupby("product_id").indices.items():
        ranges = promo_ranges_by_product.get(product_id)
        if not ranges:
            continue
        dates = pd.DatetimeIndex(df["date"].to_numpy()[idx])
        days_to, days_since = _promo_distances(dates, ranges)
        days_to_all[idx] = days_to
        days_since_all[idx] = days_since
    df["days_to_promo"] = days_to_all
    df["days_since_promo"] = days_since_all

    discount_by_product_date: dict[tuple[str, pd.Timestamp], float] = {}
    for row in promotions.itertuples(index=False):
        for d in pd.date_range(row.start_date, row.end_date):
            discount_by_product_date[(row.product_id, d)] = row.discount_pct
    df["discount_pct"] = [
        discount_by_product_date.get((pid, d), 0.0) if promo else 0.0
        for pid, d, promo in zip(df["product_id"], df["date"], df["on_promo"], strict=True)
    ]

    prod = products[["product_id", "category", "shelf_life_days", "is_random_weight"]].copy()
    df = df.merge(prod, on="product_id", how="left")
    df["category"] = df["category"].astype("category")

    return df
