"""批217（step3 搬码队列第 6 笔）：NAS 线 `e5f9c78` — 反向查角色必须走**共用 reader**。

缺口现读（`workorders/判定表-step3-逐笔-20261013.md` §一-e5f9c78）：
`butler/api/doubao_webhook.py:80-92` 的 `_conversation_to_role` 自己 `json.loads(state_path.read_text())`，
⛔ 走 `butler/roles/state.py:read_role_state`（批70b 收敛尺子 `read_json_object` 的读者壳）。两件事：
1. **写读分叉**：这张 `role_state.json` 有两个写手（`roles/state.py`、`memory/feeder.py`），读侧再自开
   一套解析＝第 4 把口径（批164 D5 同一族）；
2. **半路 abort**：`state.get("conversation_id")` 前⛔ `isinstance(state, dict)`——盘上任何一个值是
   非 dict（写手崩在半路、人工修档留下 `"role": "绑错成字符串"`），第一条就抛 `AttributeError`，
   被同一个 `except Exception` 吞成一行 warning ⇒ **后面本来能命中的角色整批看不见**。
   手机侧表现＝消息被静默丢弃（不崩，只是不见了）。

判据两半（⛔ 只做形状那一半＝看不见半腿）：
- **行为腿**：真临时档、真函数体，现读「命中／非 dict 值⛔ 吞掉后面那条／坏档返 None 且⛔ 抛穿／缺档 None」；
- **形状腿**：函数体调 `read_role_state` 且⛔ 自带 `json.loads`。形状腿配**应咬**合成件（旧形制要红）。

⛔ 把「行为腿 4 格全过」读成语义等价：共用 reader 的差别在**坏档有独立出口**（`unreadable` 判定），
本笔的收益是第 2 格那种「一条坏值吃掉全部后续」的整批失明。
"""
from __future__ import annotations

import ast
import json
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
WEBHOOK_PY = REPO_ROOT / "butler" / "api" / "doubao_webhook.py"


def _webhook_module():
    from butler.api import doubao_webhook
    return doubao_webhook


def _func_source(source: str, funcname: str) -> str | None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == funcname:
            return ast.get_source_segment(source, node)
    return None


def _call_names(source: str) -> set[str]:
    """函数体里出现过的**被调名**（`a.b(...)` 取 `b`）。⛔ 用「正文含该串」判——注释里提一句就假绿。"""
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


class ReverseLookupBehaviour(unittest.TestCase):
    """行为腿：真档、真函数体，只把档的位置挪到 tmp（整目录 `addCleanup` 收走，⛔ 留盘）。"""

    def setUp(self):
        import tempfile
        d = tempfile.TemporaryDirectory(prefix="b217_rolestate_")
        self.addCleanup(d.cleanup)
        self.tmpdir = pathlib.Path(d.name)

    def _lookup(self, content: str, cid: str) -> str | None:
        w = _webhook_module()
        path = pathlib.Path(content)
        real = w.role_state_file
        w.role_state_file = lambda: path
        try:
            return w._conversation_to_role(cid)
        finally:
            w.role_state_file = real

    def _write(self, name: str, payload) -> str:
        p = self.tmpdir / name
        if isinstance(payload, (dict, list)):
            p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        else:
            p.write_bytes(payload)
        return str(p)

    def test_matching_role_found(self):
        with self.subTest("好路⛔ 被改坏"):
            p = self._write(
                "ok.json",
                {"lou_q": {"conversation_id": "CID-1"}, "gu": {"conversation_id": "CID-2"}})
            self.assertEqual(self._lookup(p, "CID-2"), "gu")

    def test_non_dict_value_does_not_blind_later_roles(self):
        """★ 本笔的承重格：前一条值是裸字符串 ⇒ ⛔ 让它的 AttributeError 吃掉后面能命中的角色。"""
        p = self._write(
            "half.json",
            {"broken": "绑错成了字符串", "gu": {"conversation_id": "CID-2"}})
        self.assertEqual(
            self._lookup(p, "CID-2"), "gu",
            "非 dict 值抛 AttributeError 被 except 吞＝后面全部角色看不见＝手机端消息静默丢弃")

    def test_corrupt_file_returns_none_and_does_not_raise(self):
        p = self._write("bad.json", b"{\"gu\": [1,2")
        self.assertIsNone(self._lookup(p, "CID-2"))

    def test_missing_file_returns_none(self):
        p = self.tmpdir / "never-written.json"
        self.assertIsNone(self._lookup(str(p), "CID-2"))


class SharedReaderShape(unittest.TestCase):
    """形状腿：读侧必须收敛到共用 reader，⛔ 自带一套 JSON 解析。"""

    @classmethod
    def setUpClass(cls):
        cls.source = WEBHOOK_PY.read_text(encoding="utf-8")
        cls.body = _func_source(cls.source, "_conversation_to_role")
        if cls.body is None:
            raise AssertionError("`_conversation_to_role` 不在源码里（文件被搬过？）⇒ 本腿无读数")
        cls.calls = _call_names(cls.body)

    def test_uses_shared_role_state_reader(self):
        self.assertIn("read_role_state", self.calls,
                      f"仍自带解析＝第 4 把读口径；现读调用名 {sorted(self.calls)}")

    def test_does_not_parse_json_itself(self):
        self.assertNotIn("loads", self.calls,
                         "函数体里还有 json.loads＝写读分叉没收敛（批70b 的尺子绕开了）")

    def test_ruler_bites_on_old_form(self):
        old = ("def _conversation_to_role(cid):\n"
               "    data = json.loads(path.read_text(encoding='utf-8'))\n"
               "    for rid, st in data.items():\n"
               "        if st.get('conversation_id') == cid:\n"
               "            return rid\n")
        names = _call_names(old)
        self.assertNotIn("read_role_state", names, "合成件构造失败＝尺子没东西可判")
        self.assertIn("loads", names)
        self.assertTrue(("read_role_state" not in names) and ("loads" in names),
                        "形状尺⛔ 咬旧形制＝恒绿尺")

    def test_ruler_passes_on_new_form(self):
        new = ("def _conversation_to_role(cid):\n"
               "    for rid, st in read_role_state(path).items():\n"
               "        if isinstance(st, dict) and st.get('conversation_id') == cid:\n"
               "            return rid\n")
        names = _call_names(new)
        self.assertNotIn("loads", names)
        self.assertIn("read_role_state", names)


if __name__ == "__main__":
    unittest.main()
