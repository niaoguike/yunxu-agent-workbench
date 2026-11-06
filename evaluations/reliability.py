"""Deterministic fault injection against production dispatch and merge code."""

import argparse
import asyncio
import json
from pathlib import Path

from workbench.config import Settings
from workbench.store import Store, now, uid
from workbench.workflow import Workflow


def fixture(root):
    settings = Settings(data_dir=root)
    store = Store(root / "workbench.sqlite3")
    workflow = Workflow(store, settings, None)
    workflow.schedule = lambda *args: (
        None
    )  # No native execution in fault-injection replay.
    owner, project, parent = uid(), uid(), uid()
    store.execute(
        "INSERT INTO users VALUES(?,?,?,'')",
        (owner, owner + "@example.com", "not-a-login"),
    )
    store.execute(
        "INSERT INTO projects(id,owner_id,name,created_at) VALUES(?,?,?,?)",
        (project, owner, "faults", now()),
    )
    store.execute(
        "INSERT INTO tasks(id,project_id,title,mode,engine,status,allow_collaboration,created_at) VALUES(?,?,?,'free','codex','running',1,?)",
        (parent, project, "leader", now()),
    )
    parent = store.one("SELECT * FROM tasks WHERE id=?", (parent,))
    return workflow, parent


async def run(root, batches=10, attempts=20):
    rows = []
    for batch in range(batches):
        for stable in (False, True):
            flow, parent = fixture(
                root / f"batch-{batch}-{'stable' if stable else 'fresh'}"
            )
            collab, store = flow.collaboration, flow.store
            main = flow.workspace(parent)
            (main / "result.txt").write_text("baseline")
            member = collab.create(
                parent["project_id"], "worker", "narrow role", "codex"
            )
            receipts = [
                collab.dispatch(
                    member,
                    "same logical request",
                    "stable" if stable else uid(),
                    parent,
                )
                for _ in range(attempts)
            ]
            accepted = len({r["assignment_id"] for r in receipts})
            rows.append(
                {
                    "scenario": "response_lost_retry",
                    "batch": batch,
                    "strategy": "stable_key" if stable else "fresh_key_baseline",
                    "attempts": attempts,
                    "accepted_assignments": accepted,
                    "duplicate_logical_assignments": accepted - 1,
                    "consistent_receipts": accepted == 1,
                }
            )
            if stable:
                # Restart the durable boundary after the original response was lost.
                store.connection.close()
                flow.store = Store(root / f"batch-{batch}-stable" / "workbench.sqlite3")
                collab.store = flow.store
                store = flow.store
                replay = collab.dispatch(
                    member, "same logical request", "stable", parent
                )
                rows.append(
                    {
                        "scenario": "restart_receipt",
                        "batch": batch,
                        "passed": replay["assignment_id"]
                        == receipts[0]["assignment_id"]
                        and replay["replayed"],
                    }
                )
                rejected = False
                try:
                    collab.dispatch(member, "changed payload", "stable", parent)
                except ValueError:
                    rejected = True
                rows.append(
                    {"scenario": "payload_conflict", "batch": batch, "passed": rejected}
                )
                task_id = receipts[0]["task_id"]
                store.execute(
                    "UPDATE tasks SET status='delivered' WHERE id=?", (task_id,)
                )
                child = store.one("SELECT * FROM tasks WHERE id=?", (task_id,))
                (flow.workspace(child) / "result.txt").write_text("worker change")
                (main / "result.txt").write_text("leader concurrent edit")
                rejected = False
                try:
                    await collab.tool(
                        parent["id"],
                        "apply_worker_file",
                        {"task_id": task_id, "path": "result.txt"},
                    )
                except ValueError:
                    rejected = True
                rows.append(
                    {
                        "scenario": "merge_conflict",
                        "batch": batch,
                        "passed": rejected
                        and (main / "result.txt").read_text()
                        == "leader concurrent edit",
                    }
                )
                (main / "result.txt").write_text("baseline")
                args = {"task_id": task_id, "path": "result.txt"}
                first = await collab.tool(parent["id"], "apply_worker_file", args)
                again = await collab.tool(parent["id"], "apply_worker_file", args)
                rows.append(
                    {
                        "scenario": "merge_replay",
                        "batch": batch,
                        "passed": first["applied"]
                        and again.get("already_applied")
                        and (main / "result.txt").read_text() == "worker change",
                    }
                )
            store.connection.close()
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    rows = asyncio.run(run(args.output / "data"))
    (args.output / "results.json").write_text(
        json.dumps(rows, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "rows": len(rows),
                "invariant_passes": sum(r.get("passed", False) for r in rows),
                "duplicate_assignments": {
                    strategy: sum(
                        r.get("duplicate_logical_assignments", 0)
                        for r in rows
                        if r.get("strategy") == strategy
                    )
                    for strategy in ("fresh_key_baseline", "stable_key")
                },
            }
        )
    )


if __name__ == "__main__":
    main()
