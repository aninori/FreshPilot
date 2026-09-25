"""Prints the data summary CLAUDE.md/BRD ask for after `make data`: row counts,
products per department, answer-key counts per type, and example messy listings."""

from __future__ import annotations

from typing import Any

import duckdb


def _fetchone(cur: duckdb.DuckDBPyConnection) -> tuple[Any, ...]:
    row = cur.fetchone()
    assert row is not None
    return row


def print_summary(con: duckdb.DuckDBPyConnection, n_examples: int = 3) -> None:
    print("Row counts per table:")
    tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
    for table in sorted(tables):
        n = _fetchone(con.execute(f"SELECT count(*) FROM {table}"))[0]
        print(f"  {table:22s} {n}")

    print("\nProducts per department:")
    for dept, n in con.execute(
        "SELECT dept_id, count(*) FROM products GROUP BY 1 ORDER BY 1"
    ).fetchall():
        print(f"  {dept:15s} {n}")

    print("\nAnswer-key counts per type:")
    for type_, n in con.execute(
        "SELECT type, count(*) FROM answer_key GROUP BY 1 ORDER BY 1"
    ).fetchall():
        print(f"  {type_:20s} {n}")

    print(f"\n{n_examples} example messy supplier listings for the same product:")
    row = con.execute(
        """
        SELECT product_id, listing_ids FROM answer_key
        WHERE type = 'duplicate_listing'
          AND json_extract(details, '$.n_listings') = ?
          AND json_extract(details, '$.planted_hard') = true
        LIMIT 1
        """,
        [n_examples],
    ).fetchone()
    if row is None:
        print("  (no product has that many planted listings)")
        return
    product_id, listing_ids = row
    name = _fetchone(con.execute("SELECT name FROM products WHERE product_id = ?", [product_id]))[0]
    print(f"  {product_id} - canonical name: {name!r}")
    for listing_id in listing_ids.split(","):
        supplier, raw_name, raw_unit, raw_pack, price = _fetchone(
            con.execute(
                "SELECT supplier_id, raw_name, raw_unit, raw_pack, price "
                "FROM supplier_listings WHERE listing_id = ?",
                [listing_id],
            )
        )
        print(
            f"    [{listing_id}] {supplier}: raw_name={raw_name!r} raw_unit={raw_unit!r} "
            f"raw_pack={raw_pack!r} price=${price}"
        )


def main() -> None:
    con = duckdb.connect("data/freshpilot.duckdb", read_only=True)
    try:
        print_summary(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
