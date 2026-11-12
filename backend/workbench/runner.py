"""JSONL process boundary for native executors. stdout is protocol only."""

import asyncio
import json
import sys
import uuid
import threading
import os
import secrets
import hmac
import re
from pathlib import Path
from .permissions import codex_permissions, claude_needs_human
from .loop_guard import ToolLoopGuard
from .loop_review import LoopReviewMonitor, SCHEMA, redact
from .native_compaction import NativeCompaction


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
    compaction = NativeCompaction(request.get("compaction_threshold_pct", 90))
    permission_mode = request.get("permission_mode", "ask")
    if permission_mode not in {"auto", "ask"}:
        emit("completed", status="failed", error="Unknown permission mode")
        return
    workspace = "/workspace" if config["container"] else request["workspace"]
    if config["container"]:
        Path(os.environ["HOME"]).mkdir(parents=True, exist_ok=True)
        Path(os.environ["CODEX_HOME"]).mkdir(parents=True, exist_ok=True)
    approvals = {}
    platform_calls = {}
    bridge_server = None
    stop = asyncio.Event()
    guard = ToolLoopGuard()
    guard_error = None
    monitor = LoopReviewMonitor() if request.get("adaptive_loop_review") else None

    def inspect_tool(tool, inputs, result, failed=False):
        nonlocal guard_error
        verdict = guard.observe(tool, inputs, result, failed=failed)
        if monitor:
            review = monitor.observe(tool, inputs, result, failed)
            if review:
                emit("loop_review_request", **review)
            if (
                verdict
                and verdict["decision"] == "stop"
                and guard.actions < guard.action_limit
            ):
                guard.stopped = False
                verdict = None
        if verdict:
            emit("loop_guard", **verdict)
            if verdict["decision"] == "stop":
                guard_error = verdict["reason"]
                stop.set()
        return verdict

    async def controls():
        nonlocal guard_error
        while True:
            line = await lines.get()
            if line is None:
                stop.set()
                return
            value = json.loads(line)
            if value.get("stop"):
                stop.set()
                for future in platform_calls.values():
                    if not future.done():
                        future.set_result({"ok": False, "error": "任务已停止"})
            elif value.get("loop_review_result") and monitor and not stop.is_set():
                result = monitor.apply(value["loop_review_result"], value["result"])
                if result:
                    emit("loop_review", **result)
                    if result["decision"] == "stop":
                        guard_error = "重复工具调用复核停止：" + result["reason"]
                        stop.set()
            elif value.get("tool_result") in platform_calls:
                future = platform_calls[value["tool_result"]]
                if not future.done():
                    future.set_result(value["result"])
            elif (
                value.get("approval_id") in approvals
                and not approvals[value["approval_id"]].done()
            ):
                approvals[value["approval_id"]].set_result(value["decision"])

    control = asyncio.create_task(controls())
    if request.get("platform_bridge"):
        bridge_token = secrets.token_urlsafe(32)

        async def handle_bridge(reader, writer):
            call_id = None
            try:
                raw = await asyncio.wait_for(reader.readline(), 10)
                value = json.loads(raw)
                if not hmac.compare_digest(str(value.get("token", "")), bridge_token):
                    raise ValueError("Unauthorized bridge request")
                if stop.is_set():
                    raise ValueError("任务已停止")
                from .bridge import available_tools

                allowed = {
                    tool["name"]
                    for tool in available_tools(request.get("bridge_role", "leader"))
                }
                if value.get("name") not in allowed or not isinstance(
                    value.get("arguments"), dict
                ):
                    raise ValueError("平台工具或参数不可用")
                call_id = str(uuid.uuid4())
                future = loop.create_future()
                platform_calls[call_id] = future
                emit(
                    "platform_tool",
                    call_id=call_id,
                    name=value["name"],
                    arguments=value["arguments"],
                )
                result = await asyncio.wait_for(future, 35)
            except Exception as error:
                result = {"ok": False, "error": str(error) or "Bridge request failed"}
            finally:
                if call_id:
                    platform_calls.pop(call_id, None)
            writer.write((json.dumps(result, ensure_ascii=True) + "\n").encode())
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        bridge_server = await asyncio.start_server(
            handle_bridge, "127.0.0.1", 0, limit=131072
        )
        port = bridge_server.sockets[0].getsockname()[1]
        request["mcp_servers"] = {
            **request.get("mcp_servers", {}),
            "workbench": {
                "command": sys.executable,
                "args": ["-m", "workbench.bridge"],
                "env": {
                    "WORKBENCH_BRIDGE_ENDPOINT": f"127.0.0.1:{port}",
                    "WORKBENCH_BRIDGE_TOKEN": bridge_token,
                    "WORKBENCH_BRIDGE_ROLE": request.get("bridge_role", "leader"),
                },
            },
        }

    async def permission(tool, details):
        if stop.is_set() or request.get("review_only"):
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
            if request.get("review_only"):
                options.tools = []
                options.mcp_servers = {}
                options.hooks = {}
                options.permission_mode = "dontAsk"
                options.max_turns = 1
                options.output_format = {"type": "json_schema", "schema": request.get("review_schema") or SCHEMA}
            async with ClaudeSDKClient(options=options) as client:

                async def interrupt():
                    await stop.wait()
                    await client.interrupt()

                stopper = asyncio.create_task(interrupt())
                try:
                    terminal = None
                    await client.query(request["prompt"])
                    async for msg in client.receive_response():
                        if isinstance(msg, SystemMessage) and msg.subtype == "init":
                            emit("session", session_id=msg.data["session_id"])
                        elif isinstance(msg, SystemMessage) and msg.subtype == "compact_boundary":
                            compaction.boundary()
                            emit("context_compacted", **{
                                key: value for key, value in msg.data.items()
                                if key in {"summary", "compact_metadata"}
                            })
                        elif isinstance(msg, AssistantMessage):
                            for block in msg.content:
                                if isinstance(block, TextBlock):
                                    emit("message", text=block.text)
                        elif isinstance(msg, ResultMessage):
                            if request.get("review_only") and getattr(
                                msg, "structured_output", None
                            ):
                                emit(
                                    "message",
                                    text=json.dumps(
                                        msg.structured_output, ensure_ascii=True
                                    ),
                                )
                            emit("session", session_id=msg.session_id)
                            terminal = dict(
                                status="failed"
                                if guard_error
                                else "interrupted"
                                if stop.is_set()
                                else ("failed" if msg.is_error else "completed"),
                                error=guard_error,
                            )
                    if terminal and terminal["status"] == "completed" and not request.get("review_only") and not stop.is_set():
                        try:
                            async with asyncio.timeout(15):
                                usage = await client.get_context_usage()
                            compaction.usage(usage.get("totalTokens"), usage.get("rawMaxTokens"))
                            emit("context_usage", context_tokens=usage.get("totalTokens"), context_window=usage.get("rawMaxTokens"))
                            if compaction.should_compact():
                                emit("context_compaction_started", threshold=compaction.threshold, source="host")
                                async with asyncio.timeout(120):
                                    await client.query("/compact")
                                    boundary = False
                                    async for compact_msg in client.receive_response():
                                        if isinstance(compact_msg, SystemMessage) and compact_msg.subtype == "compact_boundary":
                                            boundary = True
                                            compaction.boundary()
                                            emit("context_compacted", **{
                                                key: value for key, value in compact_msg.data.items()
                                                if key in {"summary", "compact_metadata"}
                                            })
                                        elif isinstance(compact_msg, ResultMessage) and compact_msg.is_error:
                                            raise RuntimeError("Native compact returned an error")
                                    if not boundary:
                                        raise RuntimeError("Native compact returned no completion boundary")
                        except Exception as error:
                            # Auxiliary maintenance must not erase a successful user turn.
                            emit("diagnostic", message="Context maintenance unavailable: " + str(error)[:500])
                            if compaction.fired:
                                emit("context_compaction_failed", needs_rollover=True)
                    if terminal:
                        if stop.is_set() and not guard_error:
                            terminal["status"] = "interrupted"
                        emit("completed", **terminal)
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
            terminal = None
            maintaining = False
            compact_done = asyncio.Event()

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
                nonlocal turn, terminal
                while line := await proc.stdout.readline():
                    msg = json.loads(line)
                    if "method" not in msg:
                        future = pending.pop(msg.get("id"), None)
                        if future and not future.done():
                            if "error" in msg:
                                future.set_exception(RuntimeError(str(msg["error"])))
                            else:
                                future.set_result(msg.get("result", {}))
                    elif (
                        "id" not in msg
                        and thread
                        and msg.get("params", {}).get("threadId") not in (None, thread)
                    ):
                        if msg["method"] in {"turn/started", "turn/completed"}:
                            emit(
                                "subagent_activity",
                                thread_id=msg["params"]["threadId"],
                                event=msg["method"],
                            )
                        continue
                    elif "id" in msg:
                        task = asyncio.create_task(respond(msg))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
                    elif msg["method"] == "item/agentMessage/delta":
                        if not maintaining:
                            emit("delta", text=msg["params"]["delta"])
                    elif msg["method"] == "thread/tokenUsage/updated":
                        usage = msg.get("params", {}).get("tokenUsage", {})
                        compaction.usage(usage.get("last", {}).get("totalTokens"), usage.get("modelContextWindow"))
                        emit(
                            "usage",
                            session_id=thread,
                            cumulative=usage.get("total"),
                            last=usage.get("last"),
                        )
                        emit(
                            "context_usage",
                            context_tokens=usage.get("last", {}).get("totalTokens"),
                            context_window=usage.get("modelContextWindow"),
                        )
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
                        if (
                            msg["method"] == "item/completed"
                            and item.get("type") == "contextCompaction"
                        ):
                            compaction.boundary()
                            compact_done.set()
                            emit("context_compacted", item_id=item.get("id"), **(
                                {"summary": item["summary"]} if isinstance(item.get("summary"), str) else {}
                            ))
                        if item.get("type") in {
                            "commandExecution",
                            "fileChange",
                            "mcpToolCall",
                        }:
                            emit(
                                "tool_activity",
                                item_id=item.get("id"),
                                tool=item.get("type"),
                                name=item.get("tool", item.get("type")),
                                arguments=json.loads(redact(json.dumps(item.get("command", item.get("arguments", item.get("changes"))), default=str))),
                                result=json.loads(redact(json.dumps(item.get("aggregatedOutput", item.get("result")), default=str))),
                                error=item.get("error"),
                                exit_code=item.get("exitCode"),
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
                        if maintaining:
                            if native["status"] != "completed":
                                emit("context_compaction_failed", needs_rollover=True)
                                compact_done.set()
                            continue
                        terminal = dict(
                            status="failed" if guard_error else native["status"],
                            error=guard_error or native.get("error"),
                        )
                        done.set()
                if terminal is None or (maintaining and not compact_done.is_set()):
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
                    servers = dict(request["mcp_servers"])
                    if request.get("platform_bridge"):
                        servers["workbench"] = {
                            **servers["workbench"],
                            "default_tools_approval_mode": "approve"
                            if permission_mode == "auto"
                            else "prompt",
                        }
                    params["config"] = {"mcp_servers": servers}
                if request.get("platform_bridge"):
                    params.setdefault("config", {})["project_doc_max_bytes"] = 0
                if request.get("review_only"):
                    effective = (
                        await rpc("config/read", {"includeLayers": False})
                    ).get("config", {})
                    isolated = {
                        "web_search": "disabled",
                        "include_apply_patch_tool": False,
                        "project_doc_max_bytes": 0,
                        "memories.generate_memories": False,
                        "memories.use_memories": False,
                    }
                    for feature in (
                        "shell_tool",
                        "unified_exec",
                        "shell_snapshot",
                        "view_image",
                        "code_mode",
                        "code_mode_only",
                        "js_repl",
                        "browser_use",
                        "computer_use",
                        "image_generation",
                        "multi_agent",
                        "multi_agent_v2",
                        "apps",
                        "remote_plugin",
                    ):
                        isolated["features." + feature] = False
                    for name in effective.get("mcp_servers", {}):
                        key = (
                            name
                            if re.fullmatch(r"[A-Za-z0-9_-]+", name)
                            else json.dumps(name)
                        )
                        isolated["mcp_servers." + key + ".enabled"] = False
                    for name in effective.get("plugins", {}):
                        key = (
                            name
                            if re.fullmatch(r"[A-Za-z0-9_-]+", name)
                            else json.dumps(name)
                        )
                        isolated["plugins." + key + ".enabled"] = False
                    params.update(
                        approvalPolicy="never",
                        sandbox="read-only",
                        ephemeral=True,
                        config=isolated,
                    )
                    params.pop("approvalsReviewer", None)
                if request["session_id"]:
                    params["threadId"] = request["session_id"]
                input_prompt = request["prompt"]
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
                        if request.get("platform_bridge"):
                            params["config"]["mcp_servers"]["workbench"][
                                "default_tools_approval_mode"
                            ] = "prompt"
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
                        input_prompt = request.get("replay_prompt") or request["prompt"]
                    else:
                        raise
                thread = result["thread"]["id"]
                emit("session", session_id=thread)
                emit(
                    "native_config",
                    model=result.get("model", request.get("model")),
                    provider=result.get("modelProvider"),
                )
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
                        "input": [{"type": "text", "text": input_prompt}],
                        "approvalPolicy": params["approvalPolicy"],
                        **(
                            {"approvalsReviewer": params["approvalsReviewer"]}
                            if "approvalsReviewer" in params
                            else {}
                        ),
                        "sandboxPolicy": {"type": "readOnly", "networkAccess": False}
                        if request.get("review_only")
                        else {
                            "type": "workspaceWrite",
                            "writableRoots": [workspace],
                            "networkAccess": False,
                        },
                        **(
                            {"outputSchema": request.get("review_schema") or SCHEMA}
                            if request.get("review_only")
                            else {}
                        ),
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
                    await asyncio.wait_for(done.wait(), 30)
                elif reader_task in finished:
                    await reader_task
                if terminal and terminal["status"] == "completed" and not request.get("review_only") and not stop.is_set() and compaction.should_compact():
                    maintaining = True
                    emit("context_compaction_started", threshold=compaction.threshold, source="host")
                    compact_done.clear()
                    compact_wait = None
                    try:
                        async with asyncio.timeout(120):
                            await rpc("thread/compact/start", {"threadId": thread})
                            compact_wait = asyncio.create_task(compact_done.wait())
                            settled, _ = await asyncio.wait([compact_wait, stop_task, reader_task], return_when=asyncio.FIRST_COMPLETED)
                            if stop_task in settled:
                                await rpc("turn/interrupt", {"threadId": thread, "turnId": turn})
                            elif reader_task in settled:
                                await reader_task
                    except Exception as error:
                        emit("diagnostic", message="Native compaction failed: " + str(error)[:500])
                        emit("context_compaction_failed", needs_rollover=True)
                    finally:
                        if compact_wait:
                            compact_wait.cancel()
                if terminal:
                    if stop.is_set() and not guard_error:
                        terminal["status"] = "interrupted"
                    emit("completed", **terminal)
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
        if bridge_server:
            bridge_server.close()
            await bridge_server.wait_closed()
        for future in platform_calls.values():
            if not future.done():
                future.cancel()


if __name__ == "__main__":
    asyncio.run(main())
