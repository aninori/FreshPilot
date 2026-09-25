"""CAT-3: turn a (listing, candidate product) pair into the feature vector CAT-4
trains on: token-set ratio, TF-IDF cosine (embedding substitute), unit
compatibility, pack ratio, price ratio.
"""

from __future__ import annotations

import re

import pandas as pd
from rapidfuzz import fuzz

from freshpilot.catalog.units import normalize_unit, parse_pack

FEATURE_COLUMNS = [
    "token_set_ratio",
    "tfidf_cosine",
    "internal_unit_ok",
    "unit_matches_product",
    "pack_ratio",
    "price_ratio",
    "name_size_match",
    "weakest_ratio",
]


def _ratio(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        return 0.0
    return min(a, b) / max(a, b)


# Catches size mentions embedded in free-text names ("6 oz clamshell", "half
# gallon", "1/4 lb") that raw_pack/raw_unit never see — needed to tell same-family,
# different-size products apart (e.g. "Yellow bell peppers, 6 oz" vs "..., 12 oz"),
# which is exactly the kind of confusion BRD's hard_negative pairs are about.
_FRACTIONS = {"1/4": "0.25", "1/2": "0.5", "3/4": "0.75"}
_WORD_NUM = {"half": 0.5, "one": 1.0, "two": 2.0, "three": 3.0}
_UNIT_TO_OZ = {"oz": 1.0, "lb": 16.0, "lbs": 16.0, "gal": 128.0, "gallon": 128.0, "qt": 32.0, "quart": 32.0}
_SIZE_RE = re.compile(r"([\d.]+|half|one|two|three)\s*(oz|lbs|lb|gallons?|gal|quarts?|qt)\b", re.IGNORECASE)


def _extract_size_oz(text: str) -> float | None:
    for frac, dec in _FRACTIONS.items():
        text = text.replace(frac, dec)
    match = _SIZE_RE.search(text.lower())
    if not match:
        return None
    raw_value, unit = match.groups()
    value = _WORD_NUM.get(raw_value)
    if value is None:
        try:
            value = float(raw_value)
        except ValueError:
            return None
    return value * _UNIT_TO_OZ[unit.rstrip("s") if unit.rstrip("s") in _UNIT_TO_OZ else unit]


def _name_size_match(listing_name: str, product_name: str) -> float:
    listing_size = _extract_size_oz(listing_name)
    product_size = _extract_size_oz(product_name)
    if listing_size is None or product_size is None:
        return 0.5  # no size mentioned in one or both names: no evidence either way
    return _ratio(listing_size, product_size)


_NEW_FEATURE_COLUMNS = [c for c in FEATURE_COLUMNS if c != "tfidf_cosine"]


def compute_features(candidates: pd.DataFrame, listings: pd.DataFrame, products: pd.DataFrame) -> pd.DataFrame:
    """`candidates` has columns [listing_id, product_id, tfidf_cosine] (from blocking).

    Returns `candidates` with the remaining CAT-3 feature columns appended
    (tfidf_cosine is already a column on `candidates` and is left as-is).
    """
    listings_by_id = listings.set_index("listing_id")
    products_by_id = products.set_index("product_id")

    rows = []
    for cand in candidates.itertuples(index=False):
        listing = listings_by_id.loc[cand.listing_id]
        product = products_by_id.loc[cand.product_id]

        token_set_ratio = fuzz.token_set_ratio(str(listing["raw_name"]), str(product["name"])) / 100.0

        parsed = parse_pack(str(listing["raw_unit"]), str(listing["raw_pack"]))
        product_unit = normalize_unit(str(product["sell_unit"]))
        unit_matches_product = 1.0 if (parsed.canonical_unit is None or parsed.canonical_unit == product_unit) else 0.0

        pack_ratio = _ratio(float(parsed.case_count), float(product["case_pack"])) if parsed.case_count else 0.5

        listing_price = float(listing["price"])
        product_case_price = float(product["unit_cost"]) * float(product["case_pack"])
        price_ratio = _ratio(listing_price, product_case_price)

        name_size_match = _name_size_match(str(listing["raw_name"]), str(product["name"]))
        # A single bad ratio (e.g. right family, wrong pack size) should sink the
        # pair even if every other signal looks fine — logistic regression can't
        # learn this AND-like behavior from the ratios alone without help.
        weakest_ratio = min(pack_ratio, price_ratio, name_size_match)

        rows.append(
            {
                "token_set_ratio": token_set_ratio,
                "internal_unit_ok": 1.0 if parsed.unit_compatible else 0.0,
                "unit_matches_product": unit_matches_product,
                "pack_ratio": pack_ratio,
                "price_ratio": price_ratio,
                "name_size_match": name_size_match,
                "weakest_ratio": weakest_ratio,
            }
        )

    features = pd.DataFrame(rows, columns=_NEW_FEATURE_COLUMNS)
    return pd.concat([candidates.reset_index(drop=True), features], axis=1)
