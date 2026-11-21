from workbench.shared_memory import retrieve
from workbench.store import Store, now


def test_scoped_ranked_bounded_retrieval(tmp_path):
    store = Store(tmp_path / "retrieval.db")
    store.execute(
        "INSERT INTO users(id,email,password) VALUES('u','u@example.com','x')"
    )
    for project in ("project", "other"):
        store.execute(
            "INSERT INTO projects(id,owner_id,name,created_at) VALUES(?,?,?,?)",
            (project, "u", project, now()),
        )
    for item, project, content, state in [
        ("old", "project", "unrelated", "active"),
        ("hit", "project", "codex testing", "active"),
        ("private", "other", "codex private", "active"),
        ("pending", "project", "codex candidate", "pending"),
    ]:
        store.execute(
            "INSERT INTO memories(id,project_id,category,content,state,scope,revision,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                item,
                project,
                "project",
                content,
                state,
                "project",
                2 if item == "hit" else 1,
                now(),
            ),
        )
    assert retrieve(store, "project", "codex", budget=5) == [
        dict(id="hit", category="project", content="codex", revision=2)
    ]
    assert retrieve(store, "project", "") == []
    store.connection.close()
