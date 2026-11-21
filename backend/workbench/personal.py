"""Account-owned assistant, using the existing durable conversation and permissions."""

from fastapi import HTTPException
from pydantic import BaseModel, Field

from .store import now, uid


class AssistantMessage(BaseModel):
    content: str = Field(min_length=1, max_length=100000)


class PersonalAssistant:
    def __init__(self, store, routines):
        self.store, self.routines = store, routines
        store.connection.executescript("""
        CREATE TABLE IF NOT EXISTS personal_assistants(owner_id TEXT PRIMARY KEY REFERENCES users(id),project_id TEXT NOT NULL REFERENCES projects(id),task_id TEXT NOT NULL REFERENCES tasks(id),name TEXT NOT NULL DEFAULT '云序');
        """)

    def get(self, owner):
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT * FROM personal_assistants WHERE owner_id=?", (owner,)
            ).fetchone()
            if not row:
                project_id, task_id = uid(), uid()
                db.execute(
                    "INSERT INTO projects(id,owner_id,name,instructions,created_at) VALUES(?,?,?,?,?)",
                    (
                        project_id,
                        owner,
                        "个人助手",
                        "你是用户的长期个人助手。持续承接责任，复用已授权上下文。明确区分计划、执行结果和未完成事项。涉及外部行动时遵守工具授权。",
                        now(),
                    ),
                )
                db.execute(
                    "INSERT INTO tasks(id,project_id,title,mode,engine,permission_mode,allow_collaboration,created_at) VALUES(?,?,?,'free','codex','ask',1,?)",
                    (task_id, project_id, "与云序对话", now()),
                )
                db.execute(
                    "INSERT INTO personal_assistants(owner_id,project_id,task_id) VALUES(?,?,?)",
                    (owner, project_id, task_id),
                )
            row = dict(
                db.execute(
                    "SELECT * FROM personal_assistants WHERE owner_id=?", (owner,)
                ).fetchone()
            )
            project = db.execute(
                "SELECT deleted_at FROM projects WHERE id=?", (row["project_id"],)
            ).fetchone()
            if project["deleted_at"] is not None:
                raise HTTPException(409, "请先从回收区恢复个人助手项目")
        row["messages"] = self.store.all(
            "SELECT * FROM messages WHERE task_id=? ORDER BY created_at DESC LIMIT 100",
            (row["task_id"],),
        )[::-1]
        row["task"] = self.store.one(
            "SELECT * FROM tasks WHERE id=?", (row["task_id"],)
        )
        row["instructions"] = self.store.one(
            "SELECT instructions FROM users WHERE id=?", (owner,)
        )["instructions"]
        row["reports"] = self.store.all(
            "SELECT r.task_id,r.status,r.content FROM member_reports r JOIN tasks t ON t.id=r.task_id WHERE t.project_id=? ORDER BY r.created_at DESC LIMIT 20",
            (row["project_id"],),
        )
        return row

    async def send(self, owner, content):
        row = self.get(owner)
        await self.routines.submit(row["task_id"], content)
        return self.get(owner)
