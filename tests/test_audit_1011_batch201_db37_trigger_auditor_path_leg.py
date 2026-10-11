"""批201 DB37 门：`TriggerAuditor` 默认库硬编码 `/app/data/push_guard.db`（第三期审计报告 第十一轮 · low）。

现读 `butler/triggers/audit.py:59` ＝ `def __init__(self, db_path: str = "/app/data/push_guard.db")`，
而唯一的生产构造点 `butler/triggers/registry.py:92` 是**无参** `TriggerAuditor()`
⇒ 用户按文档把 `DATA_DIR` 指到 NAS 挂载点后，别的审计写挂载点、这一张 `trigger_audit` 表还在容器内置路径，
正是批164 那条「同一个逻辑文件变成两个物理文件」的第 7 枚——批164 当时把「构造参数默认值」这一形制
显式排除在外（其门 :19-20 写着「本批⛔ 动」），因为那 13 处 `data_dir` 生产全都传了实参（已复核）。
**`TriggerAuditor` 是这一形制里唯一真被无参构造的一枚**⇒它才是活的病灶。

修法＝挂批164 那把惰性尺子（`core/data_paths.data_file`）：口径 `settings.data_dir` → `DATA_DIR` → `/app/data`，
最后一档就是今天的容器现值⇒compose 没设 `DATA_DIR` 时行为逐字节相同（test_02 钉这条）。

⚠ 本门⛔ 在 Windows 上构造「默认路径」的实例：`__init__` 会 `mkdir(parents=True)`，
拿默认值构造会在我这台机上开 `C:\\app\\data`。⇒ 判解析一律走纯函数 `_default_db_path()`，
构造只测显式传入的 tmp 目录。
"""
import os
import pathlib
import tempfile
import types
import unittest
from unittest import mock

import butler.config as cfg
from butler.triggers import audit

ROOT = pathlib.Path(__file__).resolve().parents[1]
AUDIT_PY = ROOT / "butler" / "triggers" / "audit.py"
REGISTRY_PY = ROOT / "butler" / "triggers" / "registry.py"

HARDCODED_NEEDLE = 'db_path: str = "/app/data'


def _tmp() -> str:
    return tempfile.mkdtemp(prefix="butler_test_batch201_db37_")


class _RootSwap(unittest.TestCase):
    """把 `butler.config._settings` 换成一份只带 data_dir 的假档，tearDown 原样还回去。"""

    def setUp(self):
        self._old = cfg._settings
        self.dir = _tmp()

    def tearDown(self):
        cfg._settings = self._old

    def use(self, data_dir: str = "", env_data_dir: str | None = None):
        cfg._settings = types.SimpleNamespace(data_dir=data_dir)
        patcher = mock.patch.dict(os.environ, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        if env_data_dir is not None:
            os.environ["DATA_DIR"] = env_data_dir


class TestBatch201Db37TriggerAuditorPath(_RootSwap):

    def test_01_default_db_follows_settings_data_dir(self):
        self.assertTrue(hasattr(audit, "_default_db_path"),
                        "默认库路径仍烤死在函数签名里⇒没有可判的解析点")
        self.use(data_dir=self.dir)
        self.assertEqual(pathlib.Path(audit._default_db_path()),
                         pathlib.Path(self.dir) / "push_guard.db",
                         "DATA_DIR 指到别处后 trigger_audit 表还在容器内置路径＝两个物理文件")

    def test_02_container_default_unchanged(self):
        """⛔ 顺手改掉现网行为：什么都没给时仍然是 /app/data/push_guard.db。

        比 Path ⛔ 比 str：这台机是 Windows，`str(Path(...))` 会渲染成反斜杠，
        那是我的尺子在改斜杠，不是生产的码在改根目录。
        """
        self.assertTrue(hasattr(audit, "_default_db_path"), "缺解析点")
        self.use(data_dir="")
        self.assertEqual(pathlib.Path(audit._default_db_path()),
                         pathlib.Path("/app/data/push_guard.db"))

    def test_03_env_data_dir_used_when_settings_blank(self):
        self.assertTrue(hasattr(audit, "_default_db_path"), "缺解析点")
        self.use(data_dir="", env_data_dir=self.dir)
        self.assertEqual(pathlib.Path(audit._default_db_path()),
                         pathlib.Path(self.dir) / "push_guard.db")

    def test_04_explicit_db_path_still_wins(self):
        """构造点必须还能被显式指定（批188 那张门就是靠这个把库放进 tmp）。"""
        target = pathlib.Path(self.dir) / "explicit.db"
        a = audit.TriggerAuditor(str(target))
        self.assertEqual(str(a.db_path), str(target))
        self.assertTrue(target.exists(), "显式路径的库没建出来")

    def test_05_resolution_is_lazy_not_import_time(self):
        """换了 settings 再取必须变——模块级死值就是本格的病灶形制。"""
        self.assertTrue(hasattr(audit, "_default_db_path"), "缺解析点")
        self.use(data_dir="")
        first = pathlib.Path(audit._default_db_path())
        cfg._settings = types.SimpleNamespace(data_dir=self.dir)
        second = pathlib.Path(audit._default_db_path())
        self.assertNotEqual(first, second, "解析发生在 import 期＝又是一枚模块级死值")

    # ── 形状尺：⛔ 再把根目录烤进签名 ────────────────────
    def test_06_signature_carries_no_hardcoded_root(self):
        src = AUDIT_PY.read_text(encoding="utf-8")
        self.assertNotIn(HARDCODED_NEEDLE, src,
                         "TriggerAuditor 的默认库路径又回到字面量")

    def test_07_shape_ruler_bites_synthetic(self):
        synth = 'def __init__(self, db_path: str = "/app/data/push_guard.db"):\n    pass\n'
        self.assertIn(HARDCODED_NEEDLE, synth, "尺子咬不住字面量默认值⇒test_06 是摆设")
        clean = 'def __init__(self, db_path: str | None = None):\n    pass\n'
        self.assertNotIn(HARDCODED_NEEDLE, clean)

    def test_08_production_call_site_is_still_no_arg(self):
        """registry 那枚无参构造是这条修复的承重点：它若改成传实参，本格应当亮起来让人重判。"""
        src = REGISTRY_PY.read_text(encoding="utf-8")
        self.assertIn("TriggerAuditor()", src,
                      "生产构造点形态变了⇒重新核对本格的「默认值是否活着」")


if __name__ == "__main__":
    unittest.main()
