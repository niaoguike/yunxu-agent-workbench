"""Platform HTTP + native event seam; test executor never runs in production."""

from pathlib import Path
import time

from fastapi.testclient import TestClient
from workbench.api import create_app
from workbench.config import Settings
from workbench.runtime import RuntimeEvent
from test_api import EngineFixture, wait_task
from test_api import setup as setup


class TeamEngine(EngineFixture):
    def __init__(self):
        super().__init__()
        self.responses = {}
        self.receipts = []
        self.foreign_task = None
        self.memory = False

    async def respond_tool(self, run_id, call_id, result):
        self.responses[(run_id, call_id)] = result

    def result(self, request, call_id):
        return self.responses[(request.run_id, call_id)]

    async def execute(self, request):
        if request.bridge_role == "worker":
            assert "主任务后续汇总" in request.instructions
            assert "仍必须如实报告" in request.instructions
            yield RuntimeEvent(
                "platform_tool",
                {
                    "call_id": "report",
                    "name": "send_to_lead",
                    "arguments": {"content": "Worker started"},
                },
            )
            assert self.result(request, "report")["ok"]
            yield RuntimeEvent(
                "platform_tool",
                {
                    "call_id": "denied",
                    "name": "create_worker",
                    "arguments": {
                        "name": "Recursive",
                        "role": "Do not allow",
                        "engine": "codex",
                    },
                },
            )
            assert not self.result(request, "denied")["ok"]
            async for event in super().execute(request):
                if event.kind == "completed" and event.data["status"] == "completed":
                    (Path(request.workspace) / "team.txt").write_text(
                        "member result", encoding="utf-8"
                    )
                yield event
            return
        yield RuntimeEvent(
            "platform_tool",
            {
                "call_id": "create",
                "name": "create_worker",
                "arguments": {
                    "name": "Builder",
                    "role": "Create a result",
                    "engine": "codex",
                },
            },
        )
        worker = self.result(request, "create")["result"]["worker"]
        assert self.result(request, "create")["result"]["accepted"] is False
        arguments = {
            "worker_id": worker["id"],
            "content": "Produce team.txt",
            "assignment_id": "stable-work-1",
        }
        for call in ("dispatch", "retry"):
            yield RuntimeEvent(
                "platform_tool",
                {"call_id": call, "name": "send_to_worker", "arguments": arguments},
            )
            self.receipts.append(self.result(request, call)["result"])
        if self.foreign_task:
            yield RuntimeEvent(
                "platform_tool",
                {
                    "call_id": "foreign",
                    "name": "read_worker",
                    "arguments": {"task_id": self.foreign_task},
                },
            )
            assert not self.result(request, "foreign")["ok"]
        task_id = self.receipts[-1]["task_id"]
        yield RuntimeEvent(
            "platform_tool",
            {
                "call_id": "wait",
                "name": "wait_worker",
                "arguments": {"task_id": task_id, "timeout": 10},
            },
        )
        if not self.result(request, "wait")["ok"]:
            yield RuntimeEvent("completed", {"status": "interrupted"})
            return
        assert self.result(request, "wait")["result"]["status"] == "delivered"
        yield RuntimeEvent(
            "platform_tool",
            {
                "call_id": "apply",
                "name": "apply_worker_file",
                "arguments": {"task_id": task_id, "path": "team.txt"},
            },
        )
        assert self.result(request, "apply")["result"]["applied"]
        if self.memory:
            yield RuntimeEvent(
                "platform_tool",
                {
                    "call_id": "memory",
                    "name": "memory_save",
                    "arguments": {
                        "category": "project",
                        "content": "Use UTC timestamps",
                    },
                },
            )
            assert self.result(request, "memory")["ok"]
        async for event in super().execute(request):
            yield event


def create_team_client(tmp_path, engine):
    return TestClient(create_app(Settings(data_dir=tmp_path), engine))


def test_collaboration_requires_opt_in_and_preserves_requirement_version(setup):
    client, project, _ = setup
    task = client.post(
        f"/api/projects/{project['id']}/tasks", json={"title": "Solo"}
    ).json()
    collaboration = client.app.state.workflow.collaboration
    import pytest

    with pytest.raises(ValueError):
        collaboration.leader(task["id"])
    enabled = client.patch(
        f"/api/tasks/{task['id']}", json={"allow_collaboration": True}
    ).json()
    assert enabled["version"] == task["version"]
    assert enabled["stage"] == task["stage"]
    assert collaboration.leader(task["id"])["allow_collaboration"]


def login_project(client):
    assert (
        client.post(
            "/api/auth/register",
            json={"email": "team@example.com", "password": "team-password-123"},
        ).status_code
        == 201
    )
    return client.post("/api/projects", json={"name": "Team"}).json()


def test_lead_dispatch_wait_report_and_conflict_checked_apply(tmp_path):
    engine = TeamEngine()
    engine.memory = True
    with create_team_client(tmp_path, engine) as client:
        project = login_project(client)
        client.patch(f"/api/projects/{project['id']}", json={"memory_enabled": True})
        other = client.post("/api/projects", json={"name": "Other"}).json()
        engine.foreign_task = client.post(
            f"/api/projects/{other['id']}/tasks", json={"title": "Private other task"}
        ).json()["id"]
        task = client.post(
            f"/api/projects/{project['id']}/tasks",
            json={"title": "Delegate", "allow_collaboration": True},
        ).json()
        client.post(
            f"/api/tasks/{task['id']}/messages",
            json={"content": "Complete with a persistent collaborator"},
        )
        result = wait_task(client, task["id"], {"delivered", "failed"})
        assert result["status"] == "delivered", result["runs"]
        assert engine.receipts[0]["task_id"] == engine.receipts[1]["task_id"]
        assert engine.receipts[1]["replayed"] is True
        assert len(client.get(f"/api/projects/{project['id']}/members").json()) == 1
        assert (
            client.get(
                f"/api/projects/{project['id']}/file", params={"path": "team.txt"}
            ).content
            == b"member result"
        )
        events = client.get(f"/api/tasks/{task['id']}/events").json()
        assert {
            "assignment_accepted",
            "collaboration_message",
            "collaboration_report",
            "worker_file_applied",
        } <= {event["kind"] for event in events}
        assert (
            client.get(f"/api/projects/{project['id']}/memories").json()[0]["content"]
            == "Use UTC timestamps"
        )


def test_stopping_lead_stops_its_member_and_preserves_assignment(tmp_path):
    engine = TeamEngine()
    engine.hold = True
    with create_team_client(tmp_path, engine) as client:
        project = login_project(client)
        task = client.post(
            f"/api/projects/{project['id']}/tasks",
            json={"title": "Stop team", "allow_collaboration": True},
        ).json()
        client.post(f"/api/tasks/{task['id']}/messages", json={"content": "Delegate"})
        for _ in range(300):
            tasks = client.get(f"/api/projects/{project['id']}/tasks").json()
            children = [item for item in tasks if item["parent_task_id"] == task["id"]]
            if children and children[0]["status"] == "running":
                break
            time.sleep(0.01)
        assert children
        client.post(f"/api/tasks/{task['id']}/stop")
        assert wait_task(client, task["id"], {"interrupted"})["status"] == "interrupted"
        assert (
            wait_task(client, children[0]["id"], {"interrupted"})["status"]
            == "interrupted"
        )
        assert engine.receipts[0]["accepted"]


def test_busy_member_queues_idempotently_and_stop_pauses_queue(setup):
    client, project, engine = setup
    engine.hold = True
    member = client.post(
        f"/api/projects/{project['id']}/members",
        json={"name": "Member", "role": "Work"},
    ).json()
    first = client.post(
        f"/api/members/{member['id']}/assign",
        json={"content": "first", "assignment_id": "one"},
    ).json()
    wait_task(client, first["id"], {"running"})
    body = {"content": "second", "assignment_id": "two"}
    queued = client.post(f"/api/members/{member['id']}/assign", json=body).json()
    replay = client.post(f"/api/members/{member['id']}/assign", json=body).json()
    assert queued["id"] == first["id"] == replay["id"]
    assert queued["dispatch"]["delivery"] == "queued"
    assert replay["dispatch"]["replayed"]
    assert len(client.get(f"/api/tasks/{first['id']}").json()["pending_inputs"]) == 1
    assert (
        client.post(
            f"/api/members/{member['id']}/assign",
            json={"content": "changed", "assignment_id": "two"},
        ).status_code
        == 409
    )
    client.post(f"/api/projects/{project['id']}/stop")
    result = wait_task(client, first["id"], {"interrupted"})
    assert result["pending_inputs"][0]["status"] == "paused"


def test_memory_index_and_scoped_chunk_read(setup):
    import asyncio
    import pytest
    client, project, _ = setup
    other = client.post("/api/projects", json={"name": "Private"}).json()
    content = "summary " + "x" * 3000
    memory = client.post(f"/api/projects/{project['id']}/memories",
                         json={"category": "project", "content": content}).json()
    foreign = client.post(f"/api/projects/{other['id']}/memories",
                          json={"category": "project", "content": "private"}).json()
    task = client.post(f"/api/projects/{project['id']}/tasks", json={"title": "Read"}).json()
    client.app.state.store.execute("UPDATE tasks SET status='running' WHERE id=?", (task["id"],))
    def call(name, args):
        return asyncio.run(client.app.state.workflow.collaboration.tool(task["id"], name, args))
    with pytest.raises(ValueError):
        call("memory_read", {"id": memory["id"]})
    client.patch(f"/api/projects/{project['id']}", json={"memory_enabled": True})
    assert call("memory_search", {"query": "summary"}) == []
    client.post(f"/api/memories/{memory['id']}/approve")
    index = call("memory_search", {"query": "summary"})
    assert len(index[0]["summary"]) == 80
    assert "content" not in index[0]
    first = call("memory_read", {"id": memory["id"]})
    assert first["content"] == content[:2000]
    assert first["next_offset"] == 2000
    last = call("memory_read", {"id": memory["id"], "offset": 2000})
    assert last["content"] == content[2000:]
    assert last["next_offset"] is None
    for args in ({"id": foreign["id"]}, {"id": memory["id"], "limit": 2001},
                 {"id": memory["id"], "offset": -1}):
        with pytest.raises(ValueError):
            call("memory_read", args)
