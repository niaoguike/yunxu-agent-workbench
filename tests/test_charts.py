import asyncio

import pytest
from fastapi.testclient import TestClient

from workbench.api import create_app
from workbench.charts import parse_csv
from workbench.config import Settings


@pytest.mark.parametrize("raw", [b"", b"x,y\na,NaN", b"x,y\na,Infinity", b"x,x\na,1", b"x,y\na,1,2", b"x,y\n", b"x,y\n" + b"a,1\n" * 1001, b"\xff"])
def test_bad_csv(raw):
    with pytest.raises(ValueError):
        parse_csv(raw)


def test_csv_quoted_and_bom():
    result = parse_csv('\ufeff月份,收入\r\n"一月,华东",-12.5\r\n二月,0\r\n'.encode())
    assert result["rows"] == [["一月,华东", "-12.5"], ["二月", "0"]]
    assert result["numeric"] == ["收入"]


def test_chart_versions_and_restart(tmp_path):
    settings = Settings(data_dir=tmp_path)
    credentials = {"email": "charts@example.com", "password": "safe-password-123"}
    with TestClient(create_app(settings)) as client:
        client.post("/api/auth/register", json=credentials)
        project = client.post("/api/projects", json={"name": "CSV"}).json()
        task = client.post(f"/api/projects/{project['id']}/tasks", json={"title": "Chart"}).json()
        url = f"/api/tasks/{task['id']}/charts"
        assert client.post(url, files={"file": ("bad.csv", b"x,y\na,NaN")}).status_code == 400
        result = client.post(url, files={"file": ("sales.csv", '月份,收入,季度\n一月,-10,Q1\n四月,20,Q2\n'.encode())})
        assert result.status_code == 201, result.text
        chart = client.get(url).json()[0]
        config = {**chart["versions"][0]["config"], "kind": "line", "filter_column": "季度", "filter_value": "Q2", "title": "第二季度"}
        revision_url = f"/api/charts/{chart['id']}/versions"
        assert client.post(revision_url, json={**config, "base_version": 1}).json() == {"version": 2}
        assert client.post(revision_url, json={**config, "base_version": 1}).status_code == 409
        assert client.post(revision_url, json={**config, "y": "季度", "base_version": 2}).status_code == 400
        assert client.post(revision_url, json={**config, "kind": "script", "base_version": 2}).status_code == 422
        assert client.put(f"/api/resume/{task['id']}").status_code == 200
        assert len(client.get(f"/api/tasks/{task['id']}").json()["messages"]) == 2
        source = tmp_path / "workspaces" / project["id"] / "main" / chart["source"]
        assert source.exists()
        source.write_text("changed externally", encoding="utf-8")
    with TestClient(create_app(settings)) as client:
        client.post("/api/auth/login", json=credentials)
        chart = client.get(url).json()[0]
        assert chart["versions"][1]["config"] == config
        assert chart["versions"][0]["config"]["kind"] == "bar"
        assert chart["data"]["rows"][0][1] == "-10"
        assert client.get("/api/resume").json()["task_id"] == task["id"]
        client.cookies.clear()
        client.post("/api/auth/register", json={**credentials, "email": "other@example.com"})
        assert client.get(url).status_code == 404
        assert client.post(revision_url, json={**config, "base_version": 2}).status_code == 404
        client.post("/api/auth/login", json=credentials)
        client.delete(f"/api/projects/{project['id']}")
        assert client.get(url).status_code == 404


def test_agent_chart_tools_are_task_scoped(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        client.post("/api/auth/register", json={"email": "tools@example.com", "password": "safe-password-123"})
        project = client.post("/api/projects", json={"name": "Chart tools"}).json()
        base = f"/api/projects/{project['id']}/tasks"
        task = client.post(base, json={"title": "Source"}).json()
        other = client.post(base, json={"title": "Other"}).json()
        url = f"/api/tasks/{task['id']}/charts"
        client.post(url, files={"file": ("sales.csv", b"month,sales\nJan,10\nFeb,20")})
        store = app.state.store
        store.execute("UPDATE tasks SET status='running'")
        tool = app.state.workflow.collaboration.tool
        charts = asyncio.run(tool(task["id"], "chart_list", {}))
        chart = charts[0]
        arguments = {"chart_id": chart["id"], "base_version": 1, **chart["config"], "kind": "line"}
        with pytest.raises(ValueError, match="不属于"):
            asyncio.run(tool(other["id"], "chart_revise", arguments))
        assert asyncio.run(tool(task["id"], "chart_revise", arguments)) == {"version": 2}
        assert client.get(url).json()[0]["versions"][-1]["config"]["kind"] == "line"
        assert client.get(f"/api/tasks/{task['id']}").json()["messages"][-1]["role"] == "assistant"
        with pytest.raises(ValueError, match="已更新"):
            asyncio.run(tool(task["id"], "chart_revise", arguments))
        store.execute("UPDATE tasks SET status='interrupted'")
        with pytest.raises(ValueError, match="停止|执行中"):
            asyncio.run(tool(task["id"], "chart_list", {}))
