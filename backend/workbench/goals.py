"""Durable goals layered over the existing task executor."""

import asyncio
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from .store import now, uid


class GoalInput(BaseModel):
    project_id: str
    title: str = Field(min_length=1, max_length=240)
    desired_outcome: str = Field(min_length=1, max_length=16000)
    success_criteria: list[str] = Field(min_length=1, max_length=30)
    engine: Literal["codex", "claude"] = "codex"
    kind: Literal["goal", "responsibility", "watch"] = "goal"
    review_interval: int = Field(default=3600, ge=60, le=2592000)
    permission_mode: Literal["auto", "ask"] = "ask"
    daily_limit: int = Field(default=3, ge=1, le=20)

    @field_validator("title", "desired_outcome")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("内容不能为空")
        return value.strip()

    @field_validator("success_criteria")
    @classmethod
    def valid_criteria(cls, value):
        if any(not item.strip() or len(item) > 2000 for item in value):
            raise ValueError("成功标准不能为空且每项最多 2000 字符")
        return [item.strip() for item in value]


class GoalPatch(BaseModel):
    revision: int = Field(ge=1)
    config: GoalInput


class GoalChange(BaseModel):
    revision: int
    evidence: dict[str, str] = Field(default_factory=dict)
    status: Literal["active", "paused", "cancelled", "completed"]


class GoalMessage(BaseModel):
    content: str = Field(min_length=1, max_length=16000)


class Goals:
    def __init__(self, store, routines):
        self.store, self.routines = store, routines
        self.job = None
        store.connection.executescript("""
        CREATE TABLE IF NOT EXISTS goals(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL REFERENCES users(id),project_id TEXT NOT NULL REFERENCES projects(id),config TEXT NOT NULL,status TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,reason TEXT NOT NULL DEFAULT '',next_review REAL,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS goal_events(id TEXT PRIMARY KEY,goal_id TEXT NOT NULL REFERENCES goals(id),event_key TEXT NOT NULL,kind TEXT NOT NULL,content TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',task_id TEXT REFERENCES tasks(id),created_at REAL NOT NULL,UNIQUE(goal_id,event_key));
        """)

    def get(self, goal_id, owner):
        row = self.store.one(
            "SELECT g.* FROM goals g JOIN projects p ON p.id=g.project_id WHERE g.id=? AND g.owner_id=? AND p.deleted_at IS NULL",
            (goal_id, owner),
        )
        if not row:
            raise HTTPException(404, "目标不存在")
        row["config"] = json.loads(row["config"])
        row["events"] = self.store.all(
            "SELECT * FROM goal_events WHERE goal_id=? ORDER BY created_at DESC LIMIT 100",
            (goal_id,),
        )
        return row

    def emit(self, goal_id, key, kind, content=""):
        self.store.execute(
            "INSERT OR IGNORE INTO goal_events(id,goal_id,event_key,kind,content,created_at) VALUES(?,?,?,?,?,?)",
            (uid(), goal_id, key, kind, content, now()),
        )

    def change(self, goal_id, owner, change):
        g = self.get(goal_id, owner)
        if change.status == "completed":
            criteria = g["config"].get("success_criteria", [])
            if (
                g["config"].get("kind", "goal") != "goal"
                or not criteria
                or any(
                    not change.evidence.get(str(i), "").strip()
                    for i in range(len(criteria))
                )
            ):
                raise HTTPException(409, "每项成功标准都需要用户确认的证据")
        if g["status"] in ("completed", "cancelled"):
            raise HTTPException(409, "目标已经结束")
        with self.store.transaction() as db:
            count = db.execute(
                "UPDATE goals SET status=?,revision=revision+1,reason=? WHERE id=? AND revision=?",
                (change.status, "", goal_id, change.revision),
            ).rowcount
            if not count:
                raise HTTPException(409, "目标已更新，请刷新")
            if change.status == "completed":
                db.execute(
                    "INSERT INTO goal_events(id,goal_id,event_key,kind,content,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        uid(),
                        goal_id,
                        "complete:" + str(change.revision),
                        "user.confirmed",
                        json.dumps(change.evidence, ensure_ascii=False),
                        now(),
                    ),
                )
            if (
                change.status == "active"
                and not db.execute(
                    "SELECT id FROM goal_events WHERE goal_id=? AND (status IN ('dispatched','dispatching','unknown') OR (status='pending' AND kind='user.input'))",
                    (goal_id,),
                ).fetchone()
            ):
                db.execute(
                    "INSERT INTO goal_events(id,goal_id,event_key,kind,content,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        uid(),
                        goal_id,
                        "activate:" + str(change.revision),
                        "user.input",
                        "",
                        now(),
                    ),
                )
        return self.get(goal_id, owner)

    async def tick(self):
        for g in self.store.all(
            "SELECT g.* FROM goals g JOIN projects p ON p.id=g.project_id WHERE g.status='active' AND p.deleted_at IS NULL"
        ):
            config = json.loads(g["config"])
            if g["next_review"] and g["next_review"] <= now():
                self.emit(
                    g["id"],
                    "due:" + str(g["next_review"]),
                    "user.input",
                    "例行复查：仅在存在新信息时行动，避免重复外部操作。",
                )
                self.store.execute(
                    "UPDATE goals SET next_review=NULL WHERE id=?", (g["id"],)
                )
            for event in self.store.all(
                "SELECT e.*,t.status AS task_status FROM goal_events e JOIN tasks t ON t.id=e.task_id WHERE e.goal_id=? AND e.status IN ('dispatched','dispatching','unknown')",
                (g["id"],),
            ):
                if event["task_status"] in (
                    "delivered",
                    "completed",
                    "failed",
                    "interrupted",
                    "paused",
                ):
                    self.store.execute(
                        "UPDATE goal_events SET status='resolved' WHERE id=?",
                        (event["id"],),
                    )
                    self.emit(
                        g["id"],
                        "result:" + event["task_id"],
                        "task.finished",
                        "执行结果：" + event["task_status"],
                    )
                    latest = self.store.one(
                        "SELECT next_review,reason FROM goals WHERE id=?", (g["id"],)
                    )
                    recurring = (
                        config.get("kind", "goal") != "goal" or latest["next_review"]
                    ) and event["task_status"] in ("delivered", "completed")
                    self.store.execute(
                        "UPDATE goals SET status=?,reason=?,next_review=?,revision=revision+1 WHERE id=?",
                        (
                            "active" if recurring else "waiting",
                            "等待下次复查"
                            if recurring
                            else "请检查成果并补充下一步或确认成功标准",
                            (
                                latest["next_review"]
                                or now() + config.get("review_interval", 3600)
                            )
                            if recurring
                            else None,
                            g["id"],
                        ),
                    )
            if self.store.one(
                "SELECT id FROM goal_events WHERE goal_id=? AND status IN ('dispatched','dispatching','unknown')",
                (g["id"],),
            ):
                continue
            current = self.store.one("SELECT status FROM goals WHERE id=?", (g["id"],))
            if current["status"] != "active":
                continue
            event = self.store.one(
                "SELECT * FROM goal_events WHERE goal_id=? AND status='pending' AND kind='user.input' ORDER BY created_at LIMIT 1",
                (g["id"],),
            )
            if not event:
                continue
            count = self.store.one(
                "SELECT COUNT(*) n FROM goal_events WHERE goal_id=? AND task_id IS NOT NULL AND created_at>?",
                (g["id"], now() - 86400),
            )["n"]
            if count >= config["daily_limit"]:
                self.store.execute(
                    "UPDATE goals SET status='waiting',reason='自动执行预算耗尽',revision=revision+1 WHERE id=?",
                    (g["id"],),
                )
                continue
            ready = await self.routines.ready(config["engine"])
            if self.store.one(
                "SELECT revision,status FROM goals WHERE id=?", (g["id"],)
            ) != {"revision": g["revision"], "status": "active"}:
                continue
            if not ready:
                self.store.execute(
                    "UPDATE goals SET status='waiting',reason='执行器不可用，请配置后恢复',revision=revision+1 WHERE id=? AND status='active' AND revision=?",
                    (g["id"], g["revision"]),
                )
                continue
            with self.store.transaction() as db:
                task_id = uid()
                db.execute(
                    "INSERT INTO tasks(id,project_id,title,mode,engine,permission_mode,created_at) VALUES(?,?,?,'free',?,?,?)",
                    (
                        task_id,
                        g["project_id"],
                        config["title"],
                        config["engine"],
                        config.get("permission_mode", "ask"),
                        now(),
                    ),
                )
                db.execute(
                    "UPDATE goal_events SET task_id=?,status='dispatching' WHERE id=?",
                    (task_id, event["id"]),
                )
            prompt = (
                "持续目标："
                + config["desired_outcome"]
                + "\n成功标准："
                + json.dumps(config["success_criteria"], ensure_ascii=False)
                + "\n本次补充："
                + event["content"]
                + "\n推进一次有边界的工作，交付真实成果与检查证据。说明下一行动或等待条件，不要把任务交付当成目标完成。"
            )
            context = "\n".join(
                e["content"][:8000]
                for e in reversed(
                    self.store.all(
                        "SELECT content FROM goal_events WHERE goal_id=? ORDER BY created_at DESC LIMIT 10",
                        (g["id"],),
                    )
                )
            )
            prompt += "\n最近进展和要求：\n" + context
            if (
                self.store.one("SELECT status FROM goals WHERE id=?", (g["id"],))[
                    "status"
                ]
                != "active"
            ):
                self.store.execute(
                    "UPDATE goal_events SET status='pending',task_id=NULL WHERE id=?",
                    (event["id"],),
                )
                continue
            await self.routines.submit(task_id, prompt)
            self.store.execute(
                "UPDATE goal_events SET status='dispatched' WHERE id=?", (event["id"],)
            )

    async def loop(self):
        while True:
            try:
                await self.tick()
            except Exception:
                import logging

                logging.getLogger(__name__).exception("Goal dispatch failed")
            await asyncio.sleep(5)

    def start(self):
        self.store.execute(
            "UPDATE goals SET status='waiting',reason='服务中断，检查关联任务后恢复',revision=revision+1 WHERE id IN (SELECT goal_id FROM goal_events WHERE status='dispatching')"
        )
        self.store.execute(
            "UPDATE goal_events SET status='unknown' WHERE status='dispatching'"
        )
        self.job = asyncio.create_task(self.loop())

    async def close(self):
        if self.job:
            self.job.cancel()
            await asyncio.gather(self.job, return_exceptions=True)
