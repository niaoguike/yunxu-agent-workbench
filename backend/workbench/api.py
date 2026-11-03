import asyncio
import hashlib
import hmac
import json
import os
import secrets
import shutil
import sqlite3
import zipfile
import tempfile
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings
from .files import digest, import_zip, inventory, safe_path
from .runtime import RuntimeManager
from .store import Store, now, uid
from .workflow import STAGES, Workflow


class Credentials(BaseModel):
    email: str = Field(
        min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$"
    )
    password: str = Field(min_length=10, max_length=256)


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    instructions: str = Field(default="", max_length=16000)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    instructions: str | None = Field(default=None, max_length=16000)
    memory_enabled: bool | None = None


class TaskInput(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    mode: Literal["free", "development"] = "free"
    engine: Literal["codex", "claude"] = "codex"
    model: str | None = Field(default=None, max_length=120)
    require_acceptance: bool = False


class TaskPatch(BaseModel):
    mode: Literal["free", "development"] | None = None
    engine: Literal["codex", "claude"] | None = None
    model: str | None = Field(default=None, max_length=120)
    require_acceptance: bool | None = None


class Content(BaseModel):
    content: str = Field(min_length=1, max_length=100000)


class GateDecision(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str = Field(default="", max_length=16000)


class ApprovalDecision(BaseModel):
    decision: Literal["accept", "decline"]


class MemoryInput(BaseModel):
    category: Literal["user", "feedback", "project", "reference"] = "project"
    content: str = Field(min_length=1, max_length=4000)


class ExtensionInput(BaseModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[\w-]+$")
    kind: Literal["skill", "mcp"] = "skill"
    content: str = Field(min_length=1, max_length=32000)
    enabled: bool = True


class MemberInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=2000)
    engine: Literal["codex", "claude"] = "codex"


class FileInput(BaseModel):
    content: str = Field(max_length=1000000)


class IntegrateInput(BaseModel):
    path: str = Field(min_length=1, max_length=500)


class RepositoryInput(BaseModel):
    url: str = Field(min_length=1, max_length=300)


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return (
        salt
        + ":"
        + hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 310000).hex()
    )


def create_app(settings: Settings | None = None, runtime=None):
    settings = settings or Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(settings.data_dir / "workbench.sqlite3")
    runtime = runtime or RuntimeManager(settings)
    workflow = Workflow(store, settings, runtime)

    @asynccontextmanager
    async def lifespan(app):
        workflow.recover()
        yield
        await workflow.close()
        store.connection.close()

    app = FastAPI(title="Agent Workbench", lifespan=lifespan)
    app.state.settings, app.state.store, app.state.workflow = settings, store, workflow

    @app.middleware("http")
    async def security(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != settings.origin.rstrip("/"):
                return Response("Cross-origin mutation rejected", status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return Response("Cross-site mutation rejected", status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    async def user(request: Request):
        token = request.cookies.get("workbench_session", "")
        found = store.one(
            "SELECT u.id,u.email,u.instructions FROM users u JOIN sessions s ON u.id=s.user_id WHERE s.token=? AND s.expires>?",
            (hashlib.sha256(token.encode()).hexdigest(), now()),
        )
        if not found:
            raise HTTPException(401, "请先登录")
        return found

    def project_owned(project_id, owner):
        project = store.one(
            "SELECT * FROM projects WHERE id=? AND owner_id=? AND deleted_at IS NULL",
            (project_id, owner["id"]),
        )
        if not project:
            raise HTTPException(404, "项目不存在")
        return project

    def task_owned(task_id, owner):
        task = store.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not task:
            raise HTTPException(404, "任务不存在")
        project_owned(task["project_id"], owner)
        return task

    def child_owned(table, item_id, owner):
        item = store.one(f"SELECT * FROM {table} WHERE id=?", (item_id,))
        if not item:
            raise HTTPException(404, "记录不存在")
        project_owned(item["project_id"], owner)
        return item

    def project_workspace(project_id):
        root = settings.data_dir / "workspaces" / project_id / "main"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def checked_path(root, path):
        try:
            return safe_path(root, path)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

    def session(response, account):
        token = secrets.token_urlsafe(32)
        store.execute(
            "INSERT INTO sessions VALUES(?,?,?)",
            (
                hashlib.sha256(token.encode()).hexdigest(),
                account["id"],
                now() + 7 * 86400,
            ),
        )
        response.set_cookie(
            "workbench_session",
            token,
            httponly=True,
            secure=settings.secure_cookies,
            samesite="strict",
            max_age=7 * 86400,
        )
        return {key: account[key] for key in ("id", "email")}

    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "runner": settings.runner,
            "stages": [stage[0] for stage in STAGES],
        }

    @app.post("/api/auth/register", status_code=201)
    async def register(body: Credentials, response: Response):
        account = {"id": uid(), "email": body.email.strip().lower()}
        hashed = await asyncio.to_thread(password_hash, body.password)
        try:
            store.execute(
                "INSERT INTO users(id,email,password) VALUES(?,?,?)",
                (account["id"], account["email"], hashed),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "邮箱已注册")
        return session(response, account)

    @app.post("/api/auth/login")
    async def login(body: Credentials, response: Response):
        account = store.one(
            "SELECT * FROM users WHERE email=?", (body.email.strip().lower(),)
        )
        expected = account["password"] if account else "0" * 32 + ":" + "0" * 64
        actual = await asyncio.to_thread(
            password_hash, body.password, expected.split(":")[0]
        )
        if not hmac.compare_digest(actual, expected) or not account:
            raise HTTPException(401, "邮箱或密码错误")
        return session(response, account)

    @app.get("/api/auth/me")
    async def me(owner=Depends(user)):
        return owner

    @app.patch("/api/auth/me")
    async def update_me(body: FileInput, owner=Depends(user)):
        if len(body.content) > 16000:
            raise HTTPException(400, "指令过长")
        store.execute(
            "UPDATE users SET instructions=? WHERE id=?", (body.content, owner["id"])
        )
        return {"instructions": body.content}

    @app.post("/api/auth/logout")
    async def logout(request: Request, response: Response):
        token = request.cookies.get("workbench_session", "")
        store.execute(
            "DELETE FROM sessions WHERE token=?",
            (hashlib.sha256(token.encode()).hexdigest(),),
        )
        response.delete_cookie("workbench_session")
        return {"ok": True}

    @app.get("/api/engines")
    async def engines(owner=Depends(user)):
        return await runtime.readiness()

    @app.get("/api/projects")
    async def projects(owner=Depends(user)):
        return store.all(
            "SELECT * FROM projects WHERE owner_id=? AND deleted_at IS NULL ORDER BY created_at DESC",
            (owner["id"],),
        )

    @app.get("/api/trash")
    async def trash(owner=Depends(user)):
        return store.all(
            "SELECT * FROM projects WHERE owner_id=? AND deleted_at IS NOT NULL ORDER BY deleted_at DESC",
            (owner["id"],),
        )

    @app.delete("/api/projects/{project_id}")
    async def delete_project(project_id: str, owner=Depends(user)):
        project_owned(project_id, owner)
        await stop_project(project_id)
        store.execute(
            "UPDATE projects SET deleted_at=? WHERE id=?", (now(), project_id)
        )
        return {"ok": True, "retained": True}

    @app.post("/api/projects/{project_id}/restore")
    async def restore_project(project_id: str, owner=Depends(user)):
        if not store.one(
            "SELECT id FROM projects WHERE id=? AND owner_id=? AND deleted_at IS NOT NULL",
            (project_id, owner["id"]),
        ):
            raise HTTPException(404, "回收区项目不存在")
        store.execute("UPDATE projects SET deleted_at=NULL WHERE id=?", (project_id,))
        return project_owned(project_id, owner)

    @app.post("/api/projects", status_code=201)
    async def create_project(body: ProjectInput, owner=Depends(user)):
        project_id = uid()
        store.execute(
            "INSERT INTO projects(id,owner_id,name,instructions,created_at) VALUES(?,?,?,?,?)",
            (project_id, owner["id"], body.name, body.instructions, now()),
        )
        project_workspace(project_id)
        return project_owned(project_id, owner)

    @app.get("/api/projects/{project_id}")
    async def get_project(project_id: str, owner=Depends(user)):
        return project_owned(project_id, owner)

    @app.patch("/api/projects/{project_id}")
    async def update_project(project_id: str, body: ProjectPatch, owner=Depends(user)):
        project_owned(project_id, owner)
        values = body.model_dump(exclude_none=True)
        for key, value in values.items():
            store.execute(
                f"UPDATE projects SET {key}=? WHERE id=?", (value, project_id)
            )
        await stop_project(project_id)
        return project_owned(project_id, owner)

    @app.get("/api/projects/{project_id}/tasks")
    async def tasks(project_id: str, owner=Depends(user)):
        project_owned(project_id, owner)
        return store.all(
            "SELECT * FROM tasks WHERE project_id=? ORDER BY created_at DESC",
            (project_id,),
        )

    def insert_task(project_id, body, member_id=None):
        task_id = uid()
        store.execute(
            "INSERT INTO tasks(id,project_id,title,mode,engine,model,member_id,created_at,require_acceptance) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                task_id,
                project_id,
                body.title,
                body.mode,
                body.engine,
                body.model or None,
                member_id,
                now(),
                body.require_acceptance,
            ),
        )
        return store.one("SELECT * FROM tasks WHERE id=?", (task_id,))

    @app.post("/api/projects/{project_id}/tasks", status_code=201)
    async def create_task(project_id: str, body: TaskInput, owner=Depends(user)):
        project_owned(project_id, owner)
        return insert_task(project_id, body)

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str, owner=Depends(user)):
        task = task_owned(task_id, owner)
        for key in (
            "messages",
            "runs",
            "gates",
            "approvals",
            "artifacts",
            "pending_inputs",
            "checks",
        ):
            task[key] = store.all(
                f"SELECT * FROM {key} WHERE task_id=? ORDER BY "
                + ("id" if key == "approvals" else "created_at"),
                (task_id,),
            )
        for approval in task["approvals"]:
            approval["details"] = json.loads(approval["details"])
        return task

    @app.patch("/api/tasks/{task_id}")
    async def update_task(task_id: str, body: TaskPatch, owner=Depends(user)):
        task_owned(task_id, owner)
        if store.one(
            "SELECT id FROM runs WHERE task_id=? AND status IN ('queued','running','stopping')",
            (task_id,),
        ):
            raise HTTPException(409, "先停止当前执行，再修改模式或执行器")
        for key, value in body.model_dump(exclude_none=True).items():
            store.execute(f"UPDATE tasks SET {key}=? WHERE id=?", (value, task_id))
        store.execute(
            "UPDATE tasks SET version=version+1,stage=0,session_id=NULL,status='idle' WHERE id=?",
            (task_id,),
        )
        store.execute(
            "UPDATE gates SET status='invalidated' WHERE task_id=? AND status IN ('pending','approved')",
            (task_id,),
        )
        store.event(
            task_id,
            "configuration_changed",
            {
                **body.model_dump(exclude_none=True),
                "handoff": "New native session; platform message history retained",
            },
        )
        return store.one("SELECT * FROM tasks WHERE id=?", (task_id,))

    @app.post("/api/tasks/{task_id}/messages", status_code=201)
    async def send(task_id: str, body: Content, owner=Depends(user)):
        task = task_owned(task_id, owner)
        active = store.one(
            "SELECT id FROM runs WHERE task_id=? AND status IN ('queued','running','stopping')",
            (task_id,),
        )
        if active:
            if (
                store.one(
                    "SELECT COUNT(*) AS count FROM pending_inputs WHERE task_id=? AND status IN ('queued','paused')",
                    (task_id,),
                )["count"]
                >= 20
            ):
                raise HTTPException(429, "单任务最多保留 20 条待执行补充")
            message = {
                "id": uid(),
                "task_id": task_id,
                "role": "user",
                "content": body.content,
                "created_at": now(),
                "status": "queued",
            }
            store.execute(
                "INSERT INTO pending_inputs(id,task_id,content,created_at) VALUES(?,?,?,?)",
                (message["id"], task_id, body.content, message["created_at"]),
            )
            store.event(task_id, "input_queued", message)
            return message
        available = next(
            (e for e in await runtime.readiness() if e["engine"] == task["engine"]),
            None,
        )
        if not available or not available["available"]:
            raise HTTPException(
                409, available["reason"] if available else "执行器未配置"
            )
        message = {
            "id": uid(),
            "task_id": task_id,
            "role": "user",
            "content": body.content,
            "created_at": now(),
        }
        with store.transaction() as db:
            db.execute(
                "INSERT INTO messages VALUES(:id,:task_id,:role,:content,:created_at)",
                message,
            )
            if task["status"] != "idle":
                db.execute(
                    "UPDATE tasks SET version=version+1,stage=0,session_id=NULL WHERE id=?",
                    (task_id,),
                )
            db.execute(
                "UPDATE gates SET status='invalidated' WHERE task_id=? AND status IN ('pending','approved')",
                (task_id,),
            )
        workflow.enqueue(task_id)
        return message

    @app.patch("/api/tasks/{task_id}/inputs/{input_id}")
    async def edit_input(
        task_id: str, input_id: str, body: Content, owner=Depends(user)
    ):
        task_owned(task_id, owner)
        changed = store.execute(
            "UPDATE pending_inputs SET content=? WHERE id=? AND task_id=? AND status IN ('queued','paused')",
            (body.content, input_id, task_id),
        )
        if not changed:
            raise HTTPException(409, "消息已派发或不存在")
        return {"ok": True}

    @app.delete("/api/tasks/{task_id}/inputs/{input_id}")
    async def withdraw_input(task_id: str, input_id: str, owner=Depends(user)):
        task_owned(task_id, owner)
        changed = store.execute(
            "UPDATE pending_inputs SET status='withdrawn' WHERE id=? AND task_id=? AND status IN ('queued','paused')",
            (input_id, task_id),
        )
        if not changed:
            raise HTTPException(409, "消息已派发或不存在")
        return {"ok": True}

    @app.post("/api/tasks/{task_id}/resume-queue")
    async def resume_queue(task_id: str, owner=Depends(user)):
        task_owned(task_id, owner)
        if store.one(
            "SELECT id FROM runs WHERE task_id=? AND status IN ('running','queued','stopping')",
            (task_id,),
        ):
            raise HTTPException(409, "任务仍在执行")
        pending = store.one(
            "SELECT * FROM pending_inputs WHERE task_id=? AND status IN ('paused','queued') ORDER BY created_at LIMIT 1",
            (task_id,),
        )
        if not pending:
            raise HTTPException(404, "没有待恢复消息")
        store.execute(
            "UPDATE pending_inputs SET status='queued' WHERE task_id=? AND status='paused'",
            (task_id,),
        )
        store.execute(
            "UPDATE pending_inputs SET status='dispatched' WHERE id=?", (pending["id"],)
        )
        store.execute(
            "INSERT INTO messages VALUES(?,?,?,?,?)",
            (pending["id"], task_id, "user", pending["content"], pending["created_at"]),
        )
        store.execute(
            "UPDATE gates SET status='invalidated' WHERE task_id=? AND status IN ('pending','approved')",
            (task_id,),
        )
        store.execute(
            "UPDATE tasks SET version=version+1,stage=0,session_id=NULL WHERE id=?",
            (task_id,),
        )
        workflow.enqueue(task_id)
        return {"ok": True}

    @app.post("/api/tasks/{task_id}/stop")
    async def stop(task_id: str, owner=Depends(user)):
        task_owned(task_id, owner)
        return await workflow.stop(task_id)

    @app.get("/api/tasks/{task_id}/events")
    async def events(task_id: str, after: int = 0, owner=Depends(user)):
        task_owned(task_id, owner)
        rows = store.all(
            "SELECT * FROM events WHERE task_id=? AND id>? ORDER BY id LIMIT 500",
            (task_id, after),
        )
        return [{**row, "data": json.loads(row["data"])} for row in rows]

    @app.get("/api/tasks/{task_id}/files")
    async def task_files(task_id: str, owner=Depends(user)):
        task = task_owned(task_id, owner)
        return inventory(workflow.workspace(task))

    @app.get("/api/tasks/{task_id}/file")
    async def task_file(task_id: str, path: str, owner=Depends(user)):
        task = task_owned(task_id, owner)
        target = checked_path(workflow.workspace(task), path)
        if not target.is_file():
            raise HTTPException(404, "文件不存在")
        return FileResponse(
            target, filename=target.name, media_type="application/octet-stream"
        )

    @app.post("/api/tasks/{task_id}/gates/{gate_id}")
    async def decide_gate(
        task_id: str, gate_id: str, body: GateDecision, owner=Depends(user)
    ):
        task = task_owned(task_id, owner)
        with store.transaction() as db:
            gate = db.execute(
                "SELECT * FROM gates WHERE id=? AND task_id=?", (gate_id, task_id)
            ).fetchone()
            if (
                not gate
                or gate["status"] != "pending"
                or gate["version"] != task["version"]
                or gate["stage"] != task["stage"]
            ):
                raise HTTPException(409, "确认已失效或已处理")
            db.execute(
                "UPDATE gates SET status=?,comment=? WHERE id=?",
                (
                    "approved" if body.decision == "approve" else "rejected",
                    body.comment,
                    gate_id,
                ),
            )
            if body.decision == "reject":
                db.execute(
                    "UPDATE tasks SET status='idle',version=version+1,stage=0,session_id=NULL WHERE id=?",
                    (task_id,),
                )
                db.execute(
                    "UPDATE gates SET status='invalidated' WHERE task_id=? AND status='approved'",
                    (task_id,),
                )
            elif task["mode"] == "free":
                db.execute("UPDATE tasks SET status='delivered' WHERE id=?", (task_id,))
            else:
                db.execute(
                    "UPDATE tasks SET stage=stage+1,status='idle' WHERE id=?",
                    (task_id,),
                )
            if body.comment:
                db.execute(
                    "INSERT INTO messages VALUES(?,?,?,?,?)",
                    (uid(), task_id, "user", "确认反馈：" + body.comment, now()),
                )
        if body.decision == "approve" and task["mode"] == "development":
            workflow.enqueue(task_id)
        return {"ok": True}

    @app.post("/api/tasks/{task_id}/approvals/{approval_id}")
    async def decide_approval(
        task_id: str, approval_id: str, body: ApprovalDecision, owner=Depends(user)
    ):
        task_owned(task_id, owner)
        approval = store.one(
            "SELECT * FROM approvals WHERE id=? AND task_id=?", (approval_id, task_id)
        )
        if not approval or approval["status"] != "pending":
            raise HTTPException(409, "审批不存在或已处理")
        await runtime.approve(approval["run_id"], approval["native_id"], body.decision)
        store.execute(
            "UPDATE approvals SET status=? WHERE id=?", (body.decision, approval_id)
        )
        store.event(
            task_id,
            "approval_decided",
            {
                "approval_id": approval["native_id"],
                "id": approval_id,
                "decision": body.decision,
            },
            approval["run_id"],
        )
        store.execute("UPDATE tasks SET status='running' WHERE id=?", (task_id,))
        return {"ok": True}

    @app.get("/api/projects/{project_id}/files")
    async def files(project_id: str, owner=Depends(user)):
        project_owned(project_id, owner)
        return inventory(project_workspace(project_id))

    @app.get("/api/projects/{project_id}/file")
    async def download(project_id: str, path: str, owner=Depends(user)):
        project_owned(project_id, owner)
        target = checked_path(project_workspace(project_id), path)
        if not target.is_file():
            raise HTTPException(404, "文件不存在")
        return FileResponse(
            target, filename=target.name, media_type="application/octet-stream"
        )

    def ensure_project_idle(project_id):
        active = store.one(
            "SELECT r.id FROM runs r JOIN tasks t ON t.id=r.task_id WHERE t.project_id=? AND r.status IN ('running','stopping','queued')",
            (project_id,),
        )
        if active:
            raise HTTPException(409, "项目正在执行，请先停止后修改文件")

    def invalidate_project_gates(project_id):
        with store.transaction() as db:
            db.execute(
                "UPDATE gates SET status='invalidated' WHERE task_id IN (SELECT id FROM tasks WHERE project_id=?) AND status IN ('pending','approved')",
                (project_id,),
            )
            db.execute(
                "UPDATE tasks SET version=version+1,stage=0,status='idle',session_id=NULL WHERE project_id=? AND status IN ('waiting_confirmation','delivered','review')",
                (project_id,),
            )

    @app.put("/api/projects/{project_id}/file")
    async def write_file(
        project_id: str, path: str, body: FileInput, owner=Depends(user)
    ):
        project_owned(project_id, owner)
        ensure_project_idle(project_id)
        target = checked_path(project_workspace(project_id), path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body.content, encoding="utf-8")
        invalidate_project_gates(project_id)
        return {"path": path, "size": target.stat().st_size}

    @app.post("/api/projects/{project_id}/upload")
    async def upload(
        project_id: str, file: UploadFile = File(...), owner=Depends(user)
    ):
        project_owned(project_id, owner)
        ensure_project_idle(project_id)
        raw = await file.read(settings.max_upload + 1)
        ensure_project_idle(project_id)
        if len(raw) > settings.max_upload:
            raise HTTPException(413, "文件超过 20MB 限制")
        root = project_workspace(project_id)
        if file.filename and file.filename.lower().endswith(".zip"):
            try:
                count = import_zip(root, raw, settings.max_upload * 5)
                invalidate_project_gates(project_id)
                return {"imported": count}
            except (ValueError, OSError, zipfile.BadZipFile) as error:
                raise HTTPException(400, str(error)) from error
        target = checked_path(root, file.filename or "upload")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        invalidate_project_gates(project_id)
        return {"path": target.relative_to(root).as_posix(), "size": len(raw)}

    @app.get("/api/artifacts/{artifact_id}")
    async def artifact(artifact_id: str, owner=Depends(user)):
        item = store.one("SELECT * FROM artifacts WHERE id=?", (artifact_id,))
        if not item:
            raise HTTPException(404, "成果不存在")
        task_owned(item["task_id"], owner)
        path = checked_path(
            settings.data_dir / "artifacts" / item["run_id"], item["path"]
        )
        return FileResponse(
            path, filename=path.name, media_type="application/octet-stream"
        )

    @app.post("/api/artifacts/{artifact_id}/restore")
    async def restore_artifact(
        artifact_id: str, body: IntegrateInput, owner=Depends(user)
    ):
        item = store.one("SELECT * FROM artifacts WHERE id=?", (artifact_id,))
        if not item:
            raise HTTPException(404, "历史成果不存在")
        task = task_owned(item["task_id"], owner)
        if body.path != item["path"]:
            raise HTTPException(400, "请明确确认要覆盖的原成果路径")
        ensure_project_idle(task["project_id"])
        source = checked_path(
            settings.data_dir / "artifacts" / item["run_id"], item["path"]
        )
        target = checked_path(workflow.workspace(task), item["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        invalidate_project_gates(task["project_id"])
        return {
            "path": item["path"],
            "scope": "one file only; external side effects unchanged",
        }

    @app.get("/api/projects/{project_id}/preview")
    async def preview(project_id: str, path: str, owner=Depends(user)):
        project_owned(project_id, owner)
        target = checked_path(project_workspace(project_id), path)
        if not target.is_file() or target.suffix.lower() not in {
            ".html",
            ".htm",
            ".txt",
            ".md",
            ".json",
            ".csv",
        }:
            raise HTTPException(400, "仅支持 HTML 和文本的隔离静态预览")
        if target.stat().st_size > 1000000:
            raise HTTPException(413, "预览文件过大")
        content = target.read_text(encoding="utf-8", errors="replace")
        if target.suffix.lower() not in {".html", ".htm"}:
            import html

            content = "<pre>" + html.escape(content) + "</pre>"
        return HTMLResponse(
            content,
            headers={
                "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'none'; base-uri 'none'",
                "X-Frame-Options": "SAMEORIGIN",
            },
        )

    @app.post("/api/projects/{project_id}/import-repository")
    async def import_repository(
        project_id: str, body: RepositoryInput, owner=Depends(user)
    ):
        project_owned(project_id, owner)
        ensure_project_idle(project_id)
        if not re.fullmatch(
            r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?/?", body.url
        ):
            raise HTTPException(400, "首版仅支持公开 GitHub HTTPS 仓库地址")
        if not shutil.which("git"):
            raise HTTPException(409, "服务器未安装 Git")
        with tempfile.TemporaryDirectory(dir=settings.data_dir) as temporary:
            destination = Path(temporary) / "source"
            process = await asyncio.create_subprocess_exec(
                "git",
                "-c",
                "credential.helper=",
                "-c",
                "core.hooksPath=",
                "clone",
                "--depth=1",
                "--no-recurse-submodules",
                "--",
                body.url,
                str(destination),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env={
                    **os.environ,
                    "GIT_TERMINAL_PROMPT": "0",
                    "GIT_LFS_SKIP_SMUDGE": "1",
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": os.devnull,
                },
            )
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), 90)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise HTTPException(408, "仓库导入超时")
            if process.returncode:
                raise HTTPException(
                    400, "公开仓库无法导入：" + stderr.decode(errors="replace")[-500:]
                )
            ensure_project_idle(project_id)
            files = inventory(destination)
            if sum(file["size"] for file in files) > settings.max_upload * 5:
                raise HTTPException(413, "仓库文件超过 100MB 限制")
            root = project_workspace(project_id)
            for file in files:
                source = checked_path(destination, file["path"])
                target = checked_path(root, file["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            invalidate_project_gates(project_id)
            return {"imported": len(files), "url": body.url}

    @app.get("/api/projects/{project_id}/memories")
    async def memories(project_id: str, q: str = "", owner=Depends(user)):
        project_owned(project_id, owner)
        return store.all(
            "SELECT * FROM memories WHERE project_id=? AND content LIKE ? ORDER BY created_at DESC",
            (project_id, "%" + q + "%"),
        )

    @app.post("/api/projects/{project_id}/memories", status_code=201)
    async def create_memory(project_id: str, body: MemoryInput, owner=Depends(user)):
        project_owned(project_id, owner)
        memory_id = uid()
        store.execute(
            "INSERT INTO memories(id,project_id,category,content,created_at) VALUES(?,?,?,?,?)",
            (memory_id, project_id, body.category, body.content, now()),
        )
        return store.one("SELECT * FROM memories WHERE id=?", (memory_id,))

    @app.patch("/api/memories/{memory_id}")
    async def update_memory(memory_id: str, body: MemoryInput, owner=Depends(user)):
        item = child_owned("memories", memory_id, owner)
        store.execute(
            "UPDATE memories SET category=?,content=?,revision=revision+1 WHERE id=?",
            (body.category, body.content, memory_id),
        )
        await stop_project(item["project_id"])
        return store.one("SELECT * FROM memories WHERE id=?", (memory_id,))

    @app.delete("/api/memories/{memory_id}")
    async def delete_memory(memory_id: str, owner=Depends(user)):
        item = child_owned("memories", memory_id, owner)
        store.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        await stop_project(item["project_id"])
        return {"ok": True}

    async def stop_project(project_id):
        for task in store.all(
            "SELECT * FROM tasks WHERE project_id=? AND status IN ('queued','running','waiting_permission','stopping')",
            (project_id,),
        ):
            await workflow.stop(task["id"])
        # Configuration revocation must not resume a session carrying the old tool configuration.
        store.execute(
            "UPDATE tasks SET session_id=NULL WHERE project_id=?", (project_id,)
        )
        invalidate_project_gates(project_id)

    def validate_extension(body):
        if body.kind == "mcp":
            try:
                config = json.loads(body.content)
                if (
                    not isinstance(config, dict)
                    or not isinstance(config.get("command"), str)
                    or not isinstance(config.get("args", []), list)
                ):
                    raise ValueError(
                        "MCP 配置需要 command 和 args；仅支持执行环境内 stdio 服务"
                    )
                if (
                    not config["command"].strip()
                    or len(config.get("args", [])) > 100
                    or any(
                        not isinstance(argument, str)
                        for argument in config.get("args", [])
                    )
                ):
                    raise ValueError(
                        "MCP command 不能为空，args 必须是最多 100 项的字符串数组"
                    )
                if any(key not in {"command", "args", "env"} for key in config):
                    raise ValueError("MCP 配置仅允许 command、args 和 env")
                if config.get("env"):
                    raise ValueError(
                        "首版不接收明文 MCP 密钥；请在隔离执行环境配置服务身份"
                    )
            except (ValueError, TypeError) as error:
                raise HTTPException(400, str(error)) from error

    @app.get("/api/projects/{project_id}/extensions")
    async def extensions(project_id: str, owner=Depends(user)):
        project_owned(project_id, owner)
        return store.all(
            "SELECT * FROM extensions WHERE project_id=? ORDER BY created_at",
            (project_id,),
        )

    @app.post("/api/projects/{project_id}/extensions", status_code=201)
    async def create_extension(
        project_id: str, body: ExtensionInput, owner=Depends(user)
    ):
        project_owned(project_id, owner)
        validate_extension(body)
        if store.one(
            "SELECT id FROM extensions WHERE project_id=? AND name=?",
            (project_id, body.name),
        ):
            raise HTTPException(409, "项目内扩展名称已存在")
        extension_id = uid()
        store.execute(
            "INSERT INTO extensions(id,project_id,name,kind,content,enabled,created_at) VALUES(?,?,?,?,?,?,?)",
            (
                extension_id,
                project_id,
                body.name,
                body.kind,
                body.content,
                body.enabled,
                now(),
            ),
        )
        await stop_project(project_id)
        return store.one("SELECT * FROM extensions WHERE id=?", (extension_id,))

    @app.patch("/api/extensions/{extension_id}")
    async def update_extension(extension_id: str, body: dict, owner=Depends(user)):
        item = child_owned("extensions", extension_id, owner)
        unknown = set(body) - {"name", "kind", "content", "enabled"}
        if unknown:
            raise HTTPException(400, "未知扩展字段")
        try:
            parsed = ExtensionInput(**{**item, **body})
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        validate_extension(parsed)
        store.execute(
            "UPDATE extensions SET name=?,kind=?,content=?,enabled=?,revision=revision+1 WHERE id=?",
            (parsed.name, parsed.kind, parsed.content, parsed.enabled, extension_id),
        )
        await stop_project(item["project_id"])
        return store.one("SELECT * FROM extensions WHERE id=?", (extension_id,))

    @app.delete("/api/extensions/{extension_id}")
    async def delete_extension(extension_id: str, owner=Depends(user)):
        item = child_owned("extensions", extension_id, owner)
        store.execute("DELETE FROM extensions WHERE id=?", (extension_id,))
        await stop_project(item["project_id"])
        return {"ok": True}

    @app.get("/api/projects/{project_id}/members")
    async def members(project_id: str, owner=Depends(user)):
        project_owned(project_id, owner)
        return store.all(
            "SELECT * FROM members WHERE project_id=? ORDER BY created_at",
            (project_id,),
        )

    @app.post("/api/projects/{project_id}/members", status_code=201)
    async def create_member(project_id: str, body: MemberInput, owner=Depends(user)):
        project_owned(project_id, owner)
        member_id = uid()
        store.execute(
            "INSERT INTO members VALUES(?,?,?,?,?,?)",
            (member_id, project_id, body.name, body.role, body.engine, now()),
        )
        return store.one("SELECT * FROM members WHERE id=?", (member_id,))

    @app.post("/api/members/{member_id}/assign", status_code=201)
    async def assign(member_id: str, body: Content, owner=Depends(user)):
        member = child_owned("members", member_id, owner)
        ensure_project_idle(member["project_id"])
        task = insert_task(
            member["project_id"],
            TaskInput(
                title=f"{member['name']} · {body.content[:60]}", engine=member["engine"]
            ),
            member_id,
        )
        previous = store.one(
            "SELECT session_id FROM tasks WHERE member_id=? AND id!=? AND session_id IS NOT NULL ORDER BY created_at DESC LIMIT 1",
            (member_id, task["id"]),
        )
        if previous:
            store.execute(
                "UPDATE tasks SET session_id=? WHERE id=?",
                (previous["session_id"], task["id"]),
            )
        root = project_workspace(member["project_id"])
        destination = workflow.workspace(task)
        for file in inventory(root):
            source = checked_path(root, file["path"])
            target = checked_path(destination, file["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            store.execute(
                "INSERT INTO baselines VALUES(?,?,?)",
                (task["id"], file["path"], digest(source)),
            )
        store.execute(
            "INSERT INTO messages VALUES(?,?,?,?,?)",
            (uid(), task["id"], "user", member["role"] + "\n\n" + body.content, now()),
        )
        workflow.enqueue(task["id"])
        return task

    @app.post("/api/projects/{project_id}/integrate/{task_id}")
    async def integrate(
        project_id: str, task_id: str, body: IntegrateInput, owner=Depends(user)
    ):
        project_owned(project_id, owner)
        task = task_owned(task_id, owner)
        ensure_project_idle(project_id)
        if task["project_id"] != project_id or not task["member_id"]:
            raise HTTPException(400, "需要当前项目的协作者任务")
        root = project_workspace(project_id)
        source = checked_path(workflow.workspace(task), body.path)
        target = checked_path(root, body.path)
        baseline = store.one(
            "SELECT digest FROM baselines WHERE task_id=? AND path=?",
            (task_id, body.path),
        )
        if digest(target) != (baseline["digest"] if baseline else None):
            raise HTTPException(409, "主工作区文件已变化，请人工解决冲突")
        if not source.is_file():
            raise HTTPException(404, "协作者文件不存在")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        invalidate_project_gates(project_id)
        return {"path": body.path, "digest": digest(target)}

    frontend = settings.frontend_dir
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app
