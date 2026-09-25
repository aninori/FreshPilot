"""The `AnswerRow` shape shared by names.py (catalog planting) and generate.py
(everything else), plus assembly into the `answer_key` DataFrame."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass
class AnswerRow:
    type: str
    product_id: str | None = None
    product_id_b: str | None = None
    listing_ids: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    details: dict[str, object] | None = None


def answer_rows_to_df(rows: list[AnswerRow]) -> pd.DataFrame:
    out = []
    for i, r in enumerate(rows, start=1):
        out.append(
            {
                "answer_id": f"ANS-{i:05d}",
                "type": r.type,
                "product_id": r.product_id,
                "product_id_b": r.product_id_b,
                "listing_ids": r.listing_ids,
                "start_date": r.start_date,
                "end_date": r.end_date,
                "details": json.dumps(r.details) if r.details is not None else None,
            }
        )
    return pd.DataFrame(out)
