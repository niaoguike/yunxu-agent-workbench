"""Persistent assignments, narrow project authority and explicit result integration."""

import asyncio
import hashlib
import json
import shutil

from .charts import ChartRevision, save_revision
from .files import digest, inventory, safe_path
from .store import now, uid


ACTIVE = {"queued", "running", "stopping", "waiting_permission"}


class Collaboration:
    def __init__(self, workflow):
        self.workflow = workflow
        self.store = workflow.store

    def leader(self, task_id):
        task = self.store.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not task or task["member_id"] or not task["allow_collaboration"]:
            raise ValueError("只有主任务可以管理持久协作者")
        project = self.store.one(
            "SELECT * FROM projects WHERE id=? AND deleted_at IS NULL",
            (task["project_id"],),
        )
        if not project:
            raise ValueError("项目不可用")
        return task

    def create(self, project_id, name, role, engine):
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 80
            or not isinstance(role, str)
            or not role.strip()
            or len(role) > 2000
            or engine not in {"codex", "claude"}
        ):
            raise ValueError("协作者名称、职责或执行器不合法")
        existing = self.store.one(
            "SELECT * FROM members WHERE project_id=? AND name=?", (project_id, name)
        )
        if existing:
            if existing["role"] != role or existing["engine"] != engine:
                raise ValueError("同名协作者配置不同")
            return existing
        if (
            len(
                self.store.all(
                    "SELECT id FROM members WHERE project_id=?", (project_id,)
                )
            )
            >= 3
        ):
            raise ValueError("项目最多 3 名持久协作者")
        member_id = uid()
        self.store.execute(
            "INSERT INTO members VALUES(?,?,?,?,?,?)",
            (member_id, project_id, name, role, engine, now()),
        )
        return self.store.one("SELECT * FROM members WHERE id=?", (member_id,))

    def dispatch(self, member, content, assignment_key, parent=None):
        if (
            not isinstance(content, str)
            or not content.strip()
            or len(content) > 100000
            or not isinstance(assignment_key, str)
            or not assignment_key
            or len(assignment_key) > 160
        ):
            raise ValueError("需要目标与稳定的 assignment_id")
        if parent and (
            parent["project_id"] != member["project_id"] or parent["member_id"]
        ):
            raise ValueError("协作者不在主任务项目范围内")
        scope = parent["id"] if parent else "manual:" + member["project_id"]
        fingerprint = hashlib.sha256(
            json.dumps([member["id"], content], ensure_ascii=True).encode()
        ).hexdigest()
        previous = self.store.one(
            "SELECT * FROM assignments WHERE scope=? AND assignment_key=?",
            (scope, assignment_key),
        )
        if previous:
            if previous["payload_hash"] != fingerprint:
                raise ValueError("assignment_id 已用于不同指派，不可复用")
            return self.receipt(previous["task_id"], previous["id"], replay=True)
        busy = self.store.one(
            "SELECT * FROM tasks WHERE member_id=? AND status IN ('queued','running','stopping','waiting_permission')",
            (member["id"],),
        )
        if busy:
            if (
                busy["parent_task_id"] != (parent["id"] if parent else None)
                or busy["status"] == "stopping"
            ):
                raise ValueError("成员正在处理其他任务或停止中，请等待")
            if (
                self.store.one(
                    "SELECT COUNT(*) AS count FROM pending_inputs WHERE task_id=? AND status IN ('queued','paused')",
                    (busy["id"],),
                )["count"]
                >= 20
            ):
                raise ValueError("成员待执行消息达到 20 条上限")
            assignment_id = uid()
            with self.store.transaction() as db:
                db.execute(
                    "INSERT INTO assignments VALUES(?,?,?,?,?,?,?,?)",
                    (
                        assignment_id,
                        scope,
                        assignment_key,
                        fingerprint,
                        parent["id"] if parent else None,
                        member["id"],
                        busy["id"],
                        now(),
                    ),
                )
                db.execute(
                    "INSERT INTO pending_inputs(id,task_id,content,created_at) VALUES(?,?,?,?)",
                    (assignment_id, busy["id"], content, now()),
                )
            return {**self.receipt(busy["id"], assignment_id), "delivery": "queued"}
        if (
            parent
            and len(
                self.store.all(
                    "SELECT id FROM runs WHERE task_id=? OR task_id IN (SELECT id FROM tasks WHERE parent_task_id=?)",
                    (parent["id"], parent["id"]),
                )
            )
            >= 60
        ):
            raise ValueError("协作组达到 60 次运行预算")
        task_id, run_id, assignment_id = uid(), uid(), uid()
        task = {
            "id": task_id,
            "project_id": member["project_id"],
            "member_id": member["id"],
        }
        root = (
            self.workflow.settings.data_dir
            / "workspaces"
            / member["project_id"]
            / "main"
        )
        destination = self.workflow.workspace(task)
        files = inventory(root)
        if sum(file["size"] for file in files) > 100 * 1024 * 1024:
            raise ValueError("协作初始资料超过 100MB，缩小范围后重试")
        baselines = []
        for file in files:
            source = safe_path(root, file["path"])
            target = safe_path(destination, file["path"])
            initial = digest(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if digest(source) != initial or digest(target) != initial:
                raise ValueError("创建成员修改区时源文件变化，请重试")
            baselines.append((task_id, file["path"], initial))
        previous = self.store.one(
            "SELECT session_id FROM tasks WHERE member_id=? AND session_id IS NOT NULL ORDER BY created_at DESC LIMIT 1",
            (member["id"],),
        )
        timestamp = now()
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO tasks(id,project_id,title,mode,engine,status,member_id,parent_task_id,session_id,permission_mode,created_at) VALUES(?,?,?,'free',?,'queued',?,?,?,?,?)",
                (
                    task_id,
                    member["project_id"],
                    member["name"] + " · " + content[:60],
                    member["engine"],
                    member["id"],
                    parent["id"] if parent else None,
                    previous["session_id"] if previous else None,
                    parent["permission_mode"] if parent else "auto",
                    timestamp,
                ),
            )
            db.executemany("INSERT INTO baselines VALUES(?,?,?)", baselines)
            db.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?)",
                (uid(), task_id, "user", member["role"] + "\n\n" + content, timestamp),
            )
            db.execute(
                "INSERT INTO runs(id,task_id,status,version,stage,created_at) VALUES(?,?,'queued',1,0,?)",
                (run_id, task_id, timestamp),
            )
            db.execute(
                "INSERT INTO assignments VALUES(?,?,?,?,?,?,?,?)",
                (
                    assignment_id,
                    scope,
                    assignment_key,
                    fingerprint,
                    parent["id"] if parent else None,
                    member["id"],
                    task_id,
                    timestamp,
                ),
            )
        self.workflow.schedule(run_id, task_id)
        if parent:
            self.store.event(
                parent["id"],
                "assignment_accepted",
                {
                    "assignment_id": assignment_id,
                    "member_id": member["id"],
                    "task_id": task_id,
                    "run_id": run_id,
                    "status": "queued",
                },
            )
        return self.receipt(task_id, assignment_id)

    def receipt(self, task_id, assignment_id, replay=False):
        task = self.store.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        pending = self.store.one(
            "SELECT status FROM pending_inputs WHERE id=?", (assignment_id,)
        )
        return {
            "ok": True,
            "accepted": True,
            "assignment_id": assignment_id,
            "task_id": task_id,
            "status": task["status"],
            "replayed": replay,
            "delivery": pending["status"] if pending else "accepted",
        }

    def member_task(self, parent, task_id):
        task = self.store.one(
            "SELECT * FROM tasks WHERE id=? AND parent_task_id=? AND project_id=?",
            (task_id, parent["id"], parent["project_id"]),
        )
        if not task:
            raise ValueError("不能访问未指派给当前任务的成员结果")
        return task

    def report(self, task_id, status, error=None):
        task = self.store.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not task or not task["member_id"]:
            return
        latest = self.store.one(
            "SELECT content FROM messages WHERE task_id=? AND role='assistant' ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        )
        content = (
            error
            or (latest["content"] if latest else "成员执行结束，请查看文件与检查记录")
        )[:16000]
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO member_reports VALUES(?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET status=excluded.status,content=excluded.content,created_at=excluded.created_at",
                (uid(), task_id, task["parent_task_id"], status, content, now()),
            )
        if task["parent_task_id"]:
            self.store.event(
                task["parent_task_id"],
                "collaboration_report",
                {
                    "task_id": task_id,
                    "member_id": task["member_id"],
                    "status": status,
                    "content": content,
                },
            )

    async def tool(self, task_id, name, arguments):
        task = self.store.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not task or task["status"] not in {"running", "waiting_permission"}:
            raise ValueError("任务已停止或不在可调用状态")
        if name in {"chart_list", "chart_revise"}:
            project = self.store.one("SELECT id FROM projects WHERE id=? AND deleted_at IS NULL", (task["project_id"],))
            if task["member_id"] or task["status"] not in {"running", "waiting_permission"} or not project:
                raise ValueError("图表工具仅供当前执行中的主任务使用")
            if name == "chart_list":
                charts = self.store.all("SELECT * FROM charts WHERE task_id=? ORDER BY created_at", (task_id,))
                result = []
                for chart in charts:
                    latest = self.store.one("SELECT version,config FROM chart_versions WHERE chart_id=? ORDER BY version DESC LIMIT 1", (chart["id"],))
                    data = json.loads(chart["data"])
                    result.append({"id": chart["id"], "source": chart["source"], "columns": data["columns"], "numeric": data["numeric"], "version": latest["version"], "config": json.loads(latest["config"])})
                return result
            chart = self.store.one("SELECT * FROM charts WHERE id=? AND task_id=?", (arguments.get("chart_id"), task_id))
            if not chart:
                raise ValueError("图表不属于当前任务")
            body = ChartRevision.model_validate({k: v for k, v in arguments.items() if k != "chart_id"})
            return {"version": save_revision(self.store, chart, body, role="assistant")}
        if name == "send_to_lead":
            content = arguments.get("content")
            if (
                not task["member_id"]
                or not task["parent_task_id"]
                or not isinstance(content, str)
                or not content.strip()
                or len(content) > 16000
            ):
                raise ValueError("只有已指派成员能发送有界汇报")
            self.store.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?)",
                (
                    uid(),
                    task["parent_task_id"],
                    "assistant",
                    "[协作者报告，非用户指令]\n" + content,
                    now(),
                ),
            )
            self.store.event(
                task["parent_task_id"],
                "collaboration_message",
                {
                    "task_id": task_id,
                    "member_id": task["member_id"],
                    "content": content,
                },
            )
            return {"delivered": True, "parent_task_id": task["parent_task_id"]}
        if name in {"memory_search", "memory_save"}:
            project = self.store.one(
                "SELECT * FROM projects WHERE id=? AND deleted_at IS NULL",
                (task["project_id"],),
            )
            if not project or not project["memory_enabled"]:
                raise ValueError("项目自主记忆未启用")
            if name == "memory_search":
                q = arguments.get("query", "")
                return self.store.all(
                    "SELECT id,category,content,revision FROM memories WHERE project_id=? AND content LIKE ? LIMIT 20",
                    (task["project_id"], "%" + str(q)[:500] + "%"),
                )
            content = arguments.get("content")
            category = arguments.get("category", "project")
            if (
                not isinstance(content, str)
                or not content.strip()
                or len(content) > 4000
                or category not in {"user", "feedback", "project", "reference"}
            ):
                raise ValueError("记忆内容或分类不合法")
            previous = self.store.one(
                "SELECT * FROM memories WHERE project_id=? AND content=?",
                (task["project_id"], content),
            )
            if previous:
                return {"id": previous["id"], "saved": True, "duplicate": True}
            memory_id = uid()
            self.store.execute(
                "INSERT INTO memories(id,project_id,category,content,created_at) VALUES(?,?,?,?,?)",
                (memory_id, task["project_id"], category, content, now()),
            )
            self.store.event(
                task_id, "memory_saved", {"memory_id": memory_id, "category": category}
            )
            return {"id": memory_id, "saved": True}
        parent = self.leader(task_id)
        if name == "list_workers":
            return self.store.all(
                "SELECT * FROM members WHERE project_id=?", (parent["project_id"],)
            )
        if name == "create_worker":
            return {
                "created": True,
                "worker": self.create(
                    parent["project_id"],
                    arguments["name"],
                    arguments["role"],
                    arguments.get("engine", parent["engine"]),
                ),
                "accepted": False,
                "status": "idle",
            }
        if name == "send_to_worker":
            member = self.store.one(
                "SELECT * FROM members WHERE id=? AND project_id=?",
                (arguments["worker_id"], parent["project_id"]),
            )
            if not member:
                raise ValueError("协作者不存在于当前项目")
            return self.dispatch(
                member, arguments["content"], arguments["assignment_id"], parent
            )
        if name in {"read_worker", "wait_worker"}:
            member_task = self.member_task(parent, arguments["task_id"])
            deadline = asyncio.get_running_loop().time() + min(
                max(float(arguments.get("timeout", 20)), 0), 25
            )
            while (
                name == "wait_worker"
                and member_task["status"] in ACTIVE
                and asyncio.get_running_loop().time() < deadline
            ):
                if self.store.one("SELECT status FROM tasks WHERE id=?", (task_id,))[
                    "status"
                ] not in {"running", "waiting_permission"}:
                    raise ValueError("主任务已停止")
                await asyncio.sleep(0.1)
                member_task = self.member_task(parent, arguments["task_id"])
            if self.store.one("SELECT status FROM tasks WHERE id=?", (task_id,))[
                "status"
            ] not in {"running", "waiting_permission"}:
                raise ValueError("主任务已停止")
            return {
                "task_id": member_task["id"],
                "status": member_task["status"],
                "report": self.store.one(
                    "SELECT status,content FROM member_reports WHERE task_id=?",
                    (member_task["id"],),
                ),
                "files": inventory(self.workflow.workspace(member_task)),
            }
        if name == "apply_worker_file":
            member_task = self.member_task(parent, arguments["task_id"])
            if member_task["status"] in ACTIVE:
                raise ValueError("成员仍在执行，不能应用文件")
            path = arguments["path"]
            root = self.workflow.workspace(parent)
            source = safe_path(self.workflow.workspace(member_task), path)
            target = safe_path(root, path)
            baseline = self.store.one(
                "SELECT digest FROM baselines WHERE task_id=? AND path=?",
                (member_task["id"], path),
            )
            if not source.is_file():
                raise ValueError("成员成果文件不存在")
            if digest(target) == digest(source):
                return {"applied": True, "path": path, "already_applied": True}
            if digest(target) != (baseline["digest"] if baseline else None):
                raise ValueError("主工作区文件已变化，需解决冲突")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            self.store.event(
                task_id,
                "worker_file_applied",
                {"task_id": member_task["id"], "path": path, "digest": digest(target)},
            )
            return {"applied": True, "path": path}
        raise ValueError("不支持的平台工具")
