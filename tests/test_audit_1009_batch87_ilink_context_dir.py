"""批87 验收：iLink 会话档目录必须跟随 data_dir 配置（报告《第三轮·全面功能与稳定性》B-19）。

在册基底（现读 `butler/integrations/ilink/bridge.py:42`）：

    self._context_dir = Path("/app/data/ilink/contexts")

报告 B-19 的修法一栏就一句「用 settings.data_dir」，本仓同族已按这个形状收口过三处：
`store/write_failures.py:53`（注释里就写着 ⛔ 硬编码 /app/data）、`devices.py:86`、
`api/doubao_webhook.py:77`（D18 那句「原先烤死容器内置的绝对路径」）。

危害两格，本件各钉一枚：
① 用户按文档把 `DATA_DIR` 指到别处 ⇒ 对话历史仍写容器内置路径 ⇒ 账实分叉（备份／迁移都找不到档）；
② `/app` 不可写的运行面（宿主、只读根）里**构造即 PermissionError** ⇒ 批73 那 14 枚在册守卫用例
   从没被执行过一次（批86 底表里它们整档落 B2「跑法受限」）。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from butler.integrations.ilink.bridge import ILinkBridge


class _FakeRuntime:
    """只带一枚 settings.data_dir 的运行时替身（真运行时同名字段，见 memory/feeder.py:42）。"""

    def __init__(self, data_dir: str):
        self.settings = SimpleNamespace(data_dir=data_dir)


class BridgeContextDirTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="b87ctx")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, True)

    def test_context_dir_follows_runtime_settings(self):
        """注入运行时的 data_dir 必须决定会话档目录（⛔ 烤死容器内置路径）。"""
        bridge = ILinkBridge(client=None, runtime=_FakeRuntime(self.dir))
        self.assertEqual(
            Path(bridge._context_dir),
            Path(self.dir) / "ilink" / "contexts",
            "会话档目录没跟随注入的 data_dir⇒B-19 未修，用户换 DATA_DIR 后历史仍写容器内置路径",
        )

    def test_construction_creates_dir_under_injected_data_dir(self):
        """构造后目录应已就位（修法⛔ 只是把 mkdir 删掉——那会让首次写入直接炸）。"""
        bridge = ILinkBridge(client=None, runtime=_FakeRuntime(self.dir))
        self.assertTrue(Path(bridge._context_dir).is_dir(),
                        "注入路径下没建出会话档目录⇒mkdir 被顺手删了或走的是别的路径")

    def test_context_dir_falls_back_to_get_settings_without_runtime(self):
        """无运行时（测试／独立装配）时取 get_settings().data_dir，⛔ 取字面量。"""
        fake = SimpleNamespace(data_dir=self.dir)
        with mock.patch("butler.integrations.ilink.bridge.get_settings",
                        return_value=fake, create=True):
            bridge = ILinkBridge(client=None, runtime=None)
        self.assertEqual(
            Path(bridge._context_dir),
            Path(self.dir) / "ilink" / "contexts",
            "runtime=None 时仍不跟随 settings⇒宿主面构造即 PermissionError",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
