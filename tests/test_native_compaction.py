import asyncio
import sys
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from workbench.native_compaction import NativeCompaction, save_digest
from workbench.runtime import RuntimeManager, RunRequest
from workbench.store import Store


def test_threshold_once_and_boundary_discards_stale_usage():
    policy = NativeCompaction()
    for used, window in [(True, 100), (float("nan"), 100), (80, None)]:
        policy.usage(used, window)
    assert not policy.should_compact()
    policy.usage(89, 100)
    assert not policy.should_compact()
    policy.usage(92, 100)
    assert policy.should_compact()
    assert not policy.should_compact()
    policy.boundary()
    assert not policy.should_compact()
    policy.usage(90, 100)
    assert policy.should_compact()
    with pytest.raises(ValueError):
        NativeCompaction(99)


def test_digest_opt_in_provenance_redaction_dedup_and_missing_summary(tmp_path):
    store = Store(tmp_path / "digest.sqlite")
    store.execute("INSERT INTO users VALUES('owner','owner@example.com','hash','')")
    store.execute("INSERT INTO projects(id,owner_id,name,created_at) VALUES('p','owner','P',0)")
    args = (store, "p", "task", "codex", "session")
    data = {"summary": '目标：完成任务。token=sk-abcdefghijklmnop\n' + "结果" * 3000}
    assert save_digest(*args, data) is None
    store.execute("UPDATE projects SET memory_enabled=1 WHERE id='p'")
    assert save_digest(*args, {}) is None
    memory_id = save_digest(*args, data)
    assert save_digest(*args, data) == memory_id
    row = store.one("SELECT * FROM memories WHERE id=?", (memory_id,))
    assert row["category"] == "reference"
    assert len(row["content"]) <= 4000
    assert "task=task" in row["content"]
    assert "sk-abcdefghijklmnop" not in row["content"]
    assert "省略" in row["content"]
    store.execute("UPDATE projects SET deleted_at=1 WHERE id='p'")
    assert save_digest(*args, {"summary": "new"}) is None
    store.connection.close()


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("recover", [False, True])
async def test_codex_compacts_after_user_turn_without_overwriting_result(tmp_path, fail, recover):
    script = '''import sys,json
def send(x): print(json.dumps(x),flush=True)
for line in sys.stdin:
 m=json.loads(line); method=m.get('method')
 if method=='initialize': send({'id':m['id'],'result':{}})
 elif method=='thread/resume': send({'id':m['id'],'error':{'message':'no rollout found'}})
 elif method=='thread/start': send({'id':m['id'],'result':{'thread':{'id':'main'}}})
 elif method=='turn/start':
  assert m['params']['input'][0]['text']==EXPECTED
  send({'id':m['id'],'result':{'turn':{'id':'user-turn'}}})
  send({'method':'thread/tokenUsage/updated','params':{'threadId':'main','tokenUsage':{'last':{'totalTokens':92},'modelContextWindow':100}}})
  send({'method':'item/agentMessage/delta','params':{'threadId':'main','delta':'original result'}})
  send({'method':'turn/completed','params':{'turn':{'id':'user-turn','status':'completed'}}})
 elif method=='thread/compact/start':
  assert m['params']=={'threadId':'main'}
  if FAIL:
   send({'id':m['id'],'error':{'message':'summarization produced empty response'}})
  else:
   send({'id':m['id'],'result':{}})
   send({'method':'item/agentMessage/delta','params':{'threadId':'main','delta':'internal maintenance text'}})
   send({'method':'item/completed','params':{'threadId':'main','item':{'id':'boundary','type':'contextCompaction'}}})
'''.replace("FAIL", repr(fail)).replace("EXPECTED", repr("full preserved history" if recover else "fixture"))
    (tmp_path / "app-server").write_text(script, encoding="utf-8")
    manager = RuntimeManager(SimpleNamespace(runner="trusted-local", codex_command=sys.executable, approval_timeout=2))
    async with asyncio.timeout(20):
        events = [event async for event in manager.execute(RunRequest("compact-test", "codex", str(tmp_path), "fixture", session_id="missing" if recover else None, replay_prompt="full preserved history"))]
    assert [e.data["text"] for e in events if e.kind == "delta"] == ["original result"]
    assert sum(e.kind == "context_compaction_started" for e in events) == 1
    assert sum(e.kind == "completed" for e in events) == 1
    assert events[-1].data["status"] == "completed"
    if fail:
        assert any(e.kind == "context_compaction_failed" and e.data["needs_rollover"] for e in events)
    else:
        boundary = next(e for e in events if e.kind == "context_compacted")
        assert "summary" not in boundary.data


@pytest.mark.parametrize("review", [False, True])
async def test_claude_native_compact_is_auxiliary_and_reviews_skip_it(tmp_path, monkeypatch, review):
    stub = '''from types import SimpleNamespace
class ClaudeAgentOptions(SimpleNamespace): pass
class HookMatcher(SimpleNamespace): pass
class PermissionResultAllow(SimpleNamespace): pass
class PermissionResultDeny(SimpleNamespace): pass
class TextBlock:
 def __init__(self,text): self.text=text
class AssistantMessage:
 def __init__(self,text): self.content=[TextBlock(text)]
class SystemMessage:
 def __init__(self,subtype,data): self.subtype=subtype;self.data=data
class ResultMessage:
 session_id='claude-session'
 is_error=False
class ClaudeSDKClient:
 def __init__(self,options): pass
 async def __aenter__(self): return self
 async def __aexit__(self,*args): pass
 async def interrupt(self): pass
 async def query(self,prompt): self.prompt=prompt
 async def get_context_usage(self): return {'totalTokens':92,'rawMaxTokens':100}
 async def receive_response(self):
  if self.prompt=='/compact':
   yield SystemMessage('compact_boundary',{'summary':'Goal: preserve constraints. Done: tests. Pending: release.'})
   yield AssistantMessage('internal maintenance response')
  else:
   yield SystemMessage('init',{'session_id':'claude-session'})
   yield AssistantMessage('user result')
  yield ResultMessage()
'''
    (tmp_path / "claude_agent_sdk.py").write_text(stub, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path) + os.pathsep + str(Path(__file__).resolve().parents[1] / "backend"))
    manager = RuntimeManager(SimpleNamespace(runner="trusted-local", codex_command=sys.executable, approval_timeout=2))
    async with asyncio.timeout(20):
        events = [e async for e in manager.execute(RunRequest("claude-compact", "claude", str(tmp_path), "fixture", review_only=review))]
    assert [e.data["text"] for e in events if e.kind == "message"] == ["user result"], events
    assert events[-1].data["status"] == "completed"
    boundaries = [e for e in events if e.kind == "context_compacted"]
    assert len(boundaries) == (0 if review else 1)
    if not review:
        assert boundaries[0].data["summary"].startswith("Goal:")
