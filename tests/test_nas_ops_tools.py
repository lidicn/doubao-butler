"""new-api + Docker 运维工具测试。

批187 收编：本档此前是 pytest 形制（类名⛔ 继承 TestCase、裸 assert、吃 `tmp_path` fixture、
7 格挂 `@pytest.mark.asyncio`），在 unittest 回归尺里长期 `rc=5 ran=0`＝**一次都没跑过**。
现改为 `unittest.TestCase` 同步格 18 枚（Schema 4／NewApiClient 7／Dispatch 7），格数 1:1 保留，
`tmp_path` 换成 `tempfile.mkdtemp()` + `tearDown` 清理，async 腿一律 `asyncio.run(...)` 包。

⚠ 桩件形制必须跟真身一致：`butler/integrations/docker_tools.py` 的 `ps/restart/logs/compose_up`
四枚全是 `async def`，`butler/tools/desk_pilot.py` 的 `_dispatch_docker` 对它们逐个 `await`。
原先 `FakeDocker` 写成同步，三条 docker 腿报 `object dict can't be used in 'await' expression`
——红的是桩件⛔ 生产码。本节把四条腿改 `async def`，钉它的那两格在
`tests/test_audit_1011_batch187_pytest_shape.py::TestBatch187PytestShape`（test_06／test_07）。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from butler.core.tools import TOOL_SCHEMAS, dispatch_tool


# ── Schema 注册检查 ──

NEWAPI_TOOLS = {"newapi_list_channels", "newapi_list_models", "newapi_get_model_allocation", "newapi_set_model_allocation"}
DOCKER_TOOLS = {"docker_ps", "docker_restart", "docker_logs", "docker_compose_up"}


class TestSchemaRegistration(unittest.TestCase):
    def test_newapi_tools_registered(self):
        registered = {s["function"]["name"] for s in TOOL_SCHEMAS}
        self.assertTrue(NEWAPI_TOOLS.issubset(registered), f"缺失: {NEWAPI_TOOLS - registered}")

    def test_docker_tools_registered(self):
        registered = {s["function"]["name"] for s in TOOL_SCHEMAS}
        self.assertTrue(DOCKER_TOOLS.issubset(registered), f"缺失: {DOCKER_TOOLS - registered}")

    def test_set_model_allocation_has_allocations_array(self):
        for s in TOOL_SCHEMAS:
            if s["function"]["name"] == "newapi_set_model_allocation":
                props = s["function"]["parameters"]["properties"]
                self.assertEqual(props["allocations"]["type"], "array")
                self.assertIn("channel_id", props["allocations"]["items"]["properties"])

    def test_total_tool_count(self):
        nap = sum(1 for s in TOOL_SCHEMAS if s["function"]["name"].startswith("newapi_"))
        dk = sum(1 for s in TOOL_SCHEMAS if s["function"]["name"].startswith("docker_"))
        self.assertEqual(nap, 4)
        self.assertEqual(dk, 4)


# ── new-api 客户端测试（临时 SQLite） ──

def _make_test_db(tmp_dir):
    """创建一个临时 new-api 测试数据库，含 channels/abilities 表和测试数据。"""
    db = os.path.join(tmp_dir, "test-new-api.db")
    conn = sqlite3.connect(db)
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
    return db


class TestNewApiClient(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="b187_newapi_")
        self.db = _make_test_db(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_list_channels(self):
        from butler.integrations.newapi import NewApiClient
        r = NewApiClient(self.db).list_channels()
        self.assertTrue(r["ok"])
        self.assertEqual(r["result"]["count"], 3)
        self.assertEqual(r["result"]["channels"][0]["name"], "渠道A")

    def test_list_virtual_models(self):
        from butler.integrations.newapi import NewApiClient
        r = NewApiClient(self.db).list_virtual_models()
        self.assertTrue(r["ok"])
        self.assertEqual(r["result"]["count"], 1)
        self.assertEqual(r["result"]["models"][0]["model"], "model-x")

    def test_get_model_allocation(self):
        from butler.integrations.newapi import NewApiClient
        r = NewApiClient(self.db).get_model_allocation("model-x")
        self.assertTrue(r["ok"])
        self.assertEqual(r["result"]["count"], 1)
        self.assertEqual(r["result"]["allocations"][0]["channel_name"], "渠道A")

    def test_set_model_allocation_three_table_sync(self):
        """核心测试：set_model_allocation 必须同时更新 channels.models + model_mapping + abilities。"""
        from butler.integrations.newapi import NewApiClient
        c = NewApiClient(self.db, backup_dir=self.tmp)
        r = c.set_model_allocation("model-y", [
            {"channel_id": 2, "weight": 50, "priority": 1},
            {"channel_id": 3, "weight": 100, "priority": 0},
        ])
        self.assertTrue(r["ok"])
        self.assertEqual(r["result"]["channel_count"], 2)

        conn = sqlite3.connect(self.db)
        try:
            ch2 = conn.execute('SELECT models FROM channels WHERE id=2').fetchone()
            self.assertIn("model-y", json.loads(ch2[0]))
            ch2m = conn.execute('SELECT model_mapping FROM channels WHERE id=2').fetchone()
            self.assertIn("model-y", json.loads(ch2m[0]))
            ab = conn.execute('SELECT count(*) FROM abilities WHERE model="model-y"').fetchone()
            self.assertEqual(ab[0], 2)
            abx = conn.execute('SELECT count(*) FROM abilities WHERE model="model-x"').fetchone()
            self.assertEqual(abx[0], 1)
        finally:
            conn.close()

    def test_set_model_allocation_removes_from_unassigned_channels(self):
        """全量替换时，不再分配的渠道应从 channels.models 移除 model。"""
        from butler.integrations.newapi import NewApiClient
        c = NewApiClient(self.db, backup_dir=self.tmp)
        r = c.set_model_allocation("model-x", [{"channel_id": 2, "weight": 100}])
        self.assertTrue(r["ok"])
        conn = sqlite3.connect(self.db)
        try:
            ch1 = conn.execute('SELECT models FROM channels WHERE id=1').fetchone()
            self.assertNotIn("model-x", json.loads(ch1[0]))
            ch2 = conn.execute('SELECT models FROM channels WHERE id=2').fetchone()
            self.assertIn("model-x", json.loads(ch2[0]))
        finally:
            conn.close()

    def test_set_model_allocation_invalid_params(self):
        from butler.integrations.newapi import NewApiClient
        r = NewApiClient(self.db).set_model_allocation("", [{"channel_id": 1}])
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "invalid_parameter")

    def test_backup_created(self):
        from butler.integrations.newapi import NewApiClient
        NewApiClient(self.db, backup_dir=self.tmp).set_model_allocation("model-z", [{"channel_id": 1}])
        backups = [f for f in os.listdir(self.tmp) if f.startswith("new-api.db.bak.")]
        self.assertGreaterEqual(len(backups), 1)


# ── 工具分发测试（桩件） ──

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
    """四条腿必须与 `DockerClient` 同为协程——同步桩件会让 `await` 抛错并伪装成产品缺陷。"""

    async def ps(self, all_containers=False):
        return {"ok": True, "tool": "docker_ps", "result": {"count": 1, "containers": [{"name": "new-api"}]}}

    async def restart(self, container):
        return {"ok": True, "tool": "docker_restart", "result": {"container": container, "status": "running"}}

    async def logs(self, container, tail=50):
        return {"ok": True, "tool": "docker_logs", "result": {"container": container, "line_count": 2, "logs": "line1\nline2"}}

    async def compose_up(self, project_dir, services=None, build=True, timeout=300):
        return {"ok": True, "tool": "docker_compose_up", "result": {"project_dir": project_dir}}


def _dispatch(tool, args, agent):
    return json.loads(asyncio.run(dispatch_tool(tool, args, agent)))


class TestDispatch(unittest.TestCase):
    def test_newapi_no_client(self):
        self.assertEqual(_dispatch("newapi_list_channels", {}, FakeAgent())["error"], "not_implemented")

    def test_newapi_list_channels(self):
        self.assertTrue(_dispatch("newapi_list_channels", {}, FakeAgent(newapi=FakeNewApi()))["ok"])

    def test_newapi_set_model_allocation(self):
        obj = _dispatch("newapi_set_model_allocation",
                        {"model": "x", "allocations": [{"channel_id": 1, "weight": 100}]},
                        FakeAgent(newapi=FakeNewApi()))
        self.assertTrue(obj["ok"])
        self.assertEqual(obj["result"]["channel_count"], 1)

    def test_docker_ps(self):
        self.assertTrue(_dispatch("docker_ps", {}, FakeAgent(docker=FakeDocker()))["ok"])

    def test_docker_restart(self):
        obj = _dispatch("docker_restart", {"container": "new-api"}, FakeAgent(docker=FakeDocker()))
        self.assertEqual(obj["result"]["container"], "new-api")

    def test_docker_compose_up(self):
        obj = _dispatch("docker_compose_up", {"project_dir": "/vol1/1000/docker/x"},
                        FakeAgent(docker=FakeDocker()))
        self.assertTrue(obj["ok"])

    def test_unknown_newapi_tool(self):
        obj = _dispatch("newapi_nonexistent", {}, FakeAgent(newapi=FakeNewApi()))
        self.assertEqual(obj["error"], "invalid_parameter")


if __name__ == "__main__":
    unittest.main()
