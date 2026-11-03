"""JSONL process boundary for native executors. stdout is protocol only."""
import asyncio
import json
import sys
import uuid

def emit(kind, **data):
    print(json.dumps({'kind': kind, 'data': data}), flush=True)

async def main():
    config = json.loads(await asyncio.to_thread(sys.stdin.readline))
    request = config['request']
    workspace = '/workspace' if config['container'] else request['workspace']
    approvals = {}
    stop = asyncio.Event()
    async def controls():
        while line := await asyncio.to_thread(sys.stdin.readline):
            value = json.loads(line)
            if value.get('stop'):
                stop.set()
            elif value.get('approval_id') in approvals:
                approvals[value['approval_id']].set_result(value['decision'])
    control = asyncio.create_task(controls())
    async def permission(tool, details):
        key = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        approvals[key] = future
        emit('approval', approval_id=key, tool=tool, details=details)
        try:
            return await asyncio.wait_for(future, config['approval_timeout'])
        except asyncio.TimeoutError:
            return 'decline'
        finally:
            approvals.pop(key, None)
    try:
        if request['engine'] == 'claude':
            from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, PermissionResultAllow, PermissionResultDeny, ResultMessage, AssistantMessage, TextBlock, SystemMessage
            async def can_use_tool(name, inputs, context):
                decision = await permission(name, inputs)
                return PermissionResultAllow(updated_input=inputs) if decision == 'accept' else PermissionResultDeny(message='Denied by user')
            options = ClaudeAgentOptions(cwd=workspace, resume=request['session_id'], model=request['model'], system_prompt=request['instructions'] or None, mcp_servers=request['mcp_servers'], permission_mode='default', can_use_tool=can_use_tool)
            async with ClaudeSDKClient(options=options) as client:
                async def interrupt():
                    await stop.wait()
                    await client.interrupt()
                stopper = asyncio.create_task(interrupt())
                try:
                    await client.query(request['prompt'])
                    async for msg in client.receive_response():
                        if isinstance(msg, SystemMessage) and msg.subtype == 'init':
                            emit('session', session_id=msg.data['session_id'])
                        elif isinstance(msg, AssistantMessage):
                            for block in msg.content:
                                if isinstance(block, TextBlock):
                                    emit('message', text=block.text)
                        elif isinstance(msg, ResultMessage):
                            emit('session', session_id=msg.session_id)
                            emit('completed', status='interrupted' if stop.is_set() else ('failed' if msg.is_error else 'completed'))
                finally:
                    stopper.cancel()
        else:
            proc = await asyncio.create_subprocess_exec(config['codex_command'], 'app-server', stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, cwd=workspace)
            seq = 0
            pending = {}
            thread = None
            turn = None
            async def send(value):
                proc.stdin.write((json.dumps(value)+'\n').encode())
                await proc.stdin.drain()
            async def rpc(method, params):
                nonlocal seq
                seq += 1
                future = asyncio.get_running_loop().create_future()
                pending[seq] = future
                await send(dict(id=seq, method=method, params=params))
                return await asyncio.wait_for(future, 60)
            done = asyncio.Event()
            async def respond(message):
                method = message['method']
                if method in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval'):
                    decision = await permission(method, message.get('params', {}))
                    await send({'id': message['id'], 'result': {'decision': decision}})
                else:
                    await send({'id': message['id'], 'error': {'code': -32601, 'message': 'Unsupported native request'}})
            tasks = set()
            async def reader():
                nonlocal turn
                while line := await proc.stdout.readline():
                    msg = json.loads(line)
                    if 'method' not in msg:
                        future = pending.pop(msg.get('id'), None)
                        if future and not future.done():
                            if 'error' in msg: future.set_exception(RuntimeError(str(msg['error'])))
                            else: future.set_result(msg.get('result', {}))
                    elif 'id' in msg:
                        task = asyncio.create_task(respond(msg)); tasks.add(task); task.add_done_callback(tasks.discard)
                    elif msg['method'] == 'item/agentMessage/delta':
                        emit('delta', text=msg['params']['delta'])
                    elif msg['method'] == 'turn/started':
                        turn = msg['params']['turn']['id']
                    elif msg['method'] == 'turn/completed':
                        native = msg['params']['turn']
                        emit('completed', status=native['status'], error=native.get('error'))
                        done.set()
                        return
                raise RuntimeError('Native server closed before terminal event')
            reader_task = asyncio.create_task(reader())
            try:
                await rpc('initialize', {'clientInfo': {'name': 'workbench', 'version': '0.1.0'}, 'capabilities': {'experimentalApi': False}})
                await send({'method': 'initialized', 'params': {}})
                params = {'cwd': workspace, 'approvalPolicy': 'untrusted', 'sandbox': 'workspace-write'}
                if request['model']: params['model'] = request['model']
                if request['instructions']: params['developerInstructions'] = request['instructions']
                if request['mcp_servers']: params['config'] = {'mcp_servers': request['mcp_servers']}
                if request['session_id']: params['threadId'] = request['session_id']
                result = await rpc('thread/resume' if request['session_id'] else 'thread/start', params)
                thread = result['thread']['id']; emit('session', session_id=thread)
                result = await rpc('turn/start', {'threadId': thread, 'input': [{'type': 'text', 'text': request['prompt']}]})
                turn = result['turn']['id']
                stop_task = asyncio.create_task(stop.wait())
                done_task = asyncio.create_task(done.wait())
                finished, _ = await asyncio.wait([stop_task, done_task, reader_task], return_when=asyncio.FIRST_COMPLETED)
                if stop_task in finished and not done.is_set():
                    await rpc('turn/interrupt', {'threadId': thread, 'turnId': turn})
                    await asyncio.wait_for(asyncio.shield(reader_task), 30)
                elif reader_task in finished:
                    await reader_task
                stop_task.cancel(); done_task.cancel()
            finally:
                reader_task.cancel()
                for task in tasks: task.cancel()
                if proc.returncode is None:
                    proc.kill(); await proc.wait()
    except Exception as exc:
        emit('completed', status='failed', error=str(exc))
    finally:
        control.cancel()

if __name__ == '__main__':
    asyncio.run(main())
