"""Schema + DuckDB write roundtrip (small-scale, not the full 1280-product generator)."""

from __future__ import annotations

from pathlib import Path

import duckdb
from freshpilot.data.generate import generate, write_duckdb
from freshpilot.data.schema import TABLE_ORDER


def test_write_duckdb_roundtrip(tmp_path: Path) -> None:
    tables = generate(42)
    db_path = tmp_path / "freshpilot.duckdb"
    write_duckdb(tables, db_path)

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        created = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
        assert created == set(TABLE_ORDER)
        for table in TABLE_ORDER:
            n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            assert n == len(tables[table])
    finally:
        con.close()
