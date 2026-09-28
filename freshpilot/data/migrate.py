"""Additive migrations for existing demo databases; never regenerate raw history."""

import duckdb


def migrate(con: duckdb.DuckDBPyConnection) -> None:
    columns = {r[1] for r in con.execute("PRAGMA table_info('products')").fetchall()}
    if "max_shelf_capacity" not in columns:
        con.execute("ALTER TABLE products ADD COLUMN max_shelf_capacity DOUBLE")
        con.execute("UPDATE products SET max_shelf_capacity = max_order_cases * case_pack")
