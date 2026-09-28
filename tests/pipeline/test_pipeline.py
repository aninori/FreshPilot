"""M7: publish complete snapshots only; preserve failed runs and repeatability."""

import json
from dataclasses import replace

import duckdb
import pandas as pd
import pytest
from freshpilot.config import load_settings
from freshpilot.data.generate import generate, write_duckdb
from freshpilot.pipeline.run import run


@pytest.fixture(scope="module")
def pipeline_db(tmp_path_factory):
    tables = generate(42)
    ids = set(tables["products"].head(3)["product_id"])
    for name, frame in tables.items():
        for column in ("product_id", "parent_product_id", "child_product_id"):
            if column in frame.columns:
                frame = frame[frame[column].isin(ids)]
        tables[name] = frame
    listings = tables["answer_key"].query("type == 'duplicate_listing'")
    mappings = [(lid, r.product_id) for r in listings.itertuples() for lid in r.listing_ids.split(",")]
    lids = {lid for lid, _ in mappings}
    tables["supplier_listings"] = tables["supplier_listings"][tables["supplier_listings"]["listing_id"].isin(lids)]
    db = tmp_path_factory.mktemp("pipeline") / "test.duckdb"
    write_duckdb(tables, db)
    dates = pd.date_range("2025-06-03", "2025-06-30")
    forecasts = pd.DataFrame([{"date": d, "product_id": pid, **{q: 20.0 for q in
        ("q10", "q25", "q50", "q60", "q70", "q75", "q85", "q90", "q10_cal", "q90_cal")}}
        for d in dates for pid in ids])
    with duckdb.connect(str(db)) as con:
        con.append("forecasts", forecasts)
        for lid, pid in mappings:
            con.execute("INSERT INTO match_candidates VALUES (?, ?, .95, 'auto_merge')", [lid, pid])
    return db


def test_M7_completed_snapshots_are_repeatable_and_failures_remain_inspectable(pipeline_db):
    settings = replace(load_settings(), llm_mode="off", demo_date=None)
    first = run(pipeline_db, settings=settings)
    second = run(pipeline_db, settings=settings)
    assert first["items"] == second["items"] == 3
    assert first["pending"] == second["pending"]
    with pytest.raises(ValueError, match="forecast"):
        run(pipeline_db, date="2020-01-01", settings=settings)
    with duckdb.connect(str(pipeline_db)) as con:
        assert con.execute("SELECT count(*) FROM pipeline_runs WHERE status='complete'").fetchone()[0] == 2
        assert con.execute("SELECT count(*) FROM pipeline_runs WHERE status='failed'").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM pipeline_stages WHERE run_id=?", [first["run_id"]]).fetchone()[0] == 12
        query = "SELECT payload FROM run_items WHERE run_id=? ORDER BY product_id"
        a = con.execute(query, [first["run_id"]]).fetchall()
        b = con.execute(query, [second["run_id"]]).fetchall()
        assert [json.loads(r[0]) for r in a] == [json.loads(r[0]) for r in b]
        assert first["duration_s"] < 300


def test_API_contracts_and_GRD6_transactional_action_gates(pipeline_db, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from freshpilot.api.app import create_app

    monkeypatch.setenv("FRESHPILOT_LLM", "off")
    current = run(pipeline_db, settings=replace(load_settings(), llm_mode="off", demo_date=None))
    rid = current["run_id"]
    with duckdb.connect(str(pipeline_db)) as con:
        pid, calc_id = con.execute("SELECT product_id,calc_id FROM orders WHERE run_id=? LIMIT 1", [rid]).fetchone()
        con.execute("DELETE FROM decisions WHERE run_id=?", [rid])
        con.execute("UPDATE orders SET status='approved' WHERE run_id=?", [rid])
        con.execute("INSERT INTO decisions VALUES ('D1', ?, ?, 'pending', 4, 4, '{}')", [rid, pid])
        con.execute("UPDATE orders SET status='pending' WHERE run_id=? AND product_id=?", [rid, pid])
        lid = con.execute("SELECT listing_id FROM supplier_listings LIMIT 1").fetchone()[0]
        review = json.dumps({"decision": "keep_separate", "reason": "Different product"})
        con.execute("INSERT INTO match_reviews VALUES ('R1', ?, ?, ?, 'pending', ?)", [rid, lid, pid, review])
        con.execute("INSERT INTO traces VALUES ('T1', ?, 'test', ?, 'review', 0, 0, 0, ?)",
                    [rid, pid, json.dumps({"trace": [{"name": "validate", "type": "rules"}]})])
    metrics = {"waste_pct": .05, "stockout_pct": .1, "lost_sales_usd": 100, "margin_usd": 200,
               "total_waste": 10}
    (tmp_path / "eval.json").write_text(json.dumps({"G5_waste_and_stockout_vs_baseline": {
        "freshpilot": metrics, "baseline": {**metrics, "waste_pct": .1, "margin_usd": 100}}}))
    client = TestClient(create_app(pipeline_db, tmp_path))
    client.headers["X-FreshPilot"] = "1"
    assert client.post("/api/auth/login", json={
        "email": "admin@freshpilot.demo", "password": "FreshPilot-demo!",
    }).status_code == 200
    for path in ("summary/today", "departments", "activity", "decisions", "items", f"items/{pid}",
                 "orders", "catalog/summary", "catalog/reviews", "impact", "obs/pipeline", "obs/traces",
                 "obs/model", "obs/events"):
        response = client.get("/api/"+path)
        assert response.status_code == 200, (path, response.text)
    assert client.get("/api/items/unknown").status_code == 404
    review_items = client.get("/api/items").json()["items"]
    assert next(item for item in review_items if item["product_id"] == pid)["decision_id"] == "D1"
    assert all(item["decision_id"] is None for item in review_items if item["product_id"] != pid)
    assert client.get("/api/items?page=0").status_code == 422
    assert client.post("/api/orders/send").status_code == 409
    assert client.post("/api/decisions/D1/adjust", json={"cases": 7}).status_code == 422
    adjusted = client.post("/api/decisions/D1/adjust", json={"cases": 7, "reason": "Special event"})
    assert adjusted.json()["status"] == "needs_category_approval"
    assert adjusted.json()["calc_id"].startswith("CALC-manager_override")
    assert client.post("/api/orders/send").status_code == 409
    assert client.post("/api/decisions/D1/undo").json()["status"] == "pending"
    assert client.post("/api/decisions/D1/approve").json()["status"] == "approved"
    # A resolved flagged item must still link to Decisions, never the auto-approved override form.
    resolved_item = client.get("/api/items", params={"q": pid}).json()["items"][0]
    assert resolved_item["decision_id"] == "D1" and resolved_item["status"] == "approved"
    assert client.post("/api/orders/send").json()["sent"] == 3
    assert client.post("/api/orders/send").json()["sent"] == 0
    assert client.post("/api/decisions/D1/undo").status_code == 409
    assert client.post("/api/catalog/reviews/unknown", json={"action": "accept"}).status_code == 404
    assert client.get("/api/obs/traces/unknown").status_code == 404
    assert client.get("/api/obs/traces/T1").json()["trace"][0]["name"] == "validate"
    assert client.post("/api/catalog/reviews/R1", json={"action": "accept"}).json()["status"] == "accept"
    assert client.post("/api/catalog/reviews/R1", json={"action": "reject"}).status_code == 409
    chat = client.post("/api/chat", json={"message": "Change my order"})
    assert chat.status_code == 200 and "text/event-stream" in chat.headers["content-type"]
    assert "sources" in chat.text and "Approve" in chat.text
    shelf = client.post("/api/chat", json={"message": "Which items need a shelf check?"})
    assert "sources" in shelf.text and "shelf check" in shelf.text
    product = client.post("/api/chat", json={"message": "Why this item?", "product_id": pid})
    assert calc_id in product.text and "cases" in product.text
    unrelated = client.post("/api/chat", json={"message": "What is the capital of Delhi?"})
    assert "outside my scope" in unrelated.text
    assert rid not in unrelated.text
    lost_sales = client.post("/api/chat", json={"message": "How did we avoid lost sales?"})
    assert "baseline minus" in lost_sales.text and "synthetic demand" in lost_sales.text
    assert client.post("/api/pipeline/run", json={"date": "2020-01-01"}).status_code == 422
    rerun = client.post("/api/pipeline/run", json={"date": "2025-06-30"})
    assert rerun.status_code == 200 and rerun.json()["items"] == 3
