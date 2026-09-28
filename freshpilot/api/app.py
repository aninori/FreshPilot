"""Local demo API. Every manager mutation is transactional and audited."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from datetime import time as clock_time
from pathlib import Path
from typing import Any, Literal

import duckdb
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from freshpilot.agents.budget import RunBudget, complete
from freshpilot.api.access import actor, audit, ensure_access, install_access, outside_band
from freshpilot.api.workflow import install_workflow
from freshpilot.config import load_settings
from freshpilot.engine.calculations import compute_order
from freshpilot.guardrails.unit_veto import veto_merge_if_unit_incompatible
from freshpilot.obs.store import dump, ensure_schema
from freshpilot.pipeline.run import run as run_pipeline


class Adjustment(BaseModel):
    cases: int = Field(ge=0, strict=True)
    reason: str = Field(default="", max_length=1000)


RejectReason = Literal[
    "Shelf is already stocked", "Forecast looks too high/low", "Supplier issue", "Discontinued", "Other",
]


class ManagerOverride(BaseModel):
    cases: int | None = Field(default=None, ge=0, strict=True)
    reason: RejectReason
    note: str = Field(default="", max_length=1000)
    counted_qty: float | None = Field(default=None, ge=0)


class DepartmentControl(BaseModel):
    auto_approve: bool


class CatalogAction(BaseModel):
    action: Literal["accept", "reject"]


class ChatTurn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    product_id: str | None = None


class PipelineRequest(BaseModel):
    date: str | None = None


def rows(con: duckdb.DuckDBPyConnection, query: str, parameters: Any = ()) -> list[dict[str, Any]]:
    cursor = con.execute(query, parameters)
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def create_app(db_path: Path = Path("data/freshpilot.duckdb"), reports: Path = Path("reports")) -> FastAPI:
    app = FastAPI(title="FreshPilot", version="0.1.0")
    lock = threading.RLock()
    settings = load_settings()
    chat_budget = RunBudget(settings.max_run_usd)

    @contextmanager
    def db(write: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
        with lock, duckdb.connect(str(db_path)) as con:
            ensure_schema(con)
            ensure_access(con)
            if write:
                con.execute("BEGIN TRANSACTION")
            try:
                yield con
                if write:
                    con.execute("COMMIT")
            except Exception:
                if write:
                    con.execute("ROLLBACK")
                raise

    def latest(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
        found = rows(con, "SELECT * FROM pipeline_runs WHERE status='complete' ORDER BY started_at DESC LIMIT 1")
        if not found:
            raise HTTPException(503, "Run the daily pipeline first")
        return found[0]

    def report() -> Any:
        path = reports / "eval.json"
        if not path.exists():
            raise HTTPException(503, "Run the evaluation first")
        return json.loads(path.read_text(encoding="utf-8"))

    def event(con: duckdb.DuckDBPyConnection, run_id: str, action: str, subject: str,
              before: Any, after: Any, reason: str = "") -> None:
        con.execute("INSERT INTO human_events VALUES (?, ?, current_timestamp, ?, ?, ?, ?, ?)",
                    [uuid.uuid4().hex, run_id, action, subject, dump(before), dump(after), reason])
        audit(con, action, subject, {"run_id": run_id, "before": before, "after": after, "reason": reason})

    install_access(app, db, rows)
    install_workflow(app, db, rows, latest, event)

    def feedback(con: duckdb.DuckDBPyConnection, rid: str, product_id: str, action: str,
                 agent_cases: int, manager_cases: int, reason: str, note: str) -> None:
        product = rows(con, "SELECT dept_id,category FROM products WHERE product_id=?", [product_id])[0]
        con.execute("INSERT INTO manager_feedback VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp, true, true)",
                    [uuid.uuid4().hex, rid, product_id, product["dept_id"], product["category"], action,
                     agent_cases, manager_cases, reason, note])
        recent = rows(con, """SELECT count(*) n FROM manager_feedback
            WHERE category=? AND created_at>=current_timestamp-INTERVAL 14 DAY""", [product["category"]])[0]["n"]
        if recent == 3:
            candidates = rows(con, """SELECT o.product_id,o.cases,i.payload FROM orders o
                JOIN run_items i USING(run_id,product_id) WHERE o.run_id=? AND i.category=? AND o.status='approved'
                AND o.product_id<>?
                AND NOT EXISTS (SELECT 1 FROM decisions d WHERE d.run_id=o.run_id AND d.product_id=o.product_id)""",
                [rid, product["category"], product_id])
            for candidate in candidates:
                item = json.loads(candidate["payload"])
                finding = item.get("finding", {"summary": "Manager review required after repeated overrides",
                                               "reasons": ["Three overrides occurred in this category within 14 days"]})
                con.execute("INSERT INTO decisions VALUES (?, ?, ?, 'pending', ?, ?, ?)",
                            [uuid.uuid4().hex, rid, candidate["product_id"], candidate["cases"],
                             candidate["cases"], dump(finding)])
            con.execute("""UPDATE orders SET status='pending' WHERE run_id=? AND product_id IN
                (SELECT product_id FROM run_items WHERE run_id=? AND category=?) AND product_id<>?
                AND status='approved' AND NOT EXISTS (SELECT 1 FROM order_overrides x
                WHERE x.run_id=orders.run_id AND x.product_id=orders.product_id)""",
                [rid, rid, product["category"], product_id])
            event(con, rid, "auto_approve_tightened", product["category"], {"overrides": 2},
                  {"overrides": recent, "window_days": 14},
                  f"{product['category']}: auto-approve tightened")

    def departments(con: duckdb.DuckDBPyConnection, rid: str) -> list[dict[str, Any]]:
        return rows(con, """SELECT d.dept_id, d.name, count(*) AS items,
            count(*) FILTER (WHERE o.status='approved') AS approved,
            count(*) FILTER (WHERE o.status IN ('pending','needs_category_approval')) AS pending,
            count(*) FILTER (WHERE o.status='sent') AS sent,
            count(*) FILTER (WHERE o.status='recalled') AS recalled, sum(o.value) AS value,
            coalesce(c.auto_approve,true) AS auto_approve
            FROM orders o JOIN run_items i USING(run_id,product_id)
            JOIN departments d ON d.dept_id=i.dept_id
            LEFT JOIN department_controls c ON c.run_id=o.run_id AND c.dept_id=d.dept_id
            WHERE o.run_id=?
            GROUP BY d.dept_id,d.name,c.auto_approve ORDER BY d.name""", [rid])

    @app.get("/api/summary/today")
    def summary() -> Any:
        with db() as con:
            latest_run = latest(con)
            rid = latest_run["run_id"]
            counts = rows(con, "SELECT status,count(*) AS count FROM orders WHERE run_id=? GROUP BY status", [rid])
            count_map = {r["status"]: r["count"] for r in counts}
            metrics = report()
            performance = metrics["G5_waste_and_stockout_vs_baseline"]
            fp, base = performance["freshpilot"], performance["baseline"]
            weekly = rows(con, """SELECT date_trunc('week',w.date) AS date, sum(w.qty_wasted*p.unit_cost) AS value
                FROM waste_log w JOIN products p USING(product_id) WHERE w.date<=?
                GROUP BY 1 ORDER BY 1 DESC LIMIT 8""", [latest_run["date"]])
            return {**json.loads(latest_run["summary"]), "pending": count_map.get("pending", 0)
                    + count_map.get("needs_category_approval", 0), "statuses": count_map,
                    "store": rows(con, "SELECT * FROM stores LIMIT 1")[0],
                    "kpis": {"waste_reduction": 1-fp["waste_pct"]/base["waste_pct"],
                             "lost_sales_avoided": base["lost_sales_usd"]-fp["lost_sales_usd"],
                             "margin_added": fp["margin_usd"]-base["margin_usd"],
                             "in_stock": 1-fp["stockout_pct"]},
                    "weekly_waste": list(reversed(weekly)), "source_ref": "reports/eval.json"}

    @app.get("/api/departments")
    def get_departments() -> Any:
        with db() as con:
            return departments(con, latest(con)["run_id"])

    @app.get("/api/activity")
    def activity(limit: int = Query(20, ge=1, le=100)) -> Any:
        with db() as con:
            return rows(con, """SELECT t.run_id,t.agent,t.product_id,t.outcome,p.name FROM traces t
                JOIN products p USING(product_id) WHERE pipeline_run_id=?
                ORDER BY CASE WHEN agent='investigator' THEN 0 ELSE 1 END,t.run_id LIMIT ?""",
                [latest(con)["run_id"], limit])

    @app.get("/api/decisions")
    def decisions() -> Any:
        with db() as con:
            found = rows(con, """SELECT d.*,i.payload AS item,x.action AS override_action,
                x.reason AS override_reason,x.note AS override_note,x.counted_qty
                FROM decisions d JOIN run_items i USING(run_id,product_id)
                LEFT JOIN order_overrides x USING(run_id,product_id)
                WHERE d.run_id=? ORDER BY d.product_id""", [latest(con)["run_id"]])
            rules = json.loads(rows(con, "SELECT payload FROM app_rules WHERE id=1")[0]["payload"])
            return [{**r, "finding": json.loads(r.pop("payload")), "item": json.loads(r["item"]),
                     "override_band": rules} for r in found]

    def change_decision(decision_id: str, action: str, adjustment: Adjustment | None = None) -> Any:
        with db(write=True) as con:
            rid = latest(con)["run_id"]
            found = rows(con, "SELECT * FROM decisions WHERE id=? AND run_id=?", [decision_id, rid])
            if not found:
                raise HTTPException(404, "Decision not found in current run")
            before = found[0]
            order = rows(con, "SELECT * FROM orders WHERE run_id=? AND product_id=?", [rid, before["product_id"]])[0]
            if order["status"] == "sent":
                raise HTTPException(409, "Sent orders cannot be changed")
            cases = before["recommended_cases"] if adjustment is None else adjustment.cases
            product = rows(con, "SELECT * FROM products WHERE product_id=?", [before["product_id"]])[0]
            item = json.loads(rows(con, "SELECT payload FROM run_items WHERE run_id=? AND product_id=?",
                                   [rid, before["product_id"]])[0]["payload"])
            pack = item["calculation"]["case_pack"]
            if cases * pack > product["max_shelf_capacity"]:
                raise HTTPException(422, "Quantity exceeds shelf capacity")
            if action != "undo" and con.execute(
                "SELECT 1 FROM shelf_tasks WHERE run_id=? AND product_id=? AND status='open'",
                [rid, before["product_id"]],
            ).fetchone():
                raise HTTPException(409, "Complete the assigned shelf check first.")
            outside = outside_band(con, cases, before["recommended_cases"])
            if outside and (adjustment is None or not adjustment.reason.strip()):
                raise HTTPException(422, "An adjustment outside the policy band requires a reason")
            status = "pending" if action == "undo" else ("needs_category_approval" if outside else "approved")
            calc_id = item["calculation"]["calc_id"]
            if action == "adjust":
                calc = compute_order(before["product_id"], "manager_override", str(latest(con)["date"]),
                    cases*pack, 0, 0, pack, 0, cases,
                    {"recommended_calc_id": calc_id, "manager_reason": adjustment.reason if adjustment else ""})
                calc_id = calc.calc_id
                con.execute("INSERT INTO calculations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING", [
                    calc_id, calc.product_id, calc.calc_type, calc.date, calc.target_qty, calc.estimated_on_hand,
                    calc.order_cases, calc.order_units, dump(calc.breakdown),
                ])
            if action == "undo":
                con.execute("DELETE FROM order_overrides WHERE run_id=? AND product_id=?", [rid, before["product_id"]])
            con.execute("UPDATE decisions SET cases=?,status=? WHERE id=?", [cases, status, decision_id])
            con.execute("UPDATE orders SET cases=?,units=?,value=?,status=?,calc_id=? WHERE run_id=? AND product_id=?",
                        [cases, cases*pack, cases*pack*product["unit_cost"], status, calc_id,
                         rid, before["product_id"]])
            after = {"id": decision_id, "cases": cases, "status": status, "calc_id": calc_id}
            event(con, rid, action, decision_id, before, after, adjustment.reason if adjustment else "")
            return after

    @app.post("/api/decisions/{decision_id}/approve")
    def approve(decision_id: str) -> Any:
        return change_decision(decision_id, "approve")

    @app.post("/api/decisions/{decision_id}/adjust")
    def adjust(decision_id: str, body: Adjustment) -> Any:
        return change_decision(decision_id, "adjust", body)

    @app.post("/api/decisions/{decision_id}/undo")
    def undo(decision_id: str) -> Any:
        return change_decision(decision_id, "undo")

    @app.post("/api/decisions/{decision_id}/reject")
    def reject(decision_id: str, body: ManagerOverride) -> Any:
        with db(write=True) as con:
            rid = latest(con)["run_id"]
            found = rows(con, "SELECT * FROM decisions WHERE id=? AND run_id=?", [decision_id, rid])
            if not found:
                raise HTTPException(404, "Decision not found in current run")
            decision = found[0]
            order = rows(con, "SELECT * FROM orders WHERE run_id=? AND product_id=?",
                         [rid, decision["product_id"]])[0]
            if order["status"] == "sent":
                raise HTTPException(409, "Recall sent orders before changing them")
            con.execute("""INSERT OR REPLACE INTO order_overrides VALUES
                (?, ?, 'reject', ?, 0, ?, ?, ?, current_timestamp)""",
                [rid, decision["product_id"], decision["recommended_cases"], body.reason, body.note, body.counted_qty])
            con.execute("UPDATE decisions SET cases=0,status='rejected' WHERE id=?", [decision_id])
            con.execute("UPDATE orders SET cases=0,units=0,value=0,status='rejected' WHERE run_id=? AND product_id=?",
                        [rid, decision["product_id"]])
            feedback(con, rid, decision["product_id"], "reject", decision["recommended_cases"], 0,
                     body.reason, body.note)
            after = {"id": decision_id, "cases": 0, "status": "rejected", "reason": body.reason,
                     "note": body.note, "counted_qty": body.counted_qty}
            event(con, rid, "reject", decision_id, decision, after, body.reason)
            return after

    @app.get("/api/items")
    def items(type: str = "all", dept: str = "all", q: str = "", page: int = Query(1, ge=1)) -> Any:
        with db() as con:
            rid = latest(con)["run_id"]
            where, args = "i.run_id=?", [rid]
            if dept != "all":
                where += " AND i.dept_id=?"
                args.append(dept)
            if type == "needs":
                where += " AND o.status IN ('pending','needs_category_approval')"
            elif type != "all":
                where += " AND i.category=?"
                args.append(type)
            if q:
                where += " AND (p.name ILIKE ? OR i.product_id ILIKE ?)"
                args.extend([f"%{q}%", f"%{q}%"])
            join = " FROM run_items i JOIN orders o USING(run_id,product_id) JOIN products p USING(product_id) "
            count = rows(con, "SELECT count(*) AS n"+join+" WHERE "+where, args)[0]["n"]
            found = rows(con, "SELECT i.product_id,i.dept_id,i.category,p.name,o.cases,o.status,i.payload,"
                         "(SELECT d.id FROM decisions d WHERE d.run_id=i.run_id "
                         "AND d.product_id=i.product_id LIMIT 1) AS decision_id,"
                         "x.action AS override_action,x.original_cases,x.manager_cases,x.reason AS override_reason,"
                         "x.note AS override_note,x.counted_qty"+join+
                         "LEFT JOIN order_overrides x ON x.run_id=i.run_id AND x.product_id=i.product_id WHERE "+where
                         +" ORDER BY p.name LIMIT 25 OFFSET ?", [*args, (page-1)*25])
            counts = rows(con, "SELECT category,count(*) AS count FROM run_items "
                          "WHERE run_id=? GROUP BY category", [rid])
            return {"items": [{**r, "detail": json.loads(r.pop("payload"))} for r in found],
                    "total": count, "page": page, "page_size": 25, "counts": counts}

    @app.get("/api/items/{product_id}")
    def item(product_id: str) -> Any:
        with db() as con:
            found = rows(con, "SELECT payload FROM run_items WHERE run_id=? AND product_id=?",
                         [latest(con)["run_id"], product_id])
            if not found:
                raise HTTPException(404, "Item not found")
            return json.loads(found[0]["payload"])

    def override_item(product_id: str, body: ManagerOverride, reject_order: bool) -> Any:
        with db(write=True) as con:
            rid = latest(con)["run_id"]
            found = rows(con, """SELECT o.*,i.payload,p.dept_id,p.category,p.unit_cost
                FROM orders o JOIN run_items i USING(run_id,product_id) JOIN products p USING(product_id)
                WHERE o.run_id=? AND o.product_id=?""", [rid, product_id])
            if not found:
                raise HTTPException(404, "Item not found in current run")
            before = found[0]
            if before["status"] == "sent":
                raise HTTPException(409, "Recall sent orders before changing them")
            item_payload = json.loads(before["payload"])
            if con.execute("SELECT 1 FROM shelf_tasks WHERE run_id=? AND product_id=? AND status='open'",
                           [rid, product_id]).fetchone():
                raise HTTPException(409, "Complete the assigned shelf check first.")
            original_cases = item_payload["calculation"]["order_cases"]
            cases = 0 if reject_order else body.cases
            if cases is None:
                raise HTTPException(422, "Override quantity is required")
            pack = item_payload["calculation"]["case_pack"]
            capacity = rows(con, "SELECT max_shelf_capacity FROM products WHERE product_id=?", [product_id])[0]
            if cases * pack > capacity["max_shelf_capacity"]:
                raise HTTPException(422, "Quantity exceeds shelf capacity")
            action = "reject" if reject_order else "override"
            calc = compute_order(product_id, "manager_override", str(latest(con)["date"]), cases*pack, 0, 0,
                                 pack, 0, cases, {"recommended_calc_id": before["calc_id"],
                                                 "manager_reason": body.reason})
            con.execute("INSERT INTO calculations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING", [
                calc.calc_id, calc.product_id, calc.calc_type, calc.date, calc.target_qty, calc.estimated_on_hand,
                calc.order_cases, calc.order_units, dump(calc.breakdown),
            ])
            con.execute("""INSERT OR REPLACE INTO order_overrides VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)""",
                [rid, product_id, action, original_cases, cases, body.reason, body.note, body.counted_qty])
            status = "rejected" if reject_order else (
                "needs_category_approval" if outside_band(con, cases, original_cases) else "approved")
            con.execute("UPDATE orders SET cases=?,units=?,value=?,status=?,calc_id=? WHERE run_id=? AND product_id=?",
                        [cases, cases*pack, cases*pack*before["unit_cost"], status, calc.calc_id, rid, product_id])
            con.execute("UPDATE decisions SET cases=?,status=? WHERE run_id=? AND product_id=?",
                        [cases, status, rid, product_id])
            feedback(con, rid, product_id, action, original_cases, cases, body.reason, body.note)
            after = {"product_id": product_id, "cases": cases, "status": status, "calc_id": calc.calc_id,
                     "reason": body.reason, "counted_qty": body.counted_qty}
            event(con, rid, action, product_id, before, after, body.reason)
            return after

    @app.post("/api/items/{product_id}/override")
    def manager_override(product_id: str, body: ManagerOverride) -> Any:
        return override_item(product_id, body, False)

    @app.post("/api/items/{product_id}/reject")
    def manager_reject(product_id: str, body: ManagerOverride) -> Any:
        return override_item(product_id, body, True)

    @app.post("/api/items/{product_id}/restore")
    def restore_item(product_id: str) -> Any:
        with db(write=True) as con:
            rid = latest(con)["run_id"]
            order = rows(con, "SELECT status FROM orders WHERE run_id=? AND product_id=?", [rid, product_id])
            if order and order[0]["status"] == "sent":
                raise HTTPException(409, "Recall sent orders before changing them")
            if con.execute("SELECT 1 FROM shelf_tasks WHERE run_id=? AND product_id=? AND status='open'",
                           [rid, product_id]).fetchone():
                raise HTTPException(409, "Complete the assigned shelf check first.")
            override = rows(con, "SELECT * FROM order_overrides WHERE run_id=? AND product_id=?", [rid, product_id])
            if not override:
                raise HTTPException(404, "No manager override exists for this item")
            item_payload = json.loads(rows(con, "SELECT payload FROM run_items WHERE run_id=? AND product_id=?",
                                           [rid, product_id])[0]["payload"])
            calc = item_payload["calculation"]
            product = rows(con, "SELECT unit_cost FROM products WHERE product_id=?", [product_id])[0]
            decision = rows(con, "SELECT id FROM decisions WHERE run_id=? AND product_id=?", [rid, product_id])
            status = "pending" if decision else "approved"
            con.execute("UPDATE orders SET cases=?,units=?,value=?,status=?,calc_id=? WHERE run_id=? AND product_id=?",
                        [calc["order_cases"], calc["order_units"], calc["order_units"]*product["unit_cost"], status,
                         calc["calc_id"], rid, product_id])
            if decision:
                con.execute(
                    "UPDATE decisions SET cases=recommended_cases,status='pending' WHERE run_id=? AND product_id=?",
                    [rid, product_id],
                )
            con.execute("DELETE FROM order_overrides WHERE run_id=? AND product_id=?", [rid, product_id])
            event(con, rid, "restore_agent_order", product_id, override[0], {"status": status,
                                                                           "cases": calc["order_cases"]})
            return {"product_id": product_id, "status": status, "cases": calc["order_cases"]}

    @app.get("/api/orders")
    def orders() -> Any:
        with db() as con:
            rid = latest(con)["run_id"]
            batch = rows(con, "SELECT * FROM order_batches WHERE run_id=?", [rid])
            return {"departments": departments(con, rid), "batch": batch[0] if batch else None}

    @app.post("/api/orders/departments/{dept_id}")
    def set_department_control(dept_id: str, body: DepartmentControl) -> Any:
        with db(write=True) as con:
            rid = latest(con)["run_id"]
            if not rows(con, "SELECT dept_id FROM departments WHERE dept_id=?", [dept_id]):
                raise HTTPException(404, "Department not found")
            previous = rows(con, "SELECT auto_approve FROM department_controls WHERE run_id=? AND dept_id=?",
                            [rid, dept_id])
            before = previous[0]["auto_approve"] if previous else True
            con.execute("INSERT OR REPLACE INTO department_controls VALUES (?, ?, ?, current_timestamp)",
                        [rid, dept_id, body.auto_approve])
            event(con, rid, "department_auto_approve", dept_id, {"auto_approve": before},
                  {"auto_approve": body.auto_approve}, "Department kill switch")
            return {"dept_id": dept_id, "auto_approve": body.auto_approve}

    @app.post("/api/orders/send")
    def send() -> Any:
        with db(write=True) as con:
            rid = latest(con)["run_id"]
            if con.execute("""SELECT 1 FROM shelf_tasks t JOIN run_items i USING(run_id,product_id)
                LEFT JOIN department_controls c ON c.run_id=t.run_id AND c.dept_id=i.dept_id
                WHERE t.run_id=? AND t.status='open' AND coalesce(c.auto_approve,true)""", [rid]).fetchone():
                raise HTTPException(409, "Complete outstanding shelf checks before sending.")
            enabled = "coalesce(c.auto_approve,true)"
            pending = rows(con, f"""SELECT count(*) AS n FROM orders o JOIN run_items i USING(run_id,product_id)
                LEFT JOIN department_controls c ON c.run_id=o.run_id AND c.dept_id=i.dept_id
                WHERE o.run_id=? AND {enabled} AND o.status NOT IN ('approved','sent','rejected')""",
                           [rid])[0]["n"]
            if pending:
                raise HTTPException(409, "Resolve pending decisions and category approvals before sending")
            count = rows(con, f"""SELECT count(*) AS n FROM orders o JOIN run_items i USING(run_id,product_id)
                LEFT JOIN department_controls c ON c.run_id=o.run_id AND c.dept_id=i.dept_id
                WHERE o.run_id=? AND o.status='approved' AND {enabled}""", [rid])[0]["n"]
            held = rows(con, f"""SELECT count(*) AS n FROM orders o JOIN run_items i USING(run_id,product_id)
                LEFT JOIN department_controls c ON c.run_id=o.run_id AND c.dept_id=i.dept_id
                WHERE o.run_id=? AND o.status='approved' AND NOT {enabled}""", [rid])[0]["n"]
            con.execute(f"""UPDATE orders SET status='sent' WHERE run_id=? AND status='approved' AND product_id IN
                (SELECT o.product_id FROM orders o JOIN run_items i USING(run_id,product_id)
                LEFT JOIN department_controls c ON c.run_id=o.run_id AND c.dept_id=i.dept_id
                WHERE o.run_id=? AND {enabled})""", [rid, rid])
            if count:
                now = datetime.now()
                cutoff = datetime.combine(now.date(), clock_time(18, 0))
                con.execute("INSERT OR REPLACE INTO order_batches VALUES (?, ?, ?, NULL)", [rid, now, cutoff])
                event(con, rid, "send", rid, {"approved": count, "held": held}, {"sent": count})
            return {"status": "sent", "sent": count, "held": held,
                    "delivery": "Simulated; no supplier messages sent"}

    @app.post("/api/orders/recall")
    def recall() -> Any:
        with db(write=True) as con:
            rid = latest(con)["run_id"]
            batch = rows(con, "SELECT * FROM order_batches WHERE run_id=?", [rid])
            if not batch or batch[0]["recalled_at"] is not None:
                raise HTTPException(409, "No sent order batch is available to recall")
            if datetime.now() > batch[0]["recall_until"]:
                raise HTTPException(409, "The 6 PM recall window has closed")
            count = rows(con, "SELECT count(*) n FROM orders WHERE run_id=? AND status='sent'", [rid])[0]["n"]
            con.execute("UPDATE orders SET status='approved' WHERE run_id=? AND status='sent'", [rid])
            con.execute("UPDATE order_batches SET recalled_at=current_timestamp WHERE run_id=?", [rid])
            event(con, rid, "recall", rid, {"sent": count}, {"recalled": count}, "Recalled before 6 PM")
            return {"status": "recalled", "recalled": count}

    @app.get("/api/catalog/summary")
    def catalog_summary() -> Any:
        with db() as con:
            rid = latest(con)["run_id"]
            return {"listings": rows(con, "SELECT count(*) n FROM supplier_listings")[0]["n"],
                    "products": rows(con, "SELECT count(*) n FROM products")[0]["n"],
                    "auto_merged": rows(con, "SELECT count(*) n FROM product_listing_map WHERE method='auto'")[0]["n"],
                    "pending": rows(con, "SELECT count(*) n FROM match_reviews WHERE run_id=? AND status='pending'",
                                    [rid])[0]["n"]}

    @app.get("/api/catalog/reviews")
    def catalog_reviews(page: int = Query(1, ge=1)) -> Any:
        with db() as con:
            rid = latest(con)["run_id"]
            total = rows(con, "SELECT count(*) n FROM match_reviews WHERE run_id=?", [rid])[0]["n"]
            found = rows(con, "SELECT * FROM match_reviews WHERE run_id=? ORDER BY listing_id LIMIT 25 OFFSET ?",
                         [rid, (page-1)*25])
            return {"reviews": [{**r, "review": json.loads(r.pop("payload"))} for r in found],
                    "total": total, "page": page, "page_size": 25}

    @app.post("/api/catalog/reviews/{review_id}")
    def catalog_action(review_id: str, body: CatalogAction) -> Any:
        with db(write=True) as con:
            rid = latest(con)["run_id"]
            found = rows(con, "SELECT * FROM match_reviews WHERE id=? AND run_id=?", [review_id, rid])
            if not found:
                raise HTTPException(404, "Review not found")
            review = found[0]
            payload = json.loads(review["payload"])
            if review["status"] != "pending":
                raise HTTPException(409, "Review already resolved")
            if body.action == "accept" and payload["decision"] == "merge":
                product = rows(con, "SELECT sell_unit FROM products WHERE product_id=?", [review["product_id"]])[0]
                if veto_merge_if_unit_incompatible(payload["raw_unit"], payload["raw_pack"], product["sell_unit"]):
                    raise HTTPException(422, "Unit compatibility veto blocks this merge")
                con.execute("DELETE FROM product_listing_map WHERE listing_id=?", [review["listing_id"]])
                con.execute("INSERT INTO product_listing_map VALUES (?, ?, ?, 'human')", [
                    review["listing_id"], review["product_id"], payload["score"]])
            con.execute("UPDATE match_reviews SET status=? WHERE id=?", [body.action, review_id])
            event(con, rid, "catalog_"+body.action, review_id, review, {"status": body.action})
            return {"status": body.action}

    @app.get("/api/impact")
    def impact() -> Any:
        result = report()
        diagnostic = reports / "delivery-diagnostic.json"
        return {"evaluation": result, "diagnostic": json.loads(diagnostic.read_text()) if diagnostic.exists() else None,
                "source_ref": "reports/eval.json", "food_saved_lb": None, "meals": None, "emissions_tonnes": None,
                "methodology": "28-day synthetic holdout; identical generated delivery cadence; literal BRD baseline. "
                               "Weight, meals and carbon conversions are not measured."}

    @app.get("/api/obs/pipeline")
    def pipeline() -> Any:
        with db() as con:
            current = latest(con)
            return {"run": current, "stages": rows(con, "SELECT * FROM pipeline_stages WHERE run_id=? ORDER BY ordinal",
                                                  [current["run_id"]])}

    @app.get("/api/obs/traces")
    def traces() -> Any:
        with db() as con:
            return rows(con, "SELECT run_id,agent,product_id,outcome,tokens_in,tokens_out,cost_usd FROM traces "
                        "WHERE pipeline_run_id=? ORDER BY agent DESC, product_id", [latest(con)["run_id"]])

    @app.get("/api/obs/traces/{run_id}")
    def trace(run_id: str) -> Any:
        with db() as con:
            found = rows(con, "SELECT payload FROM traces WHERE run_id=?", [run_id])
            if not found:
                raise HTTPException(404, "Trace not found")
            return json.loads(found[0]["payload"])

    @app.get("/api/obs/model")
    def model() -> Any:
        with db() as con:
            return json.loads(rows(con, "SELECT payload FROM model_health WHERE run_id=?",
                                   [latest(con)["run_id"]])[0]["payload"])

    @app.get("/api/obs/events")
    def events() -> Any:
        with db() as con:
            rid = latest(con)["run_id"]
            return {"guardrails": rows(con, "SELECT * FROM guardrail_events WHERE run_id=?", [rid]),
                    "human": rows(con, "SELECT * FROM human_events WHERE run_id=? ORDER BY created_at DESC", [rid])}

    @app.post("/api/chat")
    def chat(body: ChatTurn) -> Any:
        if actor.get()["role"] == "staff":
            tasks = app.state.staff_tasks()["tasks"]
            if body.product_id:
                tasks = [t for t in tasks if t["product_id"] == body.product_id]
                if not tasks:
                    raise HTTPException(403, "This item is not assigned to you.")
            answer = "Your assigned shelf checks: " + ("; ".join(
                f"{t['name']} ({t['status']})" for t in tasks) or "none")
            answer += (". Record total stock and damaged stock in My tasks. "
                       "Your manager reviews the updated recommendation.")
            return StreamingResponse(iter(["data: "+dump({"text": answer, "sources": ["my_tasks"]})+"\n\n",
                                           "event: done\ndata: {}\n\n"]), media_type="text/event-stream")
        refine_with_llm = False
        model_evidence: dict[str, Any] = {}
        with db() as con:
            completed_run = latest(con)
            rid = completed_run["run_id"]
            text = body.message.lower()
            pending = rows(con, "SELECT count(*) n FROM decisions WHERE run_id=? AND status='pending'", [rid])[0]["n"]
            sources = ["decisions:"+rid]
            pending_rows = rows(con, """SELECT d.product_id,p.name,d.status,d.payload
                FROM decisions d JOIN products p USING(product_id)
                WHERE d.run_id=? AND d.status='pending' ORDER BY p.name""", [rid])
            model_evidence = {
                "run_summary": json.loads(completed_run["summary"]),
                "evaluation": report(),
                "pending_decisions": [
                    {**{k: v for k, v in row.items() if k != "payload"}, "details": json.loads(row["payload"])}
                    for row in pending_rows
                ],
                "pipeline_stages": rows(
                    con,
                    "SELECT ordinal,name,executor,duration_s,status,count,details "
                    "FROM pipeline_stages WHERE run_id=? ORDER BY ordinal",
                    [rid],
                ),
            }
            if actor.get()["role"] != "admin":
                model_evidence.pop("pipeline_stages", None)
            g5 = model_evidence["evaluation"]["G5_waste_and_stockout_vs_baseline"]
            freshpilot_metrics, baseline_metrics = g5["freshpilot"], g5["baseline"]
            model_evidence["verified_comparisons"] = {
                "lost_sales_avoided_usd": (
                    baseline_metrics["lost_sales_usd"] - freshpilot_metrics["lost_sales_usd"]
                ),
                "waste_rate_change_percentage_points": 100 * (
                    freshpilot_metrics["waste_pct"] - baseline_metrics["waste_pct"]
                ),
                "stockout_rate_change_percentage_points": 100 * (
                    freshpilot_metrics["stockout_pct"] - baseline_metrics["stockout_pct"]
                ),
                "total_waste_units_change": (
                    freshpilot_metrics["total_waste"] - baseline_metrics["total_waste"]
                ),
                "comparison_note": (
                    "FreshPilot has a lower waste rate but higher total waste units because it fulfills much "
                    "more demand; never describe its total waste units as lower than baseline."
                ),
            }
            if any(word in text for word in ("change", "approve", "adjust", "send", "order it")):
                answer = (f"I can explain the {pending} pending decisions, but I cannot change orders. "
                          "Use the Approve or Adjust controls on the Decisions screen.")
            elif "shelf" in text:
                items = rows(con, """SELECT p.name FROM decisions d JOIN products p USING(product_id)
                    WHERE d.run_id=? AND json_extract_string(d.payload,'$.recommended_action')='shelf_check_then_order'
                    AND d.status='pending' ORDER BY p.name""", [rid])
                names = ", ".join(r["name"] for r in items) or "none"
                answer = f"{len(items)} pending items need a shelf check: {names}."
                sources.append("findings:"+rid)
                refine_with_llm = True
            elif "lost sales" in text or "lost sale" in text:
                evaluation = report()["G5_waste_and_stockout_vs_baseline"]
                fp, baseline = evaluation["freshpilot"], evaluation["baseline"]
                avoided = baseline["lost_sales_usd"] - fp["lost_sales_usd"]
                answer = (f"The ${avoided:,.0f} is simulated lost sales avoided in the 28-day backtest: "
                          f"${baseline['lost_sales_usd']:,.0f} under the baseline minus "
                          f"${fp['lost_sales_usd']:,.0f} under FreshPilot. It is a model comparison "
                          "on synthetic demand, not money already lost or realized store savings.")
                sources = ["reports/eval.json:G5_waste_and_stockout_vs_baseline"]
                refine_with_llm = True
            elif "waste" in text or "impact" in text:
                evaluation = report()["G5_waste_and_stockout_vs_baseline"]
                fp, baseline = evaluation["freshpilot"], evaluation["baseline"]
                answer = (f"In the 28-day synthetic backtest, waste was {fp['waste_pct']:.2%} with FreshPilot "
                          f"and {baseline['waste_pct']:.2%} with the baseline. These are simulated results.")
                sources = ["reports/eval.json"]
                refine_with_llm = True
            elif body.product_id:
                found = rows(con, "SELECT payload FROM run_items WHERE run_id=? AND product_id=?",
                             [rid, body.product_id])
                if not found:
                    raise HTTPException(404, "Item not found in the completed run")
                item = json.loads(found[0]["payload"])
                model_evidence["selected_item"] = item
                calc = item["calculation"]
                answer = (f"{item['product']['name']}: {item['reason']}. The engine recommends "
                          f"{calc['order_cases']} cases ({calc['order_units']:.0f} units).")
                sources = [calc["calc_id"], "run_item:"+body.product_id]
                refine_with_llm = True
            elif any(word in text for word in (
                "run", "today", "agent", "review", "summary", "pending", "decision", "item", "handled",
            )):
                summary = json.loads(latest(con)["summary"])
                answer = (f"The completed run reviewed {summary['items']} items, auto-approved "
                          f"{summary['auto_approved']}, and has {pending} pending decisions.")
                sources.append("pipeline_run:"+rid)
                refine_with_llm = True
            else:
                answer = ("I’m FreshPilot’s operations assistant. I can explain today’s decisions, "
                          "orders, shelf checks, catalog reviews, traces, and measured waste impact. "
                          "That question is outside my scope.")
                sources = []
                refine_with_llm = True
        if refine_with_llm and settings.llm_enabled:
            try:
                with lock:
                    response = complete(
                        settings,
                        chat_budget,
                        time.monotonic() + 15,
                        messages=[
                            {"role": "system", "content": (
                                "You are FreshPilot's read-only store operations analyst. Reason directly over "
                                "the structured run evidence and answer the user's actual question; do not merely "
                                "paraphrase a draft. You may calculate differences from supplied values. Never "
                                "invent data or claim synthetic results are realized store savings. If the question "
                                "is unrelated to FreshPilot or store operations, begin exactly with OUT_OF_SCOPE:. "
                                "Silently verify comparison directions and arithmetic before answering; distinguish "
                                "rates from absolute totals and follow verified_comparisons when provided. "
                                "Do not perform mutations. Keep the answer under 120 words."
                            )},
                            {"role": "user", "content": (
                                f"Question: {body.message}\n"
                                f"Structured evidence: {json.dumps(model_evidence, default=str)}"
                            )},
                        ],
                    )
                answer = response.choices[0].message.content or answer
                if answer.startswith("OUT_OF_SCOPE:"):
                    answer = ("I can help with FreshPilot’s recorded operations, including forecasts, ordering, "
                              "pending decisions, catalog reviews, pipeline traces, and measured backtest results.")
                    sources = []
                else:
                    sources = ["pipeline_run:"+rid, "decisions:"+rid, "reports/eval.json"]
                if body.product_id and "selected_item" in model_evidence:
                    sources.append("run_item:"+body.product_id)
            except Exception:
                # The verified deterministic answer is the safe availability fallback.
                pass
        def stream() -> Any:
            yield "data: " + dump({"text": answer, "sources": sources}) + "\n\n"
            yield "event: done\ndata: {}\n\n"
        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/pipeline/run")
    def rerun(body: PipelineRequest) -> Any:
        with lock:
            try:
                return run_pipeline(db_path, date=body.date)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None

    return app


app = create_app(Path(os.environ.get("FRESHPILOT_API_DB", "data/freshpilot.duckdb")))
