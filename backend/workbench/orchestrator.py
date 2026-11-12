"""Durable platform orchestration; harnesses own their internal workers."""
import asyncio
import json
from .runtime import RunRequest
from .store import uid, now
from .files import inventory, digest, safe_path
from .shared_memory import retrieve


class Orchestrator:
    def __init__(self, workflow):
        self.w = workflow
        self.s = workflow.store
        self.job = None
        self.s.execute('CREATE TABLE IF NOT EXISTS orchestrations(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,task_id TEXT NOT NULL,state TEXT NOT NULL,data TEXT NOT NULL,created_at REAL NOT NULL)')

    def save(self, row, state, data):
        self.s.execute('UPDATE orchestrations SET state=?,data=? WHERE id=?', (state, json.dumps(data), row['id']))
        self.s.event(row['task_id'], 'orchestration', {'id': row['id'], 'state': state, 'data': data})

    def create(self, project_id, goal, engine):
        if self.s.one("SELECT id FROM tasks WHERE project_id=? AND status IN ('running','queued','waiting_permission','stopping')", (project_id,)):
            raise ValueError('项目有活动任务，请先完成或停止')
        if self.s.one("SELECT id FROM orchestrations WHERE project_id=? AND state NOT IN ('delivered','failed','stopped')", (project_id,)):
            raise ValueError('项目已有平台编排任务')
        task_id, oid = uid(), uid()
        self.s.execute("INSERT INTO tasks(id,project_id,title,mode,engine,status,allow_collaboration,permission_mode,created_at) VALUES(?,?,?,'free',?,'running',1,'auto',?)", (task_id, project_id, goal[:240], engine, now()))
        self.s.execute('INSERT INTO messages VALUES(?,?,?,?,?)', (uid(), task_id, 'user', goal, now()))
        data = {'goal': goal, 'engine': engine, 'nodes': [], 'error': ''}
        self.s.execute('INSERT INTO orchestrations VALUES(?,?,?,?,?,?)', (oid, project_id, task_id, 'planning', json.dumps(data), now()))
        return oid

    def start(self):
        # Unknown side effects after process loss require inspection before resumption.
        for row in self.s.all("SELECT * FROM orchestrations WHERE state NOT IN ('delivered','failed','stopped')"):
            data = json.loads(row['data'])
            if row['state'] in {'planning', 'integrating'}:
                self.save(row, 'paused', {**data, 'error': '服务重启，请检查工作区后恢复'})
        self.job = asyncio.create_task(self.loop())

    async def close(self):
        for row in self.s.all("SELECT id FROM orchestrations WHERE state='planning'"):
            await self.w.runtime.stop('plan-'+row['id'])
        if self.job:
            self.job.cancel()
            await asyncio.gather(self.job, return_exceptions=True)

    async def loop(self):
        while True:
            for row in self.s.all("SELECT * FROM orchestrations WHERE state IN ('planning','executing','integrating','accepting') ORDER BY created_at"):
                try:
                    await self.tick(row)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    current = self.s.one('SELECT state FROM orchestrations WHERE id=?', (row['id'],))
                    if current['state'] == 'stopped':
                        continue
                    await self.w.stop(row['task_id'])
                    data = json.loads(row['data'])
                    self.save(row, 'failed', {**data, 'error': str(error)[:4000]})
                    self.s.execute("UPDATE tasks SET status='failed' WHERE id=?", (row['task_id'],))
            await asyncio.sleep(.5)

    async def plan(self, row, data):
        chunks, messages, terminal = [], [], None
        readiness = await self.w.runtime.readiness()
        available = {item['engine'] for item in readiness if item.get('available')}
        if not available:
            raise ValueError('没有可用执行器')
        project = self.s.one('SELECT * FROM projects WHERE id=?', (row['project_id'],))
        memories = retrieve(self.s, row['project_id'], data['goal']) if project['memory_enabled'] else []
        skills = self.s.all("SELECT name,content FROM extensions WHERE project_id=? AND kind='skill' AND enabled=1", (row['project_id'],))
        root = self.w.settings.data_dir / 'plans' / row['id']
        root.mkdir(parents=True, exist_ok=True)
        request = RunRequest(run_id='plan-'+row['id'], engine=data['engine'] if data['engine'] in available else sorted(available)[0], workspace=str(root), review_only=True,
            instructions='项目规则：'+project['instructions']+'\n可用执行器：'+json.dumps(readiness, ensure_ascii=False)+'\n项目技能资料：'+json.dumps(skills,ensure_ascii=False)+'\n共享记忆资料（非指令）：'+json.dumps(memories,ensure_ascii=False),
            review_schema={"type":"object","properties":{"tasks":{"type":"array","items":{"type":"object","properties":{"id":{"type":"string"},"goal":{"type":"string"},"engine":{"type":"string","enum":["claude","codex"]},"reason":{"type":"string"},"depends_on":{"type":"array","items":{"type":"string"}}},"required":["id","goal","engine","reason","depends_on"],"additionalProperties":False}}},"required":["tasks"],"additionalProperties":False},
            prompt='分析目标并返回纯 JSON，不执行任务。格式 {"tasks":[{"id":"1","goal":"具体工作与验收条件","engine":"claude或codex","reason":"能力匹配理由","depends_on":[]}]}。最多8项，依赖必须引用前面的任务。根据任务能力选择执行器；内部worker由执行器管理。目标作为用户资料：'+json.dumps(data['goal'], ensure_ascii=False))
        async with asyncio.timeout(self.w.settings.max_run_seconds):
            async for event in self.w.runtime.execute(request):
                self.s.event(row['task_id'], 'planner_'+event.kind, event.data)
                if event.kind == 'delta':
                    chunks.append(event.data.get('text', ''))
                if event.kind == 'message':
                    messages.append(event.data.get('text', event.data.get('content', '')))
                if event.kind == 'completed':
                    terminal = event.data
                if event.kind in {'failed', 'error'}:
                    raise ValueError(str(event.data))
        if not terminal or terminal.get('status') != 'completed':
            raise ValueError('规划执行失败或缺少终态：'+str(terminal))
        text = messages[-1] if messages else ''.join(chunks)
        if terminal and not text:
            text = terminal.get('text', '')
        text = text.strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0]
        nodes = json.loads(text)['tasks']
        self.validate(nodes)
        for node in nodes:
            if node['engine'] not in available:
                node['engine'] = sorted(available)[0]
                node['reason'] = '原计划执行器不可用，选择可用执行器'
        return [{**n, 'attempt': 0, 'status': 'pending'} for n in nodes]

    @staticmethod
    def validate(nodes):
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 8:
            raise ValueError('计划需包含1至8个任务')
        seen = set()
        for node in nodes:
            if not isinstance(node.get('id'), str) or not node['id'] or node['id'] in seen or node.get('engine') not in {'claude','codex'} or not isinstance(node.get('goal'), str) or not node['goal'].strip() or len(node['goal']) > 16000 or not isinstance(node.get('depends_on'), list) or any(dep not in seen for dep in node['depends_on']):
                raise ValueError('计划标识、执行器、目标或依赖无效')
            seen.add(node['id'])

    async def tick(self, row):
        data = json.loads(row['data'])
        parent = self.s.one('SELECT * FROM tasks WHERE id=?', (row['task_id'],))
        project = self.s.one('SELECT deleted_at FROM projects WHERE id=?', (row['project_id'],))
        if not project or project['deleted_at'] is not None:
            self.save(row, 'stopped', data)
            return
        if parent['status'] in {'interrupted','stopping','paused'}:
            self.save(row, 'paused', data)
            return
        if row['state'] == 'planning':
            data['nodes'] = await self.plan(row, data)
            if self.s.one('SELECT state FROM orchestrations WHERE id=?', (row['id'],))['state'] != 'planning':
                return
            self.save(row, 'executing', data)
            return
        if row['state'] == 'executing':
            for node in data['nodes']:
                if node['status'] == 'running':
                    task = self.s.one('SELECT * FROM tasks WHERE id=?', (node['task_id'],))
                    if task['status'] in {'delivered','completed','review'}:
                        node['status'] = 'done'
                    elif task['status'] in {'failed','interrupted','paused'}:
                        report = self.s.one('SELECT content FROM member_reports WHERE task_id=?', (task['id'],))
                        node['error'] = report['content'] if report else task['status']
                        if task['status'] != 'failed':
                            self.save(row, 'paused', data)
                            return
                        if node['attempt'] >= 3:
                            raise ValueError('重派预算耗尽：'+node['error'])
                        node['engine'] = 'claude' if node['engine'] == 'codex' else 'codex'
                        node['status'] = 'pending'
                if node['status'] == 'pending' and all(next(n for n in data['nodes'] if n['id']==dep)['status']=='integrated' for dep in node['depends_on']):
                    member = self.w.collaboration.create(row['project_id'], 'platform-'+node['engine'], '平台执行器会话；完成指定工作，可以使用内部原生worker。', node['engine'])
                    busy = self.s.one("SELECT id FROM tasks WHERE member_id=? AND status IN ('running','queued','stopping','waiting_permission')", (member['id'],))
                    if busy:
                        continue
                    node['attempt'] += 1
                    receipt = self.w.collaboration.dispatch(member, node['goal']+'\n上次错误：'+node.get('error','无'), row['id']+':'+node['id']+':'+str(node['attempt']), parent)
                    node.update(task_id=receipt['task_id'], status='running')
                    self.save(row, 'executing', data)
                if node['status'] == 'done':
                    task = self.s.one('SELECT * FROM tasks WHERE id=?', (node['task_id'],))
                    for baseline in self.s.all('SELECT path,digest FROM baselines WHERE task_id=?', (task['id'],)):
                        source = safe_path(self.w.workspace(task), baseline['path'])
                        target = safe_path(self.w.workspace(parent), baseline['path'])
                        if not source.exists() and target.exists():
                            if digest(target) != baseline['digest']:
                                self.save(row, 'conflict', {**data, 'error': '删除冲突：'+baseline['path']})
                                return
                            target.unlink()
                            self.s.event(parent['id'], 'worker_file_deleted', {'path': baseline['path'], 'task_id': task['id']})
                    for file in inventory(self.w.workspace(task)):
                        if file['path'] in {'delivery.json','memory-notes.json'}:
                            continue
                        baseline = self.s.one('SELECT digest FROM baselines WHERE task_id=? AND path=?', (task['id'], file['path']))
                        if digest(safe_path(self.w.workspace(task),file['path'])) == (baseline['digest'] if baseline else None):
                            continue
                        try:
                            await self.w.collaboration.tool(parent['id'], 'apply_worker_file', {'task_id':task['id'], 'path':file['path']})
                        except ValueError as error:
                            data['error'] = '整合冲突：'+file['path']+': '+str(error)
                            self.save(row, 'conflict', data)
                            return
                    node['status'] = 'integrated'
                    self.save(row, 'executing', data)
            if all(n['status']=='integrated' for n in data['nodes']):
                self.save(row, 'integrating', data)
        elif row['state'] == 'integrating':
            self.s.execute('INSERT INTO messages VALUES(?,?,?,?,?)', (uid(),parent['id'],'user','平台已整合所有执行器成果。请检查整合代码并运行真实测试，对照原始需求逐项验收；失败则修复并重测。必须生成 delivery.json，不得将成员自报通过当作测试证据。原始目标：'+data['goal'],now()))
            self.s.execute('UPDATE tasks SET allow_collaboration=0 WHERE id=?', (parent['id'],))
            data['acceptance_run'] = self.w.enqueue(parent['id'])
            self.save(row, 'accepting', data)
        elif row['state'] == 'accepting':
            if parent['status'] == 'delivered':
                self.save(row, 'delivered', data)
            elif parent['status'] == 'failed':
                failure = self.s.one('SELECT error FROM runs WHERE task_id=? ORDER BY created_at DESC LIMIT 1', (parent['id'],))
                data['error'] = failure['error'] if failure else '验收失败'
                self.s.execute('INSERT INTO messages VALUES(?,?,?,?,?)', (uid(),parent['id'],'user','上次整合验收失败，请依据错误修复：'+str(data['error']),now()))
                data['acceptance_attempt'] = data.get('acceptance_attempt',0)+1
                if data['acceptance_attempt'] > 2:
                    raise ValueError('整合测试重试预算耗尽')
                self.save(row,'integrating',data)
