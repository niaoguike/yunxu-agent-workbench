"""Local schedules and opt-in chat dispatch; all work uses the existing workflow."""

import asyncio
import html
import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator

from .store import now, uid

ACTIVE = ("queued", "running", "stopping", "waiting_permission")
ZONES = {"Asia/Shanghai": timezone(timedelta(hours=8)), "UTC": UTC}


class RoutineInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=16000)
    frequency: Literal["daily", "weekly"] = "daily"
    time: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: Literal["Asia/Shanghai", "UTC"] = "Asia/Shanghai"
    weekday: int = Field(default=0, ge=0, le=6)
    engine: Literal["codex", "claude"] = "codex"
    permission_mode: Literal["auto", "ask"] = "ask"
    enabled: bool = False
    connection_id: str | None = None


class ConnectionInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    provider: Literal["slack", "telegram"]
    chat_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    user_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    engine: Literal["codex", "claude"] = "codex"
    permission_mode: Literal["auto", "ask"] = "ask"
    receive: bool = False
    send: bool = False
    enabled: bool = False

    @model_validator(mode="after")
    def validate_ids(self):
        if self.provider == "telegram" and (
            not re.fullmatch(r"-?\d+", self.chat_id) or not self.user_id.isdigit()
        ):
            raise ValueError("Telegram 聊天和用户 ID 必须为数字")
        return self


def next_due(config, after):
    local = datetime.fromtimestamp(after, ZONES[config["timezone"]])
    hour, minute = map(int, config["time"].split(":"))
    for days in range(8):
        candidate = (local + timedelta(days=days)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if candidate.timestamp() > after and (
            config["frequency"] == "daily" or candidate.weekday() == config["weekday"]
        ):
            return candidate.timestamp()
    raise ValueError("无法计算下次执行时间")


def token_names(connection_id):
    prefix = "WORKBENCH_CHANNEL_" + connection_id.upper()
    return {"token_env": prefix + "_TOKEN", "app_token_env": prefix + "_APP_TOKEN"}


class ChannelError(ValueError):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


class ChatTransport:
    async def call(self, connection, method, payload=None):
        names = token_names(connection["id"])
        app = method == "apps.connections.open"
        token = os.environ.get(names["app_token_env" if app else "token_env"], "")
        if not token:
            raise ChannelError("缺少连接密钥，请按页面说明配置环境变量并重启")
        provider = connection["provider"]
        url = (
            "https://slack.com/api/" + method
            if provider == "slack"
            else "https://api.telegram.org/bot" + token + "/" + method
        )
        headers = {"Content-Type": "application/json"}
        if provider == "slack":
            headers["Authorization"] = "Bearer " + token

        def request():
            req = urllib.request.Request(
                url, data=json.dumps(payload or {}).encode(), headers=headers
            )
            try:
                with urllib.request.urlopen(req, timeout=25) as response:
                    result = json.loads(response.read(2_000_000))
            except urllib.error.HTTPError as error:
                raise ChannelError(
                    f"渠道请求失败（HTTP {error.code}），请检查权限或频率限制",
                    uncertain=error.code >= 500,
                ) from None
            except Exception:  # noqa: BLE001 - boundary errors must not expose channel credentials
                # URLs and exception messages may contain the Telegram token. Never persist them.
                raise ChannelError(
                    "渠道连接失败，远端接收结果可能不明", uncertain=True
                ) from None
            if not result.get("ok"):
                raise ChannelError("渠道返回失败，请检查凭据、权限和目标聊天")
            return result

        return await asyncio.to_thread(request)

    async def send(self, connection, text, thread):
        if connection["provider"] == "slack":
            body = {
                "channel": connection["chat_id"],
                "text": html.escape(text[:2800], quote=False),
                "mrkdwn": False,
                "unfurl_links": False,
                "unfurl_media": False,
            }
            if thread:
                body["thread_ts"] = thread
            response = await self.call(connection, "chat.postMessage", body)
            return response["ts"]
        body = {
            "chat_id": connection["chat_id"],
            "text": text[:2800],
            "link_preview_options": {"is_disabled": True},
        }
        if thread:
            body["reply_parameters"] = {
                "message_id": int(thread),
                "allow_sending_without_reply": True,
            }
        response = await self.call(connection, "sendMessage", body)
        return str(response["result"]["message_id"])


class Routines:
    def __init__(self, store, workflow, runtime, transport=None):
        self.store, self.workflow, self.runtime = store, workflow, runtime
        self.transport = transport or ChatTransport()
        self.job = None
        self.notification_job = None
        self.delivery_error = ""
        self.receivers = {}
        self.dispatch_jobs = set()
        self.locks = {}
        self.last_error = ""

    def recover(self):
        self.store.execute(
            "UPDATE channel_outbox SET status='unknown',error='服务中断，发送结果不明；请先检查外部聊天' WHERE status='sending'"
        )
        for row in self.store.all(
            "SELECT * FROM routines WHERE enabled=1 AND next_due<=?", (now(),)
        ):
            config = json.loads(row["config"])
            self.store.execute(
                "INSERT OR IGNORE INTO routine_runs VALUES(?,?,?,?,?,?,?)",
                (
                    uid(),
                    row["id"],
                    str(row["next_due"]),
                    None,
                    "missed",
                    "服务关闭期间错过计划，未自动补跑",
                    now(),
                ),
            )
            self.store.execute(
                "UPDATE routines SET next_due=? WHERE id=?",
                (next_due(config, now()), row["id"]),
            )
        self.store.execute(
            "UPDATE routine_runs SET status='unknown',error='服务中断，请检查关联任务后继续' WHERE status='dispatching'"
        )
        self.store.execute(
            "UPDATE channel_events SET status='unknown',error='服务中断，请检查关联任务后继续' WHERE status='accepted'"
        )

    def start(self):
        self.recover()
        self.job = asyncio.create_task(self.loop())
        self.notification_job = asyncio.create_task(self.notification_loop())

    async def close(self):
        jobs = (
            list(self.receivers.values())
            + list(self.dispatch_jobs)
            + ([self.job] if self.job else [])
            + ([self.notification_job] if self.notification_job else [])
        )
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)

    def connection(self, connection_id):
        return self.store.one(
            "SELECT c.* FROM channel_connections c JOIN projects p ON p.id=c.project_id WHERE c.id=? AND p.deleted_at IS NULL",
            (connection_id,),
        )

    def configured(self, connection):
        names = token_names(connection["id"])
        required = [names["token_env"]]
        if connection["provider"] == "slack" and connection["receive"]:
            required.append(names["app_token_env"])
        return all(os.environ.get(key) for key in required)

    async def ready(self, engine):
        available = next(
            (e for e in await self.runtime.readiness() if e["engine"] == engine), None
        )
        return bool(available and available["available"])

    def new_task(self, project_id, title, engine, permission_mode):
        task_id = uid()
        self.store.execute(
            "INSERT INTO tasks(id,project_id,title,mode,engine,permission_mode,created_at) VALUES(?,?,?,'free',?,?,?)",
            (task_id, project_id, title[:240], engine, permission_mode, now()),
        )
        return task_id

    async def submit(self, task_id, content):
        task = self.store.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        active = self.store.one(
            "SELECT id FROM runs WHERE task_id=? AND status IN ('queued','running','stopping')",
            (task_id,),
        )
        if active:
            count = self.store.one(
                "SELECT COUNT(*) AS n FROM pending_inputs WHERE task_id=? AND status IN ('queued','paused')",
                (task_id,),
            )["n"]
            if count >= 20:
                raise ValueError("任务已有 20 条待执行消息，请先处理")
            self.store.execute(
                "INSERT INTO pending_inputs(id,task_id,content,created_at) VALUES(?,?,?,?)",
                (uid(), task_id, content, now()),
            )
            return "queued"
        self.store.execute(
            "INSERT INTO messages VALUES(?,?,?,?,?)",
            (uid(), task_id, "user", content, now()),
        )
        if not await self.ready(task["engine"]):
            self.store.execute(
                "UPDATE tasks SET status='failed' WHERE id=?", (task_id,)
            )
            self.store.event(
                task_id,
                "dispatch_failed",
                {"reason": "执行器不可用；消息已保存，请配置后手动继续"},
            )
            return "failed"
        if task["status"] != "idle":
            self.store.execute(
                "UPDATE tasks SET version=version+1,stage=0,session_id=NULL WHERE id=?",
                (task_id,),
            )
        self.store.execute(
            "UPDATE gates SET status='invalidated' WHERE task_id=? AND status IN ('pending','approved')",
            (task_id,),
        )
        self.workflow.enqueue(task_id)
        return "dispatched"

    async def fire(self, routine_id, slot=None):
        async with self.locks.setdefault(routine_id, asyncio.Lock()):
            return await self._fire(routine_id, slot)

    async def _fire(self, routine_id, slot=None):
        routine = self.store.one(
            "SELECT r.* FROM routines r JOIN projects p ON p.id=r.project_id WHERE r.id=? AND p.deleted_at IS NULL",
            (routine_id,),
        )
        if not routine:
            raise ValueError("自动化不存在")
        config = json.loads(routine["config"])
        record_id, slot = uid(), slot or "manual-" + uid()
        try:
            self.store.execute(
                "INSERT INTO routine_runs VALUES(?,?,?,?,?,?,?)",
                (record_id, routine_id, slot, None, "dispatching", "", now()),
            )
        except sqlite3.IntegrityError:
            return None
        previous = self.store.one(
            "SELECT t.id FROM routine_runs rr JOIN tasks t ON t.id=rr.task_id WHERE rr.routine_id=? AND t.status IN ('queued','running','stopping','waiting_permission','paused','waiting_confirmation') LIMIT 1",
            (routine_id,),
        )
        if previous:
            self.store.execute(
                "UPDATE routine_runs SET status='skipped',error='上次任务仍未结束' WHERE id=?",
                (record_id,),
            )
        else:
            task_id = self.new_task(
                routine["project_id"],
                config["name"],
                config["engine"],
                config["permission_mode"],
            )
            self.store.execute(
                "UPDATE routine_runs SET task_id=? WHERE id=?", (task_id, record_id)
            )
            connection = (
                self.connection(config.get("connection_id"))
                if config.get("connection_id")
                else None
            )
            if connection and connection["enabled"] and connection["send"]:
                self.store.execute(
                    "INSERT OR IGNORE INTO channel_threads VALUES(?,?,?,?)",
                    (connection["id"], "routine-" + task_id, task_id, ""),
                )
            try:
                outcome = await self.submit(task_id, config["prompt"])
                self.store.execute(
                    "UPDATE routine_runs SET status=?,error=? WHERE id=?",
                    (
                        outcome,
                        "执行器不可用，任务与消息已保存" if outcome == "failed" else "",
                        record_id,
                    ),
                )
            except Exception:  # noqa: BLE001 - boundary errors must not expose channel credentials
                self.store.execute(
                    "UPDATE routine_runs SET status='failed',error='派发失败，请检查关联任务' WHERE id=?",
                    (record_id,),
                )
        return self.store.one("SELECT * FROM routine_runs WHERE id=?", (record_id,))

    async def tick(self, at=None):
        at = at or now()
        for row in self.store.all(
            "SELECT r.* FROM routines r JOIN projects p ON p.id=r.project_id WHERE r.enabled=1 AND r.next_due<=? AND p.deleted_at IS NULL",
            (at,),
        ):
            due = row["next_due"]
            self.store.execute(
                "UPDATE routines SET next_due=? WHERE id=?",
                (next_due(json.loads(row["config"]), at), row["id"]),
            )
            if at - due > 60:
                self.store.execute(
                    "INSERT OR IGNORE INTO routine_runs VALUES(?,?,?,?,?,?,?)",
                    (
                        uid(),
                        row["id"],
                        str(due),
                        None,
                        "missed",
                        "错过执行时间，未自动补跑",
                        now(),
                    ),
                )
            else:
                await self.fire(row["id"], str(due))

    async def ingest(
        self, connection_id, event_id, chat_id, user_id, text, thread, reply_to=""
    ):
        connection = self.connection(connection_id)
        if not connection or not connection["enabled"] or not connection["receive"]:
            return False
        if (
            str(chat_id) != connection["chat_id"]
            or str(user_id) != connection["user_id"]
            or not text.strip()
            or len(text) > 16000
        ):
            return False
        old = self.store.one(
            "SELECT * FROM channel_events WHERE connection_id=? AND event_id=?",
            (connection_id, str(event_id)),
        )
        if old:
            return True
        mapped = self.store.one(
            "SELECT * FROM channel_threads WHERE connection_id=? AND thread_key=?",
            (connection_id, reply_to or thread),
        )
        if not mapped and reply_to:
            mapped = self.store.one(
                "SELECT * FROM channel_threads WHERE connection_id=? AND external_reply=?",
                (connection_id, reply_to),
            )
        with self.store.transaction():
            task_id = (
                mapped["task_id"]
                if mapped
                else self.new_task(
                    connection["project_id"],
                    text[:100],
                    connection["engine"],
                    connection["permission_mode"],
                )
            )
            self.store.execute(
                "INSERT OR IGNORE INTO channel_threads VALUES(?,?,?,?)",
                (connection_id, thread, task_id, thread),
            )
            self.store.execute(
                "INSERT INTO channel_events VALUES(?,?,?,?,?,?,?)",
                (connection_id, str(event_id), task_id, "accepted", "", text, now()),
            )
        # Persist before acknowledging the channel; dispatch separately so Socket Mode ACK is prompt.
        job = asyncio.create_task(
            self.dispatch_received(connection, str(event_id), task_id, text)
        )
        self.dispatch_jobs.add(job)
        job.add_done_callback(self.dispatch_jobs.discard)
        return True

    async def dispatch_received(self, connection, event_id, task_id, text):
        try:
            outcome = await self.submit(
                task_id, f"[{connection['provider']} 指定用户的消息]\n" + text
            )
            self.store.execute(
                "UPDATE channel_events SET status=? WHERE connection_id=? AND event_id=?",
                (outcome, connection["id"], event_id),
            )
        except Exception:  # noqa: BLE001 - boundary errors must not expose channel credentials
            self.store.execute(
                "UPDATE channel_events SET status='failed',error='消息已保留，请在工作台检查任务' WHERE connection_id=? AND event_id=?",
                (connection["id"], event_id),
            )

    async def receive(self, connection):
        connection_id = connection["id"]
        if connection["provider"] == "telegram":
            offset = connection["cursor"]
            result = await self.transport.call(
                connection,
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 15,
                    "limit": 50,
                    "allowed_updates": ["message"],
                },
            )
            for update in result["result"]:
                msg = update.get("message", {})
                if not msg.get("from", {}).get("is_bot") and msg.get("text"):
                    await self.ingest(
                        connection_id,
                        str(update["update_id"]),
                        str(msg.get("chat", {}).get("id", "")),
                        str(msg.get("from", {}).get("id", "")),
                        msg["text"],
                        str(msg["message_id"]),
                        str(msg.get("reply_to_message", {}).get("message_id", "")),
                    )
                self.store.execute(
                    "UPDATE channel_connections SET cursor=? WHERE id=?",
                    (update["update_id"] + 1, connection_id),
                )
            self.store.execute(
                "UPDATE channel_connections SET status='connected',error='' WHERE id=?",
                (connection_id,),
            )
            return
        result = await self.transport.call(connection, "apps.connections.open")
        url = result["url"]
        host = urlsplit(url).hostname or ""
        if urlsplit(url).scheme != "wss" or not (
            host.endswith((".slack.com", ".slack-msgs.com"))
        ):
            raise ChannelError("Slack 返回了无效的连接地址")
        from websockets.asyncio.client import connect

        async with connect(url, max_size=1_000_000, open_timeout=15) as socket:
            self.store.execute(
                "UPDATE channel_connections SET status='connected',error='' WHERE id=?",
                (connection_id,),
            )
            async for raw in socket:
                envelope = json.loads(raw)
                if envelope.get("type") == "disconnect":
                    return
                if envelope.get("type") != "events_api":
                    continue
                payload = envelope.get("payload", {})
                event = payload.get("event", {})
                if (
                    event.get("type") in {"message", "app_mention"}
                    and not event.get("bot_id")
                    and not event.get("subtype")
                ):
                    await self.ingest(
                        connection_id,
                        event.get("channel", "")
                        + ":"
                        + event.get("ts", envelope["envelope_id"]),
                        event.get("channel", ""),
                        event.get("user", ""),
                        event.get("text", ""),
                        event.get("thread_ts", event.get("ts", "")),
                    )
                await socket.send(json.dumps({"envelope_id": envelope["envelope_id"]}))

    async def receiver(self, connection_id):
        while True:
            connection = self.connection(connection_id)
            if not connection or not connection["enabled"] or not connection["receive"]:
                return
            if not self.configured(connection):
                self.store.execute(
                    "UPDATE channel_connections SET status='missing_credentials',error='请配置页面列出的环境变量并重启服务' WHERE id=?",
                    (connection_id,),
                )
                await asyncio.sleep(5)
                continue
            token = os.environ.get(token_names(connection_id)["token_env"])
            duplicates = self.store.all(
                "SELECT id FROM channel_connections WHERE enabled=1 AND receive=1 AND provider=? AND id<?",
                (connection["provider"], connection_id),
            )
            if any(
                os.environ.get(token_names(c["id"])["token_env"]) == token
                for c in duplicates
            ):
                self.store.execute(
                    "UPDATE channel_connections SET status='error',error='同一个机器人只能启用一个接收连接，请停用重复连接' WHERE id=?",
                    (connection_id,),
                )
                await asyncio.sleep(5)
                continue
            try:
                await self.receive(connection)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - boundary errors must not expose channel credentials
                self.store.execute(
                    "UPDATE channel_connections SET status='error',error='渠道连接失败，请检查凭据、权限或网络；稍后重试' WHERE id=?",
                    (connection_id,),
                )
                await asyncio.sleep(10)

    async def notify(self):
        rows = self.store.all(
            "SELECT DISTINCT ct.connection_id,ct.task_id FROM channel_threads ct JOIN tasks t ON t.id=ct.task_id JOIN channel_connections c ON c.id=ct.connection_id JOIN projects p ON p.id=c.project_id WHERE c.enabled=1 AND c.send=1 AND p.deleted_at IS NULL AND t.status IN ('delivered','completed','failed','interrupted','review')"
        )
        for row in rows:
            task = self.store.one("SELECT * FROM tasks WHERE id=?", (row["task_id"],))
            # Never return intermediate failure while an automatic repair is still queued.
            if self.store.one(
                "SELECT id FROM runs WHERE task_id=? AND status IN ('queued','running','stopping')",
                (task["id"],),
            ):
                continue
            connection = self.connection(row["connection_id"])
            key = f"{task['version']}:{task['status']}"
            self.store.execute(
                "INSERT OR IGNORE INTO channel_outbox VALUES(?,?,?,?,?,?,?,?)",
                (uid(), connection["id"], task["id"], key, "pending", "", "", now()),
            )
        for item in self.store.all(
            "SELECT * FROM channel_outbox WHERE status='pending' ORDER BY created_at LIMIT 10"
        ):
            connection = self.connection(item["connection_id"])
            if not connection or not connection["enabled"] or not connection["send"]:
                continue
            if not self.configured(connection):
                self.store.execute(
                    "UPDATE channel_outbox SET status='failed',error='缺少发送凭据；配置后可重试' WHERE id=?",
                    (item["id"],),
                )
                continue
            task = self.store.one("SELECT * FROM tasks WHERE id=?", (item["task_id"],))
            message = self.store.one(
                "SELECT content FROM messages WHERE task_id=? AND role='assistant' ORDER BY created_at DESC LIMIT 1",
                (task["id"],),
            )
            if not message:
                message = self.store.one(
                    "SELECT content FROM messages WHERE task_id=? ORDER BY created_at DESC LIMIT 1",
                    (task["id"],),
                )
            artifacts = self.store.all(
                "SELECT DISTINCT path FROM artifacts WHERE task_id=? LIMIT 10",
                (task["id"],),
            )
            status = {
                "delivered": "已交付",
                "completed": "已完成",
                "failed": "执行失败",
                "interrupted": "执行中断",
                "review": "待评审",
                "idle": "待开始",
                "queued": "排队中",
                "running": "执行中",
                "waiting_permission": "等待授权",
                "paused": "已暂停",
            }.get(task["status"], task["status"])
            text = f"{task['title']}：{status}\n" + (
                message["content"][:1800] if message else "请在工作台查看执行记录。"
            )
            if artifacts:
                text += "\n成果：" + "、".join(a["path"] for a in artifacts)
            thread = self.store.one(
                "SELECT * FROM channel_threads WHERE connection_id=? AND task_id=? ORDER BY rowid LIMIT 1",
                (connection["id"], task["id"]),
            )
            self.store.execute(
                "UPDATE channel_outbox SET status='sending' WHERE id=?", (item["id"],)
            )
            try:
                reply = await self.transport.send(
                    connection, text, thread["external_reply"] if thread else ""
                )
                self.store.execute(
                    "UPDATE channel_outbox SET status='sent',external_id=?,error='' WHERE id=?",
                    (reply, item["id"]),
                )
                if thread:
                    self.store.execute(
                        "INSERT OR IGNORE INTO channel_threads VALUES(?,?,?,?)",
                        (connection["id"], reply, task["id"], reply),
                    )
            except ChannelError as error:
                self.store.execute(
                    "UPDATE channel_outbox SET status=?,error=? WHERE id=?",
                    (
                        "unknown" if error.uncertain else "failed",
                        str(error),
                        item["id"],
                    ),
                )
            except Exception:  # noqa: BLE001 - boundary errors must not expose channel credentials
                self.store.execute(
                    "UPDATE channel_outbox SET status='unknown',error='发送结果不明，请先检查外部聊天' WHERE id=?",
                    (item["id"],),
                )

    async def loop(self):
        while True:
            try:
                await self.tick()
                connections = self.store.all(
                    "SELECT c.* FROM channel_connections c JOIN projects p ON p.id=c.project_id WHERE c.enabled=1 AND c.receive=1 AND p.deleted_at IS NULL"
                )
                active_ids = {c["id"] for c in connections}
                for connection_id, job in list(self.receivers.items()):
                    if connection_id not in active_ids or job.done():
                        job.cancel()
                        await asyncio.gather(job, return_exceptions=True)
                        del self.receivers[connection_id]
                for connection in connections:
                    if connection["id"] not in self.receivers:
                        self.receivers[connection["id"]] = asyncio.create_task(
                            self.receiver(connection["id"])
                        )
                self.last_error = ""
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - boundary errors must not expose channel credentials
                # Keep scheduler alive; expose a safe diagnostic without leaking transport secrets.
                self.last_error = "后台调度遇到错误，正在重试；请检查执行和渠道记录"
            await asyncio.sleep(2)

    async def notification_loop(self):
        # Slow channel requests must not delay schedule checks or receive reconnections.
        while True:
            try:
                await self.notify()
                self.delivery_error = ""
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - keep a safe, inspectable background failure
                self.delivery_error = "渠道回传遇到错误，正在重试；请检查发送记录"
            await asyncio.sleep(2)
