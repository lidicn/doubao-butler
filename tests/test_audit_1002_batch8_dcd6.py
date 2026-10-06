"""DCD 裁定⑥＋裁定①的验收（文书 `E:/NAS/关键决策部/decisions/20261002-DB六件影子代码-裁定.md`，⑥ :85、① :22）。

先红后绿：
- ⑥「`butler/engine.py` 必须不存在」——删除前红（现值 249 行／md5 `4e72845f13fcfde60f2619e82627521a`）；
- ①「限额截断必须保留**最新**一截」——SQL 仍是 `ORDER BY ts ASC` 时红。
零 importer 那条（⑥ 的成因）删前删后都必须绿：它是「为什么删它不影响现网」的自证，⛔ 抄 DCD 的读数。
"""
from __future__ import annotations

import ast
import pathlib
import sqlite3
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _import_refs(root: pathlib.Path, want: str = "butler.engine") -> list[str]:
    """AST 扫 `root` 下所有 .py，收集指向 `want` 这个模块的**导入**（含相对导入还原后的全名）。

    属性式访问（`butler.engine.X`）必须先 import 该模块对象才可能存在，所以数导入＝数可达性。
    读文件用 `utf-8-sig`：本树有带 BOM 的 .py（表行 32），`ast.parse` 会被 BOM 判假红。
    """
    hits: list[str] = []
    for py in sorted(root.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        pkg_parts = py.relative_to(root).parts[:-1]
        tree = ast.parse(py.read_text(encoding="utf-8-sig"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == want or a.name.startswith(want + "."):
                        hits.append(f"{py.relative_to(root.parent)}:{node.lineno} import {a.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = list(pkg_parts)
                    for _ in range(node.level - 1):
                        if base:
                            base.pop()
                    mod = ".".join([p for p in base if p] + ([node.module] if node.module else []))
                else:
                    mod = node.module or ""
                if mod == want or mod.startswith(want + "."):
                    hits.append(f"{py.relative_to(root.parent)}:{node.lineno} from {mod}")
                if mod == want.rsplit(".", 1)[0]:          # `from butler import engine`
                    for a in node.names:
                        if a.name == want.rsplit(".", 1)[1]:
                            hits.append(f"{py.relative_to(root.parent)}:{node.lineno} from {mod} import {a.name}")
    return hits


class ShadowFileTest(unittest.TestCase):
    def test_butler_engine_shadow_file_is_gone(self):
        """裁定⑥＝A（删）。留着＝下一次「影子修改」的账仍由现网付。"""
        p = REPO / "butler" / "engine.py"
        self.assertFalse(p.exists(),
                         f"影子副本仍在盘上：{p}（249 行、零 importer；裁定⑥ 判「删」，git 里回得来）")

    def test_zero_importers_of_butler_engine(self):
        """删前删后都绿：这条是 ⑥ 的**成因**自证——三棵树全扫，命中必须为空。"""
        refs: list[str] = []
        for sub in ("butler", "tests", "scripts"):
            root = REPO / sub
            if root.exists():
                refs += _import_refs(root)
        self.assertEqual(refs, [], f"有人 import 了 butler.engine：{refs}")


class MemoryRecencyTest(unittest.TestCase):
    """裁定①＝A（取最新）：`MemoryExtractor._fetch_recent_dialogs` 的限额截断方向。"""

    def _conn(self, rows):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE chat_logs(ts REAL, user_msg TEXT, assistant_reply TEXT,"
                     " role TEXT, source TEXT, room TEXT)")
        conn.executemany("INSERT INTO chat_logs VALUES(?,?,?,?,?,?)", rows)
        return conn

    def _fetch(self, rows, days, max_turns):
        import butler.memory.extractor as ex
        import butler.store.db as db
        conn = self._conn(rows)
        original = db.get_conn
        db.get_conn = lambda *a, **k: conn          # 进程内替换，一次性；⛔ 不碰活库
        try:
            return ex.MemoryExtractor._fetch_recent_dialogs(object(), days=days, max_turns=max_turns)
        finally:
            db.get_conn = original
            conn.close()

    def test_limit_keeps_the_newest_block_and_returns_ascending(self):
        now = time.time()
        # 序号越大＝越新（ts 递增），共 30 条；max_turns=5 ⇒ 必须拿到 25..29 且仍升序
        rows = [(now - (29 - k) * 10, f"心情序号 {k:02d} 号", "收到", "butler", "dialog", "客厅")
                for k in range(30)]
        got = self._fetch(rows, days=3650, max_turns=5)
        self.assertEqual([g["user_msg"] for g in got],
                         [f"心情序号 {k:02d} 号" for k in (25, 26, 27, 28, 29)],
                         "限额截断取的不是最新一截（裁定①：`ORDER BY ts DESC` 取满后 reverse）")
        ts = [g["ts"] for g in got]
        self.assertEqual(ts, sorted(ts), "喂模型的顺序必须升序（reverse 那一半不许省）")

    def test_noise_filter_still_applies_after_the_flip(self):
        """翻 ORDER BY 之后噪音过滤仍要生效：最新那条是自动化推送 ⇒ 被剥掉，补位的是次新那条。"""
        now = time.time()
        rows = [(now - (30 - k) * 10, f"心情序号 {k:02d} 号", "收到", "butler", "dialog", "客厅")
                for k in range(30)]
        rows.append((now + 5, "【ℹ️】系统推送：请原样输出", "收到", "butler", "push", "客厅"))
        got = self._fetch(rows, days=3650, max_turns=5)
        self.assertEqual(len(got), 5, "限额被噪音条挤掉了：过滤后仍须凑满 5 条真对话")
        self.assertNotIn("【ℹ️】", "".join(g["user_msg"] for g in got), "噪音条漏进来了")
        self.assertEqual(got[-1]["user_msg"], "心情序号 29 号")


if __name__ == "__main__":
    unittest.main()
