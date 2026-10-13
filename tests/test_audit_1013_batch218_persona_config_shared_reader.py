"""批218（step3 搬码队列第 7 笔）：NAS 线 `94295b7` — `_load_persona` 的读口径收进共用尺子。

现读（`workorders/判定表-step3-逐笔-20261013.md` §一-94295b7）：
`butler/config.py:416-428` 自带一套 `json.loads(cfg_path.read_text())` ＋ 自己判顶层不是 dict。
这套判据与批70b 的 `core.atomic_json.read_json_object`（「读不出来≠没有」的唯一尺子）逐字重复。

⚠ 这笔的性质要先说死：**行为面⛔ 收益**（批77 那档 `test_audit_1008_batch77_persona_unreadable.py`
6 例早就把「坏档留痕／顶层数组⛔ 抛穿／缺档⛔ 喊成故障」钉住了，E 侧这些腿此刻是绿的），
收益只有两枚——
1. 全仓的 JSON 读法并成一把（写侧收敛到 `write_json_atomic` 时就是这个口径）；
2. 故障文案把**三种**失败模式（坏到解析不出／字节读不出／顶层⛔ 是对象）与「盘上原件⛔ 被动过、
   留给人工修」一次说清；现文案只说 "load failed"，人工照它修档时容易误判「程序会自己盖回去」。

⇒ 判据面：形状腿（应红＝此刻还没走共用尺子）＋**⛔ 把好路改坏**的行为腿（此刻就绿，搬完须仍绿）。
行为腿⛔ 当成本笔的修复证据。
"""
from __future__ import annotations

import ast
import json
import pathlib
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_PY = REPO_ROOT / "butler" / "config.py"


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


class SharedReaderShape(unittest.TestCase):
    """形状腿：读侧并尺，⛔ 自带第二套 JSON 解析。"""

    @classmethod
    def setUpClass(cls):
        cls.source = CONFIG_PY.read_text(encoding="utf-8")
        cls.body = _func_source(cls.source, "_load_persona")
        if cls.body is None:
            raise AssertionError("`_load_persona` 不在源码里（文件被搬过？）⇒ 本腿无读数")
        cls.calls = _call_names(cls.body)

    def test_uses_converged_reader(self):
        self.assertIn("read_json_object", self.calls,
                      f"仍自带解析＝全仓第 N 把 JSON 读尺；现读调用名 {sorted(self.calls)}")

    def test_does_not_parse_json_itself(self):
        self.assertNotIn("loads", self.calls, "函数体里还有 json.loads＝并尺没并上")
        self.assertNotIn("read_text", self.calls, "函数体里还自己 read_text＝编码口径又开一份")

    def test_ruler_bites_on_old_form(self):
        old = ("def _load_persona(self):\n"
               "    raw = json.loads(cfg_path.read_text(encoding='utf-8'))\n"
               "    if not isinstance(raw, dict):\n"
               "        return\n")
        names = _call_names(old)
        self.assertNotIn("read_json_object", names, "合成件构造失败＝尺子没东西可判")
        self.assertTrue("loads" in names and "read_text" in names, "形状尺⛔ 咬旧形制＝恒绿尺")

    def test_ruler_passes_on_new_form(self):
        new = ("def _load_persona(self):\n"
               "    raw, unreadable = read_json_object(cfg_path)\n"
               "    if unreadable:\n"
               "        logger.error('config.json will not parse')\n"
               "        return\n")
        names = _call_names(new)
        self.assertIn("read_json_object", names)
        self.assertNotIn("loads", names)
        self.assertNotIn("read_text", names)


class GoodPathsNotBroken(unittest.TestCase):
    """⛔ 把好路改坏：这三格搬之前就是绿的，搬完必须仍绿（⛔ 当成本笔的修复证据）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = pathlib.Path(self.tmp.name)
        self.cfg = self.data_dir / "config.json"

    def _settings(self):
        from butler.config import Settings
        return Settings(data_dir=str(self.data_dir))

    def test_valid_config_still_overrides(self):
        self.cfg.write_text(json.dumps({"system": "定制人格", "greeting_template": "{nickname}回家"},
                                       ensure_ascii=False), encoding="utf-8")
        s = self._settings()
        s._load_persona()
        self.assertEqual(s.persona.system, "定制人格")
        self.assertEqual(s.persona.greeting_template, "{nickname}回家")

    def test_missing_config_stays_quiet(self):
        s = self._settings()
        with self.assertNoLogs("butler.config", level="ERROR"):
            s._load_persona()

    def test_unreadable_still_traces_on_config_logger(self):
        """并尺之后 config 自己那一枚 ERROR ⛔ 消失：运维只盯 `butler.config` 的 ERROR 找启动降级，
        共用尺子那句喊在 `butler.core.atomic_json`——两枚都要在。"""
        self.cfg.write_text('{"system": "beta", ', encoding="utf-8")
        s = self._settings()
        with self.assertLogs("butler.config", level="ERROR") as cm:
            s._load_persona()
        self.assertIn("config.json", " ".join(cm.output))


if __name__ == "__main__":
    unittest.main()
