"""JSONL process boundary for native executors. stdout is protocol only."""

import asyncio
import json
import sys
import uuid
import threading
import os
from pathlib import Path
from .permissions import codex_permissions, claude_needs_human
from .loop_guard import ToolLoopGuard


def emit(kind, **data):
    print(json.dumps({"kind": kind, "data": data}), flush=True)


async def main():
    loop = asyncio.get_running_loop()
    lines = asyncio.Queue()

    def read_stdin():
        for line in sys.stdin:
            try:
                loop.call_soon_threadsafe(lines.put_nowait, line)
            except RuntimeError:
                return
        try:
            loop.call_soon_threadsafe(lines.put_nowait, None)
        except RuntimeError:
            pass

    threading.Thread(target=read_stdin, daemon=True).start()
    config = json.loads(await lines.get())
    request = config["request"]
    permission_mode = request.get("permission_mode", "ask")
    if permission_mode not in {"auto", "ask"}:
        emit("completed", status="failed", error="Unknown permission mode")
        return
    workspace = "/workspace" if config["container"] else request["workspace"]
    if config["container"]:
        Path(os.environ["HOME"]).mkdir(parents=True, exist_ok=True)
        Path(os.environ["CODEX_HOME"]).mkdir(parents=True, exist_ok=True)
    approvals = {}
    stop = asyncio.Event()
    guard = ToolLoopGuard()
    guard_error = None

    def inspect_tool(tool, inputs, result, failed=False):
        nonlocal guard_error
        verdict = guard.observe(tool, inputs, result, failed=failed)
        if verdict:
            emit("loop_guard", **verdict)
            if verdict["decision"] == "stop":
                guard_error = verdict["reason"]
                stop.set()
        return verdict

    async def controls():
        while True:
            line = await lines.get()
            if line is None:
                stop.set()
                return
            value = json.loads(line)
            if value.get("stop"):
                stop.set()
            elif (
                value.get("approval_id") in approvals
                and not approvals[value["approval_id"]].done()
            ):
                approvals[value["approval_id"]].set_result(value["decision"])

    control = asyncio.create_task(controls())

    async def permission(tool, details):
        if stop.is_set():
            return "decline"
        key = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        approvals[key] = future
        emit("approval", approval_id=key, tool=tool, details=details)
        try:
            return await asyncio.wait_for(future, config["approval_timeout"])
        except asyncio.TimeoutError:
            return "decline"
        finally:
            approvals.pop(key, None)

    try:
        if request["engine"] == "claude":
            from claude_agent_sdk import (
                ClaudeSDKClient,
                ClaudeAgentOptions,
                PermissionResultDeny,
                PermissionResultAllow,
                ResultMessage,
                AssistantMessage,
                TextBlock,
                SystemMessage,
                HookMatcher,
            )

            async def before_tool(inputs, tool_use_id, context):
                if not claude_needs_human(
                    permission_mode,
                    inputs["tool_name"],
                    inputs.get("tool_input", {}),
                    workspace,
                ):
                    return {}
                decision = await permission(
                    inputs["tool_name"], inputs.get("tool_input", {})
                )
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow"
                        if decision == "accept"
                        else "deny",
                        "permissionDecisionReason": "Platform user decision",
                    }
                }

            async def can_use_tool(name, inputs, context):
                decision = await permission(name, inputs)
                return (
                    PermissionResultAllow(updated_input=inputs)
                    if decision == "accept"
                    else PermissionResultDeny(message="Denied by platform user")
                )

            async def after_tool(inputs, tool_use_id, context):
                failed = inputs.get("hook_event_name") == "PostToolUseFailure"
                verdict = inspect_tool(
                    inputs["tool_name"],
                    inputs.get("tool_input", {}),
                    inputs.get("tool_response", inputs.get("error")),
                    failed,
                )
                return {"systemMessage": verdict["reason"]} if verdict else {}

            options = ClaudeAgentOptions(
                cwd=workspace,
                resume=request["session_id"],
                model=request["model"],
                system_prompt=request["instructions"] or None,
                mcp_servers=request["mcp_servers"],
                permission_mode="auto" if permission_mode == "auto" else "default",
                can_use_tool=can_use_tool,
                hooks={
                    "PostToolUse": [HookMatcher(hooks=[after_tool])],
                    "PostToolUseFailure": [HookMatcher(hooks=[after_tool])],
                    "PreToolUse": [
                        HookMatcher(
                            hooks=[before_tool],
                            timeout=config["approval_timeout"] + 5,
                            on_failure="block",
                        )
                    ],
                },
                setting_sources=[],
                settings="{}",
                max_turns=40,
                max_budget_usd=5,
            )
            async with ClaudeSDKClient(options=options) as client:

                async def interrupt():
                    await stop.wait()
                    await client.interrupt()

                stopper = asyncio.create_task(interrupt())
                try:
                    await client.query(request["prompt"])
                    async for msg in client.receive_response():
                        if isinstance(msg, SystemMessage) and msg.subtype == "init":
                            emit("session", session_id=msg.data["session_id"])
                        elif isinstance(msg, AssistantMessage):
                            for block in msg.content:
                                if isinstance(block, TextBlock):
                                    emit("message", text=block.text)
                        elif isinstance(msg, ResultMessage):
                            emit("session", session_id=msg.session_id)
                            emit(
                                "completed",
                                status="failed"
                                if guard_error
                                else "interrupted"
                                if stop.is_set()
                                else ("failed" if msg.is_error else "completed"),
                                error=guard_error,
                            )
                finally:
                    stopper.cancel()
        else:
            proc = await asyncio.create_subprocess_exec(
                config["codex_command"],
                "app-server",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=workspace,
                limit=4 * 1024 * 1024,
            )
            seq = 0
            pending = {}
            thread = None
            turn = None

            async def send(value):
                proc.stdin.write((json.dumps(value) + "\n").encode())
                await proc.stdin.drain()

            async def rpc(method, params):
                nonlocal seq
                seq += 1
                future = asyncio.get_running_loop().create_future()
                pending[seq] = future
                await send(dict(id=seq, method=method, params=params))
                return await asyncio.wait_for(future, 60)

            done = asyncio.Event()

            async def respond(message):
                method = message["method"]
                if method in (
                    "item/commandExecution/requestApproval",
                    "item/fileChange/requestApproval",
                ):
                    decision = await permission(method, message.get("params", {}))
                    await send({"id": message["id"], "result": {"decision": decision}})
                else:
                    await send(
                        {
                            "id": message["id"],
                            "error": {
                                "code": -32601,
                                "message": "Unsupported native request",
                            },
                        }
                    )

            tasks = set()

            async def reader():
                nonlocal turn
                while line := await proc.stdout.readline():
                    msg = json.loads(line)
                    if "method" not in msg:
                        future = pending.pop(msg.get("id"), None)
                        if future and not future.done():
                            if "error" in msg:
                                future.set_exception(RuntimeError(str(msg["error"])))
                            else:
                                future.set_result(msg.get("result", {}))
                    elif "id" in msg:
                        task = asyncio.create_task(respond(msg))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
                    elif msg["method"] == "item/agentMessage/delta":
                        emit("delta", text=msg["params"]["delta"])
                    elif msg["method"] == "error":
                        error = msg.get("params", {}).get("error", {})
                        emit(
                            "diagnostic",
                            message=error.get(
                                "message", "Native executor reported an error"
                            ),
                        )
                    elif msg["method"] in {
                        "item/autoApprovalReview/started",
                        "item/autoApprovalReview/completed",
                    }:
                        data = msg.get("params", {})
                        emit(
                            "auto_review",
                            review_id=data.get("reviewId"),
                            item_id=data.get("targetItemId"),
                            review=data.get("review", {}),
                            decision_source=data.get("decisionSource"),
                            action_type=data.get("action", {}).get("type"),
                        )
                    elif msg["method"] in {"item/started", "item/completed"}:
                        item = msg.get("params", {}).get("item", {})
                        if item.get("type") in {
                            "commandExecution",
                            "fileChange",
                            "mcpToolCall",
                        }:
                            emit(
                                "tool_activity",
                                item_id=item.get("id"),
                                tool=item.get("type"),
                                status=item.get(
                                    "status",
                                    "started"
                                    if msg["method"] == "item/started"
                                    else "completed",
                                ),
                            )
                            if msg["method"] == "item/completed":
                                inspect_tool(
                                    item["type"],
                                    item.get(
                                        "command",
                                        item.get("arguments", item.get("changes", {})),
                                    ),
                                    {
                                        "output": item.get(
                                            "aggregatedOutput", item.get("result")
                                        ),
                                        "error": item.get("error"),
                                        "exit_code": item.get("exitCode"),
                                    },
                                    item.get("status") == "failed"
                                    or item.get("exitCode") not in (None, 0)
                                    or item.get("error") is not None,
                                )
                    elif msg["method"] == "turn/started":
                        turn = msg["params"]["turn"]["id"]
                    elif msg["method"] == "turn/completed":
                        native = msg["params"]["turn"]
                        emit(
                            "completed",
                            status="failed" if guard_error else native["status"],
                            error=guard_error or native.get("error"),
                        )
                        done.set()
                        return
                raise RuntimeError("Native server closed before terminal event")

            reader_task = asyncio.create_task(reader())
            try:
                await rpc(
                    "initialize",
                    {
                        "clientInfo": {"name": "workbench", "version": "0.1.0"},
                        "capabilities": {"experimentalApi": True},
                    },
                )
                await send({"method": "initialized", "params": {}})
                params = {
                    "cwd": workspace,
                    **codex_permissions(permission_mode),
                }
                if request["model"]:
                    params["model"] = request["model"]
                if request["instructions"]:
                    params["developerInstructions"] = request["instructions"]
                if request["mcp_servers"]:
                    params["config"] = {"mcp_servers": request["mcp_servers"]}
                if request["session_id"]:
                    params["threadId"] = request["session_id"]
                try:
                    result = await rpc(
                        "thread/resume" if request["session_id"] else "thread/start",
                        params,
                    )
                except RuntimeError as error:
                    if "approvalsReviewer" in str(error) or "auto_review" in str(error):
                        fallback = codex_permissions(
                            "ask",
                            auto_review_supported="unknown field" not in str(error),
                        )
                        params.pop("approvalsReviewer", None)
                        params.update(fallback)
                        emit(
                            "diagnostic",
                            message="Native auto review unavailable; falling back to human approval within workspace-write",
                        )
                        result = await rpc(
                            "thread/resume"
                            if request["session_id"]
                            else "thread/start",
                            params,
                        )
                    elif request["session_id"] and "no rollout found" in str(error):
                        emit(
                            "diagnostic",
                            message="Native session has no saved rollout; creating a new thread with platform message history",
                        )
                        params.pop("threadId", None)
                        result = await rpc("thread/start", params)
                    else:
                        raise
                thread = result["thread"]["id"]
                emit("session", session_id=thread)
                emit(
                    "permission_policy",
                    mode=permission_mode,
                    approval_policy=params["approvalPolicy"],
                    reviewer=params.get("approvalsReviewer", "user"),
                    sandbox=params["sandbox"],
                )
                result = await rpc(
                    "turn/start",
                    {
                        "threadId": thread,
                        "input": [{"type": "text", "text": request["prompt"]}],
                        "approvalPolicy": params["approvalPolicy"],
                        **(
                            {"approvalsReviewer": params["approvalsReviewer"]}
                            if "approvalsReviewer" in params
                            else {}
                        ),
                        "sandboxPolicy": {
                            "type": "workspaceWrite",
                            "writableRoots": [workspace],
                            "networkAccess": False,
                        },
                    },
                )
                turn = result["turn"]["id"]
                stop_task = asyncio.create_task(stop.wait())
                done_task = asyncio.create_task(done.wait())
                finished, _ = await asyncio.wait(
                    [stop_task, done_task, reader_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stop_task in finished and not done.is_set():
                    await rpc("turn/interrupt", {"threadId": thread, "turnId": turn})
                    await asyncio.wait_for(asyncio.shield(reader_task), 30)
                elif reader_task in finished:
                    await reader_task
                stop_task.cancel()
                done_task.cancel()
            finally:
                reader_task.cancel()
                for task in tasks:
                    task.cancel()
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
    except Exception as exc:
        emit("completed", status="failed", error=str(exc))
    finally:
        control.cancel()


if __name__ == "__main__":
    asyncio.run(main())
