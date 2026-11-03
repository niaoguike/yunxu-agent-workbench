import asyncio
import unittest
from types import SimpleNamespace
from workbench.runtime import RuntimeManager, RunRequest


class RuntimeTests(unittest.TestCase):
    def test_disabled_runner_fails_honestly(self):
        manager = RuntimeManager(
            SimpleNamespace(runner="disabled", codex_command="codex")
        )

        async def run():
            return [
                event
                async for event in manager.execute(
                    RunRequest("one", "codex", ".", "hello")
                )
            ]

        events = asyncio.run(run())
        self.assertEqual(events[-1].data["status"], "failed")
        self.assertEqual(events[-1].data["error"], "Runner disabled")
        self.assertFalse(any(event.kind == "session" for event in events))

    def test_invalid_approval_rejected(self):
        manager = RuntimeManager(SimpleNamespace(runner="disabled"))
        with self.assertRaises(ValueError):
            asyncio.run(manager.approve("missing", "approval", "anything"))

    def test_disabled_readiness(self):
        manager = RuntimeManager(
            SimpleNamespace(runner="disabled", codex_command="missing-workbench-binary")
        )
        self.assertTrue(
            all(not engine["available"] for engine in asyncio.run(manager.readiness()))
        )


class NativeProtocolFixtureTests(unittest.TestCase):
    def test_bidirectional_approval_and_terminal_through_real_worker_process(self):
        import sys
        import tempfile
        from pathlib import Path

        script = """import json,sys
def send(x):
 print(json.dumps(x),flush=True)
for line in sys.stdin:
 m=json.loads(line)
 method=m.get('method')
 if method=='initialize': send({'id':m['id'],'result':{}})
 elif method=='thread/start': send({'id':m['id'],'result':{'thread':{'id':'fixture-thread'}}})
 elif method=='turn/start':
  send({'id':m['id'],'result':{'turn':{'id':'fixture-turn'}}})
  send({'id':9000,'method':'unknown/dangerous','params':{}})
 elif m.get('id')==9000:
  assert m['error']['code']==-32601
  send({'id':9001,'method':'item/commandExecution/requestApproval','params':{'command':'fixture command'}})
 elif m.get('id')==9001:
  assert m['result']['decision']=='decline'
  send({'method':'item/agentMessage/delta','params':{'delta':'declined safely'}})
  send({'method':'turn/completed','params':{'turn':{'id':'fixture-turn','status':'completed'}}})
"""
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "app-server").write_text(script, encoding="utf-8")
            manager = RuntimeManager(
                SimpleNamespace(
                    runner="trusted-local",
                    codex_command=sys.executable,
                    approval_timeout=2,
                )
            )

            async def run():
                events = []
                async with asyncio.timeout(15):
                    async for event in manager.execute(
                        RunRequest("fixture", "codex", directory, "protocol only")
                    ):
                        events.append(event)
                        if event.kind == "approval":
                            await manager.approve(
                                "fixture", event.data["approval_id"], "decline"
                            )
                return events

            events = asyncio.run(run())
        self.assertEqual(
            [event.data["session_id"] for event in events if event.kind == "session"],
            ["fixture-thread"],
        )
        self.assertEqual(
            [event.data["text"] for event in events if event.kind == "delta"],
            ["declined safely"],
        )
        self.assertEqual(events[-1].data["status"], "completed")


class WorkerProtocolTests(unittest.TestCase):
    def test_native_start_failure_exits_with_stdin_open(self):
        import json
        import subprocess
        import sys
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.Popen(
                [sys.executable, "-m", "workbench.runner"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                process.stdin.write(
                    json.dumps(
                        {
                            "request": {"engine": "codex", "workspace": directory},
                            "container": False,
                            "codex_command": "nonexistent-workbench-executor-713",
                            "approval_timeout": 1,
                        }
                    )
                    + "\n"
                )
                process.stdin.flush()
                process.wait(timeout=10)
                event = json.loads(process.stdout.readline())
                self.assertEqual(event["kind"], "completed")
                self.assertEqual(event["data"]["status"], "failed")
                self.assertEqual(process.returncode, 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdin.close()
                process.stdout.close()
                process.stderr.close()
