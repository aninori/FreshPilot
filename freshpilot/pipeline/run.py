"""Twelve-stage daily pipeline; only completed snapshots are published."""

from __future__ import annotations

import argparse
import json
import math
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import duckdb
import lightgbm as lgb
import pandas as pd

from freshpilot.agents.budget import RunBudget
from freshpilot.agents.graph import run_investigator
from freshpilot.agents.match_review import build_fallback_match_review
from freshpilot.agents.schemas import ExceptionType
from freshpilot.agents.tools import ToolContext
from freshpilot.config import Settings, load_settings
from freshpilot.data.migrate import migrate
from freshpilot.engine.calculations import compute_order
from freshpilot.engine.simulate import EngineContext, simulate_order
from freshpilot.engine.yields import compute_yield_plan, disposition_for_surplus
from freshpilot.exceptions.run import run as run_exceptions
from freshpilot.guardrails.validation import validate_finding
from freshpilot.obs.health import model_health
from freshpilot.obs.store import dump, ensure_schema, store_trace

DB_PATH = Path("data/freshpilot.duckdb")
TABLES = ("products", "sales_daily", "inventory_book", "waste_log", "forecasts", "promotions",
          "weather", "calendar", "product_listing_map", "match_candidates", "supplier_listings", "yields")


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@contextmanager
def stage(con: duckdb.DuckDBPyConnection, run_id: str, ordinal: int, name: str,
          executor: str) -> Iterator[dict[str, Any]]:
    start, clock = now(), time.monotonic()
    info: dict[str, Any] = {"status": "complete", "count": 0, "details": ""}
    try:
        yield info
    except Exception:
        info["status"] = "failed"
        raise
    finally:
        con.execute("INSERT INTO pipeline_stages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            run_id, ordinal, name, executor, start, time.monotonic() - clock,
            info["status"], info["count"], info["details"],
        ])


def run(db_path: Path = DB_PATH, date: str | None = None, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or load_settings()
    start = time.monotonic()
    run_id = uuid.uuid4().hex
    with duckdb.connect(str(db_path)) as con:
        ensure_schema(con)
        migrate(con)
        latest = con.execute("SELECT max(date) FROM forecasts").fetchone()
        assert latest is not None
        selected = date or settings.demo_date or latest[0]
        if selected is None:
            raise ValueError("Train forecasting models before running the daily pipeline")
        day = pd.Timestamp(selected)
        date_text = str(day.date())
        con.execute("INSERT INTO pipeline_runs VALUES (?, ?, ?, 0, 'running', ?, NULL, '{}')", [
            run_id, date_text, now(), settings.llm_mode,
        ])
        try:
            result = _execute(con, db_path, run_id, day, settings)
            result["duration_s"] = time.monotonic() - start
            con.execute("UPDATE pipeline_runs SET status='complete', duration_s=?, summary=? WHERE run_id=?", [
                result["duration_s"], dump(result), run_id,
            ])
            return result
        except Exception as exc:
            con.execute("UPDATE pipeline_runs SET status='failed', duration_s=?, error=? WHERE run_id=?", [
                time.monotonic() - start, type(exc).__name__, run_id,
            ])
            raise


def _execute(con: duckdb.DuckDBPyConnection, db_path: Path, run_id: str,
             day: pd.Timestamp, settings: Settings) -> dict[str, Any]:
    date = str(day.date())
    with stage(con, run_id, 1, "Ingest", "rules") as s:
        tables = {name: con.execute("SELECT * FROM " + name).df() for name in TABLES}
        products = tables["products"].set_index("product_id", drop=False)
        daily = tables["forecasts"][tables["forecasts"]["date"] == day]
        if len(daily) != len(products) or daily["product_id"].nunique() != len(products):
            raise ValueError("Demo date must have exactly one forecast for every product")
        s["count"] = len(products)
    with stage(con, run_id, 2, "Catalog reconcile", "ml") as s:
        candidates = tables["match_candidates"]
        if len(candidates) != len(tables["supplier_listings"]):
            raise ValueError("Run catalog training before the daily pipeline")
        s.update(status="cached", count=len(candidates), details="Reused trained catalog scores for synthetic catalog")
    with stage(con, run_id, 3, "Catalog match review", "rules") as s:
        listings = tables["supplier_listings"].set_index("listing_id")
        for row in candidates[candidates["decision"] == "needs_review"].itertuples(index=False):
            listing, product = listings.loc[row.listing_id], products.loc[row.product_id]
            review = build_fallback_match_review(row.listing_id, listing.raw_unit, listing.raw_pack,
                                                product["name"], float(row.score), product.sell_unit)
            payload = {**review.model_dump(), "product_name": product["name"], "listing_name": listing.raw_name,
                       "score": float(row.score), "raw_unit": listing.raw_unit, "raw_pack": listing.raw_pack}
            con.execute("INSERT INTO match_reviews VALUES (?, ?, ?, ?, 'pending', ?)", [
                f"{run_id}:{row.listing_id}", run_id, row.listing_id, row.product_id, dump(payload),
            ])
            store_trace(con, run_id, f"{run_id}:catalog:{row.listing_id}", row.product_id,
                        {"route": "review", "finding": payload, "trace": [{"type": "rules", "name": "unit veto",
                         "summary": review.reason, "duration_s": 0.0}]}, "catalog")
        s["count"] = int((candidates["decision"] == "needs_review").sum())
    with stage(con, run_id, 4, "Demand forecast", "ml") as s:
        s.update(status="cached", count=len(daily), details="Reused held-out predictions produced by make train")
    with stage(con, run_id, 5, "Conformal calibration", "ml") as s:
        if not ((daily.q10_cal <= daily.q10) & (daily.q90_cal >= daily.q90)).all():
            raise ValueError("Invalid calibrated interval")
        s.update(status="cached", count=len(daily), details="Training-window CQR applied during make train")
        con.execute("INSERT INTO model_health VALUES (?, ?)", [run_id, dump(model_health(tables))])
    groups = {name: {pid: frame for pid, frame in tables[name].groupby("product_id")} for name in
              ("sales_daily", "inventory_book", "waste_log", "forecasts")}
    calculations: dict[str, Any] = {}
    with stage(con, run_id, 6, "Inventory estimate", "rules") as s:
        for pid in products.index:
            engine_ctx = EngineContext(tables["products"], groups["forecasts"][pid], groups["inventory_book"][pid],
                                groups["waste_log"].get(pid, tables["waste_log"].iloc[:0]),
                                groups["sales_daily"][pid], day)
            calc = simulate_order(engine_ctx, pid)
            calculations[pid] = calc
            con.execute("INSERT INTO inventory_estimates VALUES (?, ?, ?, ?, ?, ?)", [
                run_id, pid, calc.breakdown["book_on_hand"], calc.estimated_on_hand,
                bool(calc.breakdown["is_phantom_stock"]), calc.calc_id,
            ])
        s["count"] = len(calculations)
    yield_plans = {}
    with stage(con, run_id, 7, "Order engine", "rules") as s:
        for parent, children in tables["yields"].groupby("parent_product_id"):
            plan = compute_yield_plan(parent, [(r.child_product_id, calculations[r.child_product_id].target_qty,
                                               float(r.yield_per_parent)) for r in children.itertuples(index=False)])
            base, product = calculations[parent], products.loc[parent]
            yield_plans[parent] = {**asdict(plan), "dispositions": {
                pid: disposition_for_surplus(qty, int(products.loc[pid].shelf_life_days))
                for pid, qty in plan.surplus.items()}}
            calculations[parent] = compute_order(parent, "yield", date, max(base.target_qty, plan.parent_order_qty),
                base.estimated_on_hand, 0, base.case_pack, int(product.min_order_cases), int(product.max_order_cases),
                {**base.breakdown, "yield_plan": yield_plans[parent]})
            for child in plan.children:
                child_base = calculations[child.child_product_id]
                produced = calculations[parent].order_units * child.yield_per_parent
                calculations[child.child_product_id] = compute_order(
                    child.child_product_id, "yield_child", date, child.child_target_qty,
                    child_base.estimated_on_hand, produced, child_base.case_pack, 0,
                    int(products.loc[child.child_product_id].max_order_cases),
                    {**child_base.breakdown, "parent_product_id": parent, "produced_units": produced},
                )
        for pid, calc in calculations.items():
            con.execute("INSERT INTO calculations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING", [
                calc.calc_id, pid, calc.calc_type, date, calc.target_qty, calc.estimated_on_hand,
                calc.order_cases, calc.order_units, dump(calc.breakdown),
            ])
            con.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, 'proposed')", [
                run_id, pid, calc.calc_id, calc.order_cases, calc.order_units,
                calc.order_units * float(products.loc[pid].unit_cost),
            ])
        s["count"] = len(calculations)
    with stage(con, run_id, 8, "Exception checks", "rules") as s:
        run_exceptions(db_path, date=date)
        flags = con.execute("SELECT * FROM exceptions WHERE date=?", [date]).df()
        reasons = con.execute("SELECT * FROM item_reasons WHERE date=?", [date]).df().set_index("product_id")
        s["count"] = len(flags)
    budget = RunBudget(settings.max_run_usd)
    tool_names = ("products", "sales_daily", "inventory_book", "waste_log", "forecasts", "promotions",
                  "weather", "calendar", "product_listing_map", "match_candidates")
    p50_path = db_path.parent / "models/q50.txt"
    ctx = ToolContext(**{n: tables[n] for n in tool_names}, demo_date=day, budget=budget,
                      p50_model=lgb.Booster(model_file=str(p50_path)) if p50_path.exists() else None)
    results: list[dict[str, Any]] = []
    touched: set[str] = set()
    with stage(con, run_id, 9, "Investigator agent", "llm" if settings.llm_enabled else "rules") as s:
        for row in flags.sort_values(["product_id", "type"]).itertuples(index=False):
            enabled = settings.llm_enabled and (
                row.product_id in touched or len(touched) < math.floor(len(products)*.02))
            item_settings = settings if enabled else replace(settings, llm_mode="off")
            result: dict[str, Any] = dict(run_investigator(
                ctx, item_settings, row.product_id, cast(ExceptionType, row.type)))
            if result["llm_usage"]:
                touched.add(row.product_id)
            result["finding"] = result["finding"].model_dump()
            results.append(result)
            store_trace(con, run_id, f"{run_id}:{row.product_id}:{row.type}", row.product_id, result)
        s["count"] = len(results)
    by_product: dict[str, list[dict[str, Any]]] = {}
    with stage(con, run_id, 10, "Guardrail validation", "rules") as s:
        from freshpilot.agents.schemas import Finding
        for result in results:
            pid = result["product_id"]
            by_product.setdefault(pid, []).append(result)
            base = next(iter(result["calculations"].values()))
            errors = validate_finding(Finding.model_validate(result["finding"]), pid, result["exception_type"],
                result["tool_outputs"], result["calculations"], base, float(products.loc[pid].max_shelf_capacity))
            if errors:
                raise ValueError("Cannot publish an unvalidated finding")
            con.execute("INSERT INTO findings VALUES (?, ?, ?, ?, ?, ?)", [pid, date, result["exception_type"],
                        dump(result["finding"]), result["route"], result["source"]])
            for calc in result["calculations"].values():
                con.execute("INSERT INTO calculations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING", [
                    calc["calc_id"], pid, calc["calc_type"], date, calc["target_qty"], calc["estimated_on_hand"],
                    calc["order_cases"], calc["order_units"], dump(calc["breakdown"]),
                ])
            for event in result["guardrail_events"]:
                con.execute("INSERT INTO guardrail_events VALUES (?, ?, ?, ?, ?, ?, ?)", [run_id, pid,
                            event["type"], event["passed"], event["before_value"], event["after_value"],
                            event["details"]])
        s["count"] = sum(len(r["guardrail_events"]) for r in results)
    pending = 0
    with stage(con, run_id, 11, "Routing", "rules") as s:
        for pid, calc in calculations.items():
            escalated = [r for r in by_product.get(pid, []) if r["route"] == "escalate"]
            status = "pending" if escalated else "approved"
            con.execute("UPDATE orders SET status=? WHERE run_id=? AND product_id=?", [status, run_id, pid])
            if escalated:
                pending += 1
                con.execute("INSERT INTO decisions VALUES (?, ?, ?, 'pending', ?, ?, ?)", [
                    f"{run_id}:{pid}", run_id, pid, calc.order_cases, calc.order_cases, dump(escalated[0]["finding"]),
                ])
        s["count"] = pending
    with stage(con, run_id, 12, "Narration and publish", "rules") as s:
        driver_map = {}
        has_drivers = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name='forecast_drivers'").fetchone()
        if has_drivers and has_drivers[0]:
            driver_map = {pid: json.loads(value) for pid, value in con.execute(
                "SELECT product_id,drivers FROM forecast_drivers WHERE date=?", [date]).fetchall()}
        for pid, calc in calculations.items():
            product = products.loc[pid]
            item_results = by_product.get(pid, [])
            finding = item_results[0]["finding"] if item_results else None
            payload = {"product": product.to_dict(), "calculation": asdict(calc),
                       "reason": reasons.loc[pid].reason, "category": reasons.loc[pid].category,
                       "finding": finding, "findings": [r["finding"] for r in item_results],
                       "checks": [{"name": r["exception_type"], "status": r["route"]} for r in item_results],
                       "forecast": groups["forecasts"][pid].query("date <= @day").tail(28).to_dict("records"),
                       "sales": groups["sales_daily"][pid].query("date <= @day").tail(28)[
                           ["date", "qty_sold"]].to_dict("records"),
                       "yield_plan": yield_plans.get(pid), "drivers": driver_map.get(pid, [])}
            con.execute("INSERT INTO run_items VALUES (?, ?, ?, ?, ?)", [
                run_id, pid, product.dept_id, reasons.loc[pid].category, dump(payload),
            ])
        s["count"] = len(products)
    return {"run_id": run_id, "date": date, "items": len(products), "pending": pending,
            "auto_approved": len(products)-pending, "llm_touched": len(touched), "cost_usd": budget.cost_usd,
            "guardrail_events": sum(len(r["guardrail_events"]) for r in results)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date")
    args = parser.parse_args()
    print(json.dumps(run(date=args.date), indent=2))
