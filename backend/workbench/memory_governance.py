"""Versioned facts: untrusted proposals never become retrieval context implicitly."""
import hashlib
import json
from .store import uid, now
from .files import safe_path, digest


class MemoryGovernance:
    def __init__(self, store):
        self.s = store

    def propose(self, project_id, content, category='project', *, fact_key=None, scope='project', source='agent', task_id=None, evidence=None):
        if not isinstance(content,str) or not content.strip() or len(content)>4000 or not isinstance(scope,str) or not scope or len(scope)>240:
            raise ValueError('无效记忆正文或范围')
        key = fact_key or 'unclassified:'+hashlib.sha256(content.encode()).hexdigest()
        if not isinstance(key,str) or not key or len(key)>240:
            raise ValueError('无效事实键')
        previous = self.s.one("SELECT id FROM memories WHERE project_id=? AND fact_key=? AND scope=? AND content=? AND state IN ('pending','active','conflict')",(project_id,key,scope,content))
        if previous:
            return previous['id']
        mid = uid()
        with self.s.transaction() as db:
            conflicts = db.execute("SELECT id FROM memories WHERE project_id=? AND fact_key=? AND scope=? AND state IN ('active','pending','conflict')",(project_id,key,scope)).fetchall()
            state = 'conflict' if conflicts else 'pending'
            if conflicts:
                db.execute("UPDATE memories SET state='conflict',revision=revision+1 WHERE project_id=? AND fact_key=? AND scope=? AND state IN ('active','pending')",(project_id,key,scope))
            db.execute('INSERT INTO memories(id,project_id,category,content,created_at,fact_key,scope,state,source,task_id,evidence) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(mid,project_id,category,content,now(),key,scope,state,source,task_id,json.dumps(evidence or {})))
        return mid

    def publish(self, mid, *, approved=False, workspace=None):
        row = self.s.one('SELECT * FROM memories WHERE id=?',(mid,))
        if not row or row['state'] not in {'pending','conflict','active'}:
            raise ValueError('记忆不可发布')
        if not row['fact_key']:
            self.s.execute('UPDATE memories SET fact_key=? WHERE id=?',('unclassified:'+row['id'],mid))
            row['fact_key'] = 'unclassified:'+row['id']
        evidence = json.loads(row['evidence'])
        if not approved:
            if not workspace or not evidence or row['fact_key'].startswith('unclassified:'):
                return False
            try:
                path = safe_path(workspace,evidence['path'])
                if digest(path) != evidence['digest']:
                    return False
                data = json.loads(path.read_text(encoding='utf-8'))
                for part in evidence['pointer']:
                    data = data[part]
                if data != evidence['value'] or row['content'] != json.dumps(data,ensure_ascii=False,sort_keys=True):
                    return False
            except (OSError,ValueError,KeyError,TypeError,IndexError):
                return False
        with self.s.transaction() as db:
            current = db.execute('SELECT revision FROM memories WHERE id=?',(mid,)).fetchone()
            if not current or current['revision'] != row['revision']:
                raise ValueError('记忆版本已变化，请重新核验')
            old = db.execute("SELECT id FROM memories WHERE project_id=? AND fact_key=? AND scope=? AND id<>? AND state IN ('active','conflict','pending')",(row['project_id'],row['fact_key'],row['scope'],mid)).fetchall()
            for previous in old:
                db.execute("UPDATE memories SET state='superseded',superseded_by=?,revision=revision+1 WHERE id=?",(mid,previous['id']))
            db.execute("UPDATE memories SET state='active',verified_at=?,valid_from=?,revision=revision+1 WHERE id=?",(now(),now(),mid))
        return True

    def invalidate_changed_evidence(self, project_id, workspace):
        for row in self.s.all("SELECT id,evidence FROM memories WHERE project_id=? AND state='active'",(project_id,)):
            evidence = json.loads(row['evidence'])
            if not evidence:
                continue
            try:
                unchanged = digest(safe_path(workspace,evidence['path'])) == evidence['digest']
            except (OSError,ValueError,KeyError):
                unchanged = False
            if not unchanged:
                self.s.execute("UPDATE memories SET state='pending',verified_at=NULL,revision=revision+1 WHERE id=?",(row['id'],))

    def settle(self, task_id, workspace):
        for row in self.s.all("SELECT id FROM memories WHERE task_id=? AND state IN ('pending','conflict')",(task_id,)):
            self.publish(row['id'],workspace=workspace)

    def task_state(self, task_id):
        task = self.s.one('SELECT status,version,stage FROM tasks WHERE id=?',(task_id,))
        messages = self.s.all("SELECT role,content FROM messages WHERE task_id=? AND role='user' ORDER BY created_at DESC LIMIT 3",(task_id,))
        return {'task':task,'current_requirements':messages,'rule':'当前要求确定目标；工具观察确定现状；长期记忆仅为背景，冲突应核验。'}
