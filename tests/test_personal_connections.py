"""Protocol boundary tests, never real-account acceptance."""

import asyncio
import json

import pytest
from fastapi import HTTPException
from workbench.computer import Computer
from workbench.connections import Connections
from workbench.goals import Goals
from workbench.store import Store, now


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "connections.db")
    s.execute("INSERT INTO users(id,email,password) VALUES('u','u@example.com','x')")
    s.execute(
        "INSERT INTO projects(id,owner_id,name,created_at) VALUES('p','u','P',?)",
        (now(),),
    )
    yield s
    s.connection.close()


def test_oauth_state_single_use_and_secret_redaction(store, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    from cryptography.fernet import Fernet

    monkeypatch.setenv("WORKBENCH_VAULT_KEY", Fernet.generate_key().decode())
    for key in ("CLIENT_ID", "CLIENT_SECRET", "REDIRECT_URI"):
        monkeypatch.setenv("WORKBENCH_GOOGLE_" + key, "test-" + key)
    connections = Connections(store, Goals(store, None))
    url = connections.authorize("u", "gmail")["url"]
    state = parse_qs(urlsplit(url).query)["state"][0]

    async def http(*args, **kwargs):
        return {"access_token": "secret-token", "refresh_token": "refresh-secret"}

    connections.http = http
    c = asyncio.run(connections.callback("u", state, "code"))
    assert "secret" not in json.dumps(connections.list("u"))
    assert (
        "secret-token"
        not in store.one(
            "SELECT secret FROM personal_connections WHERE id=?", (c["id"],)
        )["secret"]
    )
    with pytest.raises(HTTPException):
        asyncio.run(connections.callback("u", state, "code"))
    with pytest.raises(HTTPException):
        connections.owned(c["id"], "other")
    action = connections.propose(
        c["id"], "u", {"to": "a@example.com", "subject": "Test", "body": "Hello"}
    )
    assert (
        store.one("SELECT status FROM personal_actions WHERE id=?", (action["id"],))[
            "status"
        ]
        == "pending"
    )
    asyncio.run(connections.approve(action["id"], "u"))
    with pytest.raises(HTTPException):
        asyncio.run(connections.approve(action["id"], "u"))
    asyncio.run(connections.revoke(c["id"], "u"))
    assert (
        store.one("SELECT secret FROM personal_connections WHERE id=?", (c["id"],))[
            "secret"
        ]
        == ""
    )


def test_incremental_calendar_routes_only_subscribed_goals(store, monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("WORKBENCH_VAULT_KEY", Fernet.generate_key().decode())
    goals = Goals(store, None)
    connections = Connections(store, goals)
    secret = (
        connections.cipher()
        .encrypt(
            json.dumps({"access_token": "test", "expires_at": now() + 1000}).encode()
        )
        .decode()
    )
    store.execute(
        "INSERT INTO personal_connections(id,owner_id,provider,secret) VALUES('c','u','calendar',?)",
        (secret,),
    )
    for goal in ("g", "other"):
        store.execute(
            "INSERT INTO goals(id,owner_id,project_id,config,status,created_at) VALUES(?,'u','p','{}','active',?)",
            (goal, now()),
        )
    store.execute("INSERT INTO personal_subscriptions VALUES('c','g')")
    calls = []

    async def http(*args, **kwargs):
        calls.append(kwargs["params"])
        return {
            "items": [{"id": "event", "etag": "v1", "summary": "Meeting"}],
            "nextSyncToken": "cursor",
        }

    connections.http = http
    asyncio.run(connections.sync("c", "u"))
    asyncio.run(connections.sync("c", "u"))
    assert len(store.all("SELECT * FROM goal_events")) == 1
    assert store.one("SELECT goal_id FROM goal_events")["goal_id"] == "g"
    assert calls[1]["syncToken"] == "cursor"


def test_computer_owner_and_approval_boundary(store, monkeypatch):
    computer = Computer(store)
    with pytest.raises(HTTPException):
        computer.enabled()
    monkeypatch.setenv("WORKBENCH_PERSONAL_COMPUTER", "docker")
    calls = []

    async def command(*args, **kwargs):
        calls.append(args)
        return b"{}"

    computer.command = command
    asyncio.run(computer.start("u"))
    assert "--cap-drop" in calls[0] and "--mount" in calls[0]
    assert not any("docker.sock" in a for a in calls[0])
    with pytest.raises(HTTPException):
        computer.owned("other")
    action = computer.propose(
        "u", {"kind": "navigate", "url": "https://example.com"}, "查看公开页面"
    )
    assert len(calls) == 1
    asyncio.run(computer.approve("u", action["id"]))
    with pytest.raises(HTTPException):
        asyncio.run(computer.approve("u", action["id"]))
    with pytest.raises(HTTPException):
        asyncio.run(
            computer.control("u", {"kind": "navigate", "url": "http://127.0.0.1"})
        )
