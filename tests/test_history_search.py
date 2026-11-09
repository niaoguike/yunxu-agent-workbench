from workbench.store import Store
from workbench.history_search import HistorySearch


def test_history_scope_and_sync(tmp_path):
    store = Store(tmp_path / 'test.db')
    store.execute("INSERT INTO users(id,email,password) VALUES('u','u','p')")
    for project in ['p', 'other']:
        store.execute('INSERT INTO projects(id,owner_id,name,created_at) VALUES(?,?,?,0)', (project,'u',project))
    for task, project in [('a','p'),('b','p'),('c','other')]:
        store.execute("INSERT INTO tasks(id,project_id,title,mode,engine,created_at) VALUES(?,?,?,'unified','codex',0)", (task,project,task))
        store.execute("INSERT INTO messages VALUES(?,?,'user','历史检索 SQLite',0)", (task,task))
    search = HistorySearch(store)
    result = search.search('p', '历史检索')
    assert {r['task_id'] for r in result['results']} == {'a','b'}
    store.execute("UPDATE messages SET content='changed' WHERE id='a'")
    assert [r['id'] for r in search.search('p','历史检索')['results']] == ['b']
    store.execute("DELETE FROM messages WHERE id='b'")
    assert not search.search('p','历史检索')['results']
