import json
from workbench.store import Store
from workbench.memory_governance import MemoryGovernance
from workbench.shared_memory import retrieve
from workbench.files import digest


def test_conflict_scope_and_versions(tmp_path):
    s=Store(tmp_path/'db')
    s.execute("INSERT INTO users(id,email,password) VALUES('u','u@x.com','x')")
    s.execute("INSERT INTO projects(id,owner_id,name,created_at) VALUES('p','u','p',0)")
    g=MemoryGovernance(s)
    a=g.propose('p','SQLite',fact_key='database')
    assert retrieve(s,'p','SQLite')==[]
    assert not g.publish(a)
    g.publish(a,approved=True)
    b=g.propose('p','PostgreSQL',fact_key='database')
    assert s.one('SELECT state FROM memories WHERE id=?',(a,))['state']=='conflict'
    assert retrieve(s,'p','SQLite')==[]
    g.publish(b,approved=True)
    assert s.one('SELECT superseded_by FROM memories WHERE id=?',(a,))['superseded_by']==b
    c=g.propose('p','SQLite',fact_key='database',scope='development')
    g.publish(c,approved=True)
    assert retrieve(s,'p','SQLite')==[]
    assert retrieve(s,'p','SQLite',scope='development')
    assert retrieve(s,'p','PostgreSQL')
    s.connection.close()


def test_evidence_is_verified_not_assumed(tmp_path):
    s=Store(tmp_path/'db');s.execute("INSERT INTO users(id,email,password) VALUES('u','u@x.com','x')")
    s.execute("INSERT INTO projects(id,owner_id,name,created_at) VALUES('p','u','p',0)")
    g=MemoryGovernance(s)
    path=tmp_path/'config.json';path.write_text('{"database":"pg"}',encoding='utf-8')
    evidence={'path':'config.json','digest':digest(path),'pointer':['database'],'value':'pg'}
    mid=g.propose('p',json.dumps('pg'),fact_key='database',evidence=evidence)
    assert g.publish(mid,workspace=tmp_path)
    bad=g.propose('p',json.dumps('sqlite'),fact_key='database',evidence=evidence)
    assert not g.publish(bad,workspace=tmp_path)
    path.write_text('{}',encoding='utf-8')
    assert not g.publish(mid,workspace=tmp_path)
    s.connection.close()
