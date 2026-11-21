"""Opt-in real executor smoke: creates a draft only; no external account action."""

import json
import time
from pathlib import Path
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from workbench.api import create_app
from workbench.config import Settings

root = Path(__file__).resolve().parents[1]
load_dotenv(root / ".env", override=False)
data = root / ".scratch/personal-agent/live-assistant-check"
settings = Settings(data_dir=data)
if settings.runner == "disabled":
    print("LIVE_NOT_RUN: configured runner is disabled")
else:
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/register",
            json={
                "email": f"live-{time.time_ns()}@example.com",
                "password": "isolated-live-check-password",
            },
        )
        assert response.status_code == 201
        home = client.get("/api/personal").json()
        client.post(
            "/api/personal/messages",
            json={
                "content": '这是账户主助手能力验证。仅调用 goal_create 创建一个 draft，title 为 Personal Agent Smoke，desired_outcome 为验证主助手可以持久化目标，success_criteria 为 ["草稿已真实保存"]，kind 为 goal。不要激活，不要调用外部连接器，不要创建协作者，不要修改文件。成功后简短回复已创建草稿。'
            },
        )
        deadline = time.monotonic() + 90
        result = {"status": "timeout"}
        while time.monotonic() < deadline:
            goals = client.get("/api/goals").json()
            task = client.get("/api/personal").json()["task"]
            if goals:
                assert goals[0]["status"] == "draft"
                result = {
                    "status": "passed",
                    "goal_status": goals[0]["status"],
                    "goal_title": goals[0]["config"]["title"],
                    "task_status": task["status"],
                }
                break
            if task["status"] == "waiting_permission":
                pending = app.state.store.all(
                    "SELECT * FROM approvals WHERE task_id=? AND status='pending'",
                    (home["task_id"],),
                )
                approved = False
                for approval in pending:
                    detail = json.loads(approval["details"])
                    params = detail.get("details", {})
                    metadata = params.get("_meta") or params.get("meta") or {}
                    if (
                        detail.get("tool") == "mcpServer/elicitation/request"
                        and params.get("serverName") == "workbench"
                        and metadata.get("codex_approval_kind") == "mcp_tool_call"
                        and "goal_create" in json.dumps(params)
                    ):
                        response = client.post(
                            "/api/tasks/"
                            + home["task_id"]
                            + "/approvals/"
                            + approval["id"],
                            json={"decision": "accept"},
                        )
                        assert response.status_code == 200
                        approved = True
                if approved:
                    time.sleep(0.2)
                    continue
            if task["status"] in ("failed", "waiting_permission", "interrupted"):
                result = {"status": "unverified", "task_status": task["status"]}
                break
            time.sleep(0.5)
        if result["status"] != "passed":
            client.post("/api/tasks/" + home["task_id"] + "/stop")
        (data / ("result-" + str(time.time_ns()) + ".json")).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (data / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False))
