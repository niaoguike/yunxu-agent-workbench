"""Offline acceptance: no real channel network requests or credentials."""

import json
import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from test_api import EngineFixture, wait_task
from workbench.api import create_app
from workbench.automations import ChannelError, RoutineInput, next_due, token_names
from workbench.config import Settings


class TransportFixture:
    def __init__(self):
        self.calls = []
        self.sent = []
        self.fail = None
        self.updates = []

    async def call(self, connection, method, payload=None):
        self.calls.append((method, payload))
        if method == "getUpdates":
            return {"ok": True, "result": self.updates}
        if method == "apps.connections.open":
            return {"ok": True, "url": "wss://wss-primary.slack.com/link"}
        return {"ok": True, "result": {"id": 1}}

    async def send(self, connection, text, thread):
        self.sent.append((connection["chat_id"], text, thread))
        if self.fail:
            raise self.fail
        return str(900 + len(self.sent))


@pytest.fixture
def setup(tmp_path):
    engine = EngineFixture()
    app = create_app(Settings(data_dir=tmp_path), runtime=engine)
    with TestClient(app) as client:
        manager = app.state.routines
        client.portal.call(manager.close)
        manager.transport = TransportFixture()
        client.post(
            "/api/auth/register",
            json={"email": "routine@example.com", "password": "safe-password-123"},
        )
        project = client.post("/api/projects", json={"name": "Routines"}).json()
        yield client, project, manager, engine


def connection(client, project, provider="telegram", **patch):
    body = {
        "name": "Channel",
        "provider": provider,
        "chat_id": "123" if provider == "telegram" else "C123",
        "user_id": "42" if provider == "telegram" else "U42",
        "receive": True,
        "send": True,
        "enabled": False,
        **patch,
    }
    response = client.post(f"/api/projects/{project['id']}/connections", json=body)
    assert response.status_code == 201, response.text
    return response.json(), body


def routine(client, project, **patch):
    body = {"name": "Daily analysis", "prompt": "Analyze actual project files", **patch}
    response = client.post(f"/api/projects/{project['id']}/routines", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"], body


def wait_event(manager, connection_id, event_id):
    for _ in range(100):
        item = manager.store.one(
            "SELECT * FROM channel_events WHERE connection_id=? AND event_id=?",
            (connection_id, event_id),
        )
        if item and item["status"] != "accepted":
            return item
        time.sleep(0.01)
    raise AssertionError(item)


def test_next_due_in_local_timezone_and_weekday():
    config = RoutineInput(name="Test", prompt="Work", time="09:00").model_dump()
    after = datetime(2026, 11, 7, 0, tzinfo=UTC).timestamp()
    assert next_due(config, after) == after + 3600
    assert next_due(config, after + 3600) == after + 3600 + 86400
    config.update(frequency="weekly", weekday=0)
    assert (
        datetime.fromtimestamp(next_due(config, after), UTC).isoformat()
        == "2026-11-09T01:00:00+00:00"
    )


@pytest.mark.parametrize(
    "patch",
    [{"time": "25:00"}, {"timezone": "Bad/Zone"}, {"weekday": 8}, {"prompt": ""}],
)
def test_invalid_schedule(setup, patch):
    client, project, _, _ = setup
    assert (
        client.post(
            f"/api/projects/{project['id']}/routines",
            json={"name": "Test", "prompt": "Work", **patch},
        ).status_code
        == 422
    )


def test_schedule_once_overlap_pause_and_real_workflow(setup):
    client, project, manager, engine = setup
    engine.hold = True
    routine_id, body = routine(client, project, enabled=True)
    due = time.time()
    manager.store.execute(
        "UPDATE routines SET next_due=? WHERE id=?", (due, routine_id)
    )
    client.portal.call(manager.tick, due + 0.01)
    run = manager.store.one(
        "SELECT * FROM routine_runs WHERE routine_id=?", (routine_id,)
    )
    assert run["status"] == "dispatched"
    wait_task(client, run["task_id"], {"running"})
    assert client.post(f"/api/routines/{routine_id}/run").json()["status"] == "skipped"
    manager.store.execute(
        "UPDATE routines SET next_due=? WHERE id=?", (due, routine_id)
    )
    client.portal.call(manager.tick, due + 0.02)
    assert (
        manager.store.one(
            "SELECT COUNT(*) AS n FROM routine_runs WHERE slot=?", (str(due),)
        )["n"]
        == 1
    )
    assert (
        client.put(
            f"/api/routines/{routine_id}", json={**body, "enabled": False}
        ).status_code
        == 200
    )
    engine.hold = False
    completed = wait_task(client, run["task_id"], {"delivered"})
    assert completed["messages"][0]["content"] == body["prompt"]
    assert completed["artifacts"]
    records = client.get(f"/api/projects/{project['id']}/routines").json()
    assert records[0]["runs"][-1]["task_status"] == "delivered"


def test_unavailable_runner_keeps_task_and_message(tmp_path):
    with TestClient(
        create_app(Settings(data_dir=tmp_path, runner="disabled"))
    ) as client:
        client.post(
            "/api/auth/register",
            json={"email": "disabled@example.com", "password": "safe-password-123"},
        )
        project = client.post("/api/projects", json={"name": "Disabled"}).json()
        routine_id, body = routine(client, project)
        record = client.post(f"/api/routines/{routine_id}/run").json()
        assert record["status"] == "failed"
        task = client.get(f"/api/tasks/{record['task_id']}").json()
        assert task["messages"][0]["content"] == body["prompt"]
        assert not task["runs"]


def test_ingress_allowlist_dedupe_and_reply_mapping(setup, monkeypatch):
    client, project, manager, engine = setup
    engine.hold = True
    info, body = connection(client, project, enabled=True)
    connection_id = info["id"]
    assert not client.portal.call(
        manager.ingest, connection_id, "1", "999", "42", "Work", "10"
    )
    assert not client.portal.call(
        manager.ingest, connection_id, "1", "123", "99", "Work", "10"
    )
    assert client.portal.call(
        manager.ingest, connection_id, "1", "123", "42", "First", "10"
    )
    event = wait_event(manager, connection_id, "1")
    wait_task(client, event["task_id"], {"running"})
    assert client.portal.call(
        manager.ingest, connection_id, "1", "123", "42", "Duplicate", "10"
    )
    assert client.portal.call(
        manager.ingest, connection_id, "2", "123", "42", "Continue", "11", "10"
    )
    second = wait_event(manager, connection_id, "2")
    assert event["task_id"] == second["task_id"]
    assert second["status"] == "queued"
    assert manager.store.one("SELECT COUNT(*) AS n FROM channel_events")["n"] == 2
    client.put(f"/api/connections/{connection_id}", json={**body, "enabled": False})
    assert not client.portal.call(
        manager.ingest, connection_id, "3", "123", "42", "Stopped", "12"
    )
    engine.hold = False


def test_handoff_completion_and_send_failures(setup, monkeypatch):
    client, project, manager, _engine = setup
    info, body = connection(client, project, enabled=True)
    connection_id = info["id"]
    monkeypatch.setenv(info["token_env"], "fake-token-never-sent")
    task = client.post(
        f"/api/projects/{project['id']}/tasks", json={"title": "Handoff"}
    ).json()
    assert (
        client.post(f"/api/tasks/{task['id']}/handoff/{connection_id}").status_code
        == 201
    )
    client.portal.call(manager.notify)
    assert len(manager.transport.sent) == 1
    item = manager.store.one("SELECT * FROM channel_outbox")
    assert item["status"] == "sent"
    client.portal.call(manager.notify)
    assert len(manager.transport.sent) == 1
    assert client.portal.call(
        manager.ingest,
        connection_id,
        "reply",
        "123",
        "42",
        "Please continue",
        "55",
        item["external_id"],
    )
    event = wait_event(manager, connection_id, "reply")
    assert event["task_id"] == task["id"]
    wait_task(client, task["id"], {"delivered"})
    manager.transport.fail = ChannelError("Network uncertain", uncertain=True)
    client.portal.call(manager.notify)
    unknown = manager.store.one("SELECT * FROM channel_outbox WHERE status='unknown'")
    assert unknown
    assert client.post(f"/api/outbox/{unknown['id']}/retry").status_code == 409
    manager.store.execute(
        "UPDATE channel_outbox SET status='failed' WHERE id=?", (unknown["id"],)
    )
    assert client.post(f"/api/outbox/{unknown['id']}/retry").status_code == 200
    manager.transport.fail = None
    client.portal.call(manager.notify)
    assert (
        manager.store.one(
            "SELECT status FROM channel_outbox WHERE id=?", (unknown["id"],)
        )["status"]
        == "sent"
    )
    body = client.get(f"/api/projects/{project['id']}/connections").json()
    assert "fake-token-never-sent" not in json.dumps(body)


def test_telegram_cursor_and_bot_filter(setup):
    client, project, manager, _ = setup
    info, _ = connection(client, project, enabled=True)
    manager.transport.updates = [
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123},
                "from": {"id": 42, "is_bot": True},
                "text": "Ignore bot",
            },
        },
        {
            "update_id": 2,
            "message": {
                "message_id": 11,
                "chat": {"id": 123},
                "from": {"id": 42},
                "text": "User work",
            },
        },
    ]
    client.portal.call(manager.receive, manager.connection(info["id"]))
    event = wait_event(manager, info["id"], "2")
    assert event["task_id"]
    assert manager.connection(info["id"])["cursor"] == 3
    assert manager.store.one("SELECT COUNT(*) AS n FROM channel_events")["n"] == 1


def test_slack_socket_ack_and_cross_event_dedupe(setup, monkeypatch):
    client, project, manager, _ = setup
    info, _ = connection(client, project, provider="slack", enabled=True)

    class Socket:
        def __init__(self):
            self.acks = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def __aiter__(self):
            async def events():
                for i, kind in enumerate(["message", "app_mention"]):
                    yield json.dumps(
                        {
                            "type": "events_api",
                            "envelope_id": str(i),
                            "payload": {
                                "event": {
                                    "type": kind,
                                    "channel": "C123",
                                    "user": "U42",
                                    "ts": "100.1",
                                    "text": "Build report",
                                }
                            },
                        }
                    )

            return events()

        async def send(self, value):
            self.acks.append(json.loads(value))

    socket = Socket()
    monkeypatch.setattr("websockets.asyncio.client.connect", lambda *a, **k: socket)
    client.portal.call(manager.receive, manager.connection(info["id"]))
    wait_event(manager, info["id"], "C123:100.1")
    assert len(socket.acks) == 2
    assert manager.store.one("SELECT COUNT(*) AS n FROM channel_events")["n"] == 1


def test_restart_marks_missed_and_uncertain_without_replay(tmp_path):
    credentials = {"email": "restart@example.com", "password": "safe-password-123"}
    settings = Settings(data_dir=tmp_path, runner="disabled")
    with TestClient(create_app(settings)) as client:
        manager = client.app.state.routines
        client.portal.call(manager.close)
        client.post("/api/auth/register", json=credentials)
        project = client.post("/api/projects", json={"name": "Restart"}).json()
        routine_id, _ = routine(client, project, enabled=True)
        manager.store.execute(
            "UPDATE routines SET next_due=? WHERE id=?", (time.time() - 120, routine_id)
        )
        info, _ = connection(client, project)
        task = client.post(
            f"/api/projects/{project['id']}/tasks", json={"title": "Uncertain"}
        ).json()
        manager.store.execute(
            "INSERT INTO channel_outbox VALUES('uncertain',?,?,?,'sending','','',?)",
            (info["id"], task["id"], "test", time.time()),
        )
    with TestClient(create_app(settings)) as client:
        client.post("/api/auth/login", json=credentials)
        result = client.get(f"/api/projects/{project['id']}/routines").json()[0]
        assert result["runs"][0]["status"] == "missed"
        assert result["runs"][0]["task_id"] is None
        assert (
            client.app.state.store.one(
                "SELECT status FROM channel_outbox WHERE id='uncertain'"
            )["status"]
            == "unknown"
        )
        assert (
            client.get(f"/api/projects/{project['id']}/connections").json()[0][
                "chat_id"
            ]
            == "123"
        )


def test_project_ownership_recycle_and_credential_isolation(setup):
    client, project, manager, _ = setup
    routine_id, _ = routine(client, project)
    info, _ = connection(client, project)
    assert client.post(f"/api/connections/{info['id']}/check").status_code == 200
    assert manager.transport.calls == [("getMe", None)]
    client.post(
        "/api/auth/register",
        json={"email": "other@example.com", "password": "safe-password-123"},
    )
    assert client.get(f"/api/projects/{project['id']}/routines").status_code == 404
    assert client.post(f"/api/routines/{routine_id}/run").status_code == 404
    assert client.post(f"/api/connections/{info['id']}/check").status_code == 404
    client.post(
        "/api/auth/login",
        json={"email": "routine@example.com", "password": "safe-password-123"},
    )
    client.delete(f"/api/projects/{project['id']}")
    assert client.post(f"/api/routines/{routine_id}/run").status_code == 404
    assert not client.portal.call(
        manager.ingest, info["id"], "1", "123", "42", "Work", "10"
    )


def test_plugin_atomic_import_disabled_and_group_toggle(setup):
    client, project, _manager, _ = setup
    url = f"/api/projects/{project['id']}/plugins"
    body = {
        "name": "csv",
        "version": "1",
        "extensions": [
            {"name": "analysis", "kind": "skill", "content": "Inspect data"},
            {
                "name": "tool",
                "kind": "mcp",
                "content": '{"command":"python","args":["tool.py"]}',
            },
        ],
    }
    result = client.post(url, json=body)
    assert result.status_code == 201, result.text
    plugin = client.get(url).json()[0]
    assert not any(e["enabled"] for e in plugin["extensions"])
    assert client.post(url, json=body).status_code == 409
    assert (
        client.put(
            f"/api/plugins/{plugin['id']}/enabled", json={"enabled": True}
        ).status_code
        == 200
    )
    assert all(e["enabled"] for e in client.get(url).json()[0]["extensions"])
    bad = {
        **body,
        "name": "bad",
        "extensions": [
            body["extensions"][0],
            {"name": "invalid", "kind": "mcp", "content": "{}"},
        ],
    }
    assert client.post(url, json=bad).status_code == 400
    assert len(client.get(url).json()) == 1
    assert (
        client.put(
            f"/api/plugins/{plugin['id']}/enabled", json={"enabled": False}
        ).status_code
        == 200
    )
    assert not any(e["enabled"] for e in client.get(url).json()[0]["extensions"])


async def test_transport_uses_official_endpoints_and_redacts_secrets(monkeypatch):
    import urllib.error

    from workbench.automations import ChatTransport

    transport = ChatTransport()
    connection = {"id": "test", "provider": "telegram", "chat_id": "123"}
    token = "123:private-test-value"
    monkeypatch.setenv(token_names("test")["token_env"], token)
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, limit):
            return b'{"ok":true,"result":{"message_id":99}}'

    def request(req, **kwargs):
        requests.append(req)
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", request)
    assert await transport.send(connection, "Plain text", "") == "99"
    assert (
        requests[0].full_url == "https://api.telegram.org/bot" + token + "/sendMessage"
    )
    assert json.loads(requests[0].data)["text"] == "Plain text"
    assert "parse_mode" not in json.loads(requests[0].data)

    def unavailable(req, **kwargs):
        raise OSError("Never leak " + token)

    monkeypatch.setattr("urllib.request.urlopen", unavailable)
    with pytest.raises(ChannelError) as result:
        await transport.send(connection, "Text", "")
    assert result.value.uncertain
    assert token not in str(result.value)

    def server_error(req, **kwargs):
        raise urllib.error.HTTPError(req.full_url, 503, token, {}, None)

    monkeypatch.setattr("urllib.request.urlopen", server_error)
    with pytest.raises(ChannelError) as result:
        await transport.send(connection, "Text", "")
    assert result.value.uncertain
    assert token not in str(result.value)


async def test_slack_send_is_plain_escaped_text(monkeypatch):
    from workbench.automations import ChatTransport

    monkeypatch.setenv(token_names("slack-test")["token_env"], "bot-test")
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, limit):
            return b'{"ok":true,"ts":"123.4"}'

    def request(req, **kwargs):
        requests.append(req)
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", request)
    connection = {"id": "slack-test", "provider": "slack", "chat_id": "C123"}
    assert (
        await ChatTransport().send(connection, "<!channel> & data", "100.1") == "123.4"
    )
    body = json.loads(requests[0].data)
    assert body["text"] == "&lt;!channel&gt; &amp; data"
    assert body["thread_ts"] == "100.1"
    assert body["mrkdwn"] is False
    assert requests[0].full_url == "https://slack.com/api/chat.postMessage"


def test_slow_notifications_do_not_block_schedule(setup):
    import asyncio

    client, project, manager, _engine = setup
    routine_id, _ = routine(client, project, enabled=True)

    async def verify():
        started = asyncio.Event()

        async def slow_notify():
            started.set()
            await asyncio.Event().wait()

        manager.notify = slow_notify
        manager.start()
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            manager.store.execute(
                "UPDATE routines SET next_due=? WHERE id=?", (time.time(), routine_id)
            )
            for _ in range(40):
                if manager.store.one(
                    "SELECT id FROM routine_runs WHERE routine_id=?", (routine_id,)
                ):
                    break
                await asyncio.sleep(0.1)
            assert manager.store.one(
                "SELECT task_id FROM routine_runs WHERE routine_id=?", (routine_id,)
            )["task_id"]
        finally:
            await manager.close()

    client.portal.call(verify)
