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

    flagged: set[str] = set()
    for pid in products["product_id"]:
        book_rows = inventory_book[(inventory_book["product_id"] == pid) & (inventory_book["date"] == as_of)]
        fc_rows = forecasts[(forecasts["product_id"] == pid) & (forecasts["date"] == as_of)]
        if len(book_rows) == 0 or len(fc_rows) == 0:
            continue
        book_on_hand = float(book_rows.iloc[0]["book_on_hand"])
        daily_mean = float(fc_rows.iloc[0]["q50"])
        waste_rate = trailing_waste_rate(waste_log, inventory_book, pid, as_of)
        zeros = consecutive_zero_sales_days(sales_daily, pid, as_of)
        shelf_life = int(products.loc[products["product_id"] == pid, "shelf_life_days"].iloc[0])
        estimate = estimate_inventory(shelf_life, book_on_hand, daily_mean, waste_rate, zeros)
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
