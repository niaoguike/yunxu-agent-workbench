import asyncio
import unittest
from types import SimpleNamespace
from workbench.runtime import RuntimeManager, RunRequest

class RuntimeTests(unittest.TestCase):
    def test_disabled_runner_fails_honestly(self):
        manager = RuntimeManager(SimpleNamespace(runner='disabled', codex_command='codex'))
        async def run():
            return [event async for event in manager.execute(RunRequest('one', 'codex', '.', 'hello'))]
        events = asyncio.run(run())
        self.assertEqual(events[-1].data['status'], 'failed')
        self.assertEqual(events[-1].data['error'], 'Runner disabled')
        self.assertFalse(any(event.kind == 'session' for event in events))

    def test_invalid_approval_rejected(self):
        manager = RuntimeManager(SimpleNamespace(runner='disabled'))
        with self.assertRaises(ValueError):
            asyncio.run(manager.approve('missing', 'approval', 'anything'))

    def test_disabled_readiness(self):
        manager = RuntimeManager(SimpleNamespace(runner='disabled', codex_command='missing-workbench-binary'))
        self.assertTrue(all(not engine['available'] for engine in asyncio.run(manager.readiness())))

class WorkerProtocolTests(unittest.TestCase):
    def test_native_start_failure_exits_with_stdin_open(self):
        import json
        import os
        import subprocess
        import sys
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.Popen([sys.executable, '-m', 'workbench.runner'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                process.stdin.write(json.dumps({'request': {'engine': 'codex', 'workspace': directory}, 'container': False, 'codex_command': 'nonexistent-workbench-executor-713', 'approval_timeout': 1}) + '\n')
                process.stdin.flush()
                process.wait(timeout=10)
                event = json.loads(process.stdout.readline())
                self.assertEqual(event['kind'], 'completed')
                self.assertEqual(event['data']['status'], 'failed')
                self.assertEqual(process.returncode, 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdin.close()
                process.stdout.close()
                process.stderr.close()
