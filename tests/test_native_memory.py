from pathlib import Path
from workbench.store import Store
from workbench.config import Settings
from workbench.native_memory import NativeMemorySync
from workbench.shared_memory import retrieve


def test_native_change_delete_isolation(tmp_path):
    store = Store(tmp_path/'db.sqlite')
    store.execute("INSERT INTO users(id,email,password) VALUES('u','u@example.com','x')")
    for pid in ['a','b']:
        store.execute("INSERT INTO projects(id,owner_id,name,memory_enabled,created_at) VALUES(?,'u',?,1,0)",(pid,pid))
    sync = NativeMemorySync(store, Settings(data_dir=tmp_path))
    root=tmp_path/'workspaces'/'a'/'main'/'.native'
    claude=root/'claude-memory'; codex=root/'codex'/'memories_v2'
    claude.mkdir(parents=True); codex.mkdir(parents=True)
    a=claude/'MEMORY.md'; b=codex/'MEMORY.md'
    a.write_text('shared native fact',encoding='utf-8'); b.write_text('shared native fact',encoding='utf-8')
    assert sync.sync('a')['changed']==2
    assert len(retrieve(store,'a','native'))==1
    assert retrieve(store,'b','native')==[]
    assert sync.sync('a')['changed']==0
    a.write_text('updated native fact',encoding='utf-8')
    sync.sync('a')
    assert len(retrieve(store,'a','native'))==2
    b.unlink(); sync.sync('a')
    assert [r['content'] for r in retrieve(store,'a','native')]==['updated native fact']
    mid=retrieve(store,'a','native')[0]['id']
    store.execute('UPDATE memories SET content=? WHERE id=?',('user edited native fact',mid))
    a.unlink(); sync.sync('a')
    assert retrieve(store,'a','native')[0]['content']=='user edited native fact'
    store.connection.close()


def test_disabled_and_non_memory_files(tmp_path):
    store=Store(tmp_path/'db.sqlite')
    store.execute("INSERT INTO users(id,email,password) VALUES('u','u@example.com','x')")
    store.execute("INSERT INTO projects(id,owner_id,name,memory_enabled,created_at) VALUES('p','u','p',0,0)")
    root=tmp_path/'workspaces'/'p'/'main'/'.native'/'claude-memory'
    root.mkdir(parents=True); (root/'MEMORY.md').write_text('private disabled',encoding='utf-8')
    sync=NativeMemorySync(store,Settings(data_dir=tmp_path))
    assert sync.sync('p')['files']==0
    assert store.all('SELECT * FROM memories')==[]
    store.connection.close()

def test_account_memory_project_attribution(tmp_path, monkeypatch):
    home = tmp_path/'account'
    monkeypatch.setenv('CODEX_HOME', str(home))
    store = Store(tmp_path/'account-test.sqlite')
    store.execute("INSERT INTO users(id,email,password) VALUES('u','u@example.com','x')")
    for pid in ['a','b']:
        store.execute("INSERT INTO projects(id,owner_id,name,memory_enabled,created_at) VALUES(?,'u',?,1,0)",(pid,pid))
    workspace = tmp_path/'workspaces'/'a'/'main'
    workspace.mkdir(parents=True)
    for version in ['memories','memories_v2']:
        root = home/version/'rollout_summaries'
        root.mkdir(parents=True)
        (root/'matching.md').write_text('cwd: '+str(workspace)+'\nshared account fact',encoding='utf-8')
        (root/'foreign.md').write_text('cwd: '+str(tmp_path/'foreign')+'\nforeign secret fact',encoding='utf-8')
        (root/'unknown.md').write_text('unattributed account fact',encoding='utf-8')
        (home/version/'MEMORY.md').write_text('global mixed account fact',encoding='utf-8')
    auth = home/'auth.json'; auth.write_text('must remain untouched',encoding='utf-8')
    sync = NativeMemorySync(store,Settings(data_dir=tmp_path,runner='trusted-local'))
    assert sync.sync('a')['changed']==2
    assert len(retrieve(store,'a','account'))==1
    assert retrieve(store,'a','foreign')==[]
    assert retrieve(store,'a','global')==[]
    assert sync.sync('b')['files']==0
    assert sync.sync('a')['changed']==0
    for version in ['memories','memories_v2']:
        (home/version/'rollout_summaries'/'matching.md').unlink()
    sync.sync('a')
    assert retrieve(store,'a','account')==[]
    assert auth.read_text(encoding='utf-8')=='must remain untouched'
    assert not (workspace/'.native'/'codex'/'auth.json').exists()
    store.connection.close()
