"""批164（审计 D5「配置面不一致」）：`DATA_DIR` 指到挂载点后，同一个逻辑文件变成两个物理文件。

第十四轮实测原文（`doc/审计报告/doubao-butler_第十四轮审计报告.md` §一）：

    DATA_DIR=/tmp/d5a
      Settings.data_dir   = /tmp/d5a      ← 跟随
      ALIAS_FILE          = /app/data/…   ← 不跟随
      ROUTES_FILE         = /app/data/…   ← 不跟随
      self_evolve.DB_PATH = /app/data/…   ← 不跟随
      _SESSIONS_FILE      = /app/data/…   ← 不跟随

最重的一枚是 `doubao_webhook.py:60`：它读 `/app/data/role_state.json`，而 `roles/state.py`
与 `memory/feeder.py` 写的是 `DATA_DIR` 那份 ⇒ 反向查角色恒为 None ⇒ 手机端消息被静默丢弃
（不崩溃，只是不见了）。

修法口径＝**惰性单点解析**（新增 `butler/core/data_paths.py`）：⛔ 模块级常量在 import 期定死，
也⛔ 在 import 期调 `get_settings()`（循环导入）。测试里换掉 settings 后必须立刻拿到新值。

本批⛔ 动的那一类：`config.py` 里 `"/app/data"` 是 `DATA_DIR` 的**默认值**（`_env("DATA_DIR", …)`），
以及各处 `def __init__(…, data_dir="/app/data")` 的构造参数默认——现读 `app.py:418/426/490`
三处调用方都传的是 `s.data_dir`，它们不是分叉腿。
"""
from __future__ import annotations

import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import butler.config as cfg


def _tmp(prefix: str) -> str:
    return tempfile.mkdtemp(prefix="butler_test_batch164_" + prefix + "_")


class _SettingsSwap(unittest.TestCase):
    """把 `butler.config._settings` 换成一份只带 data_dir 的假档，tearDown 原样还回去。"""

    def setUp(self):
        self._old = cfg._settings
        self.dir = _tmp(type(self).__name__ + "_" + self._testMethodName)

    def tearDown(self):
        cfg._settings = self._old

    def use(self, data_dir: str = "", env_data_dir: str | None = None):
        cfg._settings = types.SimpleNamespace(data_dir=data_dir)
        patcher = mock.patch.dict(os.environ, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        if env_data_dir is not None:
            os.environ["DATA_DIR"] = env_data_dir


class DataRootResolver(_SettingsSwap):
    """根目录解析：settings 优先，空则回 env，都没有才落 /app/data。"""

    def test_data_root_follows_settings_data_dir(self):
        from butler.core import data_paths
        self.use(data_dir=self.dir)
        self.assertEqual(str(data_paths.data_root()), self.dir)

    def test_blank_settings_fall_back_to_env_data_dir(self):
        from butler.core import data_paths
        self.use(data_dir="", env_data_dir="/mnt/nas/butler")
        self.assertEqual(data_paths.data_root(), Path("/mnt/nas/butler"))

    def test_container_default_stays_app_data(self):
        """⛔ 顺手改掉容器行为：什么都没给时仍是 /app/data（现网 compose 就是这么挂的）。

        比 Path ⛔ 比 str：这台机是 Windows，`str(Path("/app/data"))` 会渲染成 `\\app\\data`，
        那是我的尺子在改斜杠，不是生产的码在改根目录。
        """
        from butler.core import data_paths
        self.use(data_dir="")
        self.assertEqual(data_paths.data_root(), Path("/app/data"))

    def test_resolver_is_lazy_not_import_time(self):
        """换了 settings 再取必须变——模块级常量的死值就是本批要治的东西。"""
        from butler.core import data_paths
        self.use(data_dir=self.dir)
        first = data_paths.data_file("x.json")
        cfg._settings = types.SimpleNamespace(data_dir="/elsewhere")
        self.assertNotEqual(str(first), str(data_paths.data_file("x.json")))


class ForkedSitesFollowTheSameRoot(_SettingsSwap):
    """六处分叉腿必须跟着同一个根走（含最重的 webhook 那一条行为腿）。"""

    def _alias_file(self):
        from butler.core import aliases
        return aliases.alias_file()

    def _routes_file(self):
        from butler.core import fast_routes
        return fast_routes.routes_file()

    def _sessions_file(self):
        from butler.api import deps
        return deps.sessions_file()

    def _skill_dir(self):
        from butler.api import push_routes
        return push_routes.skill_dir()

    def _db_path(self):
        from butler.core import self_evolve
        return self_evolve.db_path()

    def test_alias_file_follows_data_dir(self):
        self.use(data_dir=self.dir)
        self.assertEqual(str(self._alias_file()), os.path.join(self.dir, "device_aliases.json"))

    def test_routes_file_follows_data_dir(self):
        self.use(data_dir=self.dir)
        self.assertEqual(str(self._routes_file()), os.path.join(self.dir, "fast_routes.json"))

    def test_sessions_file_follows_data_dir(self):
        self.use(data_dir=self.dir)
        self.assertEqual(str(self._sessions_file()), os.path.join(self.dir, "sessions.json"))

    def test_skill_dir_follows_data_dir(self):
        self.use(data_dir=self.dir)
        self.assertEqual(str(self._skill_dir()), os.path.join(self.dir, "skills", "user"))

    def test_self_evolve_db_path_follows_data_dir(self):
        self.use(data_dir=self.dir)
        self.assertEqual(str(self._db_path()), os.path.join(self.dir, "butler.db"))

    def test_webhook_reads_the_file_the_writers_write(self):
        """行为腿（第十四轮那句"不崩溃，消息只是不见了"）：写方落到 DATA_DIR，读方必须读得到。"""
        from butler.api import doubao_webhook
        self.use(data_dir=self.dir)
        state = Path(self.dir) / "role_state.json"
        state.write_text(json.dumps({"butler": {"conversation_id": "cid-1"}}), encoding="utf-8")
        self.assertEqual(doubao_webhook._conversation_to_role("cid-1"), "butler",
                         "反向查角色读的是另一份物理文件＝手机端消息被静默丢弃")

    def test_webhook_and_feeder_agree_on_one_path(self):
        """同一份 role_state.json 的第二个读手与写手⛔ 各算各的路径。"""
        from butler.api import doubao_webhook
        from butler.memory.feeder import MemoryFeeder
        self.use(data_dir=self.dir)
        feeder = MemoryFeeder(types.SimpleNamespace(settings=types.SimpleNamespace(data_dir=self.dir)))
        self.assertEqual(Path(doubao_webhook.role_state_file()), Path(feeder._role_state_path()))


class ShapeRulerOnModuleLevelConstants(unittest.TestCase):
    """形状尺：⛔ 再有模块级 `= Path("/app/data…")` 死值。先证它会咬。"""

    NEEDLE = '= Path("/app/data'
    FILES = ("butler/core/aliases.py", "butler/core/fast_routes.py", "butler/api/deps.py",
             "butler/api/push_routes.py", "butler/core/self_evolve.py",
             "butler/api/doubao_webhook.py")

    def _hits(self, source: str) -> list[int]:
        return [i + 1 for i, ln in enumerate(source.splitlines()) if self.NEEDLE in ln]

    def test_ruler_bites_synthetic_source(self):
        synthetic = 'ALIAS_FILE = Path("/app/data/x.json")\ny = 1\n'
        self.assertEqual(self._hits(synthetic), [1], "尺子先证明它会咬：合成源码那一行必须命中")

    def test_six_sites_have_no_module_level_literal(self):
        root = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        offenders = {}
        for rel in self.FILES:
            hits = self._hits((root / rel).read_text(encoding="utf-8"))
            if hits:
                offenders[rel] = hits
        self.assertEqual(offenders, {}, f"仍有模块级死路径：{offenders}")

    def test_only_two_places_carry_the_literal(self):
        """/app/data 字面量只许活在两处：`config.py` 的 env 默认值＋`data_paths.py` 的兜底默认。

        正对照：这两处本就该命中（尺子不是恒红）；范围口径＝⛔ 注释与文档字符串里的提及，
        只数 `DEFAULT_ROOT`／`_env("DATA_DIR", …)` 这两行赋值。
        """
        root = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        dp = (root / "butler" / "core" / "data_paths.py").read_text(encoding="utf-8")
        self.assertIn('DEFAULT_ROOT = "/app/data"', dp, "兜底默认必须就在解析点上")
        conf = (root / "butler" / "config.py").read_text(encoding="utf-8")
        self.assertIn('_env("DATA_DIR", "/app/data")', conf, "config 那份是 env 默认值，本批⛔ 动")


if __name__ == "__main__":
    unittest.main(verbosity=2)
