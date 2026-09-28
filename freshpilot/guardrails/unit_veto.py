"""GRD-4: unit-compatibility veto on catalog merges — reuses CAT-1's parser (M2),
since "does the unit make sense" is exactly what it already checks.
"""

from __future__ import annotations

from freshpilot.catalog.units import normalize_unit, parse_pack


def veto_merge_if_unit_incompatible(raw_unit: str, raw_pack: str, product_unit: str | None = None) -> bool:
    """True means the merge must be blocked."""
    parsed = parse_pack(raw_unit, raw_pack)
    return not parsed.unit_compatible or (
        product_unit is not None and (
            parsed.canonical_unit is None or normalize_unit(product_unit) is None
            or parsed.canonical_unit != normalize_unit(product_unit)
        )
    )
