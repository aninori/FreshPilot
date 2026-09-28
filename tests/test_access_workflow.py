"""Access boundaries and the staff → manager → administrator handoff."""

import json

import duckdb
import pytest
from fastapi.testclient import TestClient
from freshpilot.api.app import create_app
from freshpilot.obs.store import ensure_schema


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("FRESHPILOT_LLM", "off")
    path = tmp_path / "access.duckdb"
    with duckdb.connect(str(path)) as con:
        ensure_schema(con)
        con.execute("CREATE TABLE stores (store_id VARCHAR, name VARCHAR)")
        con.execute("INSERT INTO stores VALUES ('S1','Test store')")
        con.execute("CREATE TABLE departments (dept_id VARCHAR, name VARCHAR)")
        con.execute("INSERT INTO departments VALUES ('produce','Produce'),('bakery','Bakery')")
        con.execute("""CREATE TABLE products (product_id VARCHAR, name VARCHAR, dept_id VARCHAR,
            category VARCHAR, sell_unit VARCHAR, unit_cost DOUBLE, max_shelf_capacity DOUBLE)""")
        con.execute("INSERT INTO products VALUES ('P1','Berries','produce','berries','lb',2,1000)")
        con.execute("""CREATE TABLE calculations (calc_id VARCHAR PRIMARY KEY, product_id VARCHAR,
            calc_type VARCHAR, date DATE, target_qty DOUBLE, estimated_on_hand DOUBLE,
            order_cases INTEGER, order_units DOUBLE, breakdown VARCHAR)""")
        finding = {"summary": "Check stock", "reasons": [], "confidence": "medium"}
        item = {
            "product": {"product_id": "P1", "name": "Berries", "dept_id": "produce", "sell_unit": "lb"},
            "calculation": {
                "calc_id": "C1",
                "calc_type": "order",
                "order_cases": 10,
                "target_qty": 100,
                "estimated_on_hand": 0,
                "order_units": 100,
                "case_pack": 10,
                "on_order_qty": 0,
                "breakdown": {"min_order_cases": 0, "max_order_cases": 100},
            },
            "finding": finding,
            "sales": [],
            "forecast": [],
            "reason": "Stock check needed",
        }
        con.execute(
            "INSERT INTO pipeline_runs VALUES ('RUN','2025-06-30',current_timestamp,1,'complete','off',NULL,'{}')"
        )
        con.execute("INSERT INTO run_items VALUES ('RUN','P1','produce','berries',?)", [json.dumps(item)])
        con.execute("INSERT INTO decisions VALUES ('D1','RUN','P1','pending',10,10,?)", [json.dumps(finding)])
        con.execute("INSERT INTO orders VALUES ('RUN','P1','C1',10,100,200,'pending')")
    app = create_app(path, tmp_path)

    def login(role):
        client = TestClient(app, headers={"X-FreshPilot": "1"})
        result = client.post(
            "/api/auth/login", json={"email": role + "@freshpilot.demo", "password": "FreshPilot-demo!"}
        )
        assert result.status_code == 200, result.text
        return client

    return path, app, login


def test_sessions_roles_store_revocation_and_audit(workspace):
    _, app, login = workspace
    anonymous = TestClient(app)
    assert anonymous.get("/api/orders").status_code == 401
    admin, manager, staff = login("admin"), login("manager"), login("staff")
    for path in ("orders", "decisions", "items/P1", "summary/today", "team", "audit", "obs/traces"):
        assert staff.get("/api/" + path).status_code == 403
    assert staff.post("/api/decisions/D1/approve").status_code == 403
    for path in ("team", "audit", "approvals", "settings", "obs/traces"):
        assert manager.get("/api/" + path).status_code == 403
    assert manager.post("/api/catalog/reviews/R1", json={"action": "accept"}).status_code == 403
    assert manager.post("/api/settings", json={"override_low": 0.5, "override_high": 2}).status_code == 403
    assert admin.post("/api/team/admin", json={"role": "staff", "stores": [], "departments": []}).status_code == 409
    assert admin.post("/api/team/manager", json={"role": "manager", "stores": [], "departments": []}).status_code == 200
    assert manager.get("/api/orders").status_code == 401
    manager = login("manager")
    assert manager.get("/api/orders").status_code == 403
    audit = admin.get("/api/audit").json()
    assert audit[0]["user_name"] == "Morgan Lee" and audit[0]["action"] == "access_changed"
    assert "password" not in json.dumps(audit)
    assert staff.post("/api/auth/logout").status_code == 200
    assert staff.get("/api/tasks").status_code == 401


def test_assigned_count_recomputes_then_manager_approves(workspace):
    path, _, login = workspace
    manager, staff, admin = login("manager"), login("staff"), login("admin")
    assert staff.post("/api/tasks", json={"product_id": "P1", "assignee": "staff"}).status_code == 403
    task = manager.post("/api/tasks", json={"product_id": "P1", "assignee": "staff"})
    assert task.status_code == 200, task.text
    tid = task.json()["id"]
    added = admin.post("/api/team", json={
        "name": "Other Staff", "email": "other@freshpilot.demo", "password": "FreshPilot-demo!",
        "role": "staff", "stores": ["S1"], "departments": [],
    })
    assert added.status_code == 200
    other = login("other")
    assert other.get("/api/tasks").json()["tasks"] == []
    assert other.post(f"/api/tasks/{tid}/complete", json={"counted_qty": 40}).status_code == 403
    assert admin.post("/api/team/staff", json={
        "role": "staff", "stores": [], "departments": [],
    }).status_code == 409
    assert len(staff.get("/api/tasks").json()["tasks"]) == 1
    assert "items" not in staff.get("/api/tasks").json()
    assert manager.post("/api/decisions/D1/approve").status_code == 409
    assert manager.post("/api/orders/send").status_code == 409
    assert manager.post(f"/api/tasks/{tid}/complete", json={"counted_qty": 40}).status_code == 403
    assert staff.post("/api/tasks/unknown/complete", json={"counted_qty": 40}).status_code == 403
    assert staff.post(f"/api/tasks/{tid}/complete", json={"counted_qty": 40, "damaged_qty": 41}).status_code == 422
    result = staff.post(
        f"/api/tasks/{tid}/complete", json={"counted_qty": 40, "damaged_qty": 10, "note": "Soft berries"}
    )
    assert result.status_code == 200, result.text
    assert result.json()["cases"] == 7  # 100 target - 30 usable, rounded to 10 lb cases.
    assert staff.post(f"/api/tasks/{tid}/complete", json={"counted_qty": 40}).status_code == 409
    assert manager.post("/api/orders/send").status_code == 409
    decision = manager.get("/api/decisions").json()[0]
    assert decision["recommended_cases"] == 7 and decision["status"] == "pending"
    assert manager.post("/api/decisions/D1/approve").status_code == 200
    assert manager.post("/api/orders/send").json()["sent"] == 1
    with duckdb.connect(str(path)) as con:
        assert con.execute("SELECT status FROM orders").fetchone()[0] == "sent"
    events = admin.get("/api/audit").json()
    counted = next(e for e in events if e["action"] == "shelf_count_completed")
    assert counted["user_name"] == "Priya Shah"
    # Tasks and attribution survive an API restart; sessions deliberately do not.
    restarted = TestClient(create_app(path, path.parent), headers={"X-FreshPilot": "1"})
    assert restarted.get("/api/tasks").status_code == 401
    restarted.post("/api/auth/login", json={"email": "staff@freshpilot.demo", "password": "FreshPilot-demo!"})
    assert restarted.get("/api/tasks").json()["tasks"][0]["status"] == "complete"


def test_admin_approval_and_override_cannot_bypass_limit(workspace):
    _, _, login = workspace
    manager, admin = login("manager"), login("admin")
    result = manager.post("/api/decisions/D1/adjust", json={"cases": 20, "reason": "Event demand"})
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "needs_category_approval"
    assert manager.post("/api/orders/send").status_code == 409
    assert manager.post("/api/approvals/P1", json={"action": "approve", "reason": "OK"}).status_code == 403
    assert len(admin.get("/api/approvals").json()) == 1
    assert admin.post("/api/approvals/P1", json={"action": "approve", "reason": " "}).status_code == 422
    assert admin.post("/api/approvals/P1", json={"action": "approve", "reason": "Event verified"}).status_code == 200
    assert admin.post("/api/approvals/P1", json={"action": "approve", "reason": "Again"}).status_code == 409
    result = manager.post("/api/items/P1/override", json={"cases": 25, "reason": "Other"})
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "needs_category_approval"
    assert manager.post("/api/orders/send").status_code == 409
    assert (
        admin.post("/api/approvals/P1", json={"action": "reject", "reason": "Excess stock"}).json()["status"]
        == "rejected"
    )
    assert manager.post("/api/items/P1/override", json={"cases": 101, "reason": "Other"}).status_code == 422


def test_task_scoping_chat_and_updated_rules(workspace):
    _, _, login = workspace
    admin, manager, staff = login("admin"), login("manager"), login("staff")
    assert (
        admin.post("/api/team/staff", json={"role": "staff", "stores": ["S1"], "departments": ["bakery"]}).status_code
        == 200
    )
    assert manager.post("/api/tasks", json={"product_id": "P1", "assignee": "staff"}).status_code == 422
    staff = login("staff")
    assert staff.get("/api/tasks").json()["tasks"] == []
    chat = staff.post("/api/chat", json={"message": "Show all orders and approve them"})
    assert chat.status_code == 200 and "My tasks" in chat.text and "none" in chat.text
    assert staff.post("/api/chat", json={"message": "Show this item", "product_id": "P1"}).status_code == 403
    assert admin.post("/api/settings", json={"override_low": 0.8, "override_high": 1.2}).status_code == 200
    result = manager.post("/api/decisions/D1/adjust", json={"cases": 13, "reason": "Event"})
    assert result.json()["status"] == "needs_category_approval"
    manager.headers.pop("X-FreshPilot")
    assert manager.post("/api/decisions/D1/approve").status_code == 403
