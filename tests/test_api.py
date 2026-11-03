"""HTTP acceptance checks with a deterministic native-engine boundary fixture.

The fixture validates platform behavior only; it is never available in production.
"""
import asyncio
import io
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from workbench.api import create_app
from workbench.config import Settings
from workbench.runtime import RuntimeEvent


class EngineFixture:
    def __init__(self):
        self.requests = []
        self.hold = False
        self.stopped = set()

    async def readiness(self):
        return [{'engine': name, 'available': True, 'reason': 'test boundary'} for name in ('codex', 'claude')]

    async def execute(self, request):
        self.requests.append(request)
        yield RuntimeEvent('session', {'session_id': 'native-' + request.run_id})
        while self.hold and request.run_id not in self.stopped:
            await asyncio.sleep(0.01)
        if request.run_id in self.stopped:
            yield RuntimeEvent('completed', {'status': 'interrupted'})
            return
        for name in ('requirements.md', 'design.md', 'tasks.md', 'checks.md', 'acceptance.md', 'delivery.md'):
            (Path(request.workspace) / name).write_text('fixture evidence', encoding='utf-8')
        yield RuntimeEvent('delta', {'text': 'actual boundary fixture response'})
        yield RuntimeEvent('completed', {'status': 'completed'})

    async def stop(self, run_id):
        self.stopped.add(run_id)

    async def approve(self, *args):
        return None


@pytest.fixture
def setup(tmp_path):
    engine = EngineFixture()
    app = create_app(Settings(data_dir=tmp_path), runtime=engine)
    with TestClient(app) as client:
        assert client.post('/api/auth/register', json={'email': 'owner@example.com', 'password': 'safe-password-123'}).status_code == 201
        project = client.post('/api/projects', json={'name': 'Research'}).json()
        yield client, project, engine


def task(client, project, mode='free'):
    result = client.post(f'/api/projects/{project["id"]}/tasks', json={'title': 'Build report', 'mode': mode, 'engine': 'codex'})
    assert result.status_code == 201
    return result.json()


def wait_task(client, task_id, status):
    for _ in range(300):
        result = client.get(f'/api/tasks/{task_id}').json()
        if result['status'] in status:
            return result
        time.sleep(0.01)
    raise AssertionError(result)


def test_owner_isolation_and_logout(setup):
    client, project, _ = setup
    client.post('/api/auth/logout')
    assert client.get('/api/projects').status_code == 401
    client.post('/api/auth/register', json={'email': 'other@example.com', 'password': 'safe-password-456'})
    assert client.get('/api/projects').json() == []
    for suffix in ('', '/files', '/memories', '/members', '/extensions', '/tasks'):
        assert client.get(f'/api/projects/{project["id"]}{suffix}').status_code == 404


def test_native_success_requires_acceptance_and_keeps_artifact(setup):
    client, project, engine = setup
    item = task(client, project)
    assert client.post(f'/api/tasks/{item["id"]}/messages', json={'content': 'Create report'}).status_code == 201
    result = wait_task(client, item['id'], {'waiting_confirmation'})
    assert result['runs'][0]['status'] == 'completed'
    assert result['messages'][-1]['content'] == 'actual boundary fixture response'
    assert result['artifacts']
    gate = result['gates'][-1]
    assert client.post(f'/api/tasks/{item["id"]}/gates/{gate["id"]}', json={'decision': 'approve'}).status_code == 200
    assert client.get(f'/api/tasks/{item["id"]}').json()['status'] == 'delivered'
    artifact = result['artifacts'][0]
    assert client.get(f'/api/artifacts/{artifact["id"]}').content == b'fixture evidence'
    client.put(f'/api/projects/{project["id"]}/file', params={'path': artifact['path']}, json={'content': 'changed'})
    assert client.get(f'/api/artifacts/{artifact["id"]}').content == b'fixture evidence'
    assert engine.requests[0].session_id is None


def test_new_requirement_invalidates_old_confirmation(setup):
    client, project, _ = setup
    item = task(client, project)
    client.post(f'/api/tasks/{item["id"]}/messages', json={'content': 'First'})
    first = wait_task(client, item['id'], {'waiting_confirmation'})
    client.post(f'/api/tasks/{item["id"]}/messages', json={'content': 'Changed requirement'})
    second = wait_task(client, item['id'], {'waiting_confirmation'})
    assert second['version'] == 2
    assert client.post(f'/api/tasks/{item["id"]}/gates/{first["gates"][0]["id"]}', json={'decision': 'approve'}).status_code == 409


def test_development_stages_have_real_human_gates(setup):
    client, project, engine = setup
    item = task(client, project, 'development')
    client.post(f'/api/tasks/{item["id"]}/messages', json={'content': 'Implement'})
    for expected_stage in (0, 1, 5):
        result = wait_task(client, item['id'], {'waiting_confirmation'})
        assert result['stage'] == expected_stage
        pending = next(g for g in result['gates'] if g['status'] == 'pending')
        assert client.post(f'/api/tasks/{item["id"]}/gates/{pending["id"]}', json={'decision': 'approve'}).status_code == 200
    final = wait_task(client, item['id'], {'delivered'})
    assert final['stage'] == 6
    assert len(engine.requests) == 7


@pytest.mark.parametrize('path', ['../secret.txt', '/etc/passwd', '.native/auth.json', 'foo/../../x', 'C:/secret', 'foo\\bar'])
def test_file_traversal_rejected(setup, path):
    client, project, _ = setup
    assert client.put(f'/api/projects/{project["id"]}/file', params={'path': path}, json={'content': 'x'}).status_code == 400


def test_zip_import_rejects_traversal_before_writes(setup):
    client, project, _ = setup
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('good.txt', 'valid')
        archive.writestr('../escape.txt', 'invalid')
    response = client.post(f'/api/projects/{project["id"]}/upload', files={'file': ('source.zip', buffer.getvalue(), 'application/zip')})
    assert response.status_code == 400
    assert client.get(f'/api/projects/{project["id"]}/files').json() == []


def test_stop_cancels_native_run_and_does_not_deliver(setup):
    client, project, engine = setup
    engine.hold = True
    item = task(client, project)
    client.post(f'/api/tasks/{item["id"]}/messages', json={'content': 'Work'})
    wait_task(client, item['id'], {'running'})
    assert client.post(f'/api/tasks/{item["id"]}/messages', json={'content': 'race'}).status_code == 201
    client.post(f'/api/tasks/{item["id"]}/stop')
    result = wait_task(client, item['id'], {'interrupted'})
    assert result['gates'] == []
    assert result['pending_inputs'][0]['status'] == 'paused'


def test_memory_opt_in_and_skill_snapshot(setup):
    client, project, engine = setup
    client.post(f'/api/projects/{project["id"]}/memories', json={'category': 'project', 'content': 'Use UTC timestamps'})
    skill = client.post(f'/api/projects/{project["id"]}/extensions', json={'name': 'report', 'kind': 'skill', 'content': 'Cite sources'}).json()
    item = task(client, project)
    client.post(f'/api/tasks/{item["id"]}/messages', json={'content': 'first'})
    wait_task(client, item['id'], {'waiting_confirmation'})
    assert 'Use UTC' not in engine.requests[-1].instructions
    assert 'Cite sources' in engine.requests[-1].instructions
    client.patch(f'/api/projects/{project["id"]}', json={'memory_enabled': True})
    client.patch(f'/api/extensions/{skill["id"]}', json={'enabled': False})
    client.post(f'/api/tasks/{item["id"]}/messages', json={'content': 'second'})
    wait_task(client, item['id'], {'waiting_confirmation'})
    assert 'Use UTC timestamps' in engine.requests[-1].instructions
    assert 'Cite sources' not in engine.requests[-1].instructions


def test_collaborator_merge_rejects_changed_baseline(setup):
    client, project, _ = setup
    base = f'/api/projects/{project["id"]}'
    client.put(base + '/file', params={'path': 'requirements.md'}, json={'content': 'baseline'})
    member = client.post(base + '/members', json={'name': 'Reviewer', 'role': 'Review code', 'engine': 'codex'}).json()
    item = client.post(f'/api/members/{member["id"]}/assign', json={'content': 'Review'}).json()
    wait_task(client, item['id'], {'waiting_confirmation'})
    client.put(base + '/file', params={'path': 'requirements.md'}, json={'content': 'main changed'})
    assert client.post(base + f'/integrate/{item["id"]}', json={'path': 'requirements.md'}).status_code == 409


def test_cross_origin_write_rejected(setup):
    client, _, _ = setup
    assert client.post('/api/projects', json={'name': 'bad'}, headers={'Origin': 'https://evil.example'}).status_code == 403


def test_unavailable_engine_does_not_create_fake_response(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        client.post('/api/auth/register', json={'email': 'a@example.com', 'password': 'strong-password'})
        project = client.post('/api/projects', json={'name': 'Empty'}).json()
        item = task(client, project)
        assert client.post(f'/api/tasks/{item["id"]}/messages', json={'content': 'run'}).status_code == 409
        result = client.get(f'/api/tasks/{item["id"]}').json()
        assert result['messages'] == []
        assert result['runs'] == []
