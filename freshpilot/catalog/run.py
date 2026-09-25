"""Entry point for `make train`'s catalog half: fit the CAT-4 matcher against
data/freshpilot.duckdb, report the M2 acceptance metric, and persist decisions.

Writes `match_candidates` (every listing's best candidate + CAT-5 decision) and
`product_listing_map` (auto-merged listings only, method='auto') — replacing
prior contents, not touching any other table.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from freshpilot.catalog.matcher import best_candidate_per_listing, evaluate_f1, fit
from freshpilot.config import SEED

DB_PATH = Path("data") / "freshpilot.duckdb"


def run(db_path: Path = DB_PATH, seed: int = SEED) -> dict[str, float]:
    con = duckdb.connect(str(db_path))
    try:
        listings = con.execute("SELECT * FROM supplier_listings").df()
        products = con.execute("SELECT * FROM products").df()
        answer_key = con.execute("SELECT * FROM answer_key").df()

        result = fit(listings, products, answer_key, seed=seed)
        metrics = evaluate_f1(result)

        best = best_candidate_per_listing(result)
        match_candidates = best.rename(columns={"probability": "score"})[
            ["listing_id", "product_id", "score", "decision"]
        ]
        con.execute("DELETE FROM match_candidates")
        con.append("match_candidates", match_candidates)

        auto_merged = match_candidates[match_candidates["decision"] == "auto_merge"].copy()
        auto_merged["match_score"] = auto_merged["score"]
        auto_merged["method"] = "auto"
        con.execute("DELETE FROM product_listing_map")
        con.append("product_listing_map", auto_merged[["listing_id", "product_id", "match_score", "method"]])

        return metrics
    finally:
        con.close()


def main() -> None:
    metrics = run()
    print("Catalog reconciliation (CAT-1..CAT-5)")
    print(f"  Match F1 on test split: {metrics['f1']:.4f} (target >= 0.93)")
    print(f"  precision={metrics['precision']:.4f} recall={metrics['recall']:.4f}")
    print(f"  n_test_listings={metrics['n_test_listings']}")


if __name__ == "__main__":
    main()
