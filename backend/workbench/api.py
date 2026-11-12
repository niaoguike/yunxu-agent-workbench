import asyncio
import csv
import io
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

from .automations import Routines, RoutineInput, ConnectionInput, next_due, token_names
from .charts import ChartConfig, ChartRevision, parse_csv, save_revision, ChartConflict
from .config import Settings
from .files import digest, import_zip, inventory, safe_path
from .runtime import RuntimeManager
from .store import Store, now, uid
from .workflow import STAGES, Workflow
from .orchestrator import Orchestrator


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
    mode: Literal["free"] = "free"
    engine: Literal["codex", "claude"] = "codex"
    model: str | None = Field(default=None, max_length=120)
    require_acceptance: bool = False
    allow_collaboration: bool = False
    permission_mode: Literal["auto", "ask"] = "auto"


class TaskPatch(BaseModel):
    mode: Literal["free"] | None = None
    engine: Literal["codex", "claude"] | None = None
    model: str | None = Field(default=None, max_length=120)
    require_acceptance: bool | None = None
    allow_collaboration: bool | None = None
    permission_mode: Literal["auto", "ask"] | None = None


class Content(BaseModel):
    content: str = Field(min_length=1, max_length=100000)


class AssignmentInput(Content):
    assignment_id: str | None = Field(default=None, min_length=1, max_length=160)


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


class PluginInput(BaseModel):
    name: str = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_-]+$")
    version: str = Field(min_length=1, max_length=40)
    source: str = Field(default="用户导入", max_length=500)
    extensions: list[ExtensionInput] = Field(min_length=1, max_length=10)


class ToggleInput(BaseModel):
    enabled: bool


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
    routines = Routines(store, workflow, runtime)
    orchestrator = Orchestrator(workflow)

    @asynccontextmanager
    async def lifespan(app):
        workflow.recover()
        routines.start()
        orchestrator.start()
        workflow.native_memory.start()
        try:
            yield
        finally:
            await workflow.native_memory.close()
            await orchestrator.close()
            await routines.close()
            await workflow.close()
        store.connection.close()

    app = FastAPI(title="Agent Workbench", lifespan=lifespan)
    app.state.settings, app.state.store, app.state.workflow = settings, store, workflow
    app.state.routines = routines

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
            "automation_error": routines.last_error or routines.delivery_error,
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

    @app.get("/api/resume")
    async def resume(owner=Depends(user)):
        return store.one(
            "SELECT t.id AS task_id,t.project_id FROM resume_positions r "
            "JOIN tasks t ON t.id=r.task_id JOIN projects p ON p.id=t.project_id "
            "WHERE r.user_id=? AND p.owner_id=? AND p.deleted_at IS NULL",
            (owner["id"], owner["id"]),
        )

    @app.put("/api/resume/{task_id}")
    async def save_resume(task_id: str, owner=Depends(user)):
        task_owned(task_id, owner)
        store.execute(
            "INSERT INTO resume_positions(user_id,task_id) VALUES(?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET task_id=excluded.task_id",
            (owner["id"], task_id),
        )
        return {"ok": True}

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

    @app.get("/api/projects/{project_id}/history/search")
    def search_history(project_id: str, q: str, limit: int = 20, owner=Depends(user)):
        project_owned(project_id, owner)
        from .history_search import HistorySearch
        try:
            return HistorySearch(store).search(project_id, q, limit)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

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
            "INSERT INTO tasks(id,project_id,title,mode,engine,model,member_id,created_at,require_acceptance,permission_mode,allow_collaboration) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
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
                body.permission_mode,
                body.allow_collaboration,
            ),
        )
        return store.one("SELECT * FROM tasks WHERE id=?", (task_id,))

    @app.post("/api/projects/{project_id}/tasks", status_code=201)
    async def create_task(project_id: str, body: TaskInput, owner=Depends(user)):
        project_owned(project_id, owner)
        return insert_task(project_id, body)

    def routine_owned(routine_id, owner):
        item = store.one("SELECT * FROM routines WHERE id=?", (routine_id,))
        if not item:
            raise HTTPException(404, "自动化不存在")
        project_owned(item["project_id"], owner)
        return item

    def connection_owned(connection_id, owner):
        item = routines.connection(connection_id)
        if not item:
            raise HTTPException(404, "连接不存在")
        project_owned(item["project_id"], owner)
        return item

    def check_destination(project_id, body):
        if body.connection_id:
            connection = routines.connection(body.connection_id)
            if not connection or connection["project_id"] != project_id:
                raise HTTPException(400, "请选择当前项目的渠道连接")

    @app.get("/api/projects/{project_id}/routines")
    async def list_routines(project_id: str, owner=Depends(user)):
        project_owned(project_id, owner)
        result = store.all(
            "SELECT * FROM routines WHERE project_id=? ORDER BY created_at DESC",
            (project_id,),
        )
        for item in result:
            item["config"] = json.loads(item["config"])
            item["runs"] = store.all(
                "SELECT rr.*,t.status AS task_status FROM routine_runs rr LEFT JOIN tasks t ON t.id=rr.task_id WHERE rr.routine_id=? ORDER BY rr.created_at DESC LIMIT 30",
                (item["id"],),
            )
        return result

    @app.post("/api/projects/{project_id}/routines", status_code=201)
    async def add_routine(project_id: str, body: RoutineInput, owner=Depends(user)):
        project_owned(project_id, owner)
        check_destination(project_id, body)
        routine_id = uid()
        config = body.model_dump()
        store.execute(
            "INSERT INTO routines VALUES(?,?,?,?,?,?)",
            (
                routine_id,
                project_id,
                json.dumps(config, ensure_ascii=False),
                body.enabled,
                next_due(config, now()),
                now(),
            ),
        )
        return {"id": routine_id}

    @app.put("/api/routines/{routine_id}")
    async def update_routine(routine_id: str, body: RoutineInput, owner=Depends(user)):
        item = routine_owned(routine_id, owner)
        check_destination(item["project_id"], body)
        config = body.model_dump()
        store.execute(
            "UPDATE routines SET config=?,enabled=?,next_due=? WHERE id=?",
            (
                json.dumps(config, ensure_ascii=False),
                body.enabled,
                next_due(config, now()),
                routine_id,
            ),
        )
        return {"ok": True}

    @app.post("/api/routines/{routine_id}/run", status_code=201)
    async def run_routine(routine_id: str, owner=Depends(user)):
        routine_owned(routine_id, owner)
        return await routines.fire(routine_id)

    @app.get("/api/projects/{project_id}/connections")
    async def list_connections(project_id: str, owner=Depends(user)):
        project_owned(project_id, owner)
        result = store.all(
            "SELECT * FROM channel_connections WHERE project_id=? ORDER BY created_at DESC",
            (project_id,),
        )
        for item in result:
            item.update(token_names(item["id"]))
            item["configured"] = routines.configured(item)
            if item["enabled"] and not item["configured"]:
                item["status"] = "missing_credentials"
            item["inbox"] = store.all(
                "SELECT * FROM channel_events WHERE connection_id=? ORDER BY created_at DESC LIMIT 20",
                (item["id"],),
            )
            item["outbox"] = store.all(
                "SELECT * FROM channel_outbox WHERE connection_id=? ORDER BY created_at DESC LIMIT 20",
                (item["id"],),
            )
        return result

    @app.post("/api/projects/{project_id}/connections", status_code=201)
    async def add_connection(
        project_id: str, body: ConnectionInput, owner=Depends(user)
    ):
        project_owned(project_id, owner)
        connection_id = uid()
        store.execute(
            "INSERT INTO channel_connections(id,project_id,name,provider,chat_id,user_id,engine,permission_mode,receive,send,enabled,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                connection_id,
                project_id,
                body.name,
                body.provider,
                body.chat_id,
                body.user_id,
                body.engine,
                body.permission_mode,
                body.receive,
                body.send,
                body.enabled,
                now(),
            ),
        )
        return {"id": connection_id, **token_names(connection_id)}

    @app.put("/api/connections/{connection_id}")
    async def update_connection(
        connection_id: str, body: ConnectionInput, owner=Depends(user)
    ):
        item = connection_owned(connection_id, owner)
        if body.provider != item["provider"]:
            raise HTTPException(400, "连接类型不能修改，请新建连接")
        # Changing sender/chat would reroute historical results. Use a new connection instead.
        if body.chat_id != item["chat_id"] or body.user_id != item["user_id"]:
            raise HTTPException(400, "聊天和用户不能修改，请新建连接以保留历史归属")
        for key, value in body.model_dump().items():
            store.execute(
                f"UPDATE channel_connections SET {key}=? WHERE id=?",
                (value, connection_id),
            )
        store.execute(
            "UPDATE channel_connections SET status=?,error='' WHERE id=?",
            ("connecting" if body.enabled else "disabled", connection_id),
        )
        job = routines.receivers.pop(connection_id, None)
        if job:
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
        return {"ok": True}

    @app.post("/api/connections/{connection_id}/check")
    async def check_connection(connection_id: str, owner=Depends(user)):
        item = connection_owned(connection_id, owner)
        try:
            method = "auth.test" if item["provider"] == "slack" else "getMe"
            await routines.transport.call(item, method)
            return {
                "ok": True,
                "detail": "机器人身份验证通过；接收和发送权限需实际配置后验证",
            }
        except Exception:
            raise HTTPException(
                409, "连接验证失败，请检查环境变量、网络和机器人权限"
            ) from None

    @app.post("/api/tasks/{task_id}/handoff/{connection_id}", status_code=201)
    async def handoff(task_id: str, connection_id: str, owner=Depends(user)):
        task = task_owned(task_id, owner)
        connection = connection_owned(connection_id, owner)
        if task["member_id"] or connection["project_id"] != task["project_id"]:
            raise HTTPException(400, "仅可交接当前项目的主任务")
        if (
            not connection["enabled"]
            or not connection["send"]
            or not routines.configured(connection)
        ):
            raise HTTPException(409, "请先配置并启用渠道及发送权限")
        store.execute(
            "INSERT OR IGNORE INTO channel_threads VALUES(?,?,?,?)",
            (connection_id, "handoff-" + task_id, task_id, ""),
        )
        store.execute(
            "INSERT OR IGNORE INTO channel_outbox VALUES(?,?,?,?,?,?,?,?)",
            (
                uid(),
                connection_id,
                task_id,
                (
                    f"{task['version']}:{task['status']}"
                    if task["status"]
                    in {"delivered", "completed", "failed", "interrupted", "review"}
                    else "handoff:" + str(task["version"])
                ),
                "pending",
                "",
                "",
                now(),
            ),
        )
        return {"status": "pending", "detail": "已排队，发送状态可在渠道记录查看"}

    @app.post("/api/outbox/{outbox_id}/retry")
    async def retry_outbox(outbox_id: str, owner=Depends(user)):
        item = store.one("SELECT * FROM channel_outbox WHERE id=?", (outbox_id,))
        if not item:
            raise HTTPException(404, "发送记录不存在")
        connection_owned(item["connection_id"], owner)
        if item["status"] != "failed":
            raise HTTPException(
                409, "仅明确失败的发送可重试；结果不明时请先检查外部聊天"
            )
        store.execute(
            "UPDATE channel_outbox SET status='pending',error='' WHERE id=?",
            (outbox_id,),
        )
        return {"ok": True}

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
        task = task_owned(task_id, owner)
        if store.one(
            "SELECT id FROM runs WHERE task_id=? AND status IN ('queued','running','stopping')",
            (task_id,),
        ):
            raise HTTPException(409, "先停止当前执行，再修改模式或执行器")
        values = body.model_dump(exclude_none=True)
        if "model" in values:
            values["model"] = values["model"] or None
        changes = {key: value for key, value in values.items() if task[key] != value}
        if not changes:
            return task
        for key, value in changes.items():
            store.execute(f"UPDATE tasks SET {key}=? WHERE id=?", (value, task_id))
        if set(changes) <= {"permission_mode", "allow_collaboration"}:
            store.execute("UPDATE tasks SET session_id=NULL WHERE id=?", (task_id,))
            store.event(
                task_id,
                "permission_mode_changed",
                {
                    "previous": task["permission_mode"],
                    "mode": changes.get("permission_mode", task["permission_mode"]),
                    "allow_collaboration": changes.get(
                        "allow_collaboration", task["allow_collaboration"]
                    ),
                    "effective": "next native run; workflow gates unchanged",
                },
            )
            return store.one("SELECT * FROM tasks WHERE id=?", (task_id,))
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

    def chart_owned(chart_id, owner):
        chart = store.one("SELECT * FROM charts WHERE id=?", (chart_id,))
        if not chart:
            raise HTTPException(404, "图表不存在")
        task_owned(chart["task_id"], owner)
        return chart

    @app.get("/api/tasks/{task_id}/charts")
    async def charts(task_id: str, owner=Depends(user)):
        task_owned(task_id, owner)
        result = store.all(
            "SELECT * FROM charts WHERE task_id=? ORDER BY created_at", (task_id,)
        )
        for chart in result:
            chart["data"] = json.loads(chart["data"])
            chart["versions"] = store.all(
                "SELECT * FROM chart_versions WHERE chart_id=? ORDER BY version",
                (chart["id"],),
            )
            for version in chart["versions"]:
                version["config"] = json.loads(version["config"])
        return result

    @app.post("/api/tasks/{task_id}/charts", status_code=201)
    async def create_chart(
        task_id: str, file: UploadFile = File(...), owner=Depends(user)
    ):
        task = task_owned(task_id, owner)
        if task["member_id"]:
            raise HTTPException(400, "请在主任务中生成图表")
        try:
            data = parse_csv(await file.read(2_000_001))
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        chart_id = uid()
        source = f"charts/{chart_id}.csv"
        # A private snapshot is the source of every later version, even if the file changes.
        root = project_workspace(task["project_id"])
        target = checked_path(root, source)
        target.parent.mkdir(parents=True, exist_ok=True)
        buffer = io.StringIO(newline="")
        csv.writer(buffer).writerows([data["columns"], *data["rows"]])
        target.write_text(buffer.getvalue(), encoding="utf-8")
        config = ChartConfig(
            title=(file.filename or "CSV 图表")[:160],
            x=data["columns"][0],
            y=data["numeric"][0],
        ).model_dump()
        with store.transaction() as db:
            db.execute(
                "INSERT INTO charts VALUES(?,?,?,?,?)",
                (
                    chart_id,
                    task_id,
                    source,
                    json.dumps(data, ensure_ascii=False),
                    now(),
                ),
            )
            db.execute(
                "INSERT INTO chart_versions VALUES(?,?,?,?)",
                (chart_id, 1, json.dumps(config, ensure_ascii=False), now()),
            )
            db.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?)",
                (
                    uid(),
                    task_id,
                    "user",
                    f"上传 CSV 并生成图表：{config['title']}（版本 1）",
                    now(),
                ),
            )
        store.event(task_id, "chart_created", {"chart_id": chart_id, "version": 1})
        return {"id": chart_id}

    @app.post("/api/charts/{chart_id}/versions", status_code=201)
    async def revise_chart(chart_id: str, body: ChartRevision, owner=Depends(user)):
        chart = chart_owned(chart_id, owner)
        try:
            return {"version": save_revision(store, chart, body)}
        except ChartConflict as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(400, str(error)) from error

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
        if body.kind == "mcp" and body.name == "workbench":
            raise HTTPException(400, "workbench 是保留的平台连接名称")
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

    @app.get("/api/projects/{project_id}/plugins")
    async def list_plugins(project_id: str, owner=Depends(user)):
        project_owned(project_id, owner)
        result = store.all(
            "SELECT * FROM plugins WHERE project_id=? ORDER BY created_at DESC",
            (project_id,),
        )
        for item in result:
            item["extensions"] = store.all(
                "SELECT e.* FROM extensions e JOIN plugin_extensions pe ON pe.extension_id=e.id WHERE pe.plugin_id=?",
                (item["id"],),
            )
        return result

    @app.post("/api/projects/{project_id}/plugins", status_code=201)
    async def install_plugin(project_id: str, body: PluginInput, owner=Depends(user)):
        project_owned(project_id, owner)
        if store.one(
            "SELECT id FROM plugins WHERE project_id=? AND name=? AND version=?",
            (project_id, body.name, body.version),
        ):
            raise HTTPException(409, "该版本插件已导入")
        entries = []
        for item in body.extensions:
            if len(body.name + "-" + item.name) > 80:
                raise HTTPException(400, "插件和扩展合并后的名称不能超过 80 字符")
            parsed = ExtensionInput(
                **{
                    **item.model_dump(),
                    "name": body.name + "-" + item.name,
                    "enabled": False,
                }
            )
            validate_extension(parsed)
            if parsed.name in [e.name for e in entries] or store.one(
                "SELECT id FROM extensions WHERE project_id=? AND name=?",
                (project_id, parsed.name),
            ):
                raise HTTPException(409, "插件扩展名称冲突")
            entries.append(parsed)
        plugin_id = uid()
        manifest = body.model_dump_json()
        with store.transaction() as db:
            db.execute(
                "INSERT INTO plugins VALUES(?,?,?,?,?,?,?,?)",
                (
                    plugin_id,
                    project_id,
                    body.name,
                    body.version,
                    body.source,
                    hashlib.sha256(manifest.encode()).hexdigest(),
                    manifest,
                    now(),
                ),
            )
            for item in entries:
                extension_id = uid()
                db.execute(
                    "INSERT INTO extensions(id,project_id,name,kind,content,enabled,created_at) VALUES(?,?,?,?,?,0,?)",
                    (
                        extension_id,
                        project_id,
                        item.name,
                        item.kind,
                        item.content,
                        now(),
                    ),
                )
                db.execute(
                    "INSERT INTO plugin_extensions VALUES(?,?)",
                    (plugin_id, extension_id),
                )
        return {
            "id": plugin_id,
            "detail": "已导入并保持停用，请查看内容后启用；未进行来源安全审核",
        }

    @app.put("/api/plugins/{plugin_id}/enabled")
    async def toggle_plugin(plugin_id: str, body: ToggleInput, owner=Depends(user)):
        item = child_owned("plugins", plugin_id, owner)
        store.execute(
            "UPDATE extensions SET enabled=?,revision=revision+1 WHERE id IN (SELECT extension_id FROM plugin_extensions WHERE plugin_id=?)",
            (body.enabled, plugin_id),
        )
        await stop_project(item["project_id"])
        return {"ok": True}

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

    @app.get("/api/projects/{project_id}/orchestrations")
    async def orchestration_list(project_id: str, owner=Depends(user)):
        project_owned(project_id, owner)
        rows = store.all("SELECT * FROM orchestrations WHERE project_id=? ORDER BY created_at DESC", (project_id,))
        for row in rows:
            row["data"] = json.loads(row["data"])
        return rows

    @app.post("/api/projects/{project_id}/orchestrations", status_code=201)
    async def orchestration_create(project_id: str, body: Content, owner=Depends(user)):
        project_owned(project_id, owner)
        try:
            return {"id": orchestrator.create(project_id, body.content, "codex")}
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/orchestrations/{oid}/{action}")
    async def orchestration_control(oid: str, action: str, owner=Depends(user)):
        row = child_owned("orchestrations", oid, owner)
        data = json.loads(row["data"])
        if action == "stop":
            orchestrator.save(row, "stopped", data)
            await runtime.stop("plan-"+oid)
            await workflow.stop(row["task_id"])
            store.execute("UPDATE tasks SET status='interrupted' WHERE id=? AND status='running'", (row["task_id"],))
        elif action == "resume" and row["state"] in {"paused", "conflict"}:
            store.execute("UPDATE tasks SET status='running' WHERE id=?", (row["task_id"],))
            for node in data['nodes']:
                if node['status'] == 'running':
                    task = store.one("SELECT status FROM tasks WHERE id=?", (node['task_id'],))
                    if task['status'] in {'paused','interrupted','failed'}:
                        workflow.enqueue(node['task_id'])
            orchestrator.save(row, "executing" if data['nodes'] else "planning", data)
        else:
            raise HTTPException(409, "当前状态不支持此操作")
        return {"ok": True}

    @app.get("/api/projects/{project_id}/native-sessions")
    async def native_sessions(project_id: str, owner=Depends(user)):
        project_owned(project_id, owner)
        return store.all("SELECT id,title,engine,session_id,member_id,parent_task_id,status FROM tasks WHERE project_id=? ORDER BY created_at DESC", (project_id,))

    @app.get("/api/projects/{project_id}/memory-sync")
    async def memory_sync_status(project_id: str, owner=Depends(user)):
        project = project_owned(project_id, owner)
        return {"enabled": bool(project['memory_enabled']), "interval_seconds": 2,
                "codex_account_enabled": settings.runner == "trusted-local",
                "codex_account_scope": "exact_workspace_rollout_summaries",
                "error": workflow.native_memory.errors.get(project_id),
                "sources": store.all("SELECT source,engine,digest,updated_at FROM native_memory_files WHERE project_id=? ORDER BY source", (project_id,))}

    @app.post("/api/projects/{project_id}/memory-import/{engine}")
    async def memory_import(project_id: str, engine: str, file: UploadFile = File(...), owner=Depends(user)):
        project = project_owned(project_id, owner)
        if engine not in {"claude", "codex"}:
            raise HTTPException(400, "未知执行器")
        if not project['memory_enabled']:
            raise HTTPException(409, "请先开启项目记忆")
        raw = await file.read(256001)
        if len(raw) > 256000:
            raise HTTPException(400, "记忆文件超过256KB")
        try:
            content = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise HTTPException(400, "记忆文件必须为UTF-8文本")
        imported = []
        for start in range(0, len(content), 4000):
            chunk = content[start:start+4000].strip()
            if not chunk:
                continue
            previous = store.one("SELECT id FROM memories WHERE project_id=? AND content=?", (project_id, chunk))
            mid = previous['id'] if previous else uid()
            if not previous:
                store.execute("INSERT INTO memories(id,project_id,category,content,created_at) VALUES(?,?,'reference',?,?)", (mid,project_id,chunk,now()))
            imported.append(mid)
        store.execute("CREATE TABLE IF NOT EXISTS memory_sources(memory_id TEXT,engine TEXT,filename TEXT,digest TEXT,PRIMARY KEY(memory_id,engine,digest))")
        for mid in imported:
            store.execute("INSERT OR IGNORE INTO memory_sources VALUES(?,?,?,?)", (mid,engine,(file.filename or '')[:240],hashlib.sha256(raw).hexdigest()))
        return {"memory_ids": imported, "engine": engine}

    @app.get("/api/projects/{project_id}/members")
    async def members(project_id: str, owner=Depends(user)):
        project_owned(project_id, owner)
        entries = store.all(
            "SELECT * FROM members WHERE project_id=? ORDER BY created_at",
            (project_id,),
        )
        for member in entries:
            latest = store.one(
                "SELECT id,status,parent_task_id FROM tasks WHERE member_id=? ORDER BY created_at DESC LIMIT 1",
                (member["id"],),
            )
            member.update(
                status=latest["status"] if latest else "idle",
                task_id=latest["id"] if latest else None,
                parent_task_id=latest["parent_task_id"] if latest else None,
            )
        return entries

    @app.post("/api/projects/{project_id}/members", status_code=201)
    async def create_member(project_id: str, body: MemberInput, owner=Depends(user)):
        project_owned(project_id, owner)
        try:
            return workflow.collaboration.create(
                project_id, body.name, body.role, body.engine
            )
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/members/{member_id}/assign", status_code=201)
    async def assign(member_id: str, body: AssignmentInput, owner=Depends(user)):
        member = child_owned("members", member_id, owner)
        if store.one(
            "SELECT id FROM tasks WHERE project_id=? AND member_id IS NULL AND status IN ('running','queued','stopping','waiting_permission')",
            (member["project_id"],),
        ):
            raise HTTPException(409, "主任务正在执行；请由主 Agent 指派或等待其停止")
        try:
            receipt = workflow.collaboration.dispatch(
                member, body.content, body.assignment_id or uid()
            )
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return {
            **store.one("SELECT * FROM tasks WHERE id=?", (receipt["task_id"],)),
            "dispatch": receipt,
        }

    @app.post("/api/projects/{project_id}/stop")
    async def stop_all(project_id: str, owner=Depends(user)):
        project_owned(project_id, owner)
        for task in store.all(
            "SELECT id FROM tasks WHERE project_id=? AND parent_task_id IS NULL",
            (project_id,),
        ):
            await workflow.stop(task["id"])
        return {"ok": True, "scope": "project", "status": "stop_requested"}

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
