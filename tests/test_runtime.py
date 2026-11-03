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
