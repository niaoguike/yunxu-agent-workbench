from workbench.context_signals import ContextSignals
from workbench.store import Store


def state(tmp_path):
    store = Store(tmp_path / "test.sqlite")
    store.execute("INSERT INTO users VALUES('owner','owner@example.com','hash','')")
    for project in ("one", "two"):
        store.execute(
            "INSERT INTO projects(id,owner_id,name,created_at) VALUES(?,?,?,0)",
            (project, "owner", project),
        )
    return store


def test_context_windows_persist_and_compaction_resets(tmp_path):
    store = state(tmp_path)
    signals = ContextSignals(store)
    assert signals.usage("one", "codex", "session", 69, 100) == []
    assert [
        entry["threshold"]
        for entry in signals.usage("one", "codex", "session", 86, 100)
    ] == [0.7, 0.85]
    assert ContextSignals(store).usage("one", "codex", "session", 86, 100) == []
    assert [
        entry["threshold"]
        for entry in signals.usage("one", "codex", "session", 93, 100)
    ] == [0.92]
    signals.compact("one", "codex", "session")
    assert signals.usage("one", "codex", "session", 71, 100)[0]["epoch"] == 1
    assert signals.usage("two", "codex", "session", 71, 100)[0]["threshold"] == 0.7
    assert store.all("SELECT * FROM memories") == []
    store.connection.close()


def test_unknown_or_invalid_context_limits_are_not_estimated(tmp_path):
    store = state(tmp_path)
    signals = ContextSignals(store)
    for used, window in [
        (80, 0),
        (-1, 100),
        (80, None),
        (float("nan"), 100),
        (True, 100),
    ]:
        assert signals.usage("one", "codex", "session", used, window) == []
    assert store.all("SELECT * FROM memory_context_state") == []
    store.connection.close()
