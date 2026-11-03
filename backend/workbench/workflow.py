"""Outer workflow: native turn completion is deliberately separate from acceptance."""
import asyncio
import json
import shutil
from pathlib import Path

from .files import digest, inventory, safe_path
from .runtime import RunRequest
from .store import now, uid


STAGES = [
    ('需求澄清', '将目标、范围、约束和可验证的验收条件写入 requirements.md。列出未知事项，不擅自编造。'),
    ('方案设计', '依据已确认需求，将架构、接口、数据、风险及测试方法写入 design.md。'),
    ('任务拆解', '依据需求和设计，将有依赖关系、可独立验收的实现任务写入 tasks.md。'),
    ('开发实现', '依据已确认需求、设计与任务清单实现真实代码，更新任务完成状态，不伪造结果。'),
    ('测试与修复', '运行适用检查，有限修复失败项，将实际命令、结果和未验证项写入 checks.md。'),
    ('验收评审', '对照 requirements.md 逐项核对实际成果和 checks.md，将通过/失败/未验证写入 acceptance.md。'),
    ('成果交付', '将真实成果路径、使用步骤、检查记录和已知限制写入 delivery.md。不要将原生轮次成功等同用户验收。'),
]
REQUIRED = {0: 'requirements.md', 1: 'design.md', 2: 'tasks.md', 4: 'checks.md', 5: 'acceptance.md', 6: 'delivery.md'}
GATED = {0, 1, 5}


class Workflow:
    def __init__(self, store, settings, runtime):
        self.store, self.settings, self.runtime = store, settings, runtime
        self.jobs = {}
        self.project_locks = {}
        self.stopping = False

    def workspace(self, task):
        base = self.settings.data_dir / 'workspaces' / task['project_id']
        path = base / 'members' / task['member_id'] if task.get('member_id') else base / 'main'
        path.mkdir(parents=True, exist_ok=True)
        return path

    def recover(self):
        # Process loss makes tool side effects uncertain. Never replay an active run automatically.
        with self.store.transaction() as db:
            active = db.execute("SELECT id,task_id FROM runs WHERE status IN ('running','stopping')").fetchall()
            for run in active:
                db.execute("UPDATE runs SET status='failed',error=?,finished_at=? WHERE id=?", ('服务重启：执行结果不明，请检查文件后手动继续', now(), run['id']))
                db.execute("UPDATE tasks SET status='interrupted' WHERE id=?", (run['task_id'],))
            db.execute("UPDATE approvals SET status='expired' WHERE status='pending'")
        for run in self.store.all("SELECT id,task_id FROM runs WHERE status='queued' ORDER BY created_at"):
            self.schedule(run['id'], run['task_id'])

    def enqueue(self, task_id):
        task = self.store.one('SELECT * FROM tasks WHERE id=?', (task_id,))
        run_id = uid()
        self.store.execute('INSERT INTO runs(id,task_id,status,version,stage,created_at) VALUES(?,?,?,?,?,?)', (run_id, task_id, 'queued', task['version'], task['stage'], now()))
        self.store.execute("UPDATE tasks SET status='queued' WHERE id=?", (task_id,))
        self.store.event(task_id, 'queued', {'run_id': run_id}, run_id)
        self.schedule(run_id, task_id)
        return run_id

    def schedule(self, run_id, task_id):
        self.jobs[run_id] = asyncio.create_task(self.work(run_id, task_id))

    async def close(self):
        self.stopping = True
        jobs = list(self.jobs.items())
        for run_id, job in jobs:
            try:
                await self.runtime.stop(run_id)
            except Exception:
                pass
            job.cancel()
        await asyncio.gather(*(job for _, job in jobs), return_exceptions=True)

    async def work(self, run_id, task_id):
        task = self.store.one('SELECT * FROM tasks WHERE id=?', (task_id,))
        lock = self.project_locks.setdefault(task['project_id'], asyncio.Lock())
        try:
            async with lock:
                run = self.store.one('SELECT * FROM runs WHERE id=?', (run_id,))
                if run['status'] != 'queued':
                    return
                task = self.store.one('SELECT * FROM tasks WHERE id=?', (task_id,))
                if task['version'] != run['version']:
                    self.finish(run_id, task_id, 'failed', '需求版本已变化，请重新提交')
                    return
                self.store.execute("UPDATE runs SET status='running' WHERE id=?", (run_id,))
                self.store.execute("UPDATE tasks SET status='running' WHERE id=?", (task_id,))
                project = self.store.one('SELECT * FROM projects WHERE id=?', (task['project_id'],))
                user = self.store.one('SELECT instructions FROM users WHERE id=?', (project['owner_id'],))
                memories = self.store.all('SELECT * FROM memories WHERE project_id=?', (project['id'],)) if project['memory_enabled'] else []
                extensions = self.store.all('SELECT * FROM extensions WHERE project_id=? AND enabled=1', (project['id'],))
                mcp, skills = {}, []
                for extension in extensions:
                    if extension['kind'] == 'skill':
                        skills.append(extension['name'] + ':\n' + extension['content'])
                    else:
                        mcp[extension['name']] = json.loads(extension['content'])
                snapshot = {'extensions': [{'id': e['id'], 'revision': e['revision']} for e in extensions], 'memory_ids': [m['id'] for m in memories], 'version': task['version'], 'stage': task['stage']}
                self.store.execute('UPDATE runs SET snapshot=? WHERE id=?', (json.dumps(snapshot), run_id))
                instructions = '\n\n'.join(filter(None, [user['instructions'], project['instructions'], *skills, *[m['category'] + ': ' + m['content'] for m in memories]]))
                if project['memory_enabled']:
                    instructions += '\n可将有长期价值的项目事实写入 memory-notes.json，格式为 [{"category":"project","content":"事实"}]。不保存凭据或整段对话。'
                history = self.store.all('SELECT role,content FROM messages WHERE task_id=? ORDER BY created_at', (task_id,))
                prompt = '\n\n'.join(m['role'] + ': ' + m['content'] for m in history)[-100000:]
                if task['mode'] == 'development':
                    prompt += f'\n\n当前只执行阶段 {task["stage"] + 1}：{STAGES[task["stage"]][0]}。{STAGES[task["stage"]][1]} 不要越过本阶段。'
                request = RunRequest(run_id=run_id, engine=task['engine'], workspace=str(self.workspace(task)), prompt=prompt, session_id=task['session_id'], model=task['model'], instructions=instructions, mcp_servers=mcp)
                chunks, final_messages, terminal = [], [], None
                async with asyncio.timeout(self.settings.max_run_seconds):
                    async for event in self.runtime.execute(request):
                        self.store.event(task_id, event.kind, event.data, run_id)
                        if event.kind == 'session':
                            self.store.execute('UPDATE tasks SET session_id=? WHERE id=?', (event.data['session_id'], task_id))
                            self.store.execute('UPDATE runs SET session_id=? WHERE id=?', (event.data['session_id'], run_id))
                        elif event.kind == 'delta':
                            chunks.append(event.data.get('text', ''))
                        elif event.kind == 'message':
                            final_messages.append(event.data.get('text', ''))
                        elif event.kind == 'approval':
                            self.store.execute('INSERT INTO approvals(id,task_id,run_id,native_id,details) VALUES(?,?,?,?,?)', (uid(), task_id, run_id, event.data['approval_id'], json.dumps(event.data, ensure_ascii=False)))
                            self.store.execute("UPDATE tasks SET status='waiting_permission' WHERE id=?", (task_id,))
                        elif event.kind == 'completed':
                            terminal = event.data
                text = '\n\n'.join(final_messages) or ''.join(chunks)
                if text:
                    self.store.execute('INSERT INTO messages VALUES(?,?,?,?,?)', (uid(), task_id, 'assistant', text, now()))
                if not terminal:
                    self.finish(run_id, task_id, 'failed', '执行器未返回原生终态，结果不明')
                    return
                status = terminal.get('status', 'failed')
                native_run = self.store.one('SELECT status FROM runs WHERE id=?', (run_id,))
                if native_run['status'] == 'stopping':
                    status = 'interrupted'
                if status != 'completed':
                    self.finish(run_id, task_id, status, terminal.get('error'))
                    return
                current = self.store.one('SELECT * FROM tasks WHERE id=?', (task_id,))
                if current['version'] != run['version']:
                    self.finish(run_id, task_id, 'failed', '执行期间需求已变化，当前成果需重新确认')
                    return
                workspace = self.workspace(task)
                required = REQUIRED.get(task['stage']) if task['mode'] == 'development' else None
                if required and not safe_path(workspace, required).is_file():
                    self.finish(run_id, task_id, 'failed', f'阶段成果缺失：{required}')
                    return
                files = inventory(workspace)
                snapshot_root = self.settings.data_dir / 'artifacts' / run_id
                for file in files:
                    source = safe_path(workspace, file['path'])
                    if file['size'] > self.settings.max_upload:
                        continue
                    destination = snapshot_root / file['path']
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
                    self.store.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)', (uid(), task_id, run_id, file['path'], digest(destination), file['size'], now()))
                self.collect_memory(project, workspace)
                self.finish(run_id, task_id, 'completed')
                if task['mode'] == 'development':
                    if task['stage'] in GATED:
                        self.gate(task_id, task)
                    elif task['stage'] < 6:
                        self.store.execute("UPDATE tasks SET stage=stage+1 WHERE id=?", (task_id,))
                        self.enqueue(task_id)
                    else:
                        self.store.execute("UPDATE tasks SET status='delivered' WHERE id=?", (task_id,))
                else:
                    # Even a successful native turn requires explicit acceptance.
                    self.gate(task_id, task)
        except asyncio.CancelledError:
            if not self.stopping:
                self.finish(run_id, task_id, 'interrupted')
            raise
        except Exception as error:
            try:
                await self.runtime.stop(run_id)
            except Exception:
                pass
            self.finish(run_id, task_id, 'failed', str(error)[:1500] or type(error).__name__)
        finally:
            self.jobs.pop(run_id, None)
            current = self.store.one('SELECT status FROM tasks WHERE id=?', (task_id,))
            if not self.stopping and current['status'] not in {'interrupted', 'stopping', 'failed'}:
                pending = self.store.one("SELECT * FROM pending_inputs WHERE task_id=? AND status='queued' ORDER BY created_at LIMIT 1", (task_id,))
                if pending:
                    with self.store.transaction() as db:
                        db.execute("UPDATE pending_inputs SET status='dispatched' WHERE id=?", (pending['id'],))
                        db.execute('INSERT INTO messages VALUES(?,?,?,?,?)', (pending['id'], task_id, 'user', pending['content'], pending['created_at']))
                        db.execute("UPDATE gates SET status='invalidated' WHERE task_id=? AND status IN ('pending','approved')", (task_id,))
                        db.execute('UPDATE tasks SET version=version+1,stage=0,session_id=NULL WHERE id=?', (task_id,))
                    self.enqueue(task_id)

    def collect_memory(self, project, workspace):
        path = workspace / 'memory-notes.json'
        if not project['memory_enabled'] or not path.is_file() or path.is_symlink() or path.stat().st_size > 32000:
            return
        try:
            items = json.loads(path.read_text(encoding='utf-8'))
            for item in items[:20]:
                category, content = item['category'], item['content']
                if category not in {'user', 'feedback', 'project', 'reference'} or not isinstance(content, str) or len(content) > 4000:
                    continue
                if not self.store.one('SELECT id FROM memories WHERE project_id=? AND content=?', (project['id'], content)):
                    self.store.execute('INSERT INTO memories(id,project_id,category,content,created_at) VALUES(?,?,?,?,?)', (uid(), project['id'], category, content, now()))
        except (ValueError, KeyError, TypeError):
            self.store.event(self.store.one('SELECT id FROM tasks WHERE project_id=? ORDER BY created_at DESC', (project['id'],))['id'], 'diagnostic', {'message': 'memory-notes.json 格式不符合约定，未导入'})

    def gate(self, task_id, task):
        self.store.execute('INSERT INTO gates(id,task_id,stage,version,created_at) VALUES(?,?,?,?,?)', (uid(), task_id, task['stage'], task['version'], now()))
        self.store.execute("UPDATE tasks SET status='waiting_confirmation' WHERE id=?", (task_id,))
        self.store.event(task_id, 'gate', {'stage': task['stage'], 'version': task['version']})

    def finish(self, run_id, task_id, status, error=None):
        self.store.execute('UPDATE runs SET status=?,error=?,finished_at=? WHERE id=?', (status, error, now(), run_id))
        self.store.execute('UPDATE tasks SET status=? WHERE id=?', ('review' if status == 'completed' else status, task_id))
        self.store.execute("UPDATE approvals SET status='expired' WHERE run_id=? AND status='pending'", (run_id,))
        self.store.event(task_id, 'run_finished', {'status': status, 'error': error}, run_id)

    async def stop(self, task_id):
        self.store.execute("UPDATE pending_inputs SET status='paused' WHERE task_id=? AND status='queued'", (task_id,))
        active = self.store.all("SELECT id,status FROM runs WHERE task_id=? AND status IN ('queued','running','stopping')", (task_id,))
        for run in active:
            if run['status'] == 'queued':
                self.finish(run['id'], task_id, 'interrupted')
            else:
                self.store.execute("UPDATE runs SET status='stopping' WHERE id=?", (run['id'],))
                self.store.execute("UPDATE tasks SET status='stopping' WHERE id=?", (task_id,))
                await self.runtime.stop(run['id'])
        return {'status': 'stopping' if any(r['status'] != 'queued' for r in active) else 'interrupted'}
