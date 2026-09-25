"""CAT-4 (calibrated match probability) and CAT-5 (merge/review/separate thresholds)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, precision_score, recall_score

from freshpilot.catalog.blocking import TOP_K, block_candidates, build_embeddings, cosine_for_pairs
from freshpilot.catalog.features import FEATURE_COLUMNS, compute_features

AUTO_MERGE_THRESHOLD = 0.90
AUTO_SEPARATE_THRESHOLD = 0.60

Decision = Literal["auto_merge", "needs_review", "auto_separate"]


def decide(probability: float) -> Decision:
    if probability >= AUTO_MERGE_THRESHOLD:
        return "auto_merge"
    if probability < AUTO_SEPARATE_THRESHOLD:
        return "auto_separate"
    return "needs_review"


def true_product_map(answer_key: pd.DataFrame) -> dict[str, str]:
    """listing_id -> true product_id, from the full duplicate_listing ground truth (docs/progress.md M1)."""
    mapping = {}
    dup = answer_key[answer_key["type"] == "duplicate_listing"]
    for row in dup.itertuples(index=False):
        for listing_id in str(row.listing_ids).split(","):
            mapping[listing_id] = row.product_id
    return mapping


def split_listings(
    listing_ids: np.ndarray, rng: np.random.Generator, train_frac: float = 0.7
) -> tuple[set[str], set[str]]:
    """Seeded 70/30 split by listing (open question #2): a listing's pairs never cross the split."""
    shuffled = rng.permutation(listing_ids)
    cut = int(len(shuffled) * train_frac)
    return set(shuffled[:cut]), set(shuffled[cut:])


def build_labeled_candidates(
    listings: pd.DataFrame, products: pd.DataFrame, answer_key: pd.DataFrame, top_k: int = TOP_K
) -> pd.DataFrame:
    embeddings = build_embeddings(listings, products)
    candidates = block_candidates(listings, products, embeddings=embeddings, top_k=top_k)
    candidates = compute_features(candidates, listings, products)
    true_map = true_product_map(answer_key)
    pairs = zip(candidates["listing_id"], candidates["product_id"], strict=True)
    candidates["label"] = [int(true_map.get(lid) == pid) for lid, pid in pairs]

    # Blocking's top-k neighborhood can miss the true pair for a heavily
    # abbreviated listing; inject it (with its real cosine, not a placeholder)
    # so the model can learn from it and evaluation counts it as a miss rather
    # than silently dropping it from the denominator.
    have_positive = set(candidates.loc[candidates["label"] == 1, "listing_id"])
    missing_pairs = [(lid, pid) for lid, pid in true_map.items() if lid not in have_positive]
    if missing_pairs:
        cosines = cosine_for_pairs(embeddings, missing_pairs)
        missing_df = pd.DataFrame(
            {
                "listing_id": [p[0] for p in missing_pairs],
                "product_id": [p[1] for p in missing_pairs],
                "tfidf_cosine": cosines,
            }
        )
        missing_df = compute_features(missing_df, listings, products)
        missing_df["label"] = 1
        candidates = pd.concat([candidates, missing_df], ignore_index=True)
    return candidates


@dataclass
class MatchResult:
    model: CalibratedClassifierCV
    candidates: pd.DataFrame  # all candidate pairs with features + label + probability
    train_listing_ids: set[str]
    test_listing_ids: set[str]


def fit(
    listings: pd.DataFrame, products: pd.DataFrame, answer_key: pd.DataFrame, seed: int, top_k: int = TOP_K
) -> MatchResult:
    rng = np.random.default_rng(seed)
    candidates = build_labeled_candidates(listings, products, answer_key, top_k=top_k)
    train_ids, test_ids = split_listings(listings["listing_id"].to_numpy(), rng)

    train_df = candidates[candidates["listing_id"].isin(train_ids)]
    base = LogisticRegression(max_iter=1000, class_weight="balanced")
    model = CalibratedClassifierCV(base, method="sigmoid", cv=3)
    model.fit(train_df[FEATURE_COLUMNS].to_numpy(), train_df["label"].to_numpy())

    candidates = candidates.copy()
    candidates["probability"] = model.predict_proba(candidates[FEATURE_COLUMNS].to_numpy())[:, 1]
    return MatchResult(model=model, candidates=candidates, train_listing_ids=train_ids, test_listing_ids=test_ids)


def best_candidate_per_listing(result: MatchResult) -> pd.DataFrame:
    """One row per listing: its highest-probability candidate, whether that candidate
    is actually the true match (`label`), and the CAT-5 decision."""
    idx = result.candidates.groupby("listing_id")["probability"].idxmax()
    best = result.candidates.loc[idx, ["listing_id", "product_id", "probability", "label"]].reset_index(drop=True)
    best["decision"] = best["probability"].map(decide)
    return best


def evaluate_f1(result: MatchResult, threshold: float = 0.5) -> dict[str, float]:
    """Match F1 (BRD §12 M2 acceptance).

    Evaluated per listing — is its single best candidate (a) actually the true
    product and (b) decided as a match — not per raw blocked candidate pair.
    CAT-5 makes one decision per listing; scoring every blocked candidate as an
    independent prediction would double-penalize a listing whose correct match
    and its runner-up both score reasonably high.
    """
    best = best_candidate_per_listing(result)
    test_best = best[best["listing_id"].isin(result.test_listing_ids)]
    y_true = test_best["label"].to_numpy()
    y_pred = (test_best["probability"].to_numpy() >= threshold).astype(int)
    return {
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "n_test_listings": int(len(test_best)),
    }
