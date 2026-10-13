"""批213（NAS 线 `9cdb4f5` 的 D7 腿收编·批73 同族）：ilink 对话历史读失败被当成「没有历史」⇒ 整份覆盖抹掉该用户全部历史。

现读基线（2026-10-13 09:53Z，`sed -n '214,236p' butler/integrations/ilink/bridge.py`）：

    214  def _load_context(self, from_user):
    217      if ctx_file.exists():
    218          try: data = json.loads(ctx_file.read_text(...))
    219          except Exception:
    220              pass                    ← 档在而读不出＝静默归零
    224  def _save_context(self, from_user, user_msg, reply):
    226      history = self._load_context(from_user)     ← 拿到的是 []
    227      history.append(...)                          ← 只剩这一轮两条
    231      ctx_file.write_text(json.dumps(...))         ← 就地整份盖回＝其余历史没了

⇒ 这是 D7 一族里 **最后一处没接线**的站点：同仓的 `devices.py`（批70b）、`roles/state.py`（批70b）、
`aliases.py`、`ilink/client.py`（批72，本档现读 :58 已走 `read_json_object`）都已收编，唯独
`ilink/bridge.py` 还留着自家 `try/except: pass`＋就地 `write_text`。
`b221` 那把符号尺把它漏了——尺是全树判定的，`read_json_object` 在 `client.py` 出现就判「E 有」，
⛔ 看见**按档**的半腿（这条已登记给下一把尺）。

本批照 NAS 线口径收两条腿：
  · 读侧走收敛尺子 `read_json_object`（缺档＝真没有＝允许首写；档在读不出＝记进
    `_unreadable_users`，喊一声 warning）；
  · 写侧据该名册**拒绝对覆盖**，并改走 `write_json_atomic`（⛔ 半截会话档）。

判据钉**行为**（坏档后盘上字节必须一字未动；缺档首写照常落盘）也钉**形状**
（`_save_context` 体内须有 `write_json_atomic`、⛔ 再有 `write_text`）。形状尺配两侧合成校准。
本档 import `butler.integrations.ilink.bridge`（纯桥接模块，⛔ `butler.app`＝二次 import 开连接），
测试用 `tempfile` 自建目录并注入 `settings.data_dir`，⛔ 碰现网 WAL 库。
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

for _k, _v in (("DOUBAO_API_KEY", "dummy1"), ("DESKPILOT_API_TOKEN", "dummy2"),
               ("TASK_REPORT_TOKEN", "dummy3"), ("BUTLER_WEB_PASSWORD", "dummy4")):
    os.environ.setdefault(_k, _v)

from butler.integrations.ilink.bridge import ILinkBridge  # noqa: E402

BRIDGE_PY = REPO_ROOT / "butler" / "integrations" / "ilink" / "bridge.py"


def _bridge(tmp: str) -> ILinkBridge:
    client = types.SimpleNamespace(is_logged_in=False, bot_id="t")
    rt = types.SimpleNamespace(settings=types.SimpleNamespace(data_dir=tmp))
    return ILinkBridge(client, runtime=rt)


class UnreadableHistoryIsNotEmptyHistory(unittest.TestCase):
    """档在而读不出＝现场还在盘上⇒ 写腿⛔ 拿空内存态整份盖回去。"""

    def test_corrupt_file_survives_a_save_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = _bridge(tmp)
            f = b._context_dir / "u1.json"
            wrecked = '{"history": [{"role": "user", "content": "半'
            f.write_text(wrecked, encoding="utf-8")
            before = f.read_bytes()

            b._save_context("u1", "新一轮提问", "新一轮回答")

            self.assertEqual(f.read_bytes(), before,
                             "坏档被整份覆盖＝该用户其余历史从此消失（D7 一族）")
            self.assertIn("u1", getattr(b, "_unreadable_users", set()),
                          "读失败没登记＝下一次写照样盖下去")

    def test_missing_file_allows_first_write(self):
        """缺档＝真没有：首写⛔ 被拒，否则新装环境永远建不起会话档。"""
        with tempfile.TemporaryDirectory() as tmp:
            b = _bridge(tmp)
            f = b._context_dir / "newuser.json"
            self.assertFalse(f.exists())
            b._save_context("newuser", "第一条", "回第一条")
            self.assertTrue(f.exists(), "缺档被当成读失败＝首写也被拒（判据方向反了）")
            data = json.loads(f.read_text(encoding="utf-8"))
            self.assertEqual([m["role"] for m in data["history"]], ["user", "assistant"])

    def test_good_file_round_trips_and_keeps_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = _bridge(tmp)
            for i in range(25):
                b._save_context("u2", f"问{i}", f"答{i}")
            data = json.loads((b._context_dir / "u2.json").read_text(encoding="utf-8"))
            self.assertLessEqual(len(data["history"]), 20, "历史条数⛔ 封顶（MAX_HISTORY_PER_USER）")
            self.assertEqual(data["history"][-1]["content"], "答24")
            self.assertEqual(list((b._context_dir).glob("*.tmp")), [],
                             "原子写的临时兄弟档没被 rename 收掉")

    def test_unreadable_user_not_marked_when_file_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = _bridge(tmp)
            self.assertEqual(b._load_context("ghost"), [])
            self.assertNotIn("ghost", getattr(b, "_unreadable_users", set()),
                             "缺档也记进名册＝该用户以后永远写不进去")


class SaveUsesAtomicWriter(unittest.TestCase):
    """形状尺：`_save_context` 体内须调 `write_json_atomic`、⛔ 再就地调 `write_text`。

    判定只吃 AST 里的**真调用名**——⛔ 扫源码子串：第一版这么写，被自己注释里那句
    「就地 write_text 崩在半路」咬成红灯（裸写尺要剥注释行，本尺同理）。
    """

    def _func(self, source: str, funcname: str) -> ast.FunctionDef | None:
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef) and node.name == funcname:
                return node
        return None

    def _call_names(self, source: str, funcname: str) -> set[str]:
        fn = self._func(source, funcname)
        if fn is None:
            return set()
        names = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    names.add(node.func.attr)
        return names

    def test_ruler_bites_on_inplace_write(self):
        src = ("def _save_context(self, u):\n"
               "    ctx_file.write_text(json.dumps({}))\n")
        calls = self._call_names(src, "_save_context")
        self.assertIn("write_text", calls, "合成件里就地写没被咬住＝这把尺不配当闸")
        self.assertNotIn("write_json_atomic", calls, "合成件构造失败＝尺子没东西可判")

    def test_ruler_ignores_prose_mention(self):
        src = ("def _save_context(self, u):\n"
               "    # 表行 45 同族：就地 write_text 崩在半路＝半截会话档\n"
               "    write_json_atomic(ctx_file, payload)\n")
        calls = self._call_names(src, "_save_context")
        self.assertNotIn("write_text", calls, "注释里的字样被读成缺陷＝尺子判据错位")
        self.assertIn("write_json_atomic", calls)

    def test_real_save_context_is_atomic(self):
        src = BRIDGE_PY.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        self.assertIsNotNone(self._func(src, "_save_context"),
                             "bridge.py 里找不到 _save_context＝探测口径失效")
        calls = self._call_names(src, "_save_context")
        self.assertIn("write_json_atomic", calls,
                      "会话档仍是就地截断写＝崩在半路留半截 JSON")
        self.assertNotIn("write_text", calls, "会话档体内仍有就地 write_text 调用")


if __name__ == "__main__":
    unittest.main()
