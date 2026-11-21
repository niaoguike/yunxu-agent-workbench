import asyncio

import pytest
from fastapi import HTTPException
from workbench.goals import GoalChange, Goals
from workbench.store import Store, now


def test_goal_isolation_revision_and_event_dedup(tmp_path):
    store = Store(tmp_path / "test.db")
    store.execute(
        "INSERT INTO users(id,email,password) VALUES('u','u@example.com','x')"
    )
    store.execute(
        "INSERT INTO projects(id,owner_id,name,created_at) VALUES('p','u','P',?)",
        (now(),),
    )
    goals = Goals(store, None)
    store.execute(
        "INSERT INTO goals(id,owner_id,project_id,config,status,created_at) VALUES('g','u','p','{}','draft',?)",
        (now(),),
    )
    with pytest.raises(HTTPException) as error:
        goals.get("g", "other")
    assert error.value.status_code == 404
    goals.change("g", "u", GoalChange(revision=1, status="active"))
    with pytest.raises(HTTPException) as error:
        goals.change("g", "u", GoalChange(revision=1, status="paused"))
    assert error.value.status_code == 409
    goals.emit("g", "same", "user.input")
    goals.emit("g", "same", "user.input")
    assert len(goals.get("g", "u")["events"]) == 2
    goals.change("g", "u", GoalChange(revision=2, status="cancelled"))
    with pytest.raises(HTTPException):
        goals.change("g", "u", GoalChange(revision=3, status="active"))
    asyncio.run(goals.tick())
    assert not store.all("SELECT * FROM tasks")
    store.connection.close()


def test_goal_evidence_and_recurring_dispatch(tmp_path):
    from workbench.goals import GoalInput

    store = Store(tmp_path / "goals.db")
    store.execute(
        "INSERT INTO users(id,email,password) VALUES('u','u@example.com','x')"
    )
    store.execute(
        "INSERT INTO projects(id,owner_id,name,created_at) VALUES('p','u','P',?)",
        (now(),),
    )

    class Routines:
        def __init__(self):
            self.calls = []

        async def ready(self, engine):
            return True

        async def submit(self, task_id, content):
            self.calls.append(task_id)

    routines = Routines()
    goals = Goals(store, routines)
    config = GoalInput(
        project_id="p",
        title="演示",
        desired_outcome="完成演示",
        success_criteria=["可运行"],
    )
    store.execute(
        "INSERT INTO goals(id,owner_id,project_id,config,status,created_at) VALUES('g','u','p',?,'draft',?)",
        (config.model_dump_json(), now()),
    )
    goals.change("g", "u", GoalChange(revision=1, status="active"))
    asyncio.run(goals.tick())
    assert len(routines.calls) == 1
    goals.change("g", "u", GoalChange(revision=2, status="paused"))
    goals.change("g", "u", GoalChange(revision=3, status="active"))
    asyncio.run(goals.tick())
    assert len(routines.calls) == 1
    with pytest.raises(HTTPException):
        goals.change("g", "u", GoalChange(revision=4, status="completed"))
    goals.change(
        "g",
        "u",
        GoalChange(revision=4, status="completed", evidence={"0": "已检查演示成果"}),
    )
    assert goals.get("g", "u")["status"] == "completed"
    config.kind = "responsibility"
    store.execute(
        "INSERT INTO goals(id,owner_id,project_id,config,status,created_at) VALUES('r','u','p',?,'active',?)",
        (config.model_dump_json(), now()),
    )
    goals.emit("r", "first", "user.input")
    asyncio.run(goals.tick())
    task_id = routines.calls[-1]
    store.execute("UPDATE tasks SET status='delivered' WHERE id=?", (task_id,))
    asyncio.run(goals.tick())
    g = goals.get("r", "u")
    assert g["status"] == "active" and g["next_review"] > now()
    store.execute("UPDATE goals SET next_review=? WHERE id=?", (now() - 1, "r"))
    asyncio.run(goals.tick())
    assert len(routines.calls) == 3
    asyncio.run(goals.tick())
    assert len(routines.calls) == 3
    store.connection.close()


def test_personal_identity_is_durable_and_private(tmp_path):
    from workbench.personal import PersonalAssistant

    path = tmp_path / "personal.db"
    store = Store(path)
    for user in ("u", "v"):
        store.execute(
            "INSERT INTO users(id,email,password) VALUES(?,?,?)",
            (user, user + "@example.com", "x"),
        )
    assistant = PersonalAssistant(store, None)
    u = assistant.get("u")
    v = assistant.get("v")
    assert u["task_id"] != v["task_id"] and u["project_id"] != v["project_id"]
    assert assistant.get("u")["task_id"] == u["task_id"]
    store.connection.close()
    store = Store(path)
    assert PersonalAssistant(store, None).get("u")["task_id"] == u["task_id"]
    store.connection.close()


def test_pause_during_readiness_cannot_be_overwritten(tmp_path):
    from workbench.goals import GoalInput
    store=Store(tmp_path/'race.db')
    store.execute("INSERT INTO users(id,email,password) VALUES('u','u@example.com','x')")
    store.execute("INSERT INTO projects(id,owner_id,name,created_at) VALUES('p','u','P',?)",(now(),))
    class Routines:
        async def ready(self,engine):
            goals.change('g','u',GoalChange(revision=1,status='paused'))
            return False
        async def submit(self,*args): raise AssertionError('Paused goal dispatched')
    goals=Goals(store,Routines())
    config=GoalInput(project_id='p',title='Race',desired_outcome='Check race',success_criteria=['Safe'])
    store.execute("INSERT INTO goals(id,owner_id,project_id,config,status,created_at) VALUES('g','u','p',?,'active',?)",(config.model_dump_json(),now()))
    goals.emit('g','input','user.input')
    asyncio.run(goals.tick())
    assert goals.get('g','u')['status']=='paused'
    assert not store.all('SELECT * FROM tasks')
    store.connection.close()
