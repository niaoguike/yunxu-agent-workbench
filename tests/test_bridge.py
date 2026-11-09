import asyncio
import json
import sys
from types import SimpleNamespace

from workbench.bridge import available_tools
from workbench.runtime import RunRequest, RuntimeManager


def test_worker_does_not_inherit_leader_management_tools():
    assert {tool["name"] for tool in available_tools("solo")} == {
        "memory_search",
        "memory_save",
        "chart_list",
        "chart_revise",
    }
    worker = {tool["name"] for tool in available_tools("worker")}
    leader = {tool["name"] for tool in available_tools("leader")}
    assert worker == {"memory_search", "memory_save", "send_to_lead"}
    assert {
        "create_worker",
        "send_to_worker",
        "wait_worker",
        "apply_worker_file",
    } <= leader
    assert "send_to_lead" not in leader


async def test_stdio_bridge_authentication_and_scoped_native_tool_return(tmp_path):
    script = """import sys,json,os,subprocess,socket
def send(x):print(json.dumps(x),flush=True)
server=None
for line in sys.stdin:
 m=json.loads(line);method=m.get('method');p=m.get('params',{})
 if method=='initialize':send({'id':m['id'],'result':{}})
 elif method=='thread/start':
  server=p['config']['mcp_servers']['workbench']
  assert server['default_tools_approval_mode']=='approve'
  assert p['config']['mcp_servers']['external'].get('default_tools_approval_mode')!='approve'
  assert p['config']['project_doc_max_bytes']==0
  send({'id':m['id'],'result':{'thread':{'id':'bridge-thread'}}})
 elif method=='turn/start':
  send({'id':m['id'],'result':{'turn':{'id':'bridge-turn'}}})
  host,port=server['env']['WORKBENCH_BRIDGE_ENDPOINT'].split(':')
  with socket.create_connection((host,int(port))) as c:
   c.sendall(b'{"token":"incorrect","name":"list_workers","arguments":{}}\\n')
   assert json.loads(c.makefile('rb').readline())['ok'] is False
  requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26'}},{'jsonrpc':'2.0','id':2,'method':'tools/list'},{'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'list_workers','arguments':{}}}]
  r=subprocess.run([server['command'],*server['args']],input=''.join(json.dumps(x)+'\\n' for x in requests),text=True,capture_output=True,env={**os.environ,**server['env']},timeout=10)
  assert r.returncode==0,r.stderr
  responses=[json.loads(x) for x in r.stdout.splitlines()]
  assert 'create_worker' in {x['name'] for x in responses[1]['result']['tools']}
  assert responses[2]['result']['isError'] is False
  assert json.loads(responses[2]['result']['content'][0]['text'])['result']==['fixture-worker']
  send({'method':'turn/completed','params':{'turn':{'id':'bridge-turn','status':'completed'}}})
"""
    (tmp_path / "app-server").write_text(script, encoding="utf-8")
    manager = RuntimeManager(
        SimpleNamespace(
            runner="trusted-local", codex_command=sys.executable, approval_timeout=2
        )
    )
    events = []
    async with asyncio.timeout(20):
        async for event in manager.execute(
            RunRequest(
                "bridge-fixture",
                "codex",
                str(tmp_path),
                "fixture",
                mcp_servers={"external": {"command": "unused"}},
                platform_bridge=True,
            )
        ):
            events.append(event)
            if event.kind == "platform_tool":
                assert event.data["name"] == "list_workers"
                await manager.respond_tool(
                    "bridge-fixture",
                    event.data["call_id"],
                    {"ok": True, "result": ["fixture-worker"]},
                )
    assert events[-1].data["status"] == "completed"
    assert sum(event.kind == "platform_tool" for event in events) == 1
    assert "WORKBENCH_BRIDGE_TOKEN" not in json.dumps([event.data for event in events])
