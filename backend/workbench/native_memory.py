"""Synchronize project-owned native memory files into the shared memory store."""
import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from .store import uid, now


def native_roots(workspace):
    root = Path(workspace) / '.native'
    roots = [('claude', root / 'claude-memory'), ('codex', root / 'codex' / 'memories'), ('codex', root / 'codex' / 'memories_v2')]
    return roots


def prepare_native_memory(workspace):
    """Set per-workspace native storage without moving account-wide memories."""
    workspace = Path(workspace)
    root = workspace / '.native'
    if root.is_symlink() or (root.exists() and root.is_junction()):
        raise ValueError('Native storage cannot be a link')
    codex = root / 'codex'
    for directory in [codex, root / 'claude-memory']:
        if directory.is_symlink() or (directory.exists() and directory.is_junction()):
            raise ValueError('Native storage cannot be a link')
        directory.mkdir(parents=True, exist_ok=True)



class NativeMemorySync:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.job = None
        self.errors = {}
        store.execute('CREATE TABLE IF NOT EXISTS native_memory_files(project_id TEXT,source TEXT,engine TEXT,digest TEXT,entries TEXT,updated_at REAL,PRIMARY KEY(project_id,source))')
        store.execute('CREATE TABLE IF NOT EXISTS native_memory_generated(id TEXT PRIMARY KEY,content TEXT NOT NULL)')

    def start(self):
        self.job = asyncio.create_task(self.watch())

    async def close(self):
        if self.job:
            self.job.cancel()
            await asyncio.gather(self.job, return_exceptions=True)

    async def watch(self):
        while True:
            for row in self.store.all('SELECT id FROM projects WHERE memory_enabled=1 AND deleted_at IS NULL'):
                try:
                    self.sync(row['id'])
                    self.errors.pop(row['id'], None)
                except (OSError, ValueError) as error:
                    self.errors[row['id']] = str(error)
            await asyncio.sleep(2)

    def sync(self, project_id):
        project = self.store.one('SELECT memory_enabled,deleted_at FROM projects WHERE id=?', (project_id,))
        if not project or not project['memory_enabled'] or project['deleted_at'] is not None:
            return {'files': 0, 'changed': 0}
        base = self.settings.data_dir / 'workspaces' / project_id
        workspaces = [base / 'main']
        members = base / 'members'
        if members.is_dir() and not members.is_symlink() and not members.is_junction():
            workspaces += [p for p in members.iterdir() if p.is_dir() and not p.is_symlink() and not p.is_junction()]
        seen, changed = set(), 0
        for workspace in workspaces:
            roots = [(engine, root, False) for engine, root in native_roots(workspace)]
            if self.settings.runner == 'trusted-local':
                home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
                roots += [('codex', home / version / 'rollout_summaries', True) for version in ('memories', 'memories_v2')]
            for engine, root, account in roots:
                if any(p.is_symlink() or (p.exists() and p.is_junction()) for p in [workspace, workspace / '.native', root.parent, root]):
                    continue
                if not root.exists():
                    continue
                for parent, dirs, files in os.walk(root, followlinks=False):
                    dirs[:] = [d for d in dirs if not d.startswith('.') and not (Path(parent)/d).is_symlink() and not (Path(parent)/d).is_junction()]
                    for name in files:
                        path = Path(parent)/name
                        if path.suffix.lower() not in {'.md','.txt'} or path.is_symlink() or path.is_junction():
                            continue
                        source = ("account-codex/"+workspace.relative_to(base).as_posix()+"/"+root.parent.name+"/"+path.relative_to(root).as_posix()) if account else path.relative_to(base).as_posix()
                        seen.add(source)
                        if len(seen) > 2000:
                            raise ValueError('原生记忆文件超过2000个')
                        previous = self.store.one('SELECT * FROM native_memory_files WHERE project_id=? AND source=?', (project_id, source))
                        try:
                            with path.open('rb') as stream:
                                raw = stream.read(256001)
                            if len(raw) > 256000:
                                continue
                            content = raw.decode('utf-8-sig')
                        except (OSError, UnicodeError):
                            continue
                        if account:
                            # Global indexes combine projects. Only import single-rollout notes
                            # with exactly one explicit workspace attribution, never infer it.
                            matches = re.findall(r'^cwd:\s*([^\r\n]+)', content, re.MULTILINE)
                            if len(matches) != 1 or Path(matches[0].strip().strip('"')).resolve() != workspace.resolve():
                                seen.discard(source)
                                continue
                        fingerprint = hashlib.sha256(raw).hexdigest()
                        if previous and previous['digest'] == fingerprint:
                            continue
                        ids = []
                        with self.store.transaction() as db:
                            for start in range(0, len(content), 4000):
                                chunk = content[start:start+4000].strip()
                                if not chunk:
                                    continue
                                existing = db.execute("SELECT id FROM memories WHERE project_id=? AND content=? AND state NOT IN ('withdrawn','superseded')", (project_id, chunk)).fetchone()
                                mid = existing['id'] if existing else uid()
                                if not existing:
                                    db.execute("INSERT INTO memories(id,project_id,category,content,created_at,fact_key,source) VALUES(?,?,'reference',?,?,?,?)", (mid,project_id,chunk,now(),'unclassified:'+hashlib.sha256(chunk.encode()).hexdigest(),engine+':'+source))
                                    db.execute('INSERT INTO native_memory_generated VALUES(?,?)', (mid,chunk))
                                ids.append(mid)
                            db.execute('INSERT OR REPLACE INTO native_memory_files VALUES(?,?,?,?,?,?)', (project_id,source,engine,fingerprint,json.dumps(ids),now()))
                        changed += 1
        for old in self.store.all('SELECT source FROM native_memory_files WHERE project_id=?', (project_id,)):
            if old['source'] not in seen:
                self.store.execute('DELETE FROM native_memory_files WHERE project_id=? AND source=?', (project_id,old['source']))
                changed += 1
        live = {mid for row in self.store.all('SELECT entries FROM native_memory_files') for mid in json.loads(row['entries'])}
        for row in self.store.all('SELECT g.id,g.content FROM native_memory_generated g JOIN memories m ON m.id=g.id WHERE m.project_id=?', (project_id,)):
            if row['id'] not in live:
                self.store.execute("UPDATE memories SET state='withdrawn',revision=revision+1 WHERE id=? AND content=?", (row['id'],row['content']))
                self.store.execute('DELETE FROM native_memory_generated WHERE id=?', (row['id'],))
        return {'files': len(seen), 'changed': changed}
