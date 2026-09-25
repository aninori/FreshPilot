"""DuckDB DDL for the synthetic dataset (BRD §8.2, plus the additions logged in docs/progress.md)."""

from __future__ import annotations

import duckdb

DDL: dict[str, str] = {
    "stores": """
        CREATE TABLE stores (
            store_id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL
        )
    """,
    "departments": """
        CREATE TABLE departments (
            dept_id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL
        )
    """,
    "suppliers": """
        CREATE TABLE suppliers (
            supplier_id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL,
            lead_time_days INTEGER NOT NULL,
            delivery_days VARCHAR NOT NULL
        )
    """,
    # No product_id here on purpose: that link is exactly what catalog
    # reconciliation (M2) has to discover. dept_id is a realistic vendor-catalog
    # category, used for CAT-2 blocking; it is not a product identity.
    "supplier_listings": """
        CREATE TABLE supplier_listings (
            listing_id VARCHAR PRIMARY KEY,
            supplier_id VARCHAR NOT NULL REFERENCES suppliers(supplier_id),
            dept_id VARCHAR NOT NULL REFERENCES departments(dept_id),
            raw_name VARCHAR NOT NULL,
            raw_unit VARCHAR NOT NULL,
            raw_pack VARCHAR NOT NULL,
            price DOUBLE NOT NULL
        )
    """,
    "products": """
        CREATE TABLE products (
            product_id VARCHAR PRIMARY KEY,
            dept_id VARCHAR NOT NULL REFERENCES departments(dept_id),
            category VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            sell_unit VARCHAR NOT NULL,
            case_pack INTEGER NOT NULL,
            shelf_life_days INTEGER NOT NULL,
            unit_cost DOUBLE NOT NULL,
            unit_price DOUBLE NOT NULL,
            salvage_value DOUBLE NOT NULL,
            is_random_weight BOOLEAN NOT NULL,
            min_order_cases INTEGER NOT NULL,
            max_order_cases INTEGER NOT NULL
        )
    """,
    # Populated by M2's catalog reconciliation pipeline, not by the generator.
    "product_listing_map": """
        CREATE TABLE product_listing_map (
            listing_id VARCHAR NOT NULL REFERENCES supplier_listings(listing_id),
            product_id VARCHAR NOT NULL REFERENCES products(product_id),
            match_score DOUBLE NOT NULL,
            method VARCHAR NOT NULL
        )
    """,
    "sales_daily": """
        CREATE TABLE sales_daily (
            date DATE NOT NULL,
            store_id VARCHAR NOT NULL REFERENCES stores(store_id),
            product_id VARCHAR NOT NULL REFERENCES products(product_id),
            qty_sold DOUBLE NOT NULL,
            price DOUBLE NOT NULL,
            on_promo BOOLEAN NOT NULL
        )
    """,
    "receipts": """
        CREATE TABLE receipts (
            date DATE NOT NULL,
            store_id VARCHAR NOT NULL REFERENCES stores(store_id),
            product_id VARCHAR NOT NULL REFERENCES products(product_id),
            qty_received DOUBLE NOT NULL,
            lot_id VARCHAR NOT NULL
        )
    """,
    "waste_log": """
        CREATE TABLE waste_log (
            date DATE NOT NULL,
            store_id VARCHAR NOT NULL REFERENCES stores(store_id),
            product_id VARCHAR NOT NULL REFERENCES products(product_id),
            qty_wasted DOUBLE NOT NULL,
            reason VARCHAR NOT NULL
        )
    """,
    "inventory_book": """
        CREATE TABLE inventory_book (
            date DATE NOT NULL,
            store_id VARCHAR NOT NULL REFERENCES stores(store_id),
            product_id VARCHAR NOT NULL REFERENCES products(product_id),
            book_on_hand DOUBLE NOT NULL
        )
    """,
    "promotions": """
        CREATE TABLE promotions (
            product_id VARCHAR NOT NULL REFERENCES products(product_id),
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            discount_pct DOUBLE NOT NULL,
            expected_lift DOUBLE NOT NULL
        )
    """,
    "weather": """
        CREATE TABLE weather (
            date DATE PRIMARY KEY,
            temp_f DOUBLE NOT NULL,
            precip_prob DOUBLE NOT NULL,
            condition VARCHAR NOT NULL
        )
    """,
    "calendar": """
        CREATE TABLE calendar (
            date DATE PRIMARY KEY,
            dow INTEGER NOT NULL,
            is_holiday BOOLEAN NOT NULL,
            holiday_name VARCHAR
        )
    """,
    "yields": """
        CREATE TABLE yields (
            parent_product_id VARCHAR NOT NULL REFERENCES products(product_id),
            child_product_id VARCHAR NOT NULL REFERENCES products(product_id),
            yield_per_parent DOUBLE NOT NULL
        )
    """,
    # Historical bake quantities for Bakery (same-day shelf life, made in-store
    # rather than received) double as that department's daily supply in the
    # M1 simulation; the pipeline appends future planned bakes from M4 onward.
    "bake_plans": """
        CREATE TABLE bake_plans (
            date DATE NOT NULL,
            product_id VARCHAR NOT NULL REFERENCES products(product_id),
            planned_qty DOUBLE NOT NULL
        )
    """,
    # One generic table for all seven planted-problem types (§8.4) instead of
    # seven near-identical schemas. `details` is a JSON string with whatever
    # ground-truth values that type needs.
    "answer_key": """
        CREATE TABLE answer_key (
            answer_id VARCHAR PRIMARY KEY,
            type VARCHAR NOT NULL,
            product_id VARCHAR,
            product_id_b VARCHAR,
            listing_ids VARCHAR,
            start_date DATE,
            end_date DATE,
            details VARCHAR
        )
    """,
}

# Creation order matters: referenced tables must exist before their FKs do.
TABLE_ORDER: list[str] = [
    "stores",
    "departments",
    "suppliers",
    "products",
    "supplier_listings",
    "product_listing_map",
    "sales_daily",
    "receipts",
    "waste_log",
    "inventory_book",
    "promotions",
    "weather",
    "calendar",
    "yields",
    "bake_plans",
    "answer_key",
]


def create_schema(con: duckdb.DuckDBPyConnection) -> None:
    for table in TABLE_ORDER:
        con.execute(f"DROP TABLE IF EXISTS {table}")
    for table in TABLE_ORDER:
        con.execute(DDL[table])
