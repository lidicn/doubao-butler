"""new-api + Docker 运维工具测试。"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from butler.core.tools import TOOL_SCHEMAS, dispatch_tool


# ── Schema 注册检查 ──

NEWAPI_TOOLS = {"newapi_list_channels", "newapi_list_models", "newapi_get_model_allocation", "newapi_set_model_allocation"}
DOCKER_TOOLS = {"docker_ps", "docker_restart", "docker_logs", "docker_compose_up"}


class TestSchemaRegistration:
    def test_newapi_tools_registered(self):
        registered = {s["function"]["name"] for s in TOOL_SCHEMAS}
        assert NEWAPI_TOOLS.issubset(registered), f"缺失: {NEWAPI_TOOLS - registered}"

    def test_docker_tools_registered(self):
        registered = {s["function"]["name"] for s in TOOL_SCHEMAS}
        assert DOCKER_TOOLS.issubset(registered), f"缺失: {DOCKER_TOOLS - registered}"

    def test_set_model_allocation_has_allocations_array(self):
        for s in TOOL_SCHEMAS:
            if s["function"]["name"] == "newapi_set_model_allocation":
                props = s["function"]["parameters"]["properties"]
                assert props["allocations"]["type"] == "array"
                assert "channel_id" in props["allocations"]["items"]["properties"]

    def test_total_tool_count(self):
        # 原 35 + 4 newapi + 4 docker = 43
        nap = sum(1 for s in TOOL_SCHEMAS if s["function"]["name"].startswith("newapi_"))
        dk = sum(1 for s in TOOL_SCHEMAS if s["function"]["name"].startswith("docker_"))
        assert nap == 4
        assert dk == 4


# ── new-api 客户端测试（临时 SQLite） ──

def _make_test_db(tmp_path):
    """创建一个临时 new-api 测试数据库，含 channels/abilities 表和测试数据。"""
    import sqlite3
    db = tmp_path / "test-new-api.db"
    conn = sqlite3.connect(str(db))
    conn.execute('''CREATE TABLE channels (
        id integer PRIMARY KEY, name text, type integer DEFAULT 0, status integer DEFAULT 1,
        "group" varchar(64) DEFAULT "default", weight integer DEFAULT 0,
        priority integer DEFAULT 0, models text, model_mapping text, base_url text
    )''')
    conn.execute('''CREATE TABLE abilities (
        "group" varchar(64), model varchar(255), channel_id integer,
        enabled numeric, priority integer DEFAULT 0, weight integer DEFAULT 0, tag text,
        PRIMARY KEY ("group", model, channel_id)
    )''')
    conn.execute('INSERT INTO channels (id,name,status,models,model_mapping) VALUES (1,"渠道A",1,\'["model-x"]\',\'{"model-x":"model-x"}\')')
    conn.execute('INSERT INTO channels (id,name,status,models,model_mapping) VALUES (2,"渠道B",1,\'[]\',\'{}\')')
    conn.execute('INSERT INTO channels (id,name,status,models,model_mapping) VALUES (3,"渠道C",0,\'[]\',\'{}\')')
    conn.execute('INSERT INTO abilities VALUES ("default","model-x",1,1,0,100,NULL)')
    conn.commit()
    conn.close()
    return str(db)


class TestNewApiClient:
    def test_list_channels(self, tmp_path):
        from butler.integrations.newapi import NewApiClient
        db = _make_test_db(tmp_path)
        c = NewApiClient(db)
        r = c.list_channels()
        assert r["ok"] is True
        assert r["result"]["count"] == 3
        assert r["result"]["channels"][0]["name"] == "渠道A"

    def test_list_virtual_models(self, tmp_path):
        from butler.integrations.newapi import NewApiClient
        db = _make_test_db(tmp_path)
        c = NewApiClient(db)
        r = c.list_virtual_models()
        assert r["ok"] is True
        assert r["result"]["count"] == 1
        assert r["result"]["models"][0]["model"] == "model-x"

    def test_get_model_allocation(self, tmp_path):
        from butler.integrations.newapi import NewApiClient
        db = _make_test_db(tmp_path)
        c = NewApiClient(db)
        r = c.get_model_allocation("model-x")
        assert r["ok"] is True
        assert r["result"]["count"] == 1
        assert r["result"]["allocations"][0]["channel_name"] == "渠道A"

    def test_set_model_allocation_three_table_sync(self, tmp_path):
        """核心测试：set_model_allocation 必须同时更新 channels.models + model_mapping + abilities。"""
        import sqlite3
        from butler.integrations.newapi import NewApiClient
        db = _make_test_db(tmp_path)
        c = NewApiClient(db, backup_dir=str(tmp_path))
        r = c.set_model_allocation("model-y", [
            {"channel_id": 2, "weight": 50, "priority": 1},
            {"channel_id": 3, "weight": 100, "priority": 0},
        ])
        assert r["ok"] is True
        assert r["result"]["channel_count"] == 2

        # 验证三表同步
        conn = sqlite3.connect(db)
        # channels.models 包含 model-y
        ch2 = conn.execute('SELECT models FROM channels WHERE id=2').fetchone()
        assert "model-y" in json.loads(ch2[0])
        # channels.model_mapping 包含 model-y
        ch2m = conn.execute('SELECT model_mapping FROM channels WHERE id=2').fetchone()
        assert "model-y" in json.loads(ch2m[0])
        # abilities 有 2 条记录
        ab = conn.execute('SELECT count(*) FROM abilities WHERE model="model-y"').fetchone()
        assert ab[0] == 2
        # 旧 model-x 不受影响
        abx = conn.execute('SELECT count(*) FROM abilities WHERE model="model-x"').fetchone()
        assert abx[0] == 1
        conn.close()

    def test_set_model_allocation_removes_from_unassigned_channels(self, tmp_path):
        """全量替换时，不再分配的渠道应从 channels.models 移除 model。"""
        import sqlite3
        from butler.integrations.newapi import NewApiClient
        db = _make_test_db(tmp_path)
        c = NewApiClient(db, backup_dir=str(tmp_path))
        # model-x 原来在渠道1，现在只分配给渠道2
        r = c.set_model_allocation("model-x", [{"channel_id": 2, "weight": 100}])
        assert r["ok"] is True
        conn = sqlite3.connect(db)
        ch1 = conn.execute('SELECT models FROM channels WHERE id=1').fetchone()
        assert "model-x" not in json.loads(ch1[0])
        ch2 = conn.execute('SELECT models FROM channels WHERE id=2').fetchone()
        assert "model-x" in json.loads(ch2[0])
        conn.close()

    def test_set_model_allocation_invalid_params(self, tmp_path):
        from butler.integrations.newapi import NewApiClient
        db = _make_test_db(tmp_path)
        c = NewApiClient(db)
        r = c.set_model_allocation("", [{"channel_id": 1}])
        assert r["ok"] is False
        assert r["error"] == "invalid_parameter"

    def test_backup_created(self, tmp_path):
        from butler.integrations.newapi import NewApiClient
        db = _make_test_db(tmp_path)
        c = NewApiClient(db, backup_dir=str(tmp_path))
        c.set_model_allocation("model-z", [{"channel_id": 1}])
        backups = [f for f in os.listdir(str(tmp_path)) if f.startswith("new-api.db.bak.")]
        assert len(backups) >= 1


# ── 工具分发测试（mock） ──

class FakeAgent:
    def __init__(self, newapi=None, docker=None):
        self.newapi = newapi
        self.docker = docker


class FakeNewApi:
    def list_channels(self):
        return {"ok": True, "tool": "newapi_list_channels", "result": {"count": 2, "channels": [{"id": 1, "name": "A"}]}}
    def list_virtual_models(self):
        return {"ok": True, "tool": "newapi_list_models", "result": {"count": 1, "models": [{"model": "x"}]}}
    def get_model_allocation(self, model):
        return {"ok": True, "tool": "newapi_get_model_allocation", "result": {"model": model, "count": 1}}
    def set_model_allocation(self, model, allocations):
        return {"ok": True, "tool": "newapi_set_model_allocation", "result": {"model": model, "channel_count": len(allocations)}}


class FakeDocker:
    def ps(self, all_containers=False):
        return {"ok": True, "tool": "docker_ps", "result": {"count": 1, "containers": [{"name": "new-api"}]}}
    def restart(self, container):
        return {"ok": True, "tool": "docker_restart", "result": {"container": container, "status": "running"}}
    def logs(self, container, tail=50):
        return {"ok": True, "tool": "docker_logs", "result": {"container": container, "line_count": 2, "logs": "line1\nline2"}}
    def compose_up(self, project_dir, services=None, build=True, timeout=300):
        return {"ok": True, "tool": "docker_compose_up", "result": {"project_dir": project_dir}}


class TestDispatch:
    @pytest.mark.asyncio
    async def test_newapi_no_client(self):
        agent = FakeAgent()
        r = await dispatch_tool("newapi_list_channels", {}, agent)
        assert json.loads(r)["error"] == "not_implemented"

    @pytest.mark.asyncio
    async def test_newapi_list_channels(self):
        agent = FakeAgent(newapi=FakeNewApi())
        r = await dispatch_tool("newapi_list_channels", {}, agent)
        assert json.loads(r)["ok"] is True

    @pytest.mark.asyncio
    async def test_newapi_set_model_allocation(self):
        agent = FakeAgent(newapi=FakeNewApi())
        r = await dispatch_tool("newapi_set_model_allocation",
                                {"model": "x", "allocations": [{"channel_id": 1, "weight": 100}]}, agent)
        obj = json.loads(r)
        assert obj["ok"] is True
        assert obj["result"]["channel_count"] == 1

    @pytest.mark.asyncio
    async def test_docker_ps(self):
        agent = FakeAgent(docker=FakeDocker())
        r = await dispatch_tool("docker_ps", {}, agent)
        assert json.loads(r)["ok"] is True

    @pytest.mark.asyncio
    async def test_docker_restart(self):
        agent = FakeAgent(docker=FakeDocker())
        r = await dispatch_tool("docker_restart", {"container": "new-api"}, agent)
        assert json.loads(r)["result"]["container"] == "new-api"

    @pytest.mark.asyncio
    async def test_docker_compose_up(self):
        agent = FakeAgent(docker=FakeDocker())
        r = await dispatch_tool("docker_compose_up", {"project_dir": "/vol1/1000/docker/x"}, agent)
        assert json.loads(r)["ok"] is True

    @pytest.mark.asyncio
    async def test_unknown_newapi_tool(self):
        agent = FakeAgent(newapi=FakeNewApi())
        r = await dispatch_tool("newapi_nonexistent", {}, agent)
        assert json.loads(r)["error"] == "invalid_parameter"
