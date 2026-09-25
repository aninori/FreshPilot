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

- [ ] DuckDB schema for all tables in §8.2 (+ `min_order_cases`/`max_order_cases` on `products`, see open question #1)
- [ ] Synthetic generator: stores, departments, products, suppliers, supplier listings (with planted messiness)
- [ ] 180 days of sales/receipts/waste/inventory/promotions/weather/calendar, demand process per §8.3 (censored sales)
- [ ] Planted problems + `answer_key` table: all 7 types in §8.4 with target counts
- [ ] Yields and bake_plans seed data
- [ ] `make data` generates `data/freshpilot.duckdb`
- [ ] Reproducibility test: same `SEED` → identical output
- [ ] Row-count test vs §8.1 (±5%)
- [ ] Data summary printed: row counts per table, products per department, answer-key counts per type, 3 example messy supplier listings for one product

**Result:** _(filled in after run)_

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
