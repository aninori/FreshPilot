"""G3: phantom-stock precision/recall against the full answer_key, not just the
recall-only check M4's test suite runs (that one only checks the 12 true positives;
G3 also needs precision, i.e. how many *other* products get wrongly flagged)."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from freshpilot.engine.inventory import consecutive_zero_sales_days, estimate_inventory, trailing_waste_rate


@dataclass(frozen=True)
class PhantomMetrics:
    precision: float
    recall: float
    true_positives: int
    false_positives: int
    false_negatives: int


def compute_phantom_metrics(
    products: pd.DataFrame,
    answer_key: pd.DataFrame,
    inventory_book: pd.DataFrame,
    waste_log: pd.DataFrame,
    sales_daily: pd.DataFrame,
    forecasts: pd.DataFrame,
    as_of: pd.Timestamp,
) -> PhantomMetrics:
    """`forecasts` must be the trained model's q50 predictions (same source the
    real detection path in freshpilot.exceptions.run uses) -- using a different
    daily-mean proxy here would make G3 test a different detector than the one
    that actually runs.
    """
    truth = set(answer_key.loc[answer_key["type"] == "phantom_stock", "product_id"])

    inventory_groups = dict(tuple(inventory_book.groupby("product_id")))
    waste_groups = dict(tuple(waste_log.groupby("product_id")))
    sales_groups = dict(tuple(sales_daily.groupby("product_id")))
    forecast_groups = dict(tuple(forecasts.groupby("product_id")))

    flagged: set[str] = set()
    for product in products.itertuples(index=False):
        pid = product.product_id
        inventory = inventory_groups.get(pid, inventory_book.iloc[:0])
        waste = waste_groups.get(pid, waste_log.iloc[:0])
        sales = sales_groups.get(pid, sales_daily.iloc[:0])
        forecast = forecast_groups.get(pid, forecasts.iloc[:0])
        book_rows = inventory[inventory["date"] == as_of]
        fc_rows = forecast[forecast["date"] == as_of]
        if len(book_rows) == 0 or len(fc_rows) == 0:
            continue
        book_on_hand = float(book_rows.iloc[0]["book_on_hand"])
        daily_mean = float(fc_rows.iloc[0]["q50"])
        waste_rate = trailing_waste_rate(waste, inventory, pid, as_of)
        zeros = consecutive_zero_sales_days(sales, pid, as_of)
        estimate = estimate_inventory(int(product.shelf_life_days), book_on_hand, daily_mean, waste_rate, zeros)
        if estimate.is_phantom_stock:
            flagged.add(pid)

    true_positives = len(flagged & truth)
    false_positives = len(flagged - truth)
    false_negatives = len(truth - flagged)
    precision = true_positives / len(flagged) if flagged else 0.0
    recall = true_positives / len(truth) if truth else 0.0
    return PhantomMetrics(
        precision=precision, recall=recall,
        true_positives=true_positives, false_positives=false_positives, false_negatives=false_negatives,
    )
