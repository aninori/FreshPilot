"""Named local-demo sessions, fixed roles, and durable access/audit records.

The dataset represents one store. Membership gates that entire dataset; it does
not pretend that the global product/run tables are a multi-store data model.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import uuid
from contextvars import ContextVar
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

actor: ContextVar[Any] = ContextVar("actor", default=None)
Role = Literal["admin", "manager", "staff"]


class Login(BaseModel):
    email: str
    password: str


class Membership(BaseModel):
    role: Role
    stores: list[str]
    departments: list[str] = Field(default_factory=list)


class NewUser(Membership):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=10, max_length=200)


class Rules(BaseModel):
    override_low: float = Field(ge=0, lt=1, allow_inf_nan=False)
    override_high: float = Field(gt=1, le=10, allow_inf_nan=False)


def password_hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200000).hex()


def ensure_access(con: Any) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS app_users (
        id VARCHAR PRIMARY KEY, name VARCHAR, email VARCHAR UNIQUE, role VARCHAR,
        stores VARCHAR, departments VARCHAR, salt VARCHAR, password_hash VARCHAR)""")
    con.execute("""CREATE TABLE IF NOT EXISTS access_audit (
        id VARCHAR PRIMARY KEY, created_at TIMESTAMP DEFAULT current_timestamp,
        user_id VARCHAR, user_name VARCHAR, role VARCHAR, action VARCHAR,
        subject VARCHAR, details VARCHAR)""")
    con.execute("CREATE TABLE IF NOT EXISTS app_rules (id INTEGER PRIMARY KEY, payload VARCHAR)")
    con.execute(
        "INSERT INTO app_rules VALUES (1, ?) ON CONFLICT DO NOTHING",
        [json.dumps({"override_low": 0.6, "override_high": 1.5})],
    )
    con.execute("""CREATE TABLE IF NOT EXISTS shelf_tasks (
        id VARCHAR PRIMARY KEY, run_id VARCHAR, product_id VARCHAR, assignee VARCHAR,
        status VARCHAR, counted_qty DOUBLE, damaged_qty DOUBLE, note VARCHAR,
        created_by VARCHAR, created_at TIMESTAMP, completed_at TIMESTAMP)""")


def audit(con: Any, action: str, subject: str, details: Any) -> None:
    user = actor.get() or {}
    con.execute(
        "INSERT INTO access_audit VALUES (?, current_timestamp, ?, ?, ?, ?, ?, ?)",
        [
            uuid.uuid4().hex,
            user.get("id"),
            user.get("name"),
            user.get("role"),
            action,
            subject,
            json.dumps(details, default=str),
        ],
    )


def outside_band(con: Any, cases: int, recommended: int) -> bool:
    rules = json.loads(con.execute("SELECT payload FROM app_rules WHERE id=1").fetchone()[0])
    return (
        cases > 0 if recommended <= 0 else not (rules["override_low"] <= cases / recommended <= rules["override_high"])
    )


def install_access(app: FastAPI, db: Any, rows: Any) -> None:
    sessions: dict[str, tuple[str, float]] = {}
    attempts: dict[str, list[float]] = {}

    def public(user: dict[str, Any]) -> dict[str, Any]:
        return {
            **{k: user[k] for k in ("id", "name", "email", "role")},
            "stores": json.loads(user["stores"]),
            "departments": json.loads(user["departments"]),
        }

    def bootstrap() -> None:
        with db(write=True) as con:
            stores = [r[0] for r in con.execute("SELECT store_id FROM stores").fetchall()]
            for uid, name, role in [
                ("admin", "Morgan Lee", "admin"),
                ("manager", "Maria Reyes", "manager"),
                ("staff", "Priya Shah", "staff"),
            ]:
                if not con.execute("SELECT 1 FROM app_users WHERE id=?", [uid]).fetchone():
                    salt = secrets.token_hex(16)
                    con.execute(
                        "INSERT INTO app_users VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [
                            uid,
                            name,
                            role + "@freshpilot.demo",
                            role,
                            json.dumps(stores),
                            "[]",
                            salt,
                            password_hash("FreshPilot-demo!", salt),
                        ],
                    )

    @app.middleware("http")
    async def authorize(request: Request, call_next: Any) -> Any:
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        if path == "/api/auth/login":
            if request.headers.get("x-freshpilot") != "1":
                return JSONResponse({"detail": "Invalid request origin."}, status_code=403)
            return await call_next(request)
        session = sessions.get(request.cookies.get("freshpilot_session", ""))
        if not session or session[1] < time.time():
            return JSONResponse({"detail": "Sign in to FreshPilot."}, status_code=401)
        with db() as con:
            found = rows(con, "SELECT * FROM app_users WHERE id=?", [session[0]])
            store_ids = {r[0] for r in con.execute("SELECT store_id FROM stores").fetchall()}
        if not found:
            return JSONResponse({"detail": "Account unavailable."}, status_code=401)
        user = public(found[0])
        # A single-store snapshot cannot be safely partially filtered.
        if not path.startswith("/api/auth/") and not store_ids.issubset(set(user["stores"])):
            return JSONResponse({"detail": "You are not assigned to this store."}, status_code=403)
        role = user["role"]
        allowed = role == "admin" or path.startswith((
            "/api/auth/", "/api/summary/", "/api/departments", "/api/activity", "/api/decisions",
            "/api/items", "/api/orders", "/api/catalog", "/api/impact", "/api/tasks", "/api/chat",
        ))
        if path.startswith(("/api/team", "/api/settings", "/api/audit", "/api/approvals", "/api/obs", "/api/pipeline")):
            allowed = role == "admin"
        elif path.startswith("/api/catalog") and request.method != "GET":
            allowed = role == "admin"
        elif role == "staff":
            allowed = path.startswith(("/api/auth/", "/api/tasks", "/api/chat"))
        if not allowed:
            return JSONResponse({"detail": "Your role does not allow this action."}, status_code=403)
        # Mutations use a same-origin custom header (including streaming chat).
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-freshpilot") != "1":
            return JSONResponse({"detail": "Invalid request origin."}, status_code=403)
        token = actor.set(user)
        try:
            return await call_next(request)
        finally:
            actor.reset(token)

    @app.post("/api/auth/login")
    def login(body: Login, request: Request, response: Response) -> Any:
        bootstrap()
        key = request.client.host if request.client else "local"
        now = time.time()
        attempts[key] = [t for t in attempts.get(key, []) if t > now - 60]
        if len(attempts[key]) >= 10:
            raise HTTPException(429, "Too many sign-in attempts. Try again in a minute.")
        attempts[key].append(now)
        with db() as con:
            found = rows(con, "SELECT * FROM app_users WHERE email=?", [body.email.strip().lower()])
        user = found[0] if found else None
        digest = password_hash(body.password, user["salt"] if user else "unknown")
        if not user or not hmac.compare_digest(digest, user["password_hash"]):
            raise HTTPException(401, "Email or password is incorrect.")
        attempts.pop(key, None)
        old = request.cookies.get("freshpilot_session", "")
        sessions.pop(old, None)
        token = secrets.token_urlsafe(32)
        sessions[token] = (user["id"], now + 8 * 3600)
        response.set_cookie(
            "freshpilot_session",
            token,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            max_age=8 * 3600,
        )
        return public(user)

    @app.get("/api/auth/me")
    def me() -> Any:
        return actor.get()

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response) -> Any:
        sessions.pop(request.cookies.get("freshpilot_session", ""), None)
        response.delete_cookie("freshpilot_session")
        return {"ok": True}

    @app.get("/api/team")
    def team() -> Any:
        with db() as con:
            return {
                "users": [public(u) for u in rows(con, "SELECT * FROM app_users ORDER BY name")],
                "stores": rows(con, "SELECT * FROM stores"),
                "departments": rows(con, "SELECT * FROM departments"),
            }

    def validate_membership(con: Any, body: Membership) -> None:
        stores = {r[0] for r in con.execute("SELECT store_id FROM stores").fetchall()}
        depts = {r[0] for r in con.execute("SELECT dept_id FROM departments").fetchall()}
        if not set(body.stores) <= stores or not set(body.departments) <= depts:
            raise HTTPException(422, "Unknown store or department.")

    @app.post("/api/team/{user_id}")
    def update_member(user_id: str, body: Membership) -> Any:
        with db(write=True) as con:
            validate_membership(con, body)
            if user_id == actor.get()["id"]:
                raise HTTPException(409, "Ask another admin to change your own access.")
            before = rows(con, "SELECT * FROM app_users WHERE id=?", [user_id])
            if not before:
                raise HTTPException(404, "User not found.")
            open_tasks = rows(
                con,
                """SELECT p.dept_id FROM shelf_tasks t JOIN products p USING(product_id)
                WHERE t.assignee=? AND t.status='open'""",
                [user_id],
            )
            if open_tasks and (
                body.role != "staff"
                or not body.stores
                or (body.departments and any(t["dept_id"] not in body.departments for t in open_tasks))
            ):
                raise HTTPException(409, "Reassign this user's open tasks before removing their access.")
            con.execute(
                "UPDATE app_users SET role=?,stores=?,departments=? WHERE id=?",
                [body.role, json.dumps(body.stores), json.dumps(body.departments), user_id],
            )
            audit(con, "access_changed", user_id, {"before": public(before[0]), "after": body.model_dump()})
        for token, session in list(sessions.items()):
            if session[0] == user_id:
                del sessions[token]
        return {"ok": True}

    @app.post("/api/team")
    def create_member(body: NewUser) -> Any:
        with db(write=True) as con:
            validate_membership(con, body)
            email = body.email.strip().lower()
            if "@" not in email or con.execute("SELECT 1 FROM app_users WHERE email=?", [email]).fetchone():
                raise HTTPException(422, "Supply a unique email address.")
            uid, salt = uuid.uuid4().hex, secrets.token_hex(16)
            con.execute(
                "INSERT INTO app_users VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    uid,
                    body.name.strip(),
                    email,
                    body.role,
                    json.dumps(body.stores),
                    json.dumps(body.departments),
                    salt,
                    password_hash(body.password, salt),
                ],
            )
            audit(con, "user_created", uid, body.model_dump(exclude={"password"}))
            return {"id": uid}

    @app.get("/api/settings")
    def get_rules() -> Any:
        with db() as con:
            return json.loads(con.execute("SELECT payload FROM app_rules WHERE id=1").fetchone()[0])

    @app.post("/api/settings")
    def set_rules(body: Rules) -> Any:
        with db(write=True) as con:
            before = json.loads(con.execute("SELECT payload FROM app_rules WHERE id=1").fetchone()[0])
            con.execute("UPDATE app_rules SET payload=? WHERE id=1", [body.model_dump_json()])
            audit(con, "rules_changed", "manager_override_band", {"before": before, "after": body.model_dump()})
        return body

    @app.get("/api/audit")
    def get_audit() -> Any:
        with db() as con:
            return rows(con, "SELECT * FROM access_audit ORDER BY created_at DESC LIMIT 200")
