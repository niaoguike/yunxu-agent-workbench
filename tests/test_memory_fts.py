from workbench.store import Store
from workbench.shared_memory import search, retrieve


def test_fts_ranking_sync_and_migration(tmp_path):
    path = tmp_path / 'memory.db'
    s = Store(path)
    s.execute("INSERT INTO users(id,email,password) VALUES('u','u@x','x')")
    for project in ('p', 'other'):
        s.execute("INSERT INTO projects(id,owner_id,name,created_at) VALUES(?,'u',?,0)", (project, project))
    def add(mid, content, project='p', state='active'):
        s.execute("INSERT INTO memories(id,project_id,category,content,created_at,state) VALUES(?,?,'project',?,0,?)", (mid, project, content, state))
    add('short', 'SQLite 中文记忆')
    add('long', 'SQLite ' + 'unrelated ' * 100)
    add('hidden', 'SQLite', state='pending')
    add('foreign', 'SQLite', project='other')
    assert [r['id'] for r in search(s, 'p', 'SQLite')] == ['short', 'long']
    assert search(s, 'p', '中文记忆')[0]['id'] == 'short'
    assert search(s, 'p', '" OR * - ()') == []
    assert len(retrieve(s, 'p', 'SQLite', budget=3)[0]['content']) == 3
    s.execute("UPDATE memories SET content='PostgreSQL' WHERE id='short'")
    assert [r['id'] for r in search(s, 'p', 'SQLite')] == ['long']
    s.execute("DELETE FROM memories WHERE id='long'")
    assert search(s, 'p', 'SQLite') == []
    for trigger in ('insert', 'update', 'delete'):
        s.execute('DROP TRIGGER memory_fts_' + trigger)
    s.execute('DROP TABLE memory_fts')
    s.connection.close()
    s = Store(path)
    assert search(s, 'p', 'PostgreSQL')[0]['id'] == 'short'
    s.connection.close()
