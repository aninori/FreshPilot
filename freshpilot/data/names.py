"""Product/listing text generation: department catalogs, messy supplier text, hard negatives."""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

from freshpilot.data.answer_key import AnswerRow

DEPARTMENTS: list[tuple[str, str]] = [
    ("produce", "Produce"),
    ("meat_seafood", "Meat & Seafood"),
    ("bakery", "Bakery"),
    ("deli", "Deli"),
    ("dairy_eggs", "Dairy & Eggs"),
]

# Target product counts, BRD §8.1.
DEPT_PRODUCT_TARGET: dict[str, int] = {
    "produce": 410,
    "meat_seafood": 185,
    "bakery": 95,
    "deli": 240,
    "dairy_eggs": 350,
}


@dataclass(frozen=True)
class Family:
    name: str
    category: str
    sell_unit: str  # lb, ea, ct, oz
    shelf_life_days: tuple[int, int]  # min, max
    unit_cost: tuple[float, float]  # min, max
    is_random_weight: bool = False


_PRODUCE_BERRY = "berries"
_PRODUCE_SALAD = "salad_greens"
_PRODUCE_OTHER = "produce_other"

PRODUCE_FAMILIES: list[Family] = [
    Family("Strawberries", _PRODUCE_BERRY, "lb", (4, 7), (1.2, 2.0), True),
    Family("Blueberries", _PRODUCE_BERRY, "lb", (7, 10), (2.5, 4.0), True),
    Family("Raspberries", _PRODUCE_BERRY, "lb", (3, 5), (3.0, 5.0), True),
    Family("Blackberries", _PRODUCE_BERRY, "lb", (3, 5), (2.8, 4.5), True),
    Family("Bananas", _PRODUCE_OTHER, "lb", (5, 8), (0.4, 0.7), True),
    Family("Gala apples", _PRODUCE_OTHER, "lb", (14, 21), (1.0, 1.6), True),
    Family("Fuji apples", _PRODUCE_OTHER, "lb", (14, 21), (1.1, 1.7), True),
    Family("Honeycrisp apples", _PRODUCE_OTHER, "lb", (14, 21), (1.6, 2.4), True),
    Family("Navel oranges", _PRODUCE_OTHER, "lb", (14, 21), (0.9, 1.4), True),
    Family("Red grapes", _PRODUCE_OTHER, "lb", (10, 14), (1.8, 2.6), True),
    Family("Green grapes", _PRODUCE_OTHER, "lb", (10, 14), (1.8, 2.6), True),
    Family("Avocados", _PRODUCE_OTHER, "ea", (5, 8), (0.7, 1.3), False),
    Family("Lemons", _PRODUCE_OTHER, "lb", (21, 28), (1.0, 1.5), True),
    Family("Limes", _PRODUCE_OTHER, "lb", (21, 28), (1.0, 1.6), True),
    Family("Romaine hearts", _PRODUCE_SALAD, "ea", (10, 14), (1.5, 2.2), False),
    Family("Iceberg lettuce", _PRODUCE_SALAD, "ea", (10, 14), (0.8, 1.3), False),
    Family("Spring mix", _PRODUCE_SALAD, "lb", (7, 10), (3.5, 5.0), False),
    Family("Baby spinach", _PRODUCE_SALAD, "lb", (7, 10), (2.8, 4.0), False),
    Family("Kale", _PRODUCE_SALAD, "lb", (10, 14), (1.8, 2.6), False),
    Family("Broccoli crowns", _PRODUCE_OTHER, "lb", (10, 14), (1.4, 2.0), True),
    Family("Cauliflower", _PRODUCE_OTHER, "ea", (10, 14), (1.8, 2.6), False),
    Family("Carrots", _PRODUCE_OTHER, "lb", (21, 28), (0.6, 1.0), True),
    Family("Celery", _PRODUCE_OTHER, "ea", (14, 21), (1.2, 1.8), False),
    Family("Red bell peppers", _PRODUCE_OTHER, "lb", (10, 14), (1.8, 2.6), True),
    Family("Green bell peppers", _PRODUCE_OTHER, "lb", (10, 14), (1.3, 1.9), True),
    Family("Yellow bell peppers", _PRODUCE_OTHER, "lb", (10, 14), (1.9, 2.7), True),
    Family("Cucumbers", _PRODUCE_OTHER, "ea", (10, 14), (0.6, 1.0), False),
    Family("Roma tomatoes", _PRODUCE_OTHER, "lb", (7, 10), (1.4, 2.0), True),
    Family("Vine tomatoes", _PRODUCE_OTHER, "lb", (7, 10), (1.8, 2.5), True),
    Family("Cherry tomatoes", _PRODUCE_OTHER, "lb", (7, 10), (2.5, 3.5), True),
    Family("Yellow onions", _PRODUCE_OTHER, "lb", (28, 35), (0.6, 1.0), True),
    Family("Red onions", _PRODUCE_OTHER, "lb", (28, 35), (0.8, 1.2), True),
    Family("Russet potatoes", _PRODUCE_OTHER, "lb", (28, 35), (0.5, 0.9), True),
    Family("Red potatoes", _PRODUCE_OTHER, "lb", (21, 28), (0.7, 1.1), True),
    Family("Sweet potatoes", _PRODUCE_OTHER, "lb", (21, 28), (0.8, 1.3), True),
    Family("Garlic", _PRODUCE_OTHER, "lb", (35, 45), (2.5, 3.5), True),
    Family("Ginger root", _PRODUCE_OTHER, "lb", (21, 28), (2.5, 3.8), True),
    Family("Cilantro", _PRODUCE_OTHER, "ea", (5, 7), (0.6, 1.0), False),
    Family("Parsley", _PRODUCE_OTHER, "ea", (7, 10), (0.6, 1.0), False),
    Family("Green beans", _PRODUCE_OTHER, "lb", (7, 10), (1.6, 2.2), True),
    Family("Zucchini", _PRODUCE_OTHER, "lb", (7, 10), (1.2, 1.8), True),
    Family("Yellow squash", _PRODUCE_OTHER, "lb", (7, 10), (1.2, 1.8), True),
    Family("White mushrooms", _PRODUCE_OTHER, "lb", (7, 10), (2.2, 3.2), True),
    Family("Sweet corn", _PRODUCE_OTHER, "ea", (5, 7), (0.4, 0.7), False),
    Family("Pineapple", _PRODUCE_OTHER, "ea", (5, 7), (2.0, 3.0), False),
    Family("Mango", _PRODUCE_OTHER, "ea", (5, 7), (0.9, 1.4), False),
    Family("Watermelon", _PRODUCE_OTHER, "ea", (10, 14), (4.5, 6.5), False),
    Family("Cantaloupe", _PRODUCE_OTHER, "ea", (7, 10), (2.5, 3.5), False),
]
PRODUCE_VARIANTS = [
    "1 lb", "2 lb bag", "3 lb bag", "bunch", "each",
    "6 oz clamshell", "12 oz clamshell", "family pack", "organic", "store brand",
]

_MEAT_POULTRY = "meat_poultry"
_MEAT_RED = "meat_beef_pork"
_MEAT_SEAFOOD = "seafood"

MEAT_FAMILIES: list[Family] = [
    Family("Whole chicken, fryer", _MEAT_POULTRY, "lb", (5, 8), (1.2, 1.8)),
    Family("Chicken thighs, bone-in", _MEAT_POULTRY, "lb", (5, 8), (1.6, 2.2)),
    Family("Chicken breasts, boneless", _MEAT_POULTRY, "lb", (5, 8), (2.8, 3.8)),
    Family("Chicken wings", _MEAT_POULTRY, "lb", (5, 8), (2.2, 3.2)),
    Family("Chicken drumsticks", _MEAT_POULTRY, "lb", (5, 8), (1.4, 2.0)),
    Family("Chicken tenders", _MEAT_POULTRY, "lb", (5, 8), (3.2, 4.2)),
    Family("Ground chicken", _MEAT_POULTRY, "lb", (4, 6), (2.6, 3.4)),
    Family("Ground beef 80/20", _MEAT_RED, "lb", (4, 6), (3.6, 4.6)),
    Family("Ground beef 90/10", _MEAT_RED, "lb", (4, 6), (4.4, 5.6)),
    Family("Ribeye steak", _MEAT_RED, "lb", (5, 8), (9.0, 13.0)),
    Family("Sirloin steak", _MEAT_RED, "lb", (5, 8), (6.5, 9.5)),
    Family("NY strip steak", _MEAT_RED, "lb", (5, 8), (8.5, 12.0)),
    Family("Beef roast", _MEAT_RED, "lb", (6, 9), (4.5, 6.5)),
    Family("Beef stew meat", _MEAT_RED, "lb", (5, 7), (4.8, 6.2)),
    Family("Pork chops", _MEAT_RED, "lb", (5, 8), (2.8, 3.8)),
    Family("Pork tenderloin", _MEAT_RED, "lb", (5, 8), (3.6, 4.8)),
    Family("Baby back ribs", _MEAT_RED, "lb", (5, 8), (4.2, 5.8)),
    Family("Ground pork", _MEAT_RED, "lb", (4, 6), (2.8, 3.6)),
    Family("Bacon", _MEAT_RED, "lb", (14, 21), (5.5, 7.5)),
    Family("Ground turkey", _MEAT_POULTRY, "lb", (4, 6), (2.6, 3.4)),
    Family("Turkey breast", _MEAT_POULTRY, "lb", (5, 8), (3.4, 4.6)),
    Family("Whole turkey", _MEAT_POULTRY, "lb", (5, 8), (1.1, 1.7)),
    Family("Salmon fillet", _MEAT_SEAFOOD, "lb", (2, 4), (8.5, 12.0)),
    Family("Tilapia fillet", _MEAT_SEAFOOD, "lb", (2, 4), (5.5, 7.5)),
    Family("Cod fillet", _MEAT_SEAFOOD, "lb", (2, 4), (7.5, 10.0)),
    Family("Shrimp, raw", _MEAT_SEAFOOD, "lb", (3, 5), (7.0, 10.0)),
    Family("Shrimp, cooked", _MEAT_SEAFOOD, "lb", (3, 5), (7.5, 10.5)),
    Family("Crab legs", _MEAT_SEAFOOD, "lb", (2, 4), (11.0, 15.0)),
    Family("Lamb chops", _MEAT_RED, "lb", (4, 6), (9.5, 13.5)),
    Family("Italian sausage", _MEAT_RED, "lb", (7, 10), (3.6, 4.8)),
    Family("Breakfast sausage", _MEAT_RED, "lb", (7, 10), (3.2, 4.2)),
    Family("Hot dogs", _MEAT_RED, "lb", (14, 21), (2.6, 3.6)),
]
MEAT_VARIANTS = [
    "1 lb pack", "2 lb pack", "family pack", "value pack",
    "12 oz pack", "16 oz pack", "organic", "store brand",
]

_BAKERY_BREAD = "bread"
_BAKERY_OTHER = "bakery_other"

BAKERY_FAMILIES: list[Family] = [
    Family("White bread", _BAKERY_BREAD, "ea", (5, 7), (1.8, 2.6)),
    Family("Wheat bread", _BAKERY_BREAD, "ea", (5, 7), (2.0, 2.8)),
    Family("Sourdough bread", _BAKERY_BREAD, "ea", (3, 5), (3.2, 4.2)),
    Family("Rye bread", _BAKERY_BREAD, "ea", (4, 6), (2.8, 3.6)),
    Family("Multigrain bread", _BAKERY_BREAD, "ea", (4, 6), (2.6, 3.4)),
    Family("Bagels, plain", _BAKERY_OTHER, "ct", (5, 7), (2.6, 3.4)),
    Family("Bagels, everything", _BAKERY_OTHER, "ct", (5, 7), (2.8, 3.6)),
    Family("Dinner rolls", _BAKERY_OTHER, "ct", (3, 5), (2.2, 3.0)),
    Family("Sandwich rolls", _BAKERY_OTHER, "ct", (3, 5), (2.4, 3.2)),
    Family("Hamburger buns", _BAKERY_OTHER, "ct", (5, 7), (1.8, 2.6)),
    Family("Hot dog buns", _BAKERY_OTHER, "ct", (5, 7), (1.8, 2.6)),
    Family("Blueberry muffins", _BAKERY_OTHER, "ct", (3, 5), (3.4, 4.4)),
    Family("Chocolate chip muffins", _BAKERY_OTHER, "ct", (3, 5), (3.4, 4.4)),
    Family("Bran muffins", _BAKERY_OTHER, "ct", (3, 5), (3.2, 4.0)),
    Family("Chocolate chip cookies", _BAKERY_OTHER, "ct", (5, 7), (4.0, 5.2)),
    Family("Sugar cookies", _BAKERY_OTHER, "ct", (5, 7), (3.6, 4.6)),
    Family("Oatmeal cookies", _BAKERY_OTHER, "ct", (5, 7), (3.8, 4.8)),
    Family("Brownies", _BAKERY_OTHER, "ct", (4, 6), (4.4, 5.6)),
    Family("Birthday cake", _BAKERY_OTHER, "ea", (3, 5), (9.0, 14.0)),
    Family("Apple pie", _BAKERY_OTHER, "ea", (3, 5), (6.5, 8.5)),
    Family("Pumpkin pie", _BAKERY_OTHER, "ea", (3, 5), (6.5, 8.5)),
    Family("Croissants", _BAKERY_OTHER, "ct", (3, 5), (3.6, 4.6)),
    Family("Donuts", _BAKERY_OTHER, "ct", (2, 3), (3.6, 4.8)),
    Family("Cinnamon rolls", _BAKERY_OTHER, "ct", (3, 5), (3.8, 5.0)),
    Family("Baguette", _BAKERY_BREAD, "ea", (2, 3), (2.4, 3.2)),
]
BAKERY_VARIANTS = ["large", "mini", "6 ct", "12 ct", "4 ct", "2 ct", "seeded", "frosted", "store brand"]

_DELI_MEAT_CHEESE = "deli_meat_cheese"
_DELI_PREPARED = "deli_prepared"

DELI_FAMILIES: list[Family] = [
    Family("Sliced ham", _DELI_MEAT_CHEESE, "lb", (7, 10), (5.5, 7.5)),
    Family("Sliced turkey", _DELI_MEAT_CHEESE, "lb", (7, 10), (6.0, 8.0)),
    Family("Sliced roast beef", _DELI_MEAT_CHEESE, "lb", (7, 10), (7.5, 9.5)),
    Family("Salami", _DELI_MEAT_CHEESE, "lb", (14, 21), (6.5, 8.5)),
    Family("Pepperoni", _DELI_MEAT_CHEESE, "lb", (14, 21), (6.0, 8.0)),
    Family("Bologna", _DELI_MEAT_CHEESE, "lb", (10, 14), (3.5, 4.5)),
    Family("Cheddar cheese, sliced", _DELI_MEAT_CHEESE, "lb", (14, 21), (5.0, 6.5)),
    Family("Swiss cheese, sliced", _DELI_MEAT_CHEESE, "lb", (14, 21), (5.5, 7.0)),
    Family("Provolone cheese, sliced", _DELI_MEAT_CHEESE, "lb", (14, 21), (5.5, 7.0)),
    Family("Pepper jack cheese, sliced", _DELI_MEAT_CHEESE, "lb", (14, 21), (5.5, 7.0)),
    Family("Mozzarella cheese, sliced", _DELI_MEAT_CHEESE, "lb", (14, 21), (5.0, 6.5)),
    Family("American cheese, sliced", _DELI_MEAT_CHEESE, "lb", (14, 21), (4.5, 6.0)),
    Family("Potato salad", _DELI_PREPARED, "lb", (5, 7), (3.0, 4.0)),
    Family("Coleslaw", _DELI_PREPARED, "lb", (5, 7), (2.8, 3.8)),
    Family("Macaroni salad", _DELI_PREPARED, "lb", (5, 7), (2.8, 3.8)),
    Family("Chicken salad", _DELI_PREPARED, "lb", (4, 6), (5.0, 6.5)),
    Family("Hummus", _DELI_PREPARED, "ea", (14, 21), (3.5, 4.5)),
    Family("Olives", _DELI_PREPARED, "lb", (21, 28), (5.5, 7.0)),
    Family("Prepared sandwich", _DELI_PREPARED, "ea", (2, 3), (5.5, 7.5)),
    Family("Rotisserie chicken", _DELI_PREPARED, "ea", (3, 4), (6.5, 8.5)),
    Family("Chicken noodle soup", _DELI_PREPARED, "ea", (5, 7), (4.5, 5.5)),
    Family("Tomato soup", _DELI_PREPARED, "ea", (5, 7), (4.0, 5.0)),
    Family("Deli pizza", _DELI_PREPARED, "ea", (3, 4), (6.5, 8.5)),
    Family("Spinach dip", _DELI_PREPARED, "lb", (7, 10), (4.5, 5.5)),
    Family("Guacamole", _DELI_PREPARED, "lb", (5, 7), (5.5, 7.0)),
]
DELI_VARIANTS = [
    "1/4 lb", "1/2 lb", "1 lb", "8 oz tub", "12 oz tub",
    "16 oz tub", "per lb", "store brand", "large", "small",
]

_DAIRY_MILK = "milk"
_DAIRY_EGGS = "eggs"
_DAIRY_OTHER = "dairy_other"

DAIRY_FAMILIES: list[Family] = [
    Family("Whole milk", _DAIRY_MILK, "ea", (10, 14), (2.8, 3.8)),
    Family("2% milk", _DAIRY_MILK, "ea", (10, 14), (2.8, 3.8)),
    Family("1% milk", _DAIRY_MILK, "ea", (10, 14), (2.7, 3.7)),
    Family("Skim milk", _DAIRY_MILK, "ea", (10, 14), (2.7, 3.7)),
    Family("Lactose-free milk", _DAIRY_MILK, "ea", (14, 21), (3.8, 4.8)),
    Family("Chocolate milk", _DAIRY_MILK, "ea", (10, 14), (3.0, 4.0)),
    Family("Almond milk", _DAIRY_MILK, "ea", (21, 28), (3.2, 4.2)),
    Family("Soy milk", _DAIRY_MILK, "ea", (21, 28), (3.0, 4.0)),
    Family("Oat milk", _DAIRY_MILK, "ea", (21, 28), (3.6, 4.6)),
    Family("Large eggs", _DAIRY_EGGS, "ct", (21, 28), (2.6, 3.8)),
    Family("Extra-large eggs", _DAIRY_EGGS, "ct", (21, 28), (2.8, 4.0)),
    Family("Jumbo eggs", _DAIRY_EGGS, "ct", (21, 28), (3.0, 4.2)),
    Family("Brown eggs", _DAIRY_EGGS, "ct", (21, 28), (3.2, 4.4)),
    Family("Organic eggs", _DAIRY_EGGS, "ct", (21, 28), (4.5, 6.0)),
    Family("Cage-free eggs", _DAIRY_EGGS, "ct", (21, 28), (3.8, 5.0)),
    Family("Salted butter", _DAIRY_OTHER, "ea", (60, 90), (3.5, 4.8)),
    Family("Unsalted butter", _DAIRY_OTHER, "ea", (60, 90), (3.5, 4.8)),
    Family("Shredded cheddar", _DAIRY_OTHER, "ea", (21, 28), (3.5, 4.8)),
    Family("Shredded mozzarella", _DAIRY_OTHER, "ea", (21, 28), (3.3, 4.5)),
    Family("Shredded colby jack", _DAIRY_OTHER, "ea", (21, 28), (3.5, 4.7)),
    Family("Block parmesan", _DAIRY_OTHER, "ea", (45, 60), (5.5, 7.5)),
    Family("Greek yogurt, plain", _DAIRY_OTHER, "ea", (21, 28), (0.9, 1.3)),
    Family("Greek yogurt, vanilla", _DAIRY_OTHER, "ea", (21, 28), (0.9, 1.3)),
    Family("Greek yogurt, strawberry", _DAIRY_OTHER, "ea", (21, 28), (0.9, 1.3)),
    Family("Yogurt, blueberry", _DAIRY_OTHER, "ea", (21, 28), (0.7, 1.0)),
    Family("Cream cheese", _DAIRY_OTHER, "ea", (30, 45), (2.2, 3.0)),
    Family("Sour cream", _DAIRY_OTHER, "ea", (21, 28), (1.8, 2.6)),
    Family("Cottage cheese", _DAIRY_OTHER, "ea", (14, 21), (2.6, 3.6)),
    Family("Half and half", _DAIRY_OTHER, "ea", (14, 21), (2.4, 3.2)),
    Family("Heavy cream", _DAIRY_OTHER, "ea", (14, 21), (3.0, 4.0)),
    Family("Coffee creamer", _DAIRY_OTHER, "ea", (30, 45), (2.8, 3.8)),
    Family("Margarine", _DAIRY_OTHER, "ea", (60, 90), (2.4, 3.2)),
    Family("Whipped cream", _DAIRY_OTHER, "ea", (30, 45), (2.6, 3.4)),
]
DAIRY_VARIANTS = [
    "quart", "half gallon", "gallon", "16 oz", "32 oz",
    "12 ct", "18 ct", "dozen", "store brand", "lite", "organic",
]

DEPT_FAMILIES: dict[str, list[Family]] = {
    "produce": PRODUCE_FAMILIES,
    "meat_seafood": MEAT_FAMILIES,
    "bakery": BAKERY_FAMILIES,
    "deli": DELI_FAMILIES,
    "dairy_eggs": DAIRY_FAMILIES,
}
DEPT_VARIANTS: dict[str, list[str]] = {
    "produce": PRODUCE_VARIANTS,
    "meat_seafood": MEAT_VARIANTS,
    "bakery": BAKERY_VARIANTS,
    "deli": DELI_VARIANTS,
    "dairy_eggs": DAIRY_VARIANTS,
}

@dataclass(frozen=True)
class SupplierSpec:
    supplier_id: str
    name: str
    depts: list[str]


# Every department needs >=3 suppliers (some products get up to 3 listings):
# one specialist each, plus two broad-line distributors covering everything.
SUPPLIERS: list[SupplierSpec] = [
    SupplierSpec("SUP-1", "Golden Valley Produce Co.", ["produce"]),
    SupplierSpec("SUP-2", "Riverside Meat & Seafood Distributors", ["meat_seafood"]),
    SupplierSpec("SUP-3", "Sunrise Bakery & Deli Supply", ["bakery", "deli"]),
    SupplierSpec("SUP-4", "Heritage Dairy Cooperative", ["dairy_eggs"]),
    SupplierSpec(
        "SUP-5", "Cross-Country Foods", ["produce", "meat_seafood", "bakery", "deli", "dairy_eggs"]
    ),
    SupplierSpec(
        "SUP-6", "Coastal Wholesale Partners", ["produce", "meat_seafood", "bakery", "deli", "dairy_eggs"]
    ),
]

_ABBREV = {
    "organic": "ORG", "store": "STR", "brand": "BR", "boneless": "BNLS",
    "bone-in": "BN-IN", "sliced": "SLCD", "breast": "BRST", "breasts": "BRSTS",
    "chicken": "CHKN", "ground": "GRND", "family": "FAM", "value": "VAL",
    "pack": "PK", "clamshell": "CLAM", "everything": "EVRY", "plain": "PLN",
    "chocolate": "CHOC", "strawberry": "STRWB", "blueberry": "BLUBRY",
}

_STOPWORDS = {"and", "the", "of", "a", "in"}


def _abbreviate_tokens(name: str, rng: np.random.Generator) -> str:
    tokens = re.split(r"[\s,]+", name.strip())
    out = []
    for tok in tokens:
        low = tok.lower()
        if low in _ABBREV and rng.random() < 0.6:
            out.append(_ABBREV[low])
        else:
            out.append(tok)
    return " ".join(out)


def messify_listing(
    name: str, sell_unit: str, case_pack: int, unit_cost: float, rng: np.random.Generator
) -> tuple[str, str, str, float]:
    """Turn a clean product name/sell_unit/case_pack into vendor-style raw text.

    Returns (raw_name, raw_unit, raw_pack, price). Follows the BRD §9.1 CAT-1
    examples ("CS/12 5LB" -> 60 lb, "12CT" -> 12 ea) so the M2 parser has a
    consistent, learnable set of formats.
    """
    raw_name = _abbreviate_tokens(name, rng)
    if rng.random() < 0.35:
        raw_name = raw_name.upper()
    elif rng.random() < 0.5:
        raw_name = raw_name.lower()
    if rng.random() < 0.2:
        raw_name = f"{raw_name} #{rng.integers(1000, 9999)}"

    raw_unit: str
    raw_pack: str
    if sell_unit == "lb":
        # Per-unit weight, not case total: BRD's own CAT-1 example is
        # "CS/12 5LB" -> 60 lb, i.e. case_pack (12) x per-unit weight (5).
        per_unit_lb = round(rng.uniform(0.85, 1.2), 2)
        raw_unit = str(rng.choice(["LB", "lb", "Lb"]))
        raw_pack = str(
            rng.choice([f"CS/{case_pack} {per_unit_lb}LB", f"{per_unit_lb} LB", f"CS/{case_pack}"])
        )
    elif sell_unit in ("ct", "ea"):
        raw_unit = str(rng.choice(["EA", "ea", "CT", "ct"]))
        raw_pack = str(rng.choice([f"{case_pack}CT", f"{case_pack} EA", f"CS/{case_pack}"]))
    else:  # oz
        raw_unit = str(rng.choice(["OZ", "oz"]))
        raw_pack = f"CS/{case_pack} {sell_unit.upper()}"

    price = round(unit_cost * case_pack * rng.uniform(0.95, 1.08), 2)
    return raw_name, raw_unit, raw_pack, price


def clean_listing(
    name: str, sell_unit: str, case_pack: int, unit_cost: float, rng: np.random.Generator
) -> tuple[str, str, str, float]:
    """A consistently-formatted listing — for the 'easy' multi-source products that
    should be trivial, high-confidence auto-merges rather than planted hard cases."""
    raw_name = name.replace(",", "")
    if sell_unit == "lb":
        raw_unit, raw_pack = "LB", f"CS/{case_pack}"
    elif sell_unit in ("ct", "ea"):
        raw_unit, raw_pack = "EA", f"{case_pack}CT"
    else:
        raw_unit, raw_pack = "OZ", f"CS/{case_pack}"
    price = round(unit_cost * case_pack * rng.uniform(0.98, 1.03), 2)
    return raw_name, raw_unit, raw_pack, price


def build_products(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    seq = 1000
    for dept_id, _dept_name in DEPARTMENTS:
        families = DEPT_FAMILIES[dept_id]
        variants = DEPT_VARIANTS[dept_id]
        combos = [(f, v) for f in families for v in variants]
        order = rng.permutation(len(combos))
        target = DEPT_PRODUCT_TARGET[dept_id]
        chosen = [combos[i] for i in order[:target]]
        for family, variant in chosen:
            product_id = f"PRD-{seq:04d}"
            seq += 1
            name = f"{family.name}, {variant}" if "," not in family.name else f"{family.name} ({variant})"
            unit_cost = round(rng.uniform(*family.unit_cost), 2)
            markup = rng.uniform(1.35, 1.75)
            case_pack = int(rng.choice([6, 8, 10, 12, 16, 24]))
            rows.append(
                {
                    "product_id": product_id,
                    "dept_id": dept_id,
                    "category": family.category,
                    "name": name,
                    "sell_unit": family.sell_unit,
                    "case_pack": case_pack,
                    "shelf_life_days": int(rng.integers(family.shelf_life_days[0], family.shelf_life_days[1] + 1)),
                    "unit_cost": unit_cost,
                    "unit_price": round(unit_cost * markup, 2),
                    "salvage_value": round(unit_cost * rng.uniform(0.1, 0.3), 2),
                    "is_random_weight": bool(family.is_random_weight),
                    "min_order_cases": 0,
                    "max_order_cases": int(rng.integers(20, 60)),
                }
            )
    frame = pd.DataFrame(rows)
    # Synthetic storage policy: one maximum replenishment fits on the shelf.
    # Separate column so managers can configure a tighter physical limit.
    frame["max_shelf_capacity"] = frame["max_order_cases"] * frame["case_pack"]
    return frame


_SUPPLIER_POOL_BY_DEPT: dict[str, list[str]] = {
    dept_id: [s.supplier_id for s in SUPPLIERS if dept_id in s.depts] for dept_id, _ in DEPARTMENTS
}


def build_supplier_listings(
    products: pd.DataFrame, rng: np.random.Generator
) -> tuple[pd.DataFrame, list[AnswerRow]]:
    """Generate supplier listings and the full listing->product ground truth.

    Every product gets >=1 listing. ~450 products (the BRD §8.4 "duplicate_listing"
    figure) get 2-3 deliberately messy, divergently-formatted listings from
    different suppliers — the hard catalog-matching cases. Another chunk gets 2
    consistently-formatted listings (easy, non-planted duplicates). The rest get
    a single listing. The full mapping (hard and easy alike) is returned as
    answer_key rows so M2 has ground truth for its own train/test split, with a
    `planted_hard` flag marking the ~450 messy cases specifically.
    """
    n = len(products)
    ids = products["product_id"].to_numpy()
    order = rng.permutation(n)
    n_messy = round(n * 450 / 1280)
    n_easy = round(n * 385 / 1280)
    messy_ids = set(ids[order[:n_messy]])
    easy_ids = set(ids[order[n_messy : n_messy + n_easy]])

    listings: list[dict[str, object]] = []
    answer_rows: list[AnswerRow] = []
    listing_seq = 1
    products_by_id = products.set_index("product_id")

    for pid in ids:
        row = products_by_id.loc[pid]
        dept_id = str(row["dept_id"])
        pool = _SUPPLIER_POOL_BY_DEPT[dept_id]
        planted_hard = pid in messy_ids
        if planted_hard:
            k = min(int(rng.choice([2, 3])), len(pool))
        elif pid in easy_ids:
            k = min(2, len(pool))
        else:
            k = 1
        chosen_suppliers = rng.choice(pool, size=k, replace=False)

        lids = []
        for supplier_id in chosen_suppliers:
            listing_id = f"LST-{listing_seq:05d}"
            listing_seq += 1
            if planted_hard or k == 1:
                raw_name, raw_unit, raw_pack, price = messify_listing(
                    str(row["name"]), str(row["sell_unit"]), int(row["case_pack"]),
                    float(row["unit_cost"]), rng,
                )
            else:
                raw_name, raw_unit, raw_pack, price = clean_listing(
                    str(row["name"]), str(row["sell_unit"]), int(row["case_pack"]),
                    float(row["unit_cost"]), rng,
                )
            listings.append(
                {
                    "listing_id": listing_id,
                    "supplier_id": supplier_id,
                    "dept_id": dept_id,
                    "raw_name": raw_name,
                    "raw_unit": raw_unit,
                    "raw_pack": raw_pack,
                    "price": price,
                }
            )
            lids.append(listing_id)

        answer_rows.append(
            AnswerRow(
                type="duplicate_listing",
                product_id=str(pid),
                listing_ids=",".join(lids),
                details={"n_listings": len(lids), "planted_hard": planted_hard},
            )
        )

    return pd.DataFrame(listings), answer_rows


def _name_tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[\s,()/]+", name.lower()) if t and t not in _STOPWORDS}


def hard_negative_pairs(products: pd.DataFrame, rng: np.random.Generator, per_dept: int = 8) -> pd.DataFrame:
    """Rank same-department product-name pairs by word overlap; take the top few per department.

    Deliberately includes same-family/different-variant pairs (e.g. "Whole
    milk, quart" vs "Whole milk, gallon") as well as cross-family pairs that
    share words (e.g. "Salted butter" vs "Unsalted butter") — both are
    realistic "similar name, different product" confusions per CAT hard negatives.
    """
    rows = []
    for dept_id, group in products.groupby("dept_id"):
        ids = group["product_id"].tolist()
        tokens = {pid: _name_tokens(name) for pid, name in zip(ids, group["name"], strict=True)}
        scored = []
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = ids[i], ids[j]
                ta, tb = tokens[a], tokens[b]
                if not ta or not tb:
                    continue
                jaccard = len(ta & tb) / len(ta | tb)
                if jaccard > 0:
                    scored.append((jaccard, a, b))
        scored.sort(key=lambda t: (-t[0], t[1], t[2]))
        for _score, a, b in scored[:per_dept]:
            rows.append({"dept_id": dept_id, "product_id_a": a, "product_id_b": b})
    return pd.DataFrame(rows)
