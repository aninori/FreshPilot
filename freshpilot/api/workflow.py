"""Assigned shelf checks and explicit administrator resolution of exceptions."""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from freshpilot.api.access import actor, audit
from freshpilot.engine.calculations import compute_order
from freshpilot.obs.store import dump


class Assignment(BaseModel):
    product_id: str
    assignee: str


class Count(BaseModel):
    counted_qty: float = Field(ge=0, le=100000, allow_inf_nan=False)
    damaged_qty: float = Field(default=0, ge=0, le=100000, allow_inf_nan=False)
    note: str = Field(default="", max_length=1000)


class Resolution(BaseModel):
    action: Literal["approve", "reject"]
    reason: str = Field(min_length=1, max_length=1000)


def install_workflow(app: FastAPI, db: Any, rows: Any, latest: Any, event: Any) -> None:
    def visible_tasks(con: Any) -> list[dict[str, Any]]:
        rid = latest(con)["run_id"]
        user = actor.get()
        query = """SELECT t.*,p.name,p.dept_id,p.sell_unit,u.name AS assignee_name
            FROM shelf_tasks t JOIN products p USING(product_id)
            JOIN app_users u ON u.id=t.assignee WHERE t.run_id=?"""
        params = [rid]
        if user["role"] == "staff":
            query += " AND t.assignee=?"
            params.append(user["id"])
        tasks: list[dict[str, Any]] = rows(con, query + " ORDER BY t.created_at DESC", params)
        if user["role"] == "staff" and user["departments"]:
            tasks = [t for t in tasks if t["dept_id"] in user["departments"]]
        return tasks

    @app.get("/api/tasks")
    def tasks() -> Any:
        with db() as con:
            result: dict[str, Any] = {"tasks": visible_tasks(con)}
            if actor.get()["role"] != "staff":
                result["staff"] = [
                    {**r, "stores": json.loads(r["stores"]), "departments": json.loads(r["departments"])}
                    for r in rows(
                        con, "SELECT id,name,stores,departments FROM app_users WHERE role='staff' ORDER BY name"
                    )
                ]
                result["items"] = rows(
                    con,
                    """SELECT d.product_id,p.name,p.dept_id,d.status
                    FROM decisions d JOIN products p USING(product_id) WHERE d.run_id=?
                    AND d.status IN ('pending','needs_category_approval') ORDER BY p.name""",
                    [latest(con)["run_id"]],
                )
            return result

    @app.post("/api/tasks")
    def assign(body: Assignment) -> Any:
        if actor.get()["role"] == "staff":
            raise HTTPException(403, "Only managers and admins assign shelf checks.")
        with db(write=True) as con:
            rid = latest(con)["run_id"]
            product = rows(
                con,
                """SELECT p.* FROM products p JOIN orders o USING(product_id)
                WHERE o.run_id=? AND p.product_id=? AND o.status<>'sent'""",
                [rid, body.product_id],
            )
            staff = rows(con, "SELECT * FROM app_users WHERE id=? AND role='staff'", [body.assignee])
            if not product or not staff:
                raise HTTPException(422, "Choose a current unsent item and a staff member.")
            member = staff[0]
            stores = {r[0] for r in con.execute("SELECT store_id FROM stores").fetchall()}
            depts = json.loads(member["departments"])
            if not stores.issubset(set(json.loads(member["stores"]))) or (depts and product[0]["dept_id"] not in depts):
                raise HTTPException(422, "Staff member is not assigned to this store and department.")
            existing = rows(
                con,
                "SELECT * FROM shelf_tasks WHERE run_id=? AND product_id=? AND status='open'",
                [rid, body.product_id],
            )
            if existing:
                tid = existing[0]["id"]
                con.execute("UPDATE shelf_tasks SET assignee=? WHERE id=?", [body.assignee, tid])
            else:
                tid = uuid.uuid4().hex
                con.execute(
                    "INSERT INTO shelf_tasks VALUES (?, ?, ?, ?, 'open', NULL, NULL, '', ?, current_timestamp, NULL)",
                    [tid, rid, body.product_id, body.assignee, actor.get()["id"]],
                )
            # A requested shelf check is a prerequisite to sending this order.
            con.execute("UPDATE orders SET status='pending' WHERE run_id=? AND product_id=?", [rid, body.product_id])
            con.execute("UPDATE decisions SET status='pending' WHERE run_id=? AND product_id=?", [rid, body.product_id])
            audit(con, "shelf_check_assigned", tid, {"before": existing, **body.model_dump()})
            return {"id": tid}

    @app.post("/api/tasks/{task_id}/complete")
    def complete_task(task_id: str, body: Count) -> Any:
        with db(write=True) as con:
            user = actor.get()
            task = next((t for t in visible_tasks(con) if t["id"] == task_id), None)
            if not task or task["assignee"] != user["id"] or user["role"] != "staff":
                raise HTTPException(403, "Only the assigned staff member can complete this check.")
            if task["status"] != "open":
                raise HTTPException(409, "This check has already been completed.")
            if body.damaged_qty > body.counted_qty:
                raise HTTPException(422, "Damaged quantity cannot exceed total counted quantity.")
            rid, pid = task["run_id"], task["product_id"]
            item = json.loads(
                rows(con, "SELECT payload FROM run_items WHERE run_id=? AND product_id=?", [rid, pid])[0]["payload"]
            )
            order = rows(con, "SELECT * FROM orders WHERE run_id=? AND product_id=?", [rid, pid])[0]
            if order["status"] == "sent":
                raise HTTPException(409, "Recall the order before completing this check.")
            old = item["calculation"]
            product = rows(con, "SELECT * FROM products WHERE product_id=?", [pid])[0]
            usable = body.counted_qty - body.damaged_qty
            breakdown = old["breakdown"]
            calc = compute_order(
                pid,
                "shelf_count",
                str(latest(con)["date"]),
                old["target_qty"],
                usable,
                old.get("on_order_qty", breakdown.get("on_order_qty", 0)),
                old["case_pack"],
                breakdown.get("min_order_cases", 0),
                breakdown.get("max_order_cases", 0),
                {
                    **breakdown,
                    "shelf_task": task_id,
                    "counted_by": user["id"],
                    "counted_qty": body.counted_qty,
                    "damaged_qty": body.damaged_qty,
                    "previous_calc_id": old["calc_id"],
                },
            )
            from dataclasses import asdict

            item["calculation"] = asdict(calc)
            finding = item.get("finding") or {"reasons": [], "confidence": "medium"}
            finding["summary"] = (
                f"Shelf check completed: {usable:g} usable {product['sell_unit']}. Review the updated order."
            )
            finding["precondition"] = f"Counted by {user['name']}. {body.damaged_qty:g} damaged. {body.note}"
            finding["reasons"] = [
                "Recommendation recalculated from the recorded shelf count.",
                f"Previous recommendation: {old['order_cases']} cases.",
            ]
            item["finding"] = finding
            con.execute(
                "INSERT INTO calculations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                [
                    calc.calc_id,
                    pid,
                    calc.calc_type,
                    calc.date,
                    calc.target_qty,
                    usable,
                    calc.order_cases,
                    calc.order_units,
                    dump(calc.breakdown),
                ],
            )
            con.execute("UPDATE run_items SET payload=? WHERE run_id=? AND product_id=?", [dump(item), rid, pid])
            con.execute(
                "UPDATE orders SET cases=?,units=?,value=?,calc_id=?,status='pending' WHERE run_id=? AND product_id=?",
                [calc.order_cases, calc.order_units, calc.order_units * product["unit_cost"], calc.calc_id, rid, pid],
            )
            decision = rows(con, "SELECT id FROM decisions WHERE run_id=? AND product_id=?", [rid, pid])
            if decision:
                con.execute(
                    "UPDATE decisions SET recommended_cases=?,cases=?,payload=?,status='pending' WHERE id=?",
                    [calc.order_cases, calc.order_cases, dump(finding), decision[0]["id"]],
                )
            else:
                con.execute(
                    "INSERT INTO decisions VALUES (?, ?, ?, 'pending', ?, ?, ?)",
                    [uuid.uuid4().hex, rid, pid, calc.order_cases, calc.order_cases, dump(finding)],
                )
            con.execute("DELETE FROM order_overrides WHERE run_id=? AND product_id=?", [rid, pid])
            con.execute(
                "UPDATE shelf_tasks SET status='complete',counted_qty=?,damaged_qty=?,note=?,"
                "completed_at=current_timestamp WHERE id=?",
                [body.counted_qty, body.damaged_qty, body.note, task_id],
            )
            event(
                con,
                rid,
                "shelf_count_completed",
                pid,
                order,
                {"task_id": task_id, "cases": calc.order_cases, **body.model_dump()},
                body.note,
            )
            return {"status": "complete", "cases": calc.order_cases}

    @app.get("/api/approvals")
    def approvals() -> Any:
        with db() as con:
            return rows(
                con,
                """SELECT o.*,p.name,p.dept_id,i.payload,c.breakdown AS request_context,
                (SELECT user_name FROM access_audit a
                 WHERE json_extract_string(a.details,'$.after.calc_id')=o.calc_id
                 ORDER BY a.created_at DESC LIMIT 1) AS requested_by FROM orders o
                JOIN products p USING(product_id) JOIN run_items i USING(run_id,product_id)
                LEFT JOIN calculations c ON c.calc_id=o.calc_id
                WHERE o.run_id=? AND o.status='needs_category_approval' ORDER BY p.name""",
                [latest(con)["run_id"]],
            )

    @app.post("/api/approvals/{product_id}")
    def resolve(product_id: str, body: Resolution) -> Any:
        if not body.reason.strip():
            raise HTTPException(422, "A reason is required.")
        with db(write=True) as con:
            rid = latest(con)["run_id"]
            found = rows(
                con,
                "SELECT * FROM orders WHERE run_id=? AND product_id=? AND status='needs_category_approval'",
                [rid, product_id],
            )
            if not found:
                raise HTTPException(409, "This order is no longer waiting for admin approval.")
            before = found[0]
            status = "approved" if body.action == "approve" else "rejected"
            if body.action == "approve":
                product = rows(con, "SELECT max_shelf_capacity FROM products WHERE product_id=?", [product_id])[0]
                if before["units"] > product["max_shelf_capacity"]:
                    raise HTTPException(422, "Quantity exceeds shelf capacity.")
                if con.execute(
                    "SELECT 1 FROM shelf_tasks WHERE run_id=? AND product_id=? AND status='open'", [rid, product_id]
                ).fetchone():
                    raise HTTPException(409, "Complete the assigned shelf check first.")
            cases = before["cases"] if body.action == "approve" else 0
            if body.action == "reject":
                item = json.loads(rows(con, "SELECT payload FROM run_items WHERE run_id=? AND product_id=?",
                                       [rid, product_id])[0]["payload"])
                con.execute("""INSERT OR REPLACE INTO order_overrides VALUES
                    (?, ?, 'reject', ?, 0, ?, '', NULL, current_timestamp)""",
                            [rid, product_id, item["calculation"]["order_cases"], body.reason])
            con.execute(
                "UPDATE orders SET status=?,cases=?,units=?,value=? WHERE run_id=? AND product_id=?",
                [status, cases, before["units"] if cases else 0, before["value"] if cases else 0, rid, product_id],
            )
            con.execute(
                "UPDATE decisions SET status=?,cases=? WHERE run_id=? AND product_id=?",
                [status, cases, rid, product_id],
            )
            event(con, rid, "admin_" + body.action, product_id, before, {"status": status, "cases": cases}, body.reason)
            return {"status": status}

    # Staff chat has its own small evidence set, never the entire run or reports.
    app.state.staff_tasks = tasks
