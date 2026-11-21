"""Opt-in persistent browser containers. Control plane owns Docker; agents never do."""

import asyncio
import json
import os

from fastapi import HTTPException


class Computer:
    def __init__(self, store):
        self.store = store
        self.locks = {}
        store.connection.executescript(
            "CREATE TABLE IF NOT EXISTS computer_actions(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL REFERENCES users(id),payload TEXT NOT NULL,reason TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending');"
        )
        store.connection.executescript(
            "CREATE TABLE IF NOT EXISTS personal_computers(owner_id TEXT PRIMARY KEY REFERENCES users(id),name TEXT NOT NULL,status TEXT NOT NULL);"
        )

    def enabled(self):
        if os.getenv("WORKBENCH_PERSONAL_COMPUTER", "") != "docker":
            raise HTTPException(
                503, "尚未启用个人云电脑，请构建浏览器镜像并配置 docker 模式"
            )

    async def command(self, *args, input=None):
        self.enabled()
        process = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdin=asyncio.subprocess.PIPE if input else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _stderr = await asyncio.wait_for(
                process.communicate(input.encode() if input else None), 45
            )
        except BaseException:
            process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise HTTPException(503, "云电脑操作失败，请检查 Docker 状态和镜像")
        return stdout

    def owned(self, owner):
        row = self.store.one(
            "SELECT * FROM personal_computers WHERE owner_id=?", (owner,)
        )
        if not row:
            raise HTTPException(404, "尚未创建云电脑")
        return row

    async def start(self, owner):
        self.enabled()
        async with self.locks.setdefault(owner, asyncio.Lock()):
            row = self.store.one(
                "SELECT * FROM personal_computers WHERE owner_id=?", (owner,)
            )
            if row:
                await self.command("start", row["name"])
                self.store.execute(
                    "UPDATE personal_computers SET status='ready' WHERE owner_id=?",
                    (owner,),
                )
            else:
                import hashlib

                name = (
                    "yunxu-browser-" + hashlib.sha256(owner.encode()).hexdigest()[:24]
                )
                await self.command(
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--label",
                    "yunxu.personal-computer=true",
                    "--memory",
                    "1g",
                    "--cpus",
                    "1",
                    "--pids-limit",
                    "256",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--shm-size",
                    "256m",
                    "--mount",
                    "type=volume,source=" + name + ",target=/home/browser/profile",
                    os.getenv("WORKBENCH_BROWSER_IMAGE", "yunxu-browser:local"),
                )
                self.store.execute(
                    "INSERT INTO personal_computers VALUES(?,?,?)",
                    (owner, name, "ready"),
                )
            return self.owned(owner)

    async def stop(self, owner):
        row = self.owned(owner)
        await self.command("stop", row["name"])
        self.store.execute(
            "UPDATE personal_computers SET status='paused' WHERE owner_id=?", (owner,)
        )
        return {"status": "paused"}

    async def control(self, owner, action):
        row = self.owned(owner)
        if row["status"] != "ready":
            raise HTTPException(409, "云电脑已暂停")
        allowed = {"screenshot", "navigate", "click", "type", "key"}
        if action.get("kind") not in allowed:
            raise HTTPException(422, "不支持的电脑操作")
        if action["kind"] == "navigate":
            import ipaddress
            from urllib.parse import urlsplit

            url = urlsplit(action.get("url", ""))
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
            ):
                raise HTTPException(422, "仅允许 HTTPS 网页")
            try:
                ipaddress.ip_address(url.hostname)
            except ValueError:
                if "." not in url.hostname or url.hostname.endswith(
                    (".local", ".internal", ".localhost")
                ):
                    raise HTTPException(422, "不允许内部地址")
            else:
                raise HTTPException(422, "不允许 IP 地址")
        async with self.locks.setdefault(owner, asyncio.Lock()):
            raw = await self.command(
                "exec",
                "-i",
                row["name"],
                "python3",
                "/opt/browser.py",
                input=json.dumps(action),
            )
            try:
                return json.loads(raw)
            except ValueError:
                raise HTTPException(502, "浏览器响应无效")

    def propose(self, owner, payload, reason):
        from .store import uid

        if (
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 2000
            or len(json.dumps(payload)) > 20000
        ):
            raise HTTPException(422, "需要有效操作和原因")
        self.owned(owner)
        action_id = uid()
        self.store.execute(
            "INSERT INTO computer_actions(id,owner_id,payload,reason) VALUES(?,?,?,?)",
            (action_id, owner, json.dumps(payload), reason),
        )
        return {"id": action_id, "status": "pending"}

    async def approve(self, owner, action_id):
        action = self.store.one(
            "SELECT * FROM computer_actions WHERE id=? AND owner_id=?",
            (action_id, owner),
        )
        if not action:
            raise HTTPException(404, "电脑操作不存在")
        if not self.store.execute(
            "UPDATE computer_actions SET status='executing' WHERE id=? AND status='pending'",
            (action_id,),
        ):
            raise HTTPException(409, "操作已处理，禁止重复执行")
        try:
            result = await self.control(owner, json.loads(action["payload"]))
            self.store.execute(
                "UPDATE computer_actions SET status='done' WHERE id=?", (action_id,)
            )
            return result
        except BaseException:
            self.store.execute(
                "UPDATE computer_actions SET status='unknown' WHERE id=?", (action_id,)
            )
            raise
