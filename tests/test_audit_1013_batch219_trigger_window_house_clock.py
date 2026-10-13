"""批219（step3 搬码队列第 8 笔）：NAS 线 `5d07c6a` — 触发器的时段窗口必须吃**家庭墙钟**。

缺口现读（`workorders/判定表-step3-逐笔-20261013.md` §一-5d07c6a）：
`butler/triggers/engine.py:319-332` 的 `_in_time_range` 用裸 `datetime.now()`。
本仓其余五处时段判定（`core/agent.py`、`core/cron_task.py`、`decision/engine.py`、
`decision/aggregator.py`、`skills/engines/llm_decide/engine.py`）都走 `homesdk.time.house_now()`：
容器的 `TZ` 只写在 `docker-compose.yml` 一行里，那行没了或被改成 UTC 时，裸钟把**所有**时段窗口
整体平移 8 小时且零日志（批153 同族）⇒ 表现为「夜间静音窗失效／该早上七点发的提醒凌晨三点发」，
⛔ 报错、⛔ 留痕，只有一家人被设备叫醒。

判据两半：
- **行为腿（承重）**：把两枚钟钉成**互相矛盾**的固定时刻，逐窗现读「跟的是家庭钟」。
  配正反两窗＝⛔ 两种钉值里有一种能蒙对；
- **形状腿**：函数体调 `house_now`。AST 调用名＋应咬合成件。

⛔ 把「本机两枚钟读数相同」当成本腿的证据：这台开发机 `TZ` 与家庭区一致，裸钟与家庭钟现在看不出差别，
所以只有钉死的矛盾钟值能证明跟的是哪一把。
"""
from __future__ import annotations

import ast
import datetime as _dt
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ENGINE_PY = REPO_ROOT / "butler" / "triggers" / "engine.py"

HOUSE_NOON = _dt.datetime(2026, 10, 13, 11, 0)      # 家庭墙钟 11:00
CONTAINER_NIGHT = _dt.datetime(2026, 10, 13, 3, 0)  # 容器裸钟 03:00（差 8 小时＝TZ 漂成 UTC 的形态）


class _BareClock:
    """顶替 `engine.datetime`：`now()` 恒回容器那一枚，⛔ 让本档依赖真墙钟。"""

    @staticmethod
    def now(*a, **kw):
        return CONTAINER_NIGHT


def _func_source(source: str, funcname: str) -> str | None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == funcname:
            return ast.get_source_segment(source, node)
    return None


def _call_names(source: str) -> set[str]:
    """函数体里的**被调名**（`a.b(...)` 取 `b`）。⛔ 用「正文含该串」判＝注释提一句就假绿。"""
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


class ClockSourceBehaviour(unittest.TestCase):
    """行为腿：两枚钟钉成矛盾值，现读跟哪一把。"""

    def setUp(self):
        from butler.triggers import engine as eng
        self.eng = eng
        self._had_house = hasattr(eng, "house_now")
        self._saved_house = getattr(eng, "house_now", None)
        self._saved_dt = eng.datetime
        eng.house_now = lambda: HOUSE_NOON
        eng.datetime = _BareClock
        self.addCleanup(self._restore)

    def _restore(self):
        if self._had_house:
            self.eng.house_now = self._saved_house
        else:
            delattr(self.eng, "house_now")
        self.eng.datetime = self._saved_dt

    @staticmethod
    def _in(window: str) -> bool:
        from butler.triggers.engine import TriggerEngine
        return TriggerEngine._in_time_range(window)

    def test_day_window_follows_house_clock(self):
        self.assertTrue(self._in("09:00-18:00"),
                        "家庭钟 11:00 该在窗口内；判 False＝跟了容器裸钟 03:00（TZ 漂了＝窗口整体平移）")

    def test_night_window_follows_house_clock(self):
        self.assertFalse(self._in("22:00-06:00"),
                         "家庭钟 11:00 该在夜间窗外；判 True＝跟了容器裸钟 03:00⇒夜间静音窗形同虚设")

    def test_cross_midnight_arithmetic_intact(self):
        """⛔ 把好路改坏：跨午夜那支的算式（cur>=start or cur<=end）换钟源后须照常。"""
        self.eng.house_now = lambda: _dt.datetime(2026, 10, 13, 23, 30)
        self.assertTrue(self._in("22:00-06:00"))
        self.eng.house_now = lambda: _dt.datetime(2026, 10, 13, 5, 0)
        self.assertTrue(self._in("22:00-06:00"))
        self.eng.house_now = lambda: _dt.datetime(2026, 10, 13, 12, 0)
        self.assertFalse(self._in("22:00-06:00"))

    def test_malformed_window_still_allows(self):
        """⛔ 把好路改坏：解析不出 HH:MM-HH:MM 时放行（现树口径，本笔⛔ 改判定方向）。"""
        self.assertTrue(self._in("白天"))


class ClockSymbolShape(unittest.TestCase):
    """形状腿：读家庭钟那枚符号。"""

    @classmethod
    def setUpClass(cls):
        cls.source = ENGINE_PY.read_text(encoding="utf-8")
        cls.body = _func_source(cls.source, "_in_time_range")
        if cls.body is None:
            raise AssertionError("`_in_time_range` 不在源码里（文件被搬过？）⇒ 本腿无读数")
        cls.calls = _call_names(cls.body)

    def test_uses_house_clock(self):
        self.assertIn("house_now", self.calls, f"仍用裸钟；现读调用名 {sorted(self.calls)}")

    def test_ruler_bites_on_bare_clock(self):
        old = "def _in_time_range(w):\n    now = datetime.now()\n    return now.hour\n"
        names = _call_names(old)
        self.assertNotIn("house_now", names, "合成件构造失败＝尺子没东西可判")
        self.assertIn("now", names, "尺子认不出裸钟形制＝它压根没在看钟")

    def test_ruler_passes_on_house_clock(self):
        new = "def _in_time_range(w):\n    now = house_now()\n    return now.hour\n"
        self.assertIn("house_now", _call_names(new))


if __name__ == "__main__":
    unittest.main()
