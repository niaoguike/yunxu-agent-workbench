import asyncio
import json
import sys
from types import SimpleNamespace

from workbench.loop_review import LoopReviewMonitor
from workbench.runtime import RunRequest, RuntimeManager


def suspicion(monitor):
    result = None
    for _ in range(3):
        result = monitor.observe("shell", {"command": "false"}, "same error", True)
    return result


def test_review_grace_budget_and_late_results():
    monitor = LoopReviewMonitor()
    request = suspicion(monitor)
    assert request["used"] == 1
    assert (
        monitor.apply(request["review_id"], {"decision": "continue"})["decision"]
        == "continue"
    )
    for _ in range(20):
        assert (
            monitor.observe("shell", {"command": "false"}, "same error", True) is None
        )
    next_review = monitor.observe("shell", {"command": "false"}, "same error", True)
    assert next_review["used"] == 2
    monitor.observe("shell", {"command": "fixed"}, "new result", False)
    assert (
        monitor.apply(next_review["review_id"], {"decision": "stop"})["decision"]
        == "stale"
    )


def test_evidence_redacted_and_only_three_reviews_per_run():
    monitor = LoopReviewMonitor()
    unicode_monitor = LoopReviewMonitor()
    for _ in range(3):
        unicode_review = unicode_monitor.observe(
            "shell", {"text": "\ud800"}, "\udfff", True
        )
    assert unicode_review["evidence"][0].isascii()
    assert json.dumps(unicode_review).encode("utf-8")
    secret = "ghp_" + "x" * 40
    for index in range(3):
        request = None
        for _ in range(30):
            request = monitor.observe("shell", {"token": secret}, "same error", True)
            if request:
                break
        assert request["used"] == index + 1
        assert secret not in json.dumps(request)
        monitor.apply(request["review_id"], {"decision": "continue"})
    for _ in range(50):
        assert monitor.observe("shell", {"token": secret}, "same error", True) is None


async def test_model_stop_waits_for_native_interrupt_terminal(tmp_path):
    script = """import sys,json
def send(x):print(json.dumps(x),flush=True)
for line in sys.stdin:
 m=json.loads(line);method=m.get('method')
 if method=='initialize':send({'id':m['id'],'result':{}})
 elif method=='thread/start':send({'id':m['id'],'result':{'thread':{'id':'main'}}})
 elif method=='turn/start':
  send({'id':m['id'],'result':{'turn':{'id':'turn'}}})
  for i in range(3):send({'method':'item/completed','params':{'threadId':'main','item':{'id':str(i),'type':'commandExecution','command':'false','exitCode':1,'status':'completed','aggregatedOutput':'error'}}})
 elif method=='turn/interrupt':
  send({'id':m['id'],'result':{}})
  send({'method':'turn/completed','params':{'turn':{'id':'turn','status':'interrupted'}}})
"""
    (tmp_path / "app-server").write_text(script, encoding="utf-8")
    manager = RuntimeManager(
        SimpleNamespace(
            runner="trusted-local", codex_command=sys.executable, approval_timeout=2
        )
    )
    events = []
    async with asyncio.timeout(15):
        async for event in manager.execute(
            RunRequest(
                "adaptive", "codex", str(tmp_path), "fixture", adaptive_loop_review=True
            )
        ):
            events.append(event)
            if event.kind == "loop_review_request":
                await manager.respond_review(
                    "adaptive",
                    event.data["review_id"],
                    {
                        "decision": "stop",
                        "reason": "Confirmed unchanged failing repetition",
                    },
                )
    assert events[-1].data["status"] == "failed"
    assert "Confirmed unchanged" in events[-1].data["error"]
    assert any(
        event.kind == "loop_review" and event.data["decision"] == "stop"
        for event in events
    )


async def test_auxiliary_review_disables_tools_and_enforces_readonly(tmp_path):
    script = """import sys,json
def send(x):print(json.dumps(x),flush=True)
for line in sys.stdin:
 m=json.loads(line);method=m.get('method');p=m.get('params',{})
 if method=='initialize':send({'id':m['id'],'result':{}})
 elif method=='config/read':send({'id':m['id'],'result':{'config':{'mcp_servers':{'external':{}},'plugins':{'custom':{}}}}})
 elif method=='thread/start':
  assert p['approvalPolicy']=='never' and p['sandbox']=='read-only'
  assert p['config']['features.shell_tool'] is False
  assert p['config']['mcp_servers.external.enabled'] is False
  assert p['config']['plugins.custom.enabled'] is False
  send({'id':m['id'],'result':{'thread':{'id':'review'},'model':'fixture-model','modelProvider':'fixture'}})
 elif method=='turn/start':
  assert p['sandboxPolicy']['type']=='readOnly' and p['approvalPolicy']=='never'
  assert p['outputSchema']['properties']['decision']['enum']==['continue','stop']
  send({'id':m['id'],'result':{'turn':{'id':'turn'}}})
  send({'method':'item/agentMessage/delta','params':{'delta':'{"decision":"stop","reason":"fixture"}'}})
  send({'method':'thread/tokenUsage/updated','params':{'threadId':'review','tokenUsage':{'total':{'totalTokens':27,'inputTokens':20,'cachedInputTokens':10,'outputTokens':7},'last':{'totalTokens':27}}}})
  send({'method':'turn/completed','params':{'turn':{'id':'turn','status':'completed'}}})
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
                RunRequest(
                    "review", "codex", str(tmp_path), "fixture", review_only=True
                )
            )
        ]
    assert events[-1].data["status"] == "completed"
    assert not any(event.kind == "approval" for event in events)
    assert (
        next(e.data for e in events if e.kind == "usage")["cumulative"]["totalTokens"]
        == 27
    )
    assert (
        next(e.data for e in events if e.kind == "native_config")["model"]
        == "fixture-model"
    )
