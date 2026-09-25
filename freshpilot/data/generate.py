"""Synthetic data generator (BRD §8): builds every table and the answer key.

Determinism (CLAUDE.md rule 6): a single `np.random.default_rng(SEED)` is
threaded through every step below, called in a fixed order, so the same SEED
always produces byte-identical tables.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from freshpilot.config import SEED
from freshpilot.data import names
from freshpilot.data.answer_key import AnswerRow, answer_rows_to_df
from freshpilot.data.schema import TABLE_ORDER, create_schema

HISTORY_DAYS = 180
# Fixed anchor so generation is reproducible independent of when it's run.
END_DATE = date(2025, 6, 30)
START_DATE = END_DATE - timedelta(days=HISTORY_DAYS - 1)
STORE_ID = "318"
STORE_NAME = "Riverside"

TEST_WINDOW_DAYS = 28  # last 28 days of history, matches FC-3's test split

DOW_EFFECT = np.array([1.0, 0.95, 0.95, 1.0, 1.15, 1.30, 1.15])  # Mon..Sun
NB_DISPERSION = 8.0  # negative-binomial "r": lower = more overdispersed

HOLIDAYS = [
    (20, "Founders Day"),
    (55, "Summer Fest"),
    (95, "Harvest Day"),
    (140, "Autumn Fair"),
    (170, "Winter Lights"),
]

DEPT_CYCLE_DAYS = {"produce": 2, "deli": 2, "meat_seafood": 3, "dairy_eggs": 4}


def sample_negative_binomial(rng: np.random.Generator, mu: np.ndarray, r: float = NB_DISPERSION) -> np.ndarray:
    mu = np.clip(mu, 1e-6, None)
    p = r / (r + mu)
    return rng.negative_binomial(r, p).astype(float)


def build_calendar_and_weather(rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame, list[AnswerRow]]:
    dates = [START_DATE + timedelta(days=i) for i in range(HISTORY_DAYS)]
    holiday_by_offset = dict(HOLIDAYS)

    calendar = pd.DataFrame(
        {
            "date": dates,
            "dow": [d.weekday() for d in dates],
            "is_holiday": [i in holiday_by_offset for i in range(HISTORY_DAYS)],
            "holiday_name": [holiday_by_offset.get(i) for i in range(HISTORY_DAYS)],
        }
    )

    day_idx = np.arange(HISTORY_DAYS)
    seasonal_temp = 68 + 12 * np.sin(2 * np.pi * (day_idx - 30) / HISTORY_DAYS)
    temp_f = seasonal_temp + rng.normal(0, 4, size=HISTORY_DAYS)
    precip_prob = np.clip(rng.beta(1.5, 6, size=HISTORY_DAYS), 0, 1)
    condition = np.where(precip_prob > 0.5, "rain", "clear")

    # Plant weather_event: a handful of heat waves and rain spells (§8.4 "several").
    answer_rows: list[AnswerRow] = []
    n_events = 9
    event_starts = rng.choice(np.arange(0, HISTORY_DAYS - 4), size=n_events, replace=False)
    for i, start in enumerate(sorted(event_starts)):
        duration = int(rng.integers(2, 5))
        end = min(start + duration - 1, HISTORY_DAYS - 1)
        kind = "heat" if i % 2 == 0 else "rain"
        if kind == "heat":
            temp_f[start : end + 1] = rng.uniform(92, 103, size=end - start + 1)
            condition[start : end + 1] = "heat"
            affected = "berries, salad_greens (demand up)"
        else:
            precip_prob[start : end + 1] = rng.uniform(0.7, 0.95, size=end - start + 1)
            condition[start : end + 1] = "rain"
            affected = "bread (demand down)"
        answer_rows.append(
            AnswerRow(
                type="weather_event",
                start_date=dates[start],
                end_date=dates[end],
                details={"condition": kind, "affected_categories": affected},
            )
        )

    weather = pd.DataFrame(
        {
            "date": dates,
            "temp_f": np.round(temp_f, 1),
            "precip_prob": np.round(precip_prob, 3),
            "condition": condition,
        }
    )
    return calendar, weather, answer_rows


def build_promotions(products: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    n = len(products)
    promo_mask = rng.random(n) < 0.15
    promo_products = products.loc[promo_mask, "product_id"].to_numpy()
    rows = []
    for pid in promo_products:
        start_offset = int(rng.integers(0, HISTORY_DAYS - 10))
        duration = int(rng.integers(5, 10))
        start = START_DATE + timedelta(days=start_offset)
        end = start + timedelta(days=duration - 1)
        rows.append(
            {
                "product_id": pid,
                "start_date": start,
                "end_date": end,
                "discount_pct": round(float(rng.uniform(10, 30)), 1),
                "expected_lift": round(float(rng.uniform(1.15, 1.6)), 2),
            }
        )
    return pd.DataFrame(rows)


def plant_unit_mixups(
    listings: pd.DataFrame, rng: np.random.Generator, k: int = 20
) -> tuple[pd.DataFrame, list[AnswerRow]]:
    listings = listings.copy()
    idx = rng.choice(listings.index.to_numpy(), size=min(k, len(listings)), replace=False)
    answer_rows = []
    for i in idx:
        original_unit = listings.at[i, "raw_unit"]
        raw_pack = str(listings.at[i, "raw_pack"])
        if "LB" in raw_pack.upper():
            corrupted = str(rng.choice(["CT", "EA"]))
        elif "CT" in raw_pack.upper():
            corrupted = "LB"
        else:
            corrupted = str(rng.choice(["LB", "CT"]))
        listings.at[i, "raw_unit"] = corrupted
        answer_rows.append(
            AnswerRow(
                type="unit_mixup",
                listing_ids=str(listings.at[i, "listing_id"]),
                details={
                    "raw_pack": raw_pack,
                    "original_raw_unit": original_unit,
                    "corrupted_raw_unit": corrupted,
                },
            )
        )
    return listings, answer_rows


def plant_yield_item(products: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[AnswerRow]]:
    def first_variant(family_name: str) -> str:
        matches = products[products["name"].str.startswith(family_name)].sort_values("product_id")
        return str(matches.iloc[0]["product_id"])

    parent_id = first_variant("Whole chicken, fryer")
    children = {
        "Chicken thighs, bone-in": 0.28,
        "Chicken breasts, boneless": 0.32,
        "Chicken wings": 0.14,
    }
    rows = []
    child_ids = {}
    for family_name, fraction in children.items():
        cid = first_variant(family_name)
        child_ids[cid] = fraction
        rows.append({"parent_product_id": parent_id, "child_product_id": cid, "yield_per_parent": fraction})
    yields_df = pd.DataFrame(rows)

    # Promo on one cut (thighs), per BRD P4 / the yield demo narrative.
    promo_child = first_variant("Chicken thighs, bone-in")
    promo_start = START_DATE + timedelta(days=HISTORY_DAYS - 10)
    promo_df = pd.DataFrame(
        [
            {
                "product_id": promo_child,
                "start_date": promo_start,
                "end_date": promo_start + timedelta(days=6),
                "discount_pct": 15.0,
                "expected_lift": 1.4,
            }
        ]
    )
    answer_rows = [
        AnswerRow(
            type="yield_item",
            product_id=parent_id,
            details={"children": child_ids, "promo_child": promo_child},
        )
    ]
    return yields_df, promo_df, answer_rows


def simulate_history(
    products: pd.DataFrame,
    weather: pd.DataFrame,
    promotions: pd.DataFrame,
    rng: np.random.Generator,
) -> tuple[dict[str, pd.DataFrame], list[AnswerRow]]:
    n = len(products)
    product_ids = products["product_id"].to_numpy()
    dept_id = products["dept_id"].to_numpy()
    category = products["category"].to_numpy()
    unit_price = products["unit_price"].to_numpy(dtype=float)
    case_pack = products["case_pack"].to_numpy(dtype=float)
    shelf_life = products["shelf_life_days"].to_numpy(dtype=float)
    is_bakery = dept_id == "bakery"

    base_rate = np.clip(18.0 / np.sqrt(unit_price), 1.5, 40.0) * rng.uniform(0.7, 1.3, size=n)
    cycle_days = np.array([DEPT_CYCLE_DAYS.get(d, 3) for d in dept_id], dtype=int)
    offset = np.arange(n) % np.maximum(cycle_days, 1)
    waste_rate_baseline = np.clip(0.15 / shelf_life, 0.003, 0.05)

    # Promotion lookup: per-day boolean/lift arrays, product-indexed.
    promo_active = np.zeros((HISTORY_DAYS, n), dtype=bool)
    promo_lift = np.ones((HISTORY_DAYS, n))
    promo_discount = np.zeros((HISTORY_DAYS, n))
    pid_to_idx = {pid: i for i, pid in enumerate(product_ids)}
    for _, prow in promotions.iterrows():
        pidx = pid_to_idx[prow["product_id"]]
        start_off = (prow["start_date"] - START_DATE).days
        end_off = (prow["end_date"] - START_DATE).days
        promo_active[start_off : end_off + 1, pidx] = True
        promo_lift[start_off : end_off + 1, pidx] = prow["expected_lift"]
        promo_discount[start_off : end_off + 1, pidx] = prow["discount_pct"]

    # Weather effect arrays, category-specific (§8.3).
    temp_f = weather["temp_f"].to_numpy()
    precip_prob = weather["precip_prob"].to_numpy()
    is_berry_or_salad = np.isin(category, ["berries", "salad_greens"])
    is_bread = category == "bread"

    # Plant phantom_stock (test window only) and shrink_spike (anywhere) events.
    rng_products = rng.permutation(n)
    phantom_idx = rng_products[:12]
    shrink_idx = rng_products[12:18]

    phantom_active = np.zeros((HISTORY_DAYS, n), dtype=bool)
    shrink_mult = np.ones((HISTORY_DAYS, n))
    event_answer_rows: list[AnswerRow] = []

    test_window_start = HISTORY_DAYS - TEST_WINDOW_DAYS
    for pidx in phantom_idx:
        start = int(rng.integers(test_window_start, HISTORY_DAYS - 3))
        duration = int(rng.integers(3, 7))
        end = min(start + duration - 1, HISTORY_DAYS - 1)
        phantom_active[start : end + 1, pidx] = True
        event_answer_rows.append(
            AnswerRow(
                type="phantom_stock",
                product_id=str(product_ids[pidx]),
                start_date=START_DATE + timedelta(days=start),
                end_date=START_DATE + timedelta(days=end),
                details={"cause": "unrecorded shrink (spoilage/theft)"},
            )
        )
    for pidx in shrink_idx:
        start = int(rng.integers(0, HISTORY_DAYS - 8))
        end = start + 6
        multiplier = float(rng.uniform(2.5, 4.0))
        shrink_mult[start : end + 1, pidx] = multiplier
        event_answer_rows.append(
            AnswerRow(
                type="shrink_spike",
                product_id=str(product_ids[pidx]),
                start_date=START_DATE + timedelta(days=start),
                end_date=START_DATE + timedelta(days=end),
                details={"multiplier": round(multiplier, 2)},
            )
        )

    true_on_hand = base_rate * cycle_days * 1.4
    book_on_hand = true_on_hand.copy()

    sales_rows: list[np.ndarray] = []
    receipts_rows: list[np.ndarray] = []
    waste_rows: list[np.ndarray] = []
    inventory_rows: list[np.ndarray] = []
    bake_rows: list[np.ndarray] = []

    dates = [START_DATE + timedelta(days=i) for i in range(HISTORY_DAYS)]

    for t in range(HISTORY_DAYS):
        dow = dates[t].weekday()
        season_mult = 1 + 0.05 * np.sin(2 * np.pi * t / 90)
        holiday_mult = 1.4 if any(t == off for off, _ in HOLIDAYS) else 1.0
        weather_mult = np.ones(n)
        weather_mult[is_berry_or_salad] *= 1 + 0.4 * max(0.0, (temp_f[t] - 75) / 30)
        weather_mult[is_bread] *= 1 - 0.3 * precip_prob[t]

        mu_t = base_rate * DOW_EFFECT[dow] * season_mult * holiday_mult * promo_lift[t] * weather_mult
        demand_t = sample_negative_binomial(rng, mu_t)

        due_today = (offset == (t % np.maximum(cycle_days, 1)))
        target_qty = np.ceil(base_rate * cycle_days * 1.4 / case_pack) * case_pack
        supply_t = np.where(due_today & ~is_bakery, target_qty, 0.0)

        bake_qty = np.ceil(mu_t * 1.1)
        supply_t = np.where(is_bakery, bake_qty, supply_t)

        true_on_hand += supply_t
        book_on_hand += supply_t

        unrecorded_shrink = np.where(phantom_active[t], true_on_hand * rng.uniform(0.9, 0.99, size=n), 0.0)
        true_on_hand -= unrecorded_shrink

        waste_rate_today = waste_rate_baseline * shrink_mult[t]
        waste_t = true_on_hand * waste_rate_today
        true_on_hand -= waste_t
        book_on_hand -= waste_t

        sales_t = np.minimum(demand_t, true_on_hand)
        true_on_hand -= sales_t
        book_on_hand -= sales_t

        true_on_hand = np.clip(true_on_hand, 0, None)
        book_on_hand = np.clip(book_on_hand, 0, None)

        day_price = np.where(promo_active[t], unit_price * (1 - promo_discount[t] / 100), unit_price)

        sales_rows.append(np.column_stack([np.full(n, dates[t]), product_ids, sales_t, day_price, promo_active[t]]))
        recv_mask = (supply_t > 0) & ~is_bakery
        receipts_rows.append(
            np.column_stack(
                [
                    np.full(recv_mask.sum(), dates[t]),
                    product_ids[recv_mask],
                    supply_t[recv_mask],
                    np.array([f"LOT-{t:03d}-{i}" for i in np.where(recv_mask)[0]]),
                ]
            )
        )
        bake_rows.append(np.column_stack([np.full(n, dates[t]), product_ids, supply_t * is_bakery]))
        waste_mask = waste_t > 0.01
        waste_reason = np.where(shrink_mult[t] > 1, "shrink_spike", "spoilage")
        waste_rows.append(
            np.column_stack(
                [
                    np.full(waste_mask.sum(), dates[t]),
                    product_ids[waste_mask],
                    waste_t[waste_mask],
                    waste_reason[waste_mask],
                ]
            )
        )
        inventory_rows.append(np.column_stack([np.full(n, dates[t]), product_ids, book_on_hand.copy()]))

    sales_daily = pd.DataFrame(
        np.concatenate(sales_rows), columns=["date", "product_id", "qty_sold", "price", "on_promo"]
    )
    sales_daily["store_id"] = STORE_ID
    sales_daily = sales_daily.astype({"qty_sold": float, "price": float, "on_promo": bool})
    sales_daily = sales_daily[["date", "store_id", "product_id", "qty_sold", "price", "on_promo"]]

    receipts = pd.DataFrame(np.concatenate(receipts_rows), columns=["date", "product_id", "qty_received", "lot_id"])
    receipts["store_id"] = STORE_ID
    receipts = receipts.astype({"qty_received": float})
    receipts = receipts[["date", "store_id", "product_id", "qty_received", "lot_id"]]

    bake_plans = pd.DataFrame(np.concatenate(bake_rows), columns=["date", "product_id", "planned_qty"])
    bake_plans["planned_qty"] = bake_plans["planned_qty"].astype(float)
    bake_plans = bake_plans[bake_plans["planned_qty"] > 0].reset_index(drop=True)

    waste_log = pd.DataFrame(np.concatenate(waste_rows), columns=["date", "product_id", "qty_wasted", "reason"])
    waste_log["store_id"] = STORE_ID
    waste_log = waste_log.astype({"qty_wasted": float})
    waste_log = waste_log[["date", "store_id", "product_id", "qty_wasted", "reason"]]

    inventory_book = pd.DataFrame(
        np.concatenate(inventory_rows), columns=["date", "product_id", "book_on_hand"]
    )
    inventory_book["store_id"] = STORE_ID
    inventory_book = inventory_book.astype({"book_on_hand": float})
    inventory_book = inventory_book[["date", "store_id", "product_id", "book_on_hand"]]

    tables = {
        "sales_daily": sales_daily,
        "receipts": receipts,
        "waste_log": waste_log,
        "inventory_book": inventory_book,
        "bake_plans": bake_plans,
    }
    return tables, event_answer_rows


def generate(seed: int = SEED) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    answer_rows: list[AnswerRow] = []

    stores = pd.DataFrame([{"store_id": STORE_ID, "name": STORE_NAME}])
    departments = pd.DataFrame([{"dept_id": d, "name": n} for d, n in names.DEPARTMENTS])
    suppliers = pd.DataFrame(
        [
            {
                "supplier_id": s.supplier_id,
                "name": s.name,
                "lead_time_days": int(rng.integers(1, 6)),
                "delivery_days": ",".join(rng.choice(["Mon", "Tue", "Wed", "Thu", "Fri"], size=3, replace=False)),
            }
            for s in names.SUPPLIERS
        ]
    )

    products = names.build_products(rng)

    listings, duplicate_answer_rows = names.build_supplier_listings(products, rng)
    listings, unit_mixup_rows = plant_unit_mixups(listings, rng)
    answer_rows.extend(duplicate_answer_rows)
    answer_rows.extend(unit_mixup_rows)

    hn_pairs = names.hard_negative_pairs(products, rng)
    for _, row in hn_pairs.iterrows():
        answer_rows.append(
            AnswerRow(type="hard_negative", product_id=row["product_id_a"], product_id_b=row["product_id_b"])
        )

    calendar, weather, weather_answer_rows = build_calendar_and_weather(rng)
    answer_rows.extend(weather_answer_rows)

    promotions = build_promotions(products, rng)
    yields_df, yield_promo, yield_answer_rows = plant_yield_item(products)
    promotions = pd.concat([promotions, yield_promo], ignore_index=True)
    answer_rows.extend(yield_answer_rows)

    history_tables, event_answer_rows = simulate_history(products, weather, promotions, rng)
    answer_rows.extend(event_answer_rows)

    product_listing_map = pd.DataFrame(columns=["listing_id", "product_id", "match_score", "method"])
    match_candidates = pd.DataFrame(columns=["listing_id", "product_id", "score", "decision"])
    forecasts = pd.DataFrame(
        columns=["date", "product_id", "q10", "q25", "q50", "q60", "q70", "q75", "q85", "q90", "q10_cal", "q90_cal"]
    )

    answer_key = answer_rows_to_df(answer_rows)

    tables = {
        "stores": stores,
        "departments": departments,
        "suppliers": suppliers,
        "products": products,
        "supplier_listings": listings,
        "product_listing_map": product_listing_map,
        "match_candidates": match_candidates,
        "forecasts": forecasts,
        "promotions": promotions,
        "weather": weather,
        "calendar": calendar,
        "yields": yields_df,
        "answer_key": answer_key,
        **history_tables,
    }
    return {name: tables[name] for name in TABLE_ORDER}


def write_duckdb(tables: dict[str, pd.DataFrame], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = duckdb.connect(str(path))
    try:
        create_schema(con)
        for table in TABLE_ORDER:
            df = tables[table]
            if len(df) > 0:
                con.append(table, df)
    finally:
        con.close()


def main() -> None:
    tables = generate()
    out_path = Path("data") / "freshpilot.duckdb"
    write_duckdb(tables, out_path)
    print(f"wrote {out_path} ({sum(len(df) for df in tables.values())} total rows across {len(tables)} tables)")


if __name__ == "__main__":
    main()
