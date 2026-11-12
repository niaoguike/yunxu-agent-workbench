import json
import time
import pytest
from fastapi.testclient import TestClient
from workbench.api import create_app
from workbench.config import Settings
from workbench.runtime import RuntimeEvent
from workbench.orchestrator import Orchestrator
from test_api import EngineFixture


class Planner(EngineFixture):
    fail_first = False
    failed_once = False
    async def execute(self, request):
        if request.run_id.startswith('plan-'):
            assert request.review_schema['properties']['tasks']['items']['properties']['engine']['enum'] == ['claude','codex']
            yield RuntimeEvent('delta', {'text': json.dumps({'tasks':[{'id':'one','goal':'produce delivery','engine':'claude','reason':'implementation','depends_on':[]}]})})
            yield RuntimeEvent('completed', {'status':'completed'})
        else:
            if request.bridge_role == 'worker' and self.fail_first and not self.failed_once:
                self.failed_once = True
                yield RuntimeEvent('completed', {'status':'failed','error':'compiler fixture error'})
                return
            async for event in super().execute(request):
                yield event


def test_plan_validation():
    with pytest.raises(ValueError):
        Orchestrator.validate([{'id':'x','goal':'test','engine':'claude','depends_on':['x']}])


@pytest.mark.parametrize("retry", [False, True])
def test_platform_delivery_and_memory(tmp_path, retry):
    engine = Planner()
    engine.fail_first = retry
    app = create_app(Settings(data_dir=tmp_path), engine)
    with TestClient(app) as client:
        assert client.post('/api/auth/register', json={'email':'platform@example.com','password':'longpassword123'}).status_code == 201
        project = client.post('/api/projects', json={'name':'platform'}).json()
        pid = project['id']
        client.patch('/api/projects/'+pid,json={'memory_enabled':True})
        result = client.post(f'/api/projects/{pid}/memory-import/claude',files={'file':('MEMORY.md',b'cross harness fact','text/plain')})
        assert result.status_code == 200, result.text
        result2 = client.post(f'/api/projects/{pid}/memory-import/codex',files={'file':('MEMORY.md',b'cross harness fact','text/plain')})
        assert result.json()['memory_ids'] == result2.json()['memory_ids']
        response = client.post(f'/api/projects/{pid}/orchestrations',json={'content':'produce delivery'})
        assert response.status_code == 201, response.text
        for _ in range(100):
            rows = client.get(f'/api/projects/{pid}/orchestrations').json()
            if rows[0]['state'] in {'failed','delivered','conflict'}:
                break
            time.sleep(.1)
        assert rows[0]['state'] == 'delivered', rows
        sessions = client.get(f'/api/projects/{pid}/native-sessions').json()
        assert any(s['session_id'] for s in sessions)
        assert rows[0]['data']['nodes'][0]['attempt'] == (2 if retry else 1)
        if retry:
            assert rows[0]['data']['nodes'][0]['engine'] == 'codex'
        client.post('/api/auth/logout')
        assert client.get(f'/api/projects/{pid}/orchestrations').status_code == 401
