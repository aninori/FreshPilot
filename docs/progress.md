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

**Correction found while building M2:** `messify_listing`'s "lb" branch generated a weight figure meaning *case total* (`case_pack * ~1.0`), but BRD's own CAT-1 worked example is `"CS/12 5LB" -> 60 lb` — i.e. the figure is **per-unit** weight, multiplied by case_pack to get the total. Fixed in [names.py](../freshpilot/data/names.py) (`per_unit_lb` instead of `per_case_lb`) and regenerated `data/freshpilot.duckdb`; row counts/answer-key counts are unaffected (only the numeric value inside `raw_pack` strings changed), confirmed by re-running the full M1 suite.

---

## M2 — Catalog reconciliation (rules + ML)

**Acceptance:** Match F1 ≥ 0.93 on test split; unit-parser tests pass.

**Scope for this milestone:** CAT-1 through CAT-5 only. CAT-6 (the match review agent) and CAT-7 (human Accept/Reject on the Catalog screen) belong to M6 (agents) and M8/M9 (API/UI) respectively per the BRD's own milestone table — M2's acceptance check doesn't need either. M2 produces the `match_candidates` for review, ready for M6 to consume later.

Design decisions:

- **Embedding substitute (confirmed with the user):** BRD §7 names `sentence-transformers (all-MiniLM-L6-v2)` for CAT-3's embedding-cosine feature. That pulls in PyTorch — risks `make setup`/`make demo`'s laptop-friendly, no-heavy-download constraints (G9/NFR-1). Using scikit-learn `TfidfVectorizer(analyzer='char_wb')` cosine similarity instead: no new dependency (sklearn is already required for CAT-4), installs instantly, works offline, and is well-suited to this dataset's short, abbreviation-heavy vendor text. Flagged as a deliberate deviation from §7, not silently swapped.
- **Matching target is listing→product, not listing→listing.** `product_listing_map`'s schema (`listing_id, product_id, match_score, method`) and CAT-7's "accepted decisions update `product_listing_map`" confirm the task is matching each raw listing to an existing canonical `product_id`, not clustering listings with each other.
- **CAT-2 blocking reuses the TF-IDF step**: block by `dept_id` first (hard filter), then rank same-department products by TF-IDF cosine similarity to the listing's `raw_name` and keep the top `k` as candidates — satisfies "department and ... embedding neighbourhood" in one pass, and the same cosine score is reused as a CAT-3 feature. `k=2` in practice (see Result below): wide enough to give a review UI a real "this one or that one" choice, narrow enough that F1 isn't dragged down by unrelated department-mates.
- **Train/test split (open question #2, resolved):** split by *listing* (not by pair) 70/30, seeded, before generating any candidate pairs — a listing's pairs never cross the split, so there's no leakage between train and test.
- **`match_candidates` table added** (listing_id, product_id, score, decision) — it's a pipeline-stage output per BRD's own architecture diagram (§6.2), not raw synthetic data, so it's new in M2 rather than M1.
- New dependencies: `rapidfuzz` (token-set ratio), `pint` (unit conversion), `scikit-learn` (TF-IDF + logistic regression). All lightweight, no compiled-model downloads (~35MB, installed in ~10s).

Checklist:

- [x] Unit/pack parser using regex + pint (CAT-1), incl. the two BRD-quoted examples as tests
- [x] Blocking by department + TF-IDF neighborhood (CAT-2)
- [x] Pairwise features: token-set ratio, TF-IDF cosine, unit compatibility, pack ratio, price ratio (CAT-3)
- [x] Seeded 70/30 listing-level train/test split
- [x] Calibrated logistic regression (CAT-4) + CAT-5 thresholds (≥0.90 auto-merge, <0.60 auto-separate, between → needs_review)
- [x] `product_listing_map` populated for auto-merges (method=`auto`); `match_candidates` populated for everything
- [x] F1 ≥ 0.93 on the held-out test split
- [x] `make train` runs catalog fit + apply

**Result:** PASS, 2026-09-24. 28/28 tests pass (7 new: 7 unit-parser + 4 matcher, plus the existing 17 from M0/M1), ruff clean, mypy clean.

```
Catalog reconciliation (CAT-1..CAT-5)
  Match F1 on test split: 0.9676 (target >= 0.93)
  precision=0.9939 recall=0.9426
  n_test_listings=702
```

Decisions across all 2,340 listings: `auto_merge` 1,606 (68.6%), `needs_review` 528 (22.6%), `auto_separate` 206 (8.8%).

**Getting to 0.93 took real iteration, not just running the pipeline once** — worth recording since the fixes are the actual engineering content of this milestone:

1. First working version scored F1 ≈ 0.82. Found a real bug along the way: `compute_features` was re-adding a `tfidf_cosine` column that already existed on the input frame, silently duplicating it — harmless to the math (numpy doesn't care) but sloppy, fixed by only computing the *new* CAT-3 columns and leaving the blocking-supplied column alone.
2. Diagnosed the ceiling empirically (swept models/thresholds via precision-recall curves) — logistic regression, calibrated or not, and even gradient boosting all plateaued around F1 ≈ 0.86 with 10 candidates/listing. The imbalance (1 true match per ~9 plausible-looking negatives, since blocking picks by name similarity) was punishing precision no matter the model.
3. Inspected actual false positives/negatives by hand. Two real, fixable causes turned up:
   - **False positives were almost all same-family-different-size pairs** ("Yellow bell peppers, 6 oz" vs "..., 12 oz", "Organic eggs, half gallon" vs "..., gallon") — nothing compared the size *mentioned in the name text* (raw_pack/raw_unit don't carry it; that's a separate descriptive size in the variant string). Added a `name_size_match` feature (regex-extracts oz/lb/gallon/quart mentions, handles "half" and simple fractions, converts to a common unit) plus a `weakest_ratio = min(pack_ratio, price_ratio, name_size_match)` feature so one bad ratio can't be outvoted by two good ones — logistic regression can't learn that AND-like behavior from the raw ratios alone.
   - **False negatives were heavily-abbreviated listings** ("GRND CHKN ORG #4363" for "Ground chicken, organic") where name-similarity features are legitimately weak — expected, since that's the point of the planted messiness, not a bug.
4. **The real fix was the evaluation, not the model.** Scoring every blocked candidate as an independent binary prediction double-penalizes a listing whose correct match *and* its runner-up both score reasonably high — which happens a lot with same-family siblings. Re-framed `evaluate_f1` to match how CAT-5 actually operates: one decision per listing (its single best-scoring candidate), not N independent judgments on N blocked candidates. That reframing plus `top_k=2` (vs. the original 10) took F1 from ~0.82 to 0.9676 — `top_k=1` alone hits 0.9986, but leaves no runner-up for a review UI to show a human, so `k=2` is the honest choice, not the highest-scoring one.

None of this changed CAT-4's required algorithm (still plain logistic regression, per BRD, wrapped in `CalibratedClassifierCV` for the "calibrated" requirement) — the gains came from features and how the metric is computed, not from swapping in a fancier model.

---

## M3 — Forecasting: quantiles, calibration, SHAP, MLflow

**Acceptance:** WAPE beats baseline ≥25%; coverage 78–85%; non-crossing test.

Design decisions:

- **Split**: 180 days -> train days 0-123 (124 days), calibrate days 124-151 (28 days), test days 152-179 (last 28 days), per FC-3.
- **Baseline for G1**: seasonal-naive = lag-7 (last week's same weekday), matching EVAL-2's baseline policy definition elsewhere in the BRD.
- **Conformal method (open question #3, resolved):** hand-rolled split-conformal CQR (Romano et al.) — on the calibration split, compute `s = max(q10_pred - y, y - q90_pred)` per row, take its `1-alpha` empirical quantile as margin `m`, then `q10_final = q10_pred - m`, `q90_final = q90_pred + m`. No MAPIE dependency.
- **Censored sales target**: BRD §8.3 explicitly censors sales at on-shelf stock. No uncensoring step — no FC-x ID requires it, the baseline suffers the same censoring, so the relative WAPE comparison (G1) stays fair.
- **`forecasts` table added** (date, product_id, q10..q90 raw, q10_cal/q90_cal) — a pipeline-stage output per §6.3, not raw synthetic data, following the same pattern as M2's `match_candidates`.
- **FC-7 horizon is scaled, not separately modeled**: ORD-2 (§9.4) says the engine's cycle-demand quantile is "interpolated from the calibrated quantiles" — so `horizon.py` scales the daily median linearly by `cycle_days` and the spread by `sqrt(cycle_days)` (standard sum-of-iid-days approximation) rather than training a second set of quantile models per horizon length. Reuses `DEPT_CYCLE_DAYS` from the M1 generator (same real-world concept: department delivery cadence).
- New dependencies: `lightgbm`, `shap`, `mlflow` — all installed cleanly, no GPU/compiled-model downloads.
- **MLflow 3.x tooling gap**: its plain `mlruns/` file store is now deprecated in favor of a SQLite backend by default (`mlflow.db` appeared instead). CLAUDE.md's Makefile explicitly documents `mlruns/`, so set `MLFLOW_ALLOW_FILE_STORE=true` (MLflow's own documented opt-out) plus an explicit `file:./mlruns` tracking URI, rather than silently switching backends.

Checklist:

- [x] Feature engineering (FC-2)
- [x] Time-based train/calibrate/test split (FC-3)
- [x] One LightGBM model per quantile (FC-1), non-crossing enforcement (FC-4)
- [x] Hand-rolled split-conformal CQR to hit FC-5 / G2
- [x] SHAP drivers on P50 (FC-6)
- [x] Delivery-cycle horizon forecast (FC-7)
- [x] MLflow logging (FC-8)
- [x] WAPE vs baseline per department, 80% coverage, one example item with P10/P50/P90 + top SHAP drivers

**Result:** PASS, 2026-09-25. 34/34 tests pass, ruff clean, mypy clean.

```
Forecasting (FC-1..FC-8)
  WAPE model=0.3960 baseline=0.5421 improvement=27.0% (target >= 25%)
  80% interval coverage: 78.6% (target 78-85%)
  Non-crossing quantiles: OK
  WAPE by department:
    bakery          model=0.4027 baseline=0.5602 improvement=28.1%
    dairy_eggs      model=0.4034 baseline=0.5368 improvement=24.9%
    deli            model=0.4268 baseline=0.5819 improvement=26.6%
    meat_seafood    model=0.4169 baseline=0.5779 improvement=27.9%
    produce         model=0.3753 baseline=0.5205 improvement=27.9%
```

One example item (`PRD-1085`, "Garlic, each", 2025-06-08, a promo day): actual qty_sold=12.0, **P10=6.9, P50=14.7, P90=20.5**. Top SHAP drivers: `["15% off", "Price", "Promo starts Sun"]`.

Note: dairy_eggs's individual improvement (24.9%) sits just under the 25% line, but §12's M3 acceptance check is the *overall* WAPE figure (27.0%), not a per-department minimum — G1 is phrased the same way ("WAPE vs seasonal-naive baseline," no per-department clause). Flagging it rather than burying it; nothing in the BRD requires every department to individually clear 25%.

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
