"""批211（NAS 线 `04fac9d` 两条腿收编·第十八轮 V42＋V43）：watch 任务表的**类级共享**与**永不弹除**。

现读基线（2026-10-13 09:34Z，`grep -n "_media_stop_tasks" butler/integrations/ha.py`）：

    598:    _media_stop_tasks: dict[str, asyncio.Task] = {}     ← 挂在**类体**上
    606:        old = self._media_stop_tasks.get(entity_id)
    672:        self._media_stop_tasks[entity_id] = asyncio.create_task(_watch())

⇒ V42：类属性 dict 由**全部 HAClient 实例共用一张表**（同文件 :51 的 `_xiaomi_watch_tasks` 是
`__init__` 里的实例级，两者形制不一致）。:606 的「先取消同实体旧 watch」于是变成**跨实例互伤**：
新客户端一次播放会取消另一条会话正挂着的停播轮询 ⇒ 该停的播放停不下来（循环播放）。

⇒ V43：两张表**平时**只 `.get`＋赋值、无 `pop`／done 回调（批190 的 docstring 已把这条登记给
下一批：「那是内存回收一族，本批只保证关停时不剩活任务」）。每轮播放留一枚 done 任务在表里，
`asyncio` 又对已完成任务只持弱引用 ⇒ 表按播放次数单调增长（长驻进程里的死句柄）。

本批收这两条腿：
  · `_media_stop_tasks` 挪进 `__init__`（与 `_xiaomi_watch_tasks` 同形制）；
  · 新增 `_forget_task_when_done(table, key, task)`，两张表的两处赋值点都接上。
    回收必须**按身份**判（`table.get(key) is done`）：`cancel()` 只把 done 回调排进队列，
    同键的新任务往往已先登记，无条件 `pop(key)` 会把新 watch 抹掉 ⇒ 下次换入时⛔ 再取消旧任务。

⛔ 顺带收的：`_play_method_cache`／`_devices_mtime` 的内存增长属另一族。

形状尺（两处赋值点须各接一根回收腿）配**两侧合成校准**：应咬的合成件（只赋值不回收）要红、
应放行的合成件（赋值＋回收）要绿；⛔ 只跑真文件（恒绿与恒红都不改变真文件读数）。
本档只 import `butler.integrations.ha`（纯客户端模块，⛔ `butler.app`＝二次 import 开连接）。
"""
from __future__ import annotations

import ast
import asyncio
import os
import pathlib
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# 现网⛔ 四把键（config 有启动硬门）：这里只放**明显是假的**占位值，⛔ 抄任何真凭据。
for _k, _v in (("DOUBAO_API_KEY", "dummy1"), ("DESKPILOT_API_TOKEN", "dummy2"),
               ("TASK_REPORT_TOKEN", "dummy3"), ("BUTLER_WEB_PASSWORD", "dummy4")):
    os.environ.setdefault(_k, _v)

HA_PY = REPO_ROOT / "butler" / "integrations" / "ha.py"

import butler.integrations.ha as ha_mod  # noqa: E402
from butler.integrations.ha import HAClient  # noqa: E402


def _forget_task_when_done(table, key, task):
    """模块级符号的**取用点**（⛔ 顶层 from-import：缺符号会让整档在装载期 ImportError，
    那两格形状尺的合成校准就被一起带走了，看不出「谁没接线」）。"""
    fn = getattr(ha_mod, "_forget_task_when_done", None)
    if fn is None:
        raise AssertionError("butler/integrations/ha.py 没有 _forget_task_when_done＝回收腿未实现")
    return fn(table, key, task)


class FakeSettings:
    """HAClient.__init__ 只存引用，⛔ 取属性；构造真 Settings 要过启动硬门。"""
    pass


def _client():
    return HAClient(FakeSettings())


class WatchTableIsPerInstance(unittest.TestCase):
    """V42：media stop 表⛔ 挂在类体上（跨实例互伤）。"""

    def test_media_stop_table_not_in_class_dict(self):
        self.assertNotIn("_media_stop_tasks", HAClient.__dict__,
                         "media stop 表仍是类属性＝全部实例共用一张表")

    def test_two_clients_do_not_share_media_stop_table(self):
        a, b = _client(), _client()
        self.assertIsNot(a._media_stop_tasks, b._media_stop_tasks,
                         "两个 HAClient 共用同一 dict＝一个客户端的换入会取消另一个的 watch")
        self.assertIsNot(a._xiaomi_watch_tasks, b._xiaomi_watch_tasks)


class TaskReclaimedByIdentity(unittest.TestCase):
    """V43：任务结束后条目要自己掉下来，但⛔ 误摘同键的新任务。"""

    def test_completed_task_entry_is_dropped(self):
        table = {}

        async def main():
            task = asyncio.ensure_future(asyncio.sleep(0))
            table["k"] = task
            _forget_task_when_done(table, "k", task)
            await task
            await asyncio.sleep(0)   # 让 done 回调排完

        asyncio.run(main())
        self.assertEqual(table, {}, "已完成的 watch 任务仍占着表＝按播放次数单调增长")

    def test_newer_task_under_same_key_survives(self):
        """身份闸：旧任务回调后到时，表里若已是另一枚任务，⛔ 摘掉新那枚。"""
        table = {}

        async def main():
            old = asyncio.ensure_future(asyncio.sleep(0))
            table["k"] = old
            _forget_task_when_done(table, "k", old)
            new = asyncio.ensure_future(asyncio.sleep(30))
            table["k"] = new          # 同键换入（cancel 回调尚未跑完）
            old.cancel()
            try:
                await old
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(0)
            return new

        new = asyncio.run(main())
        self.assertIs(table.get("k"), new, "无条件 pop 会把刚登记的新 watch 抹掉")
        new.cancel()


class BothAssignmentSitesArmed(unittest.TestCase):
    """形状尺：ha.py 里每一处 watch 表赋值点必须紧跟同表的回收腿。"""

    ASSIGN_FMT = "_{name}_tasks"
    TABLES = ("xiaomi_watch", "media_stop")

    def _sites(self, source_text):
        """返回 [(函数名, 表名)]：赋值给 self._*_tasks[...] 的每一处。"""
        tree = ast.parse(source_text)
        sites = []

        def walk(node, funcname):
            for child in ast.iter_child_nodes(node):
                fname = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else funcname
                if isinstance(child, ast.Assign):
                    for tgt in child.targets:
                        if isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Attribute) \
                                and isinstance(tgt.value.value, ast.Name) \
                                and tgt.value.value.id == "self" \
                                and tgt.value.attr.endswith("_tasks"):
                            sites.append((fname, tgt.value.attr))
                walk(child, fname)

        walk(tree, "<module>")
        return sites

    def _has_forget_leg(self, source_text, funcname, table_attr):
        """该函数体内是否有一处 `_forget_task_when_done(self.<table_attr>, ...)`。"""
        tree = ast.parse(source_text)

        def scan(node):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == funcname:
                    found = [False]

                    def deep(n):
                        for c in ast.iter_child_nodes(n):
                            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) \
                                    and c.func.id == "_forget_task_when_done":
                                a0 = c.args[0] if c.args else None
                                if isinstance(a0, ast.Attribute) and a0.attr == table_attr:
                                    found[0] = True
                            deep(c)

                    deep(child)
                    return found[0]
                if scan(child):
                    return True
            return False

        return scan(tree)

    def test_ruler_bites_on_missing_leg(self):
        """★ 应咬的合成件：只赋值、无回收腿 ⇒ 尺子必须报缺（⛔ 恒绿）。"""
        src = (
            "class C:\n"
            "    def f(self):\n"
            "        self._media_stop_tasks['a'] = task()\n"
        )
        sites = self._sites(src)
        self.assertEqual(sites, [("f", "_media_stop_tasks")], "赋值点探测本身失灵")
        self.assertFalse(self._has_forget_leg(src, "f", "_media_stop_tasks"),
                         "形状尺认不出缺腿的合成件＝恒绿")

    def test_ruler_passes_on_wired_source(self):
        """★ 应放行的合成件：赋值＋同表回收腿 ⇒ 尺子必须报有。"""
        src = (
            "class C:\n"
            "    async def f(self):\n"
            "        self._media_stop_tasks['a'] = t\n"
            "        _forget_task_when_done(self._media_stop_tasks, 'a', t)\n"
        )
        self.assertTrue(self._has_forget_leg(src, "f", "_media_stop_tasks"),
                        "形状尺认不出已接线的合成件＝恒红")

    def test_both_real_tables_are_armed(self):
        src = HA_PY.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        sites = [s for s in self._sites(src) if s[1].endswith("_tasks")
                 and any(t in s[1] for t in self.TABLES)]
        self.assertTrue(sites, "ha.py 里找不到 watch 表赋值点＝探测口径失效")
        for funcname, table_attr in sites:
            with self.subTest(func=funcname, table=table_attr):
                self.assertTrue(self._has_forget_leg(src, funcname, table_attr),
                                "%s 里 %s 赋值后没接回收腿＝done 任务永久占表" % (funcname, table_attr))


if __name__ == "__main__":
    unittest.main()
