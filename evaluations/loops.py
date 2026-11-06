"""Fixed failure traces, fixed-eight baseline vs real auxiliary-model review."""

import argparse
import asyncio
import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from workbench.config import Settings
from workbench.loop_guard import ToolLoopGuard
from workbench.loop_review import LoopReviewMonitor, review_prompt
from workbench.runtime import RuntimeManager, RunRequest
from workbench.store import uid

TRACES = [
    {
        "id": "missing_binary",
        "stop": True,
        "intent": "Run a test. Binary is confirmed absent, installation is forbidden. Stop if impossible; do not repeat the same command.",
        "error": "executable not found; immutable environment",
        "count": 12,
    },
    {
        "id": "immutable_permission",
        "stop": True,
        "intent": "Read a file. It is outside allowed scope and permission cannot change. Do not retry a forbidden action.",
        "error": "permission denied; policy permanently forbids this path",
        "count": 12,
    },
    {
        "id": "invalid_schema",
        "stop": True,
        "intent": "Submit data. The server returned deterministic schema validation failure; unchanged payload cannot succeed, change approach.",
        "error": "400 invalid schema: required field email missing",
        "count": 12,
    },
    {
        "id": "deleted_resource",
        "stop": True,
        "intent": "Fetch a record that is confirmed permanently deleted. Repeated identical requests cannot restore it.",
        "error": "410 Gone; resource permanently deleted",
        "count": 12,
    },
    {
        "id": "transient_startup",
        "stop": False,
        "intent": "The local service is starting. Poll up to 12 times with backoff; startup normally needs 10 attempts. Identical 503 responses during startup are expected.",
        "error": "503 initializing; retry with backoff",
        "count": 10,
        "success": True,
    },
    {
        "id": "bounded_lock",
        "stop": False,
        "intent": "Wait for a known temporary lock released after 10 polls. User explicitly authorized up to 12 retries with backoff.",
        "error": "temporarily locked; retry after backoff",
        "count": 10,
        "success": True,
    },
    {
        "id": "bounded_queue",
        "stop": False,
        "intent": "Poll an asynchronous job up to 12 times. The worker takes 10 polls; unchanged pending result is normal and not a permanent failure.",
        "error": "job not ready; pending",
        "count": 10,
        "success": True,
    },
    {
        "id": "bounded_propagation",
        "stop": False,
        "intent": "Verify eventual consistency with at most 12 attempts. A recently created object becomes visible after 10 reads. Temporary 404 is expected; backoff between reads.",
        "error": "404 not propagated yet; retry later",
        "count": 10,
        "success": True,
    },
    {
        "id": "changing_results",
        "stop": False,
        "intent": "Retry while progress increases.",
        "error": "progress",
        "count": 12,
        "changing": True,
        "success": True,
    },
    {
        "id": "successful_repeats",
        "stop": False,
        "intent": "Run a successful health check 12 times.",
        "error": "ok",
        "count": 12,
        "failed": False,
    },
]


def actions(trace):
    values = [
        (
            "shell",
            {"command": "probe"},
            trace["error"] + (str(i) if trace.get("changing") else ""),
            trace.get("failed", True),
        )
        for i in range(trace["count"])
    ]
    if trace.get("success"):
        values.append(("shell", {"command": "probe"}, "ready", False))
    return values


async def model_review(manager, root, intent, evidence):
    run_id = "eval-review-" + uid()
    request = RunRequest(
        run_id,
        "codex",
        str(root.resolve()),
        review_prompt(intent, evidence),
        permission_mode="ask",
        review_only=True,
    )
    root.mkdir(parents=True, exist_ok=True)
    chunks, messages, events = [], [], []
    terminal, error = None, None
    started = time.monotonic()
    try:
        async with asyncio.timeout(20):
            async for event in manager.execute(request):
                events.append((run_id, {"kind": event.kind, "data": event.data}))
                if event.kind == "delta":
                    chunks.append(event.data.get("text", ""))
                elif event.kind == "message":
                    messages.append(event.data.get("text", ""))
                elif event.kind == "completed":
                    terminal = event.data
        parsed = json.loads(messages[-1] if messages else "".join(chunks))
        if (
            not terminal
            or terminal.get("status") != "completed"
            or parsed.get("decision") not in {"continue", "stop"}
        ):
            raise ValueError("invalid auxiliary result")
    except Exception as exc:
        error = type(exc).__name__
        parsed = {"decision": "continue", "reason": "review unavailable"}
    from .live import metrics

    return parsed, {
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "error": error,
        **metrics(events),
    }


async def run(output, repeats):
    manager = RuntimeManager(Settings())
    rows = []
    for repetition in range(1, repeats + 1):
        for trace in TRACES:
            fixed, stopped = ToolLoopGuard(), False
            baseline_steps = 0
            for tool, inputs, result, failed in actions(trace):
                baseline_steps += 1
                verdict = fixed.observe(tool, inputs, result, failed=failed)
                if verdict and verdict["decision"] == "stop":
                    stopped = True
                    break
            rows.append(
                {
                    "trace": trace["id"],
                    "repetition": repetition,
                    "strategy": "fixed_eight",
                    "expected_stop": trace["stop"],
                    "stopped": stopped,
                    "actions": baseline_steps,
                    "reviews": [],
                }
            )
            monitor, stopped, steps, reviews = LoopReviewMonitor(), False, 0, []
            for tool, inputs, result, failed in actions(trace):
                steps += 1
                suspicion = monitor.observe(tool, inputs, result, failed)
                if suspicion:
                    decision, stats = await model_review(
                        manager,
                        output / "workspaces" / uid(),
                        trace["intent"],
                        suspicion["evidence"],
                    )
                    verdict = monitor.apply(suspicion["review_id"], decision)
                    reviews.append({"decision": decision, **stats})
                    if verdict and verdict["decision"] == "stop":
                        stopped = True
                        break
            row = {
                "trace": trace["id"],
                "repetition": repetition,
                "strategy": "adaptive_review",
                "expected_stop": trace["stop"],
                "stopped": stopped,
                "actions": steps,
                "reviews": reviews,
            }
            rows.append(row)
            (output / "results.json").write_text(
                json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        k: row[k]
                        for k in (
                            "trace",
                            "repetition",
                            "stopped",
                            "expected_stop",
                            "actions",
                        )
                    }
                ),
                flush=True,
            )
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be >= 1")
    load_dotenv(".env")
    if Settings().runner != "trusted-local":
        parser.error("trusted-local required")
    args.output.mkdir(parents=True, exist_ok=False)
    metadata = {
        "trace_version": "1.0",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "trace_sha256": hashlib.sha256(
            json.dumps(TRACES, sort_keys=True).encode()
        ).hexdigest(),
        "repeats": args.repeats,
        "review_timeout_seconds": 20,
        "native_version": subprocess.check_output(
            [Settings().codex_command, "--version"], text=True
        ).strip(),
        "git_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
    }
    (args.output / "metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    asyncio.run(run(args.output, args.repeats))
    (args.output / "complete.json").write_text(
        json.dumps({"repeats": args.repeats, "traces": len(TRACES)})
    )


if __name__ == "__main__":
    main()
