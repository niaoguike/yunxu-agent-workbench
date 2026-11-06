import asyncio
import unittest
from types import SimpleNamespace
from workbench.runtime import RuntimeManager, RunRequest


async def test_repeat_failure_guard_waits_for_native_interrupt_terminal(tmp_path):
    import sys

    script = """import sys,json,os
from pathlib import Path
assert Path(os.environ['PATH'].split(os.pathsep)[0]).resolve()==Path(sys.executable).parent.resolve()
def send(x): print(json.dumps(x),flush=True)
for line in sys.stdin:
 m=json.loads(line);method=m.get('method')
 if method=='initialize':send({'id':m['id'],'result':{}})
 elif method=='thread/start':send({'id':m['id'],'result':{'thread':{'id':'guard-thread'}}})
 elif method=='turn/start':
  send({'id':m['id'],'result':{'turn':{'id':'guard-turn'}}})
  for i in range(8):send({'method':'item/completed','params':{'threadId':'guard-thread','item':{'type':'commandExecution','id':str(i),'command':'fixture-test','exitCode':1,'aggregatedOutput':'same error','status':'completed'}}})
 elif method=='turn/interrupt':
  send({'id':m['id'],'result':{}})
  send({'method':'turn/completed','params':{'turn':{'id':'guard-turn','status':'interrupted'}}})
"""
    (tmp_path / "app-server").write_text(script, encoding="utf-8")
    manager = RuntimeManager(
        SimpleNamespace(
            runner="trusted-local", codex_command=sys.executable, approval_timeout=2
        )
    )
    async with asyncio.timeout(15):
        events = [
            event
            async for event in manager.execute(
                RunRequest("guard-test", "codex", str(tmp_path), "fixture only")
            )
        ]
    assert [
        event.data["decision"] for event in events if event.kind == "loop_guard"
    ] == ["warn", "stop"]
    assert events[-1].kind == "completed"
    assert events[-1].data["status"] == "failed"
    assert "连续失败 8 次" in events[-1].data["error"]


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
  send({'method':'error','params':{'error':{'message':'fixture retry notice'}}})
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
        self.assertEqual(
            [event.data["message"] for event in events if event.kind == "diagnostic"],
            ["fixture retry notice"],
        )


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


class PermissionRoutingProtocolTests(unittest.TestCase):
    def test_unsupported_auto_reviewer_falls_back_to_real_human_request(self):
        import sys
        import tempfile
        from pathlib import Path

        script = """import sys,json
def send(x):print(json.dumps(x),flush=True)
for line in sys.stdin:
 m=json.loads(line);p=m.get('params',{});method=m.get('method')
 if method=='initialize':send({'id':m['id'],'result':{}})
 elif method=='thread/start':
  if 'approvalsReviewer' in p:send({'id':m['id'],'error':{'code':-32602,'message':'unknown field approvalsReviewer'}})
  else:
   assert p['approvalPolicy']=='on-request' and p['sandbox']=='workspace-write'
   send({'id':m['id'],'result':{'thread':{'id':'fallback'}}})
 elif method=='turn/start':
  assert 'approvalsReviewer' not in p and p['approvalPolicy']=='on-request'
  send({'id':m['id'],'result':{'turn':{'id':'turn'}}})
  send({'id':9001,'method':'item/commandExecution/requestApproval','params':{'command':'fixture'}})
 elif m.get('id')==9001:
  assert m['result']['decision']=='decline'
  send({'method':'turn/completed','params':{'turn':{'id':'turn','status':'completed'}}})
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
                        RunRequest(
                            "fallback",
                            "codex",
                            directory,
                            "fixture only",
                            permission_mode="auto",
                        )
                    ):
                        events.append(event)
                        if event.kind == "approval":
                            await manager.approve(
                                "fallback", event.data["approval_id"], "decline"
                            )
                return events

            events = asyncio.run(run())
        self.assertEqual(events[-1].data["status"], "completed")
        self.assertEqual(sum(event.kind == "approval" for event in events), 1)
        self.assertTrue(
            any(
                "falling back to human" in event.data.get("message", "")
                for event in events
            )
        )

    def test_auto_and_ask_override_resumed_native_policy(self):
        import sys
        import tempfile
        from pathlib import Path

        for mode, reviewer in [("auto", "auto_review"), ("ask", "user")]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                script = """import sys,json
def send(x): print(json.dumps(x),flush=True)
for line in sys.stdin:
 m=json.loads(line);method=m.get('method');p=m.get('params',{})
 if method=='initialize':send({'id':m['id'],'result':{}})
 elif method in ('thread/start','thread/resume'):
  assert p['approvalPolicy']=='on-request' and p['sandbox']=='workspace-write'
  assert p['approvalsReviewer']==REVIEWER
  send({'id':m['id'],'result':{'thread':{'id':'resumed'}}})
 elif method=='turn/start':
  assert p['approvalsReviewer']==REVIEWER and p['approvalPolicy']=='on-request'
  assert p['sandboxPolicy']['type']=='workspaceWrite' and p['sandboxPolicy']['networkAccess'] is False
  send({'id':m['id'],'result':{'turn':{'id':'turn'}}})
  if REVIEWER=='auto_review':
   send({'method':'item/autoApprovalReview/completed','params':{'reviewId':'review-one','review':{'status':'denied','riskLevel':'high','rationale':'outside authority'},'decisionSource':'agent','action':{'type':'command'}}})
   send({'method':'turn/completed','params':{'turn':{'id':'turn','status':'completed'}}})
  else:send({'id':9001,'method':'item/commandExecution/requestApproval','params':{'command':'fixture'}})
 elif m.get('id')==9001:
  assert m['result']['decision']=='decline'
  send({'method':'turn/completed','params':{'turn':{'id':'turn','status':'completed'}}})
""".replace("REVIEWER", repr(reviewer))
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
                            RunRequest(
                                "routing",
                                "codex",
                                directory,
                                "fixture only",
                                session_id="old-auto-thread",
                                permission_mode=mode,
                            )
                        ):
                            events.append(event)
                            if event.kind == "approval":
                                await manager.approve(
                                    "routing", event.data["approval_id"], "decline"
                                )
                    return events

                events = asyncio.run(run())
                self.assertEqual(events[-1].data["status"], "completed")
                if mode == "auto":
                    self.assertFalse(any(event.kind == "approval" for event in events))
                    self.assertEqual(
                        [
                            event.data["review"]["status"]
                            for event in events
                            if event.kind == "auto_review"
                        ],
                        ["denied"],
                    )
                else:
                    self.assertEqual(
                        sum(event.kind == "approval" for event in events), 1
                    )
