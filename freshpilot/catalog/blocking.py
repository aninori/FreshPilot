"""CAT-2: block candidate (listing, product) pairs by department, then by a
TF-IDF char-ngram "embedding" neighborhood, to avoid comparing every listing
against every product. See docs/progress.md M2 for why TF-IDF stands in for
the BRD §7 sentence-transformers model (confirmed with the user).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.sparse import spmatrix
from sklearn.feature_extraction.text import TfidfVectorizer

TOP_K = 2


@dataclass
class Embeddings:
    listing_vecs: spmatrix  # row order matches `listings`
    product_vecs: spmatrix  # row order matches `products`
    listing_row: dict[str, int]  # listing_id -> row index into listing_vecs
    product_row: dict[str, int]  # product_id -> row index into product_vecs


def build_embeddings(listings: pd.DataFrame, products: pd.DataFrame) -> Embeddings:
    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1)
    vectorizer.fit(pd.concat([listings["raw_name"], products["name"]], ignore_index=True))
    return Embeddings(
        listing_vecs=vectorizer.transform(listings["raw_name"]),
        product_vecs=vectorizer.transform(products["name"]),
        listing_row={lid: i for i, lid in enumerate(listings["listing_id"])},
        product_row={pid: i for i, pid in enumerate(products["product_id"])},
    )


def cosine_for_pairs(embeddings: Embeddings, pairs: list[tuple[str, str]]) -> list[float]:
    """Exact TF-IDF cosine for arbitrary (listing_id, product_id) pairs — used to score
    a listing's true match even when blocking's top_k neighborhood missed it."""
    out = []
    for listing_id, product_id in pairs:
        lvec = embeddings.listing_vecs[embeddings.listing_row[listing_id]]
        pvec = embeddings.product_vecs[embeddings.product_row[product_id]]
        out.append(float((lvec @ pvec.T)[0, 0]))
    return out


def block_candidates(
    listings: pd.DataFrame, products: pd.DataFrame, embeddings: Embeddings | None = None, top_k: int = TOP_K
) -> pd.DataFrame:
    """Returns one row per (listing_id, product_id, tfidf_cosine) candidate pair."""
    if embeddings is None:
        embeddings = build_embeddings(listings, products)

    listing_ids = listings["listing_id"].to_numpy()
    listing_dept = listings["dept_id"].to_numpy()
    product_ids = products["product_id"].to_numpy()
    product_dept = products["dept_id"].to_numpy()

    rows: list[dict[str, object]] = []
    for dept_id in np.unique(listing_dept):
        dept_listing_mask = listing_dept == dept_id
        dept_product_mask = product_dept == dept_id
        if not dept_product_mask.any():
            continue

        dept_listing_vecs = embeddings.listing_vecs[dept_listing_mask]
        dept_product_vecs = embeddings.product_vecs[dept_product_mask]
        # TF-IDF vectors are L2-normalized, so the dot product is cosine similarity.
        sims = (dept_listing_vecs @ dept_product_vecs.T).toarray()

        dept_product_ids = product_ids[dept_product_mask]
        dept_listing_ids = listing_ids[dept_listing_mask]
        k = min(top_k, sims.shape[1])
        top_idx = np.argpartition(-sims, kth=k - 1, axis=1)[:, :k]

        for row_i, listing_id in enumerate(dept_listing_ids):
            for col_j in top_idx[row_i]:
                rows.append(
                    {
                        "listing_id": listing_id,
                        "product_id": dept_product_ids[col_j],
                        "tfidf_cosine": float(sims[row_i, col_j]),
                    }
                )

    return pd.DataFrame(rows)
