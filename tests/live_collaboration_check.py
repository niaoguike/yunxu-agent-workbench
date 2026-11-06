"""Opt-in credentialed integration, isolated data, real model calls. Not a pytest fixture."""

import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi.testclient import TestClient

from workbench.api import create_app
from workbench.config import Settings


def main():
    load_dotenv(".env")
    settings = Settings(
        data_dir=Path(".data/live-team-check").resolve(), max_run_seconds=300
    )
    with TestClient(create_app(settings)) as client:
        assert (
            client.post(
                "/api/auth/register",
                json={
                    "email": f"live-{time.time_ns()}@example.com",
                    "password": "live-team-check-password",
                },
            ).status_code
            == 201
        )
        project = client.post(
            "/api/projects", json={"name": "Live native collaboration check"}
        ).json()
        client.patch(f"/api/projects/{project['id']}", json={"memory_enabled": True})
        task = client.post(
            f"/api/projects/{project['id']}/tasks",
            json={
                "title": "Native team bridge check",
                "engine": "codex",
                "allow_collaboration": True,
            },
        ).json()
        prompt = 'Use the workbench MCP persistent-collaboration tools for this test. Create exactly one codex worker with a narrow role to create note.txt containing TEAM_BRIDGE_OK and a valid delivery.json listing note.txt with a passed file check. Dispatch with a stable assignment_id and wait for the worker result; do not substitute a native subagent. Apply note.txt via apply_worker_file, verify it, save the project fact "Use UTC timestamps" via memory_save, then write your own delivery.json listing note.txt and a passed check with real evidence. No network, no external paths, no extra workers. Report limitations honestly.'
        response = client.post(
            f"/api/tasks/{task['id']}/messages", json={"content": prompt}
        )
        assert response.status_code == 201, response.text
        print("LIVE_TASK_ID", task["id"], flush=True)
        previous = None
        for _ in range(310):
            result = client.get(f"/api/tasks/{task['id']}").json()
            if result["status"] != previous:
                print("status", result["status"], flush=True)
                previous = result["status"]
            if result["status"] in {
                "delivered",
                "failed",
                "waiting_permission",
                "interrupted",
            }:
                break
            time.sleep(1)
        print("FINAL", result["status"], flush=True)
        for run in result["runs"]:
            if run["error"]:
                print("run_error", run["error"], flush=True)
        events = client.get(f"/api/tasks/{task['id']}/events").json()
        print(
            "bridge_tools",
            [
                event["data"].get("name")
                for event in events
                if event["kind"] == "platform_tool"
            ],
            flush=True,
        )
        print(
            "members",
            len(client.get(f"/api/projects/{project['id']}/members").json()),
            flush=True,
        )
        assert result["status"] == "delivered", result["status"]
        assert (
            client.get(
                f"/api/projects/{project['id']}/file", params={"path": "note.txt"}
            ).text.strip()
            == "TEAM_BRIDGE_OK"
        )
        assert any(
            memory["content"] == "Use UTC timestamps"
            for memory in client.get(f"/api/projects/{project['id']}/memories").json()
        )
        print("LIVE COLLABORATION AND MEMORY CHECK PASSED", flush=True)


if __name__ == "__main__":
    main()
