"""HTTP acceptance checks with a deterministic native-engine boundary fixture.

The fixture validates platform behavior only; it is never available in production.
"""

import asyncio
import io
import json
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from workbench.api import create_app
from workbench.config import Settings
from workbench.runtime import RuntimeEvent


class EngineFixture:
    def __init__(self):
        self.requests = []
        self.hold = False
        self.stopped = set()

    async def readiness(self):
        return [
            {"engine": name, "available": True, "reason": "test boundary"}
            for name in ("codex", "claude")
        ]

    async def execute(self, request):
        self.requests.append(request)
        yield RuntimeEvent("session", {"session_id": "native-" + request.run_id})
        while self.hold and request.run_id not in self.stopped:
            await asyncio.sleep(0.01)
        if request.run_id in self.stopped:
            yield RuntimeEvent("completed", {"status": "interrupted"})
            return
        for name in (
            "requirements.md",
            "design.md",
            "tasks.md",
            "checks.md",
            "acceptance.md",
            "delivery.md",
        ):
            (Path(request.workspace) / name).write_text(
                "fixture evidence " + request.run_id, encoding="utf-8"
            )
        (Path(request.workspace) / "delivery.json").write_text(
            json.dumps(
                {
                    "deliverables": ["delivery.md"],
                    "checks": [
                        {
                            "name": "fixture check",
                            "status": "passed",
                            "evidence": "protocol fixture " + request.run_id,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        yield RuntimeEvent("delta", {"text": "actual boundary fixture response"})
        yield RuntimeEvent("completed", {"status": "completed"})

    async def stop(self, run_id):
        self.stopped.add(run_id)

    async def approve(self, *args):
        return None


@pytest.fixture
def setup(tmp_path):
    engine = EngineFixture()
    app = create_app(Settings(data_dir=tmp_path), runtime=engine)
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/auth/register",
                json={"email": "owner@example.com", "password": "safe-password-123"},
            ).status_code
            == 201
        )
        project = client.post("/api/projects", json={"name": "Research"}).json()
        yield client, project, engine


def task(client, project, mode="free"):
    result = client.post(
        f"/api/projects/{project['id']}/tasks",
        json={
            "title": "Build report",
            "mode": mode,
            "engine": "codex",
            "require_acceptance": True,
        },
    )
    assert result.status_code == 201
    return result.json()


def wait_task(client, task_id, status):
    for _ in range(300):
        result = client.get(f"/api/tasks/{task_id}").json()
        if result["status"] in status:
            return result
        time.sleep(0.01)
    raise AssertionError(result)


def test_owner_isolation_and_logout(setup):
    client, project, _ = setup
    client.post("/api/auth/logout")
    assert client.get("/api/projects").status_code == 401
    client.post(
        "/api/auth/register",
        json={"email": "other@example.com", "password": "safe-password-456"},
    )
    assert client.get("/api/projects").json() == []
    for suffix in ("", "/files", "/memories", "/members", "/extensions", "/tasks"):
        assert client.get(f"/api/projects/{project['id']}{suffix}").status_code == 404


def test_native_success_requires_acceptance_and_keeps_artifact(setup):
    client, project, engine = setup
    item = task(client, project)
    assert (
        client.post(
            f"/api/tasks/{item['id']}/messages", json={"content": "Create report"}
        ).status_code
        == 201
    )
    result = wait_task(client, item["id"], {"waiting_confirmation"})
    assert result["runs"][0]["status"] == "completed"
    assert result["messages"][-1]["content"] == "actual boundary fixture response"
    assert result["artifacts"]
    gate = result["gates"][-1]
    assert (
        client.post(
            f"/api/tasks/{item['id']}/gates/{gate['id']}", json={"decision": "approve"}
        ).status_code
        == 200
    )
    assert client.get(f"/api/tasks/{item['id']}").json()["status"] == "delivered"
    artifact = result["artifacts"][0]
    saved = client.get(f"/api/artifacts/{artifact['id']}").content
    assert b"fixture" in saved
    client.put(
        f"/api/projects/{project['id']}/file",
        params={"path": artifact["path"]},
        json={"content": "changed"},
    )
    assert client.get(f"/api/artifacts/{artifact['id']}").content == saved
    assert engine.requests[0].session_id is None


def test_new_requirement_invalidates_old_confirmation(setup):
    client, project, _ = setup
    item = task(client, project)
    client.post(f"/api/tasks/{item['id']}/messages", json={"content": "First"})
    first = wait_task(client, item["id"], {"waiting_confirmation"})
    client.post(
        f"/api/tasks/{item['id']}/messages", json={"content": "Changed requirement"}
    )
    second = wait_task(client, item["id"], {"waiting_confirmation"})
    assert second["version"] == 2
    assert (
        client.post(
            f"/api/tasks/{item['id']}/gates/{first['gates'][0]['id']}",
            json={"decision": "approve"},
        ).status_code
        == 409
    )


def test_new_tasks_use_one_entry_and_reject_fixed_development_pipeline(setup):
    client, project, _ = setup
    base = f"/api/projects/{project['id']}/tasks"
    created = client.post(base, json={"title": "Unified task"})
    assert created.status_code == 201
    assert created.json()["mode"] == "free"
    assert (
        client.post(
            base, json={"title": "Old pipeline", "mode": "development"}
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "path",
    [
        "../secret.txt",
        "/etc/passwd",
        ".native/auth.json",
        "foo/../../x",
        "C:/secret",
        "foo\\bar",
    ],
)
def test_file_traversal_rejected(setup, path):
    client, project, _ = setup
    assert (
        client.put(
            f"/api/projects/{project['id']}/file",
            params={"path": path},
            json={"content": "x"},
        ).status_code
        == 400
    )


def test_zip_import_rejects_traversal_before_writes(setup):
    client, project, _ = setup
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("good.txt", "valid")
        archive.writestr("../escape.txt", "invalid")
    response = client.post(
        f"/api/projects/{project['id']}/upload",
        files={"file": ("source.zip", buffer.getvalue(), "application/zip")},
    )
    assert response.status_code == 400
    assert client.get(f"/api/projects/{project['id']}/files").json() == []


def test_stop_cancels_native_run_and_does_not_deliver(setup):
    client, project, engine = setup
    engine.hold = True
    item = task(client, project)
    client.post(f"/api/tasks/{item['id']}/messages", json={"content": "Work"})
    wait_task(client, item["id"], {"running"})
    assert (
        client.post(
            f"/api/tasks/{item['id']}/messages", json={"content": "race"}
        ).status_code
        == 201
    )
    client.post(f"/api/tasks/{item['id']}/stop")
    result = wait_task(client, item["id"], {"interrupted"})
    assert result["gates"] == []
    assert result["pending_inputs"][0]["status"] == "paused"


@pytest.mark.parametrize("enabled", [False, True])
def test_native_compaction_digest_and_rebuilt_history(setup, enabled):
    client, project, engine = setup
    client.patch(f"/api/projects/{project['id']}", json={"memory_enabled": enabled})
    original = engine.execute

    async def compacting(request):
        async for event in original(request):
            yield event
            if event.kind == "session":
                yield RuntimeEvent("context_compacted", {"summary": "Done: report; pending: publish."})

    engine.execute = compacting
    item = task(client, project)
    client.post(f"/api/tasks/{item['id']}/messages", json={"content": "Initial goal"})
    wait_task(client, item["id"], {"waiting_confirmation"})
    memories = client.get(f"/api/projects/{project['id']}/memories").json()
    assert len(memories) == int(enabled)
    if enabled:
        assert "Done: report" in memories[0]["content"]
    client.post(f"/api/tasks/{item['id']}/messages", json={"content": "New constraint"})
    wait_task(client, item["id"], {"waiting_confirmation"})
    assert "New constraint" in engine.requests[-1].prompt
    # New requirements deliberately reset the native session in the API.
    assert engine.requests[-1].session_id is None
    assert "Initial goal" in engine.requests[-1].prompt
    assert "Initial goal" in engine.requests[-1].replay_prompt


def test_workflow_native_resume_sends_only_new_input(setup):
    from workbench.store import uid, now

    client, project, engine = setup
    item = task(client, project)
    client.post(f"/api/tasks/{item['id']}/messages", json={"content": "Initial goal"})
    first = wait_task(client, item["id"], {"waiting_confirmation"})
    # Exercise an outer-workflow continuation without the API's intentional
    # requirement-change/session-reset behavior.
    client.app.state.store.execute("INSERT INTO messages VALUES(?,?,?,?,?)", (uid(), item["id"], "user", "Continue existing work", now()))
    client.portal.call(client.app.state.workflow.enqueue, item["id"])
    wait_task(client, item["id"], {"waiting_confirmation"})
    assert engine.requests[-1].session_id == first["session_id"]
    assert "Initial goal" not in engine.requests[-1].prompt
    assert "Continue existing work" in engine.requests[-1].prompt
    assert "Initial goal" in engine.requests[-1].replay_prompt


def test_failed_compaction_preserves_result_and_rebuilds_next_session(setup):
    client, project, engine = setup
    original = engine.execute

    async def failing(request):
        async for event in original(request):
            if event.kind == "completed":
                yield RuntimeEvent("context_compaction_failed", {"needs_rollover": True})
            yield event

    engine.execute = failing
    item = task(client, project)
    client.post(f"/api/tasks/{item['id']}/messages", json={"content": "Initial goal"})
    result = wait_task(client, item["id"], {"waiting_confirmation"})
    assert result["session_id"] is None
    assert result["runs"][-1]["status"] == "completed"
    client.post(f"/api/tasks/{item['id']}/messages", json={"content": "Continue"})
    wait_task(client, item["id"], {"waiting_confirmation"})
    assert engine.requests[-1].session_id is None
    assert "Initial goal" in engine.requests[-1].prompt


def test_memory_opt_in_and_skill_snapshot(setup):
    client, project, engine = setup
    client.post(
        f"/api/projects/{project['id']}/memories",
        json={"category": "project", "content": "Use UTC timestamps" + "x" * 100 + "BODY_ONLY"},
    )
    skill = client.post(
        f"/api/projects/{project['id']}/extensions",
        json={"name": "report", "kind": "skill", "content": "Cite sources"},
    ).json()
    item = task(client, project)
    client.post(f"/api/tasks/{item['id']}/messages", json={"content": "first"})
    wait_task(client, item["id"], {"waiting_confirmation"})
    assert "Use UTC" not in engine.requests[-1].instructions
    assert "Cite sources" in engine.requests[-1].instructions
    client.patch(f"/api/projects/{project['id']}", json={"memory_enabled": True})
    client.patch(f"/api/extensions/{skill['id']}", json={"enabled": False})
    client.post(f"/api/tasks/{item['id']}/messages", json={"content": "second"})
    wait_task(client, item["id"], {"waiting_confirmation"})
    assert "Use UTC timestamps" in engine.requests[-1].instructions
    assert "BODY_ONLY" not in engine.requests[-1].instructions
    assert "memory_read" in engine.requests[-1].instructions
    assert "Cite sources" not in engine.requests[-1].instructions


def test_collaborator_merge_rejects_changed_baseline(setup):
    client, project, _ = setup
    base = f"/api/projects/{project['id']}"
    client.put(
        base + "/file", params={"path": "requirements.md"}, json={"content": "baseline"}
    )
    member = client.post(
        base + "/members",
        json={"name": "Reviewer", "role": "Review code", "engine": "codex"},
    ).json()
    item = client.post(
        f"/api/members/{member['id']}/assign", json={"content": "Review"}
    ).json()
    wait_task(client, item["id"], {"delivered"})
    client.put(
        base + "/file",
        params={"path": "requirements.md"},
        json={"content": "main changed"},
    )
    assert (
        client.post(
            base + f"/integrate/{item['id']}", json={"path": "requirements.md"}
        ).status_code
        == 409
    )


def test_free_mode_delivers_without_unrequested_gate(setup):
    client, project, _ = setup
    item = client.post(
        f"/api/projects/{project['id']}/tasks", json={"title": "Free work"}
    ).json()
    client.post(
        f"/api/tasks/{item['id']}/messages", json={"content": "Deliver a report"}
    )
    result = wait_task(client, item["id"], {"delivered"})
    assert result["gates"] == []
    assert any(
        check["source"] == "platform" and check["status"] == "passed"
        for check in result["checks"]
    )
    assert any(check["source"] == "engine_reported" for check in result["checks"])


def test_workspace_edit_invalidates_pending_gate(setup):
    client, project, _ = setup
    item = task(client, project)
    client.post(f"/api/tasks/{item['id']}/messages", json={"content": "Write"})
    result = wait_task(client, item["id"], {"waiting_confirmation"})
    gate = result["gates"][0]
    client.put(
        f"/api/projects/{project['id']}/file",
        params={"path": "delivery.md"},
        json={"content": "changed"},
    )
    assert (
        client.post(
            f"/api/tasks/{item['id']}/gates/{gate['id']}", json={"decision": "approve"}
        ).status_code
        == 409
    )


def test_invalid_manifest_has_one_repair_then_preserves_failed_checks(setup):
    client, project, engine = setup
    original = engine.execute

    async def invalid(request):
        async for event in original(request):
            if event.kind == "completed":
                (Path(request.workspace) / "delivery.json").write_text("{}")
            yield event

    engine.execute = invalid
    item = task(client, project)
    client.post(f"/api/tasks/{item['id']}/messages", json={"content": "Produce"})
    for _ in range(300):
        result = client.get(f"/api/tasks/{item['id']}").json()
        if len(result["runs"]) == 2 and result["status"] == "failed":
            break
        time.sleep(0.01)
    assert len(result["runs"]) == 2
    assert result["status"] == "failed"
    assert all(check["status"] == "failed" for check in result["checks"])


def test_cross_origin_write_rejected(setup):
    client, _, _ = setup
    assert (
        client.post(
            "/api/projects",
            json={"name": "bad"},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )


def test_project_recycle_revokes_access_and_preserves_files(setup):
    client, project, _ = setup
    base = f"/api/projects/{project['id']}"
    client.put(
        base + "/file", params={"path": "report.txt"}, json={"content": "retained"}
    )
    assert client.delete(base).status_code == 200
    assert client.get(base + "/files").status_code == 404
    assert client.get("/api/projects").json() == []
    assert client.get("/api/trash").json()[0]["id"] == project["id"]
    assert client.post(base + "/restore").status_code == 200
    assert (
        client.get(base + "/file", params={"path": "report.txt"}).content == b"retained"
    )


def test_engine_handoff_retains_history_but_resets_native_identity(setup):
    client, project, engine = setup
    item = task(client, project)
    client.post(f"/api/tasks/{item['id']}/messages", json={"content": "First goal"})
    wait_task(client, item["id"], {"waiting_confirmation"})
    assert (
        client.patch(
            f"/api/tasks/{item['id']}", json={"engine": "claude", "mode": "free"}
        ).status_code
        == 200
    )
    client.post(
        f"/api/tasks/{item['id']}/messages",
        json={"content": "Continue explicitly on Claude"},
    )
    result = wait_task(client, item["id"], {"waiting_confirmation"})
    assert engine.requests[-1].engine == "claude"
    assert engine.requests[-1].session_id is None
    assert "First goal" in engine.requests[-1].prompt
    assert result["version"] == 2


def test_static_preview_is_sandboxed(setup):
    client, project, _ = setup
    base = f"/api/projects/{project['id']}"
    client.put(
        base + "/file",
        params={"path": "index.html"},
        json={"content": '<script>fetch("/api/projects")</script>'},
    )
    result = client.get(base + "/preview", params={"path": "index.html"})
    assert result.status_code == 200
    assert "sandbox;" in result.headers["content-security-policy"]
    assert "default-src 'none'" in result.headers["content-security-policy"]


def test_pending_inputs_can_be_edited_withdrawn_and_stay_paused(setup):
    client, project, engine = setup
    engine.hold = True
    item = task(client, project)
    base = f"/api/tasks/{item['id']}"
    client.post(base + "/messages", json={"content": "First"})
    wait_task(client, item["id"], {"running"})
    pending = client.post(base + "/messages", json={"content": "Original"}).json()
    assert (
        client.patch(
            base + f"/inputs/{pending['id']}", json={"content": "Edited"}
        ).status_code
        == 200
    )
    assert client.delete(base + f"/inputs/{pending['id']}").status_code == 200
    assert (
        client.patch(
            base + f"/inputs/{pending['id']}", json={"content": "Too late"}
        ).status_code
        == 409
    )
    client.post(base + "/stop")
    result = wait_task(client, item["id"], {"interrupted"})
    assert result["pending_inputs"][0]["status"] == "withdrawn"


def test_artifact_restore_requires_explicit_path_and_invalidates_gate(setup):
    client, project, _ = setup
    item = task(client, project)
    client.post(f"/api/tasks/{item['id']}/messages", json={"content": "Create"})
    result = wait_task(client, item["id"], {"waiting_confirmation"})
    artifact = result["artifacts"][0]
    saved = client.get(f"/api/artifacts/{artifact['id']}").content
    client.put(
        f"/api/projects/{project['id']}/file",
        params={"path": artifact["path"]},
        json={"content": "overwrite"},
    )
    assert (
        client.post(
            f"/api/artifacts/{artifact['id']}/restore", json={"path": "wrong"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/artifacts/{artifact['id']}/restore", json={"path": artifact["path"]}
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/projects/{project['id']}/file", params={"path": artifact["path"]}
        ).content
        == saved
    )


def test_unavailable_engine_does_not_create_fake_response(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        client.post(
            "/api/auth/register",
            json={"email": "a@example.com", "password": "strong-password"},
        )
        project = client.post("/api/projects", json={"name": "Empty"}).json()
        item = task(client, project)
        assert (
            client.post(
                f"/api/tasks/{item['id']}/messages", json={"content": "run"}
            ).status_code
            == 409
        )
        result = client.get(f"/api/tasks/{item['id']}").json()
        assert result["messages"] == []
        assert result["runs"] == []


def test_queued_inputs_dispatch_in_order_before_another_stage(setup):
    client, project, engine = setup
    engine.hold = True
    item = task(client, project)
    base = f"/api/tasks/{item['id']}"
    client.post(base + "/messages", json={"content": "Initial requirement"})
    wait_task(client, item["id"], {"running"})
    client.post(base + "/messages", json={"content": "Second requirement"})
    client.post(base + "/messages", json={"content": "Third requirement"})
    engine.hold = False
    result = wait_task(client, item["id"], {"waiting_confirmation"})
    assert result["version"] == 3
    assert result["stage"] == 0
    assert len(engine.requests) == 3
    assert "Second requirement" not in engine.requests[0].prompt
    assert "Third requirement" not in engine.requests[1].prompt
    assert all(entry["status"] == "dispatched" for entry in result["pending_inputs"])


def test_restart_pauses_queued_project_when_inflight_result_is_uncertain(tmp_path):
    engine = EngineFixture()
    engine.hold = True
    settings = Settings(data_dir=tmp_path)
    with TestClient(create_app(settings, engine)) as client:
        client.post(
            "/api/auth/register",
            json={"email": "restart@example.com", "password": "safe-password-123"},
        )
        project = client.post("/api/projects", json={"name": "Persistent"}).json()
        first, second = task(client, project), task(client, project)
        client.post(
            f"/api/tasks/{first['id']}/messages", json={"content": "In progress"}
        )
        wait_task(client, first["id"], {"running"})
        client.post(f"/api/tasks/{second['id']}/messages", json={"content": "Queued"})
        wait_task(client, second["id"], {"queued"})
    restarted = EngineFixture()
    with TestClient(create_app(settings, restarted)) as client:
        assert (
            client.post(
                "/api/auth/login",
                json={"email": "restart@example.com", "password": "safe-password-123"},
            ).status_code
            == 200
        )
        assert client.get(f"/api/tasks/{first['id']}").json()["status"] == "interrupted"
        assert client.get(f"/api/tasks/{second['id']}").json()["status"] == "paused"
        assert restarted.requests == []


def test_permission_mode_roundtrip_and_stage_gate_stays_independent(setup):
    client, project, engine = setup
    item = client.post(
        f"/api/projects/{project['id']}/tasks",
        json={"title": "Auto requirement", "require_acceptance": True},
    ).json()
    assert item["permission_mode"] == "auto"
    base = f"/api/tasks/{item['id']}"
    client.post(base + "/messages", json={"content": "Define requirements"})
    first = wait_task(client, item["id"], {"waiting_confirmation"})
    assert engine.requests[-1].permission_mode == "auto"
    assert first["stage"] == 0
    assert first["gates"][-1]["status"] == "pending"
    assert client.patch(base, json={"permission_mode": "ask"}).status_code == 200
    changed = client.get(base).json()
    assert changed["version"] == first["version"]
    assert changed["gates"][-1]["status"] == "pending"
    assert changed["status"] == "waiting_confirmation"
    client.post(base + "/messages", json={"content": "Continue with Ask"})
    second = wait_task(client, item["id"], {"waiting_confirmation"})
    assert engine.requests[-1].permission_mode == "ask"
    assert engine.requests[-1].session_id is None
    assert json.loads(second["runs"][-1]["snapshot"])["permission_mode"] == "ask"
    assert (
        client.patch(base, json={"permission_mode": "full-access"}).status_code == 422
    )


def test_permission_mode_cannot_change_while_a_native_turn_is_active(setup):
    client, project, engine = setup
    engine.hold = True
    item = task(client, project)
    base = f"/api/tasks/{item['id']}"
    client.post(base + "/messages", json={"content": "Work"})
    wait_task(client, item["id"], {"running"})
    assert client.patch(base, json={"permission_mode": "ask"}).status_code == 409
    client.post(base + "/stop")
    wait_task(client, item["id"], {"interrupted"})
    assert client.patch(base, json={"permission_mode": "ask"}).status_code == 200


def test_cross_device_resume_survives_restart(tmp_path):
    settings = Settings(data_dir=tmp_path)
    credentials = {"email": "resume@example.com", "password": "safe-password-123"}
    with TestClient(create_app(settings, runtime=EngineFixture())) as first:
        assert first.post("/api/auth/register", json=credentials).status_code == 201
        assert first.get("/api/resume").json() is None
        project = first.post("/api/projects", json={"name": "Continue"}).json()
        item = task(first, project)
        root = tmp_path / "workspaces" / project["id"] / "main"
        root.mkdir(parents=True, exist_ok=True)
        (root / "notes.txt").write_text("persistent file", encoding="utf-8")
        assert first.put(f"/api/resume/{item['id']}").status_code == 200
        cookie = first.cookies.get("workbench_session")
    with TestClient(create_app(settings, runtime=EngineFixture())) as second:
        assert second.get("/api/resume").status_code == 401
        assert second.post("/api/auth/login", json=credentials).status_code == 200
        assert second.get("/api/resume").json() == {
            "task_id": item["id"], "project_id": project["id"]
        }
        assert second.get(f"/api/tasks/{item['id']}").status_code == 200
        assert (root / "notes.txt").read_text(encoding="utf-8") == "persistent file"
        assert second.get(f"/api/projects/{project['id']}/files").status_code == 200
        second.cookies.set("workbench_session", cookie)
        assert second.get("/api/auth/me").status_code == 200
        second.cookies.clear()
        second.post("/api/auth/register", json={**credentials, "email": "other@example.com"})
        assert second.get("/api/resume").json() is None
        assert second.put(f"/api/resume/{item['id']}").status_code == 404
        second.post("/api/auth/login", json=credentials)
        second.delete(f"/api/projects/{project['id']}")
        assert second.get("/api/resume").json() is None
