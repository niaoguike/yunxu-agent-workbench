"""Run: python -m evaluations.live --repeats 2. Uses real credentials and model calls."""

import argparse
import hashlib
import json
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi.testclient import TestClient

from workbench.api import create_app
from workbench.config import Settings
from .cases import CASES, VERSION, validate


def metrics(events):
    sessions, models, tools = {}, set(), set()
    for task_id, event in events:
        data = event["data"]
        if event["kind"] == "usage" and isinstance(data.get("cumulative"), dict):
            key = data.get("session_id") or data.get("review_run_id") or task_id
            total = data["cumulative"]
            if total.get("totalTokens", -1) >= sessions.get(key, {}).get(
                "totalTokens", -1
            ):
                sessions[key] = total
        if event["kind"] == "native_config" and data.get("model"):
            models.add(data["model"])
        if event["kind"] == "tool_activity" and data.get("status") not in {
            "started",
            "inProgress",
        }:
            tools.add((task_id, event.get("run_id"), data.get("item_id")))
    fields = (
        "inputTokens",
        "cachedInputTokens",
        "outputTokens",
        "reasoningOutputTokens",
        "totalTokens",
    )
    return {
        "tokens": {key: sum(s.get(key, 0) for s in sessions.values()) for key in fields}
        if sessions
        else None,
        "usage_sessions": len(sessions),
        "models": sorted(models),
        "tool_actions": len(tools),
        "native_subagent_events": sum(
            e["kind"] == "subagent_activity" for _, e in events
        ),
        "review_requests": sum(e["kind"] == "loop_review_request" for _, e in events),
        "usd_cost": None,
    }


def run_case(client, settings, case_id, mode, repetition, timeout, output):
    case = CASES[case_id]
    project = client.post(
        "/api/projects", json={"name": f"eval-{case_id}-{mode}-{repetition}"}
    ).json()
    for name, content in case["files"].items():
        response = client.put(
            f"/api/projects/{project['id']}/file",
            params={"path": name},
            json={"content": content},
        )
        response.raise_for_status()
    task = client.post(
        f"/api/projects/{project['id']}/tasks",
        json={
            "title": case["title"],
            "engine": "codex",
            "allow_collaboration": mode == "team",
        },
    ).json()
    policy = (
        "Use exactly one persistent codex worker through workbench MCP: create_worker, send_to_worker with stable assignment_id, wait_worker/read_worker until terminal, then apply_worker_file for output files. Delegate the substantive task, not a trivial marker. The leader independently verifies the outputs before final delivery. Never create a second worker or use native/ephemeral subagents. Worker must write valid delivery.json. "
        if mode == "team"
        else "Complete this task yourself. Do not create persistent workers or native/ephemeral subagents. "
    )
    prompt = (
        policy
        + case["prompt"]
        + "\nWork only inside this task workspace. No network or external paths. No questions are needed: all requirements are specified. Write delivery.json listing actual artifacts and checks with real evidence. Do not treat your own manifest as external validation."
    )
    started = time.monotonic()
    client.post(
        f"/api/tasks/{task['id']}/messages", json={"content": prompt}
    ).raise_for_status()
    timed_out = False
    while True:
        state = client.get(f"/api/tasks/{task['id']}").json()
        if state["status"] in {
            "delivered",
            "failed",
            "interrupted",
            "waiting_permission",
            "waiting_confirmation",
        }:
            break
        if time.monotonic() - started > timeout:
            timed_out = True
            break
        time.sleep(0.5)
    elapsed = time.monotonic() - started
    initial_status = state["status"]
    if initial_status != "delivered":
        client.post(f"/api/projects/{project['id']}/stop")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            group = client.get(f"/api/projects/{project['id']}/tasks").json()
            if not any(
                t["status"] in {"queued", "running", "stopping", "waiting_permission"}
                for t in group
            ):
                break
            time.sleep(0.2)
    group = client.get(f"/api/projects/{project['id']}/tasks").json()
    events = [
        (t["id"], event)
        for t in group
        for event in client.get(f"/api/tasks/{t['id']}/events").json()
    ]
    root = settings.data_dir / "workspaces" / project["id"] / "main"
    checks = validate(case_id, root)
    stats = metrics(events)
    assignments = client.app.state.store.all(
        "SELECT * FROM assignments WHERE parent_task_id=?", (task["id"],)
    )
    checks.append(
        {
            "name": "execution_policy",
            "passed": len(assignments) == (1 if mode == "team" else 0)
            and stats["native_subagent_events"] == 0,
        }
    )
    trace = output / f"{case_id}-{mode}-{repetition}.json"
    trace.write_text(
        json.dumps(
            {"tasks": group, "events": events, "checks": checks},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    row = {
        "case": case_id,
        "mode": mode,
        "repetition": repetition,
        "project_id": project["id"],
        "task_id": task["id"],
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "elapsed_seconds": round(elapsed, 3),
        "status": initial_status,
        "timed_out": timed_out,
        "passed": initial_status == "delivered"
        and not timed_out
        and all(c["passed"] for c in checks),
        "checks": checks,
        "group_tasks": len(group),
        "group_runs": sum(
            len(client.get(f"/api/tasks/{t['id']}").json()["runs"]) for t in group
        ),
        "artifacts": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in case["artifacts"]
            if (root / name).is_file()
        },
        **stats,
    }
    with (output / "results.jsonl").open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                key: row[key]
                for key in (
                    "case",
                    "mode",
                    "repetition",
                    "status",
                    "passed",
                    "elapsed_seconds",
                    "tokens",
                )
            }
        ),
        flush=True,
    )
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--cases", nargs="+", choices=list(CASES), default=list(CASES))
    parser.add_argument(
        "--modes", nargs="+", choices=["single", "team"], default=["single", "team"]
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 1 or not 30 <= args.timeout <= 1800:
        parser.error("repeats >= 1, timeout in [30,1800]")
    load_dotenv(".env")
    output = args.output or Path(".data/evaluations") / datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True, exist_ok=False)
    settings = Settings(
        data_dir=(output / "data").resolve(), max_run_seconds=args.timeout
    )
    if settings.runner != "trusted-local":
        parser.error(
            "This suite currently validates trusted-local only; configure it explicitly in .env"
        )
    meta = {
        "dataset_version": VERSION,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "repeats": args.repeats,
        "timeout": args.timeout,
        "cases": args.cases,
        "modes": args.modes,
        "python": platform.python_version(),
        "native_version": subprocess.check_output(
            [settings.codex_command, "--version"], text=True
        ).strip(),
        "git_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "dirty_tree": bool(subprocess.check_output(["git", "status", "--porcelain"])),
        "runner": settings.runner,
        "source_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [
                Path("evaluations/cases.py"),
                Path("evaluations/live.py"),
                Path("backend/workbench/runner.py"),
                Path("backend/workbench/workflow.py"),
            ]
        },
    }
    (output / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print("EVALUATION_OUTPUT", output, flush=True)
    rows = []
    with TestClient(create_app(settings)) as client:
        client.post(
            "/api/auth/register",
            json={
                "email": "evaluation@example.com",
                "password": "evaluation-isolated-password",
            },
        ).raise_for_status()
        for repetition in range(1, args.repeats + 1):
            for index, case_id in enumerate(args.cases):
                modes = (
                    args.modes
                    if (index + repetition) % 2
                    else list(reversed(args.modes))
                )
                for mode in modes:
                    rows.append(
                        run_case(
                            client,
                            settings,
                            case_id,
                            mode,
                            repetition,
                            args.timeout,
                            output,
                        )
                    )
    (output / "complete.json").write_text(
        json.dumps({"runs": len(rows), "passed": sum(r["passed"] for r in rows)}),
        encoding="utf-8",
    )
    print(
        "COMPLETE",
        len(rows),
        "runs",
        sum(r["passed"] for r in rows),
        "passed",
        flush=True,
    )


if __name__ == "__main__":
    main()
