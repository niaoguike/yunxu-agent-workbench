import asyncio
from dataclasses import dataclass, field, asdict
import importlib.util
import json
import shutil
import sys
import uuid
import os
import signal


@dataclass
class RunRequest:
    run_id: str
    engine: str
    workspace: str
    prompt: str
    session_id: str | None = None
    model: str | None = None
    instructions: str = ""
    mcp_servers: dict = field(default_factory=dict)
    permission_mode: str = "auto"
    platform_bridge: bool = False
    bridge_role: str = "leader"
    adaptive_loop_review: bool = False
    review_only: bool = False


@dataclass
class RuntimeEvent:
    kind: str
    data: dict


class RuntimeManager:
    def __init__(self, settings):
        self.settings = settings
        self.processes = {}
        self.locks = {}

    async def readiness(self):
        mode = self.settings.runner
        if mode == "disabled":
            return [
                dict(engine=e, available=False, reason="Runner disabled")
                for e in ("codex", "claude")
            ]
        if mode == "docker":
            reason = "Docker CLI unavailable"
            if shutil.which("docker"):
                try:
                    process = await asyncio.create_subprocess_exec(
                        "docker",
                        "image",
                        "inspect",
                        self.settings.docker_image,
                        "--format",
                        "{{.Id}}",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await asyncio.wait_for(process.wait(), 5)
                    if process.returncode == 0:
                        return [
                            dict(
                                engine=e,
                                available=True,
                                authenticated=None,
                                reason="Runner image available; authentication is verified at native startup",
                            )
                            for e in ("codex", "claude")
                        ]
                    reason = "Docker daemon unavailable or runner image not built"
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                    reason = "Docker readiness timed out"
                except OSError as error:
                    reason = str(error)
            return [
                dict(engine=e, available=False, reason=reason)
                for e in ("codex", "claude")
            ]
        return [
            dict(
                engine=e,
                available=mode == "trusted-local" and ok,
                authenticated=None,
                reason="Trusted local runner: prerequisite available, authentication checked at startup"
                if ok
                else "Required native executable or SDK unavailable",
            )
            for e, ok in [
                ("codex", bool(shutil.which(self.settings.codex_command))),
                ("claude", importlib.util.find_spec("claude_agent_sdk") is not None),
            ]
        ]

    async def execute(self, request):
        if self.settings.runner not in ("docker", "trusted-local"):
            yield RuntimeEvent(
                "completed", {"status": "failed", "error": "Runner disabled"}
            )
            return
        if request.engine not in ("claude", "codex"):
            yield RuntimeEvent(
                "completed", {"status": "failed", "error": "Unknown engine"}
            )
            return
        if request.run_id in self.processes:
            raise ValueError("Run already active")
        command = [sys.executable, "-m", "workbench.runner"]
        container_name = "workbench-" + uuid.uuid4().hex
        if self.settings.runner == "docker":
            command = [
                "docker",
                "run",
                "--name",
                container_name,
                "--rm",
                "-i",
                "--init",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--pids-limit=256",
                "--memory=2g",
                "--cpus=2",
                "-v",
                f"{request.workspace}:/workspace",
                "-w",
                "/workspace",
                self.settings.docker_image,
                "python",
                "-m",
                "workbench.runner",
            ]
        proc = None
        terminal = False
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=4 * 1024 * 1024,
                start_new_session=os.name != "nt",
            )
            self.processes[request.run_id] = proc
            self.locks[request.run_id] = asyncio.Lock()
            await self._send(
                request.run_id,
                {
                    "request": asdict(request),
                    "codex_command": self.settings.codex_command,
                    "approval_timeout": self.settings.approval_timeout,
                    "container": self.settings.runner == "docker",
                },
            )
            while line := await proc.stdout.readline():
                event = json.loads(line)
                terminal = terminal or event["kind"] == "completed"
                yield RuntimeEvent(**event)
            await proc.wait()
            if not terminal:
                yield RuntimeEvent(
                    "completed",
                    {
                        "status": "failed",
                        "error": "Runner exited without native terminal event",
                    },
                )
        except Exception as exc:
            yield RuntimeEvent("completed", {"status": "failed", "error": str(exc)})
        finally:
            if proc and proc.returncode is None:
                try:
                    proc.stdin.write(b'{"stop":true}\n')
                    await proc.stdin.drain()
                    await asyncio.wait_for(proc.wait(), 5)
                except (asyncio.TimeoutError, BrokenPipeError, ConnectionError):
                    if proc.returncode is None:
                        if os.name == "nt":
                            killer = await asyncio.create_subprocess_exec(
                                "taskkill",
                                "/PID",
                                str(proc.pid),
                                "/T",
                                "/F",
                                stdout=asyncio.subprocess.DEVNULL,
                                stderr=asyncio.subprocess.DEVNULL,
                            )
                            await killer.wait()
                        else:
                            try:
                                os.killpg(proc.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        await proc.wait()
            self.processes.pop(request.run_id, None)
            self.locks.pop(request.run_id, None)
            if self.settings.runner == "docker":
                cleanup = await asyncio.create_subprocess_exec(
                    "docker",
                    "rm",
                    "-f",
                    container_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    await asyncio.wait_for(cleanup.wait(), 10)
                except asyncio.TimeoutError:
                    cleanup.kill()
                    await cleanup.wait()

    async def _send(self, run_id, value):
        async with self.locks[run_id]:
            proc = self.processes[run_id]
            proc.stdin.write((json.dumps(value) + "\n").encode())
            await proc.stdin.drain()

    async def stop(self, run_id):
        if run_id in self.processes:
            await self._send(run_id, {"stop": True})

    async def approve(self, run_id, approval_id, decision):
        if decision not in ("accept", "decline"):
            raise ValueError("Unknown decision")
        if run_id in self.processes:
            await self._send(run_id, {"approval_id": approval_id, "decision": decision})
        else:
            raise ValueError("Run is no longer active")

    async def respond_tool(self, run_id, call_id, result):
        if run_id not in self.processes:
            raise ValueError("Run is no longer active")
        await self._send(run_id, {"tool_result": call_id, "result": result})

    async def respond_review(self, run_id, review_id, result):
        if run_id in self.processes:
            await self._send(
                run_id, {"loop_review_result": review_id, "result": result}
            )
