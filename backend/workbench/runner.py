"""JSONL process boundary for native executors. stdout is protocol only."""

import asyncio
import json
import sys
import uuid
import threading
import os
import secrets
import hmac
from pathlib import Path
from .loop_guard import ToolLoopGuard
from .loop_review import LoopReviewMonitor
from .native_compaction import NativeCompaction
from .executors import ExecutorContext, create_executor
from .native_memory import prepare_native_memory


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
    if not request.get("review_only"):
        prepare_native_memory(workspace)
    approvals = {}
    platform_calls = {}
    bridge_server = None
    stop = asyncio.Event()
    guard = ToolLoopGuard()
    context = ExecutorContext(
        request, config, workspace, compaction, permission_mode, stop,
        permission=None, inspect_tool=None,
    )
    monitor = LoopReviewMonitor() if request.get("adaptive_loop_review") else None

    def inspect_tool(tool, inputs, result, failed=False):
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
                context.guard_error = verdict["reason"]
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
                for future in platform_calls.values():
                    if not future.done():
                        future.set_result({"ok": False, "error": "任务已停止"})
            elif value.get("loop_review_result") and monitor and not stop.is_set():
                result = monitor.apply(value["loop_review_result"], value["result"])
                if result:
                    emit("loop_review", **result)
                    if result["decision"] == "stop":
                        context.guard_error = "重复工具调用复核停止：" + result["reason"]
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

    context.permission = permission
    context.inspect_tool = inspect_tool
    try:
        await create_executor(request["engine"]).execute(context)
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
