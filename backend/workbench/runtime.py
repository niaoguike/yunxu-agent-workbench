import asyncio
from dataclasses import dataclass, field, asdict
import importlib.util
import json
import shutil
import sys

@dataclass
class RunRequest:
    run_id: str
    engine: str
    workspace: str
    prompt: str
    session_id: str | None = None
    model: str | None = None
    instructions: str = ''
    mcp_servers: dict = field(default_factory=dict)

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
        available = mode == 'trusted-local' or (mode == 'docker' and shutil.which('docker') is not None)
        return [dict(engine=e, available=available and (mode == 'docker' or ok), reason=('Container image and credentials must be provisioned' if mode == 'docker' else 'Executable and authentication are checked when starting a run') if available else 'Runner disabled or Docker unavailable') for e, ok in [('codex', bool(shutil.which(self.settings.codex_command))), ('claude', importlib.util.find_spec('claude_agent_sdk') is not None)]]

    async def execute(self, request):
        if self.settings.runner not in ('docker', 'trusted-local'):
            yield RuntimeEvent('completed', {'status': 'failed', 'error': 'Runner disabled'})
            return
        if request.engine not in ('claude', 'codex'):
            yield RuntimeEvent('completed', {'status': 'failed', 'error': 'Unknown engine'})
            return
        if request.run_id in self.processes:
            raise ValueError('Run already active')
        command = [sys.executable, '-m', 'workbench.runner']
        if self.settings.runner == 'docker':
            command = ['docker', 'run', '--rm', '-i', '--init', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=256', '--memory=2g', '--cpus=2', '-v', f'{request.workspace}:/workspace', '-w', '/workspace', self.settings.docker_image, 'python', '-m', 'workbench.runner']
        proc = None
        terminal = False
        try:
            proc = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            self.processes[request.run_id] = proc
            self.locks[request.run_id] = asyncio.Lock()
            await self._send(request.run_id, {'request': asdict(request), 'codex_command': self.settings.codex_command, 'approval_timeout': self.settings.approval_timeout, 'container': self.settings.runner == 'docker'})
            while line := await proc.stdout.readline():
                event = json.loads(line)
                terminal = terminal or event['kind'] == 'completed'
                yield RuntimeEvent(**event)
            await proc.wait()
            if not terminal:
                yield RuntimeEvent('completed', {'status': 'failed', 'error': 'Runner exited without native terminal event'})
        except Exception as exc:
            yield RuntimeEvent('completed', {'status': 'failed', 'error': str(exc)})
        finally:
            self.processes.pop(request.run_id, None)
            self.locks.pop(request.run_id, None)
            if proc and proc.returncode is None:
                proc.kill()
                await proc.wait()

    async def _send(self, run_id, value):
        async with self.locks[run_id]:
            proc = self.processes[run_id]
            proc.stdin.write((json.dumps(value) + '\n').encode())
            await proc.stdin.drain()

    async def stop(self, run_id):
        if run_id in self.processes:
            await self._send(run_id, {'stop': True})

    async def approve(self, run_id, approval_id, decision):
        if decision not in ('accept', 'decline'):
            raise ValueError('Unknown decision')
        if run_id in self.processes:
            await self._send(run_id, {'approval_id': approval_id, 'decision': decision})
