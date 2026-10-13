"""批220（step3 搬码队列第 9 笔＝本批最后一笔）：NAS 线 `15eeab5` — Bark 相对 icon 的前缀只读注入的 Settings。

缺口现读（`workorders/判定表-step3-逐笔-20261013.md` §一-15eeab5；NAS 侧原文在
`workorders/readings/1013/b219_15eeab5_bark_routes_1013.txt`，⛔ 抄进投递件）：
`butler/api/bark_routes.py:88-91` 在拼头像绝对地址时自己
`os.environ.get("BUTLER_BASE_URL", "http://…:8095")` ⇒
1. **第二份默认值**：`config.py:120/266` 已经把同一条默认收进 `Settings.base_url`，这里再烤一份，
   今后改 config 那份默认⛔ 跟着改这里＝同一件事两个答案（批68 #152 的原始判据）；
2. **绕开注入面**：测试与嵌入方拿的是注入的 `rt.settings`，⛔ 是进程环境变量⇒这类调用方拼出来的
   头像地址与管家真实对外地址无关，Bark 侧拉图静默 404（推送本身照成功，⛔ 报错）。

判据两半：
- **行为腿（承重）**：把 `Settings.base_url` 与环境变量钉成**两个不同值**，现读 icon 跟哪一枚；
  再配「base_url 为空⇒icon 保持相对」（新加的 `if base:` 那腿）＋「绝对 icon ⛔ 再前缀」（⛔ 把好路改坏）；
- **形状腿**：函数体⛔ 再 `os.environ.get` 取基址。AST 调用名＋属性名，配应咬合成件。
"""
from __future__ import annotations

import ast
import asyncio
import os
import pathlib
import types
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
BARK_PY = REPO_ROOT / "butler" / "api" / "bark_routes.py"

SETTINGS_BASE = "http://settings-house.invalid:9999"
ENV_RIVAL = "http://env-rival.invalid"


def _func_source(source: str, funcname: str) -> str | None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == funcname:
            return ast.get_source_segment(source, node)
    return None


def _reads(src: str) -> tuple[set[str], set[str]]:
    """(被调名, 被读的属性／名字) 两把集合。

    `getattr(obj, "base_url", "")` 里键名是**字符串常量**不是 Attribute 节点——只收 Attribute
    的尺子对这种形制永远判「没读 base_url」＝恒红尺。故 getattr 的第二参一并收进被读名。
    """
    tree = ast.parse(src)
    calls, attrs = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.add(node.func.id)
                if node.func.id == "getattr" and len(node.args) >= 2 \
                        and isinstance(node.args[1], ast.Constant) \
                        and isinstance(node.args[1].value, str):
                    attrs.add(node.args[1].value)
            elif isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)
        if isinstance(node, ast.Attribute):
            attrs.add(node.attr)
        if isinstance(node, ast.Name):
            attrs.add(node.id)
    return calls, attrs


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


class _Router:
    def __init__(self):
        self.kwargs = None

    async def notify(self, channel, text, title=None, trace_id="", bark_kwargs=None, **kw):
        self.kwargs = bark_kwargs or {}
        return types.SimpleNamespace(results={channel: types.SimpleNamespace(ok=True)})


async def _push(body: dict, base_url: str):
    """现读一枚相对 icon 最后被拼成什么。桩只打在系统边界（HTTP 出口＋环境变量）。"""
    from butler.api import bark_routes as br
    from butler.notify import singleton as ns

    router = _Router()
    saved = (br.guard, br.get_runtime, ns.get_router)
    br.guard = lambda request: None
    br.get_runtime = lambda: types.SimpleNamespace(
        bark=object(), settings=types.SimpleNamespace(base_url=base_url))
    ns.get_router = lambda: router
    try:
        await br.bark_push(_Req(body))
    finally:
        br.guard, br.get_runtime, ns.get_router = saved
    if router.kwargs is None:
        raise AssertionError("请求没走到 router.notify（被前置分支拦了？）⇒ 本腿无读数")
    return router.kwargs


class BaseUrlSourceBehaviour(unittest.TestCase):
    def setUp(self):
        self._saved_env = os.environ.get("BUTLER_BASE_URL")
        os.environ["BUTLER_BASE_URL"] = ENV_RIVAL
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._saved_env is None:
            os.environ.pop("BUTLER_BASE_URL", None)
        else:
            os.environ["BUTLER_BASE_URL"] = self._saved_env

    def test_relative_icon_follows_injected_settings_not_env(self):
        kwargs = asyncio.run(_push({"body": "x", "icon": "/icons/a.png"}, SETTINGS_BASE))
        self.assertEqual(
            kwargs.get("icon"), SETTINGS_BASE + "/icons/a.png",
            "拼出来的是环境变量那一枚⇒第二份默认值还活着，注入面（测试／嵌入方）被绕开")

    def test_blank_base_url_keeps_icon_relative(self):
        """`if base:` 那腿：基址没配⇒⛔ 拼出 `None/icons/…` 或空串前缀，原样交相对路径。"""
        kwargs = asyncio.run(_push({"body": "x", "icon": "/icons/a.png"}, ""))
        self.assertEqual(kwargs.get("icon"), "/icons/a.png")

    def test_absolute_icon_untouched(self):
        """⛔ 把好路改坏：已是绝对地址⛔ 再前缀。"""
        kwargs = asyncio.run(_push({"body": "x", "icon": "https://cdn.invalid/a.png"}, SETTINGS_BASE))
        self.assertEqual(kwargs.get("icon"), "https://cdn.invalid/a.png")

    def test_trailing_slash_in_base_not_doubled(self):
        kwargs = asyncio.run(_push({"body": "x", "icon": "/icons/a.png"}, SETTINGS_BASE + "/"))
        self.assertEqual(kwargs.get("icon"), SETTINGS_BASE + "/icons/a.png")


class NoSecondDefaultShape(unittest.TestCase):
    """形状腿：本档⛔ 自己读环境变量拼基址。"""

    @classmethod
    def setUpClass(cls):
        cls.source = BARK_PY.read_text(encoding="utf-8")
        cls.body = _func_source(cls.source, "bark_push")
        if cls.body is None:
            raise AssertionError("`bark_push` 不在源码里（文件被搬过？）⇒ 本腿无读数")
        cls.calls, cls.attrs = _reads(cls.body)

    def test_does_not_read_environ_here(self):
        self.assertNotIn("environ", self.attrs,
                         "还在读 os.environ⇒同一份基址两处取值（config.py 的那份是权威）")

    def test_reads_settings(self):
        self.assertTrue("settings" in self.attrs and "base_url" in self.attrs,
                        f"没走注入的 Settings；现读被读名 {sorted(self.attrs)[:16]}")

    def test_ruler_bites_on_env_form(self):
        old = ("async def bark_push(request):\n"
               "    import os\n"
               "    base = os.environ.get(\"X\", \"http://a\")\n"
               "    icon = base.rstrip(\"/\") + icon\n")
        calls, attrs = _reads(old)
        self.assertIn("environ", attrs, "合成件构造失败＝尺子没东西可判")
        self.assertNotIn("base_url", attrs)
        self.assertTrue("environ" in attrs and "base_url" not in attrs, "形状尺⛔ 咬环境变量形制＝恒绿尺")

    def test_ruler_passes_on_settings_form(self):
        new = ("async def bark_push(request):\n"
               "    base = getattr(rt.settings, \"base_url\", \"\") or \"\"\n"
               "    if base:\n"
               "        icon = base.rstrip(\"/\") + icon\n")
        _calls, attrs = _reads(new)
        self.assertNotIn("environ", attrs)
        self.assertIn("base_url", attrs)


if __name__ == "__main__":
    unittest.main()
