"""CAT-1: parse raw supplier unit/pack text into a canonical quantity, using
regex to pull out the numbers and pint to convert whatever weight unit shows up.

Worked examples from BRD §9.1: "CS/12 5LB" -> 60 lb (case_pack x per-unit weight);
"12CT" -> 12 ea.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import pint

_UREG = pint.UnitRegistry()

CanonicalUnit = Literal["lb", "ea"]

_CASE_WEIGHT_RE = re.compile(r"CS/(\d+)\s+([\d.]+)\s*(LB|OZ|KG)", re.IGNORECASE)
_WEIGHT_ONLY_RE = re.compile(r"^\s*([\d.]+)\s*(LB|OZ|KG)\s*$", re.IGNORECASE)
_COUNT_RE = re.compile(r"(\d+)\s*(CT|EA)\b", re.IGNORECASE)
_CASE_ONLY_RE = re.compile(r"CS/(\d+)\s*$")


def normalize_unit(raw_unit: str) -> CanonicalUnit | None:
    u = raw_unit.strip().lower()
    if u == "lb":
        return "lb"
    if u in ("ea", "ct"):
        return "ea"
    return None


def _to_lb(value: float, unit_token: str) -> float:
    return float(_UREG.Quantity(value, unit_token.lower()).to("lb").magnitude)


@dataclass(frozen=True)
class ParsedPack:
    total_qty: float | None  # best-known total quantity, in `canonical_unit`
    canonical_unit: CanonicalUnit | None
    case_count: int | None  # units per case, independent of per-unit size — comparable to products.case_pack
    unit_compatible: bool  # False when raw_unit contradicts what raw_pack's own format implies


def parse_pack(raw_unit: str, raw_pack: str) -> ParsedPack:
    stated_unit = normalize_unit(raw_unit)

    weight_match = _CASE_WEIGHT_RE.search(raw_pack) or _WEIGHT_ONLY_RE.match(raw_pack)
    count_match = None if weight_match else _COUNT_RE.search(raw_pack)
    case_only_match = None if (weight_match or count_match) else _CASE_ONLY_RE.search(raw_pack)

    implied_unit: CanonicalUnit | None = "lb" if weight_match else ("ea" if count_match else None)
    unit_compatible = implied_unit is None or stated_unit is None or implied_unit == stated_unit
    canonical_unit = stated_unit or implied_unit

    total_qty: float | None
    case_count: int | None
    if weight_match:
        groups = weight_match.groups()
        per_unit_lb = _to_lb(float(groups[-2]), groups[-1])
        has_case_prefix = len(groups) == 3
        case_count = int(groups[0]) if has_case_prefix else None
        total_qty = (case_count or 1) * per_unit_lb
    elif count_match:
        case_count = int(count_match.group(1))
        total_qty = float(case_count)
    elif case_only_match:
        # Case count known, but no per-unit size stated: weakest signal we have.
        case_count = int(case_only_match.group(1))
        total_qty = float(case_count)
    else:
        total_qty = None
        case_count = None

    return ParsedPack(
        total_qty=total_qty, canonical_unit=canonical_unit, case_count=case_count, unit_compatible=unit_compatible
    )
