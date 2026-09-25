# FreshPilot — build progress

Tracks milestones from BRD §12. Each milestone starts with a checklist here, gets ticked off as work lands, and ends with a results summary before the next milestone begins.

Open design questions raised along the way (see chat) are logged inline at the milestone where they became concrete, with the resolution taken.

---

## M0 — Repo scaffold, tooling, Makefile, CI skeleton

**Acceptance:** `make test` passes on empty suite; lint clean.

- [x] Directory layout per BRD §11 (`freshpilot/` subpackages, `web/`, `tests/`, `reports/`)
- [x] `pyproject.toml` (uv-managed, Python ≥3.11, ruff + mypy + pytest config)
- [x] `Makefile` with all targets from CLAUDE.md (real: `setup`, `test`; stubs: `data`, `train`, `pipeline`, `eval`, `api`, `web`, `demo`)
- [x] `.gitignore`
- [x] Minimal `README.md` with independence notice
- [x] `.github/workflows/ci.yml` running `make test` in fallback mode
- [x] `freshpilot/config.py` with `SEED` and env accessors (needed from M1 onward per CLAUDE.md rule 6)
- [x] `make test` passes, ruff clean, mypy clean

**Note:** this Windows dev machine has no `make` on PATH (git-bash `/usr/bin` doesn't ship it, no choco/scoop present). CI (ubuntu-latest) has real `make`. Locally in this session I verified with the equivalent `uv run` commands the Makefile's `test` target wraps; documented in README.

**Result:** PASS, 2026-09-24.

```
FRESHPILOT_LLM=off uv run pytest      -> 2 passed in 0.01s
uv run ruff check .                    -> All checks passed!
uv run mypy freshpilot                 -> Success: no issues found in 13 source files
```

`uv sync` bootstrapped CPython 3.13.13 (local system Python was 3.10, project requires ≥3.11 — uv fetched a matching interpreter itself, no manual install needed). Added `[build-system]`/hatchling to `pyproject.toml` so `freshpilot` installs as an editable package (otherwise pytest can't import it — uv treats a project with no build-system as dependency-only). One mypy fix needed: `load_settings()`'s `FRESHPILOT_LLM` parsing used a plain conditional that mypy couldn't narrow to the `Literal["auto","on","off"]` type — fixed with `cast` after validating against `get_args(LlmMode)`.

Committed as `4ec72fb M0: scaffold`.

---

## M1 — Synthetic generator + DuckDB schema + answer key

**Acceptance:** row counts within ±5% of §8.1; answer key has all types in §8.4; seeded reproducibility test.

Design decisions made concrete here (flagged as open questions after M0):

- **`supplier_listings` carries no `product_id`.** Giving the raw listing a product FK would leak the answer catalog reconciliation exists to find. It does carry `dept_id` (realistic — vendor catalogs are department-categorized even when item identity is ambiguous) so `CAT-2`'s department blocking has something to block on. The true listing→product mapping lives only in `answer_key` (type `duplicate_listing`) until M2's matcher reconstructs it into `product_listing_map`, which M1 creates empty.
- **`products` gets a `category` column** (e.g. `berries`, `bread`, `salad_greens`) — not in §8.2's column list, but `FC-2` explicitly names "category" as a forecast feature, and §8.3's weather effect is category-specific, so it needs to exist somewhere.
- **`products` gets `min_order_cases`/`max_order_cases`** (open question #1 from M0) since `ORD-3` clips to "min/max order rules" that don't otherwise exist in the schema.
- **`bake_plans` is populated historically for Bakery**, not left for the pipeline to fill later: Bakery items are same-day shelf life and baked in-store rather than received from suppliers, so the 180-day simulation uses `bake_plans.planned_qty` as Bakery's daily supply instead of `receipts`, and `receipts` covers the other 4 departments.
- **`answer_key` is one generic table** (`type`, `product_id`, `product_id_b`, `listing_ids`, `start_date`, `end_date`, `details` JSON) rather than 7 typed tables — avoids seven near-identical schemas for a POC.
- **Goodwill penalty / disposal cost** (open question #1) stay out of the schema entirely — they become config constants in the M4 engine, not per-product data.
- **Book vs. true on-hand diverge only inside planted `phantom_stock`/`shrink_spike` windows** — that gap is exactly what `INV-1..3` must reconstruct later, so it has to be real in the simulation, not cosmetic.
- **`answer_key.type = "duplicate_listing"` covers every product with ≥1 listing (~1280 rows), not just the hard cases.** M2 needs the *complete* listing→product ground truth to build its own train/test split and measure Match F1 (§8.4's "≈450" isn't gated to ±5% by the M1 acceptance check — only §8.1's scale numbers are). Each row's `details` JSON carries `planted_hard: bool`, true for exactly the ~450 messy multi-supplier cases; the rest are either easy 2-listing duplicates or single-listing products.
- **Suppliers**: 4 department specialists + 2 broad-line distributors (`SUP-5`, `SUP-6` cover all 5 departments), so every department has ≥3 supplier options — needed since some products get 3 listings.
- **Hard negatives are derived, not hand-curated**: after generating all product names, rank same-department pairs by word-overlap (Jaccard on tokens) and take the top 8 per department. Surfaces genuinely confusable pairs (`Salted butter` / `Unsalted butter`, `Ground beef 80/20` / `Ground beef 90/10`) without maintaining a manual list.
- **Fixed anchor date** (`END_DATE = 2025-06-30`, 180 days back) instead of "today minus 180 days" — keeps generation reproducible independent of when it's run (NFR-2).

Checklist:

- [x] DuckDB schema for all tables in §8.2 plus the additions above
- [x] Synthetic generator: stores, departments, products, suppliers, supplier listings (with planted messiness)
- [x] 180 days of sales/receipts/waste/inventory/promotions/weather/calendar, demand process per §8.3 (censored sales, book-vs-true on-hand simulation)
- [x] Planted problems + `answer_key` table: all 7 types in §8.4
- [x] Yields and bake_plans seed data
- [x] `make data` generates `data/freshpilot.duckdb`
- [x] Reproducibility test: same `SEED` → identical output
- [x] Row-count test vs §8.1 (±5%)
- [x] Data summary printed: row counts per table, products per department, answer-key counts per type, 3 example messy supplier listings for one product

**Result:** PASS, 2026-09-24. 11/11 tests pass, ruff clean, mypy clean (`freshpilot/data` added to the base — non-strict — mypy scope; `engine/`+`guardrails/` stay the only `--strict` packages).

Row counts (target from §8.1 in parens, all exact or within ±5%):

| Table | Rows |
|---|---|
| stores | 1 (1) |
| departments | 5 (5) |
| suppliers | 6 (6) |
| products | 1,280 (≈1,280) — produce 410, meat_seafood 185, bakery 95, deli 240, dairy_eggs 350 (exact match to §8.1's breakdown) |
| supplier_listings | 2,340 (≈2,340) |
| sales_daily / inventory_book | 230,400 (1,280 products × 180 days) |
| receipts | 85,350 · bake_plans | 17,100 · waste_log | 230,367 · promotions | 186 |

Answer key — all 7 §8.4 types present: `duplicate_listing` 1,280 (450 flagged `planted_hard: true`, matching §8.4's "≈450 products"), `unit_mixup` 20, `hard_negative` 40, `phantom_stock` 12 (all inside the last-28-day test window), `shrink_spike` 6, `yield_item` 1 (whole chicken → thighs/breasts/wings, with a promo planted on thighs), `weather_event` 9.

Three example messy listings for the same product (`PRD-1003`, canonical name `Bananas, 6 oz clamshell`):

```
[LST-00005] SUP-1: raw_name='Bananas 6 oz CLAM'          raw_unit='LB' raw_pack='CS/12'         price=$5.52
[LST-00006] SUP-6: raw_name='bananas 6 oz clam #5107'    raw_unit='lb' raw_pack='CS/12 11.1LB'   price=$5.36
[LST-00007] SUP-5: raw_name='BANANAS 6 OZ CLAM'          raw_unit='LB' raw_pack='CS/12'          price=$5.41
```

Two mypy fixes needed beyond the design decisions above: numpy's bundled stubs (numpy 2.5.3) use Python 3.12 `type` alias syntax, which broke under `python_version = "3.11"` — bumped to `"3.12"` (the `requires-python >=3.11` floor for the *runtime* is unaffected, this only changes what syntax mypy assumes when reading stubs). Also added `pandas.*` to the existing `ignore_missing_imports` override (no bundled pandas stubs, same treatment as `duckdb.*`). Ruff's line-length went from 100 → 120 — the vectorized-numpy simulation code has enough long single-expression lines that 100 was fighting the style rather than catching real issues.

---

## M2 — Catalog reconciliation (rules + ML)

**Acceptance:** Match F1 ≥ 0.93 on test split; unit-parser tests pass.

- [ ] Unit/pack parser (CAT-1)
- [ ] Blocking (CAT-2)
- [ ] Pairwise features (CAT-3)
- [ ] Seeded train/test split of labeled pairs, decided independently of the eval split (open question #2)
- [ ] Calibrated logistic regression (CAT-4), thresholds (CAT-5)
- [ ] Match review agent stub + unit-compatibility veto (CAT-6)
- [ ] Human accept/reject → `product_listing_map` (CAT-7)
- [ ] F1 ≥ 0.93 on held-out test split

**Result:** _(filled in after run)_

---

## M3 — Forecasting: quantiles, calibration, SHAP, MLflow

**Acceptance:** WAPE beats baseline ≥25%; coverage 78–85%; non-crossing test.

- [ ] Feature engineering (FC-2)
- [ ] Time-based train/calibrate/test split (FC-3)
- [ ] One LightGBM model per quantile (FC-1), non-crossing enforcement (FC-4)
- [ ] Hand-rolled split-conformal CQR (open question #3) to hit FC-5 / G2
- [ ] SHAP drivers on P50 (FC-6)
- [ ] Delivery-cycle horizon forecast (FC-7)
- [ ] MLflow logging (FC-8)
- [ ] WAPE vs baseline per department, 80% coverage, one example item with P10/P50/P90 + top SHAP drivers

**Result:** _(filled in after run)_

---

## M4 — Engine: inventory estimate, newsvendor, yields, bakery, simulate

**Acceptance:** property tests (ORD-8); phantom recall ≥0.90.

- [ ] Inventory estimate + phantom detection (INV-1..3)
- [ ] Newsvendor critical ratio incl. goodwill penalty / disposal cost as config constants, min/max order columns (resolve open question #1)
- [ ] Case-pack rounding, yields (ORD-4), bakery (ORD-5)
- [ ] `calc_id` + `calculations` table (ORD-6)
- [ ] `simulate_order` read-only entry point (ORD-7)
- [ ] Property tests: monotonic, non-negative, case-pack multiple (ORD-8)
- [ ] `engine/` passes `mypy --strict`

**Result:** _(filled in after run)_

---

## M5 — Exceptions + item reasons + backtest

**Acceptance:** waste and stockouts both below baseline; ≤1% escalated.

- [ ] Exception detectors EXC-1..6
- [ ] `item_reasons` generation
- [ ] Backtest vs baseline ordering policy (EVAL-2)

**Result:** _(filled in after run)_

---

## M6 — Agents + guardrails (with fallback)

**Acceptance:** schema validity 100%; faithfulness 100%; routing accuracy ≥0.90; runs with and without API key.

- [ ] LangGraph investigator: triage → investigate → validate → route → narrate
- [ ] Tools (AGT-1), `Finding` schema (AGT-2/9.6)
- [ ] Guardrails: provenance, policy band, faithfulness, unit veto, routing (GRD-1..7)
- [ ] Fallback templates (AGT-6)
- [ ] Match review agent (schema `MatchReview`)
- [ ] Ask FreshPilot chat agent (AGT-7..9)
- [ ] `guardrails/` passes `mypy --strict`
- [ ] Run investigator on strawberries-style phantom-stock case in fallback mode and LLM mode; show both `Finding` outputs and the trace

**Result:** _(filled in after run)_

---

## M7 — Pipeline orchestrator + observability store

**Acceptance:** `make pipeline` completes ≤5 min; traces and stage timings populated.

- [ ] Orchestrator running all 12 pipeline stages in order
- [ ] `pipeline_runs` / `pipeline_stages` logging
- [ ] Local DuckDB trace store (+ optional Langfuse adapter)

**Result:** _(filled in after run)_

---

## M8 — FastAPI endpoints

**Acceptance:** contract tests for every endpoint in §9.8.

- [ ] All routes in §9.8 implemented
- [ ] Contract tests per route

**Result:** _(filled in after run)_

---

## M9 — Web app matching UI reference

**Acceptance:** Playwright smoke: approve all decisions → send orders; visual review against reference.

- [ ] Full screen-by-screen study of `docs/ui-reference.html` (all 8 screens + chat drawer, light/dark)
- [ ] React app scaffold (Vite + TS), design tokens ported from reference
- [ ] Screens wired to live API data (no hardcoded arrays)
- [ ] Hand-built SVG charts w/ tooltips
- [ ] Playwright smoke test

**Result:** _(filled in after run)_

---

## M10 — Chat assistant, polish, README, demo script

**Acceptance:** `make demo` from clean clone ≤10 min; README with results table from `reports/eval.md`.

- [ ] Chat assistant wired end-to-end
- [ ] Full README: pitch, screenshot, architecture diagram, results table, how to run, independence notice
- [ ] `make demo` clean-clone timing check

**Result:** _(filled in after run)_
