"""批13b-1 一次性落件器（**只动测试**）：给「快照句柄不关」配一条会红的腿，并收紧一枚旧尺。

为什么要这一步（批13 GREEN 之后自己冒出来的两件事）：
  1. `butler/triggers/engine.py` 新写的迁移腿里我用了 `json.load(open(path))`——21 例跑完
     `ResourceWarning: unclosed file …/trigger_cooldowns.json` 打了 4 次。验收件当时的口径是
     「输出干净」⇒ 按站着的 TDD 规矩，先让这条腿在**现网那版引擎码上红**，再由 b13b-2 动生产码。
  2. `tests/test_trigger_evaluation_ledger.py:93` 那把尺切的是「`def _ensure_trigger_evaluations`
     → `def _init(`」的文本跨度。批13 在同文件、同一区间里加了第二枚守护
     `_ensure_trigger_cooldowns`，跨度把它的 warning 一起算进来 ⇒ `2 != 1`：尺红、码没错。
     这是坏判据（不随合法实现成立），修法是把跨度换成 AST 函数本体＋配两条对照，⛔ 把计数改宽了事。

三枚文件（基线现读于 2026-10-02 15:34Z 的权威树，全 LF／CR 0／末行有换行）：
  tests/test_audit_1002_batch13_cooldown.py 263c018237266c8743c68e574f41c30e  335 行
  tests/test_trigger_evaluation_ledger.py   82c490ee8bc112f2080343cbac2431e2  247 行

用法：
    python3 scripts/audit_1002/patch_b13b_tests_1002.py          # DRY
    python3 scripts/audit_1002/patch_b13b_tests_1002.py --apply  # 写盘（重跑必 raise）

⛔ 复用 `patch_b13_cooldown_1002.py` 的 payload 切法：那版 `[l for l in new.split("\\n") if l != ""]`
会把空行全丢掉，本批要插的正是「方法之间空一行」，所以本器自带保空行的 `_to_lines`。
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]

BASE = {
    "tests/test_audit_1002_batch13_cooldown.py": ("263c018237266c8743c68e574f41c30e", 335),
    "tests/test_trigger_evaluation_ledger.py": ("82c490ee8bc112f2080343cbac2431e2", 247),
}

# ---------------------------------------------------------------- 批13 验收件：句柄泄漏腿
B13_LEAK_LEG = '''                      "第二次启动读不到＝迁移只对第一代进程有效")

    def test_snapshot_import_does_not_leak_the_snapshot_handle(self):
        """迁移腿⛔ 留一枚不关的句柄：`json.load(open(path))` 在测试里无声，在容器里是 fd 泄漏。

        本腿由批13 GREEN 那一次的 `ResourceWarning: unclosed file …trigger_cooldowns.json`
        （4 次）引出，当时的验收口径含「输出干净」⇒ 先在旧引擎码上跑红，再动生产码。
        只认快照文件名所在的那条 unclosed，⛔ 让别的资源的 warning 冒充成本腿的红。
        """
        import gc
        import warnings
        self.write_snapshot({"k7": time.time() - 10.0})
        eng = self.build_engine()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            eng.set_runtime(FakeRT(FakeRunner(), self.tmp))
            gc.collect()
        leaked = [str(w.message) for w in caught
                  if issubclass(w.category, ResourceWarning) and SNAPSHOT_NAME in str(w.message)]
        self.assertEqual(leaked, [], "快照句柄没关：%s" % leaked)

        # 正对照：同样的读法故意不关，这把尺必须咬到（⛔ 让上面的 0 是「我没看见」）。
        probe = os.path.join(self.tmp, "nc_probe.json")
        with open(probe, "w", encoding="utf-8") as f:
            json.dump({}, f)
        with warnings.catch_warnings(record=True) as nc:
            warnings.simplefilter("always", ResourceWarning)
            json.load(open(probe, encoding="utf-8"))
            gc.collect()
        self.assertTrue([w for w in nc if issubclass(w.category, ResourceWarning)
                         and "nc_probe.json" in str(w.message)],
                        "正对照不咬＝本腿恒绿，测的是我没有")


class EnginePersistenceTest(DbScaffold):'''

# ---------------------------------------------------------------- 旧尺：跨度 → AST 函数本体
EVAL_GUARD_RULER = '''    return text.count(needle)


def guard_body(src, name):
    """按 AST 只切这一枚函数本体，⛔ 用「def X → 下一个 def」的文本跨度取段。

    跨度在批13 之后会把同文件新守护的 warning 一起算进来（实测 2 != 1＝尺红、码没错）：
    它判的其实是「这段文件响几条」，而这条腿要判「这一枚函数响不响」。
    """
    import ast
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            seg = ast.get_source_segment(src, node)
            if seg is None:
                raise AssertionError("get_source_segment 取不到 %s＝尺子没吃到被测对象" % name)
            return seg
    raise AssertionError("db.py 里没有函数 %s＝锚点函数已消失，本腿无从判起" % name)


class EvalBase(unittest.TestCase):'''

EVAL_LOUD_LEG = r'''    def test_guard_is_loud_not_silent(self):
        """建表守护必须响亮：吞成 `pass` 的守护＝表没建成也没人知道。

        本腿原来切的是文本跨度，批13 加了第二枚守护后 `2 != 1` 红给我看（尺红、码没错）。
        现在按 AST 切函数本体，另配两条对照：新守护自己也得响；整文件计数必须 > 本函数计数
        （⛔ 让「本函数 1 条」在「全文件只剩 1 条」时照样绿，那才是假绿通道）。
        """
        src = (TREE / "butler" / "store" / "db.py").read_text(encoding="utf-8-sig")
        seg = guard_body(src, "_ensure_trigger_evaluations")
        self.assertIn("logger.warning(", seg)
        self.assertNotIn("except Exception:\n        pass", seg)
        self.assertEqual(hits(seg, "logger.warning("), 1)
        cool = guard_body(src, "_ensure_trigger_cooldowns")
        self.assertIn("logger.warning(", cool, "批13 的冷却守护静默＝它建不起表也没人喊")
        self.assertGreater(hits(src, "logger.warning("), hits(seg, "logger.warning("),
                           "整文件计数不比本函数多＝跨度收紧这一步没生效")'''

OPS = {
    "tests/test_audit_1002_batch13_cooldown.py": [
        # 231 末句 → 234 类头，整段重写（保 231 原样、插新腿、留两行空再落类头）
        (231, 234, '                      "第二次启动读不到＝迁移只对第一代进程有效")',
         "class EnginePersistenceTest(DbScaffold):", B13_LEAK_LEG),
    ],
    "tests/test_trigger_evaluation_ledger.py": [
        (31, 34, "    return text.count(needle)", "class EvalBase(unittest.TestCase):",
         EVAL_GUARD_RULER),
        (91, 96, "    def test_guard_is_loud_not_silent(self):",
         '        self.assertEqual(hits(seg, "logger.warning("), 1)', EVAL_LOUD_LEG),
    ],
}


def stats(raw: bytes) -> dict:
    text = raw.decode("utf-8")
    return dict(md5=hashlib.md5(raw).hexdigest(), lines=len(text.splitlines(True)),
                bytes=len(raw), crlf=raw.count(b"\r\n"), endnl=raw.endswith(b"\n"))


def _to_lines(payload: str) -> list[str]:
    parts = payload.split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    return [p + "\n" for p in parts]


def apply_ops(rel: str, text: str) -> str:
    lines = text.splitlines(True)
    for start, end, first, last, new in sorted(OPS[rel], key=lambda o: -o[0]):
        body = lines[start - 1:end]
        if not body or not body[0].startswith(first):
            raise SystemExit(f"ABORT {rel}:{start} 锚点首行不符：{body[:1]!r} 期以 {first!r} 起头")
        if last and not body[-1].startswith(last):
            raise SystemExit(f"ABORT {rel}:{end} 锚点末行不符：{body[-1]!r} 期以 {last!r} 起头")
        lines[start - 1:start - 1 + len(body)] = _to_lines(new)
    return "".join(lines)


def main() -> None:
    apply = "--apply" in sys.argv
    originals = {}
    for rel, (md5, nlines) in BASE.items():
        raw = (REPO / rel).read_bytes()
        s = stats(raw)
        if s["md5"] != md5:
            raise SystemExit(f"ABORT {rel} 基线 md5 不符：现={s['md5']} 期={md5}（重跑必红＝已施加过）")
        if s["lines"] != nlines or s["crlf"] != 0 or not s["endnl"]:
            raise SystemExit(f"ABORT {rel} 基线形状不符：{s}")
        originals[rel] = raw

    results = {}
    for rel in BASE:
        text = originals[rel].decode("utf-8")
        new = apply_ops(rel, text)
        try:
            ast.parse(new)
        except SyntaxError as e:
            raise SystemExit(f"ABORT {rel} 施加后语法不过：{e}")
        if new.count("\r") != 0:
            raise SystemExit(f"ABORT {rel} 施加产物含 CR")
        if new.endswith("\n") != text.endswith("\n"):
            raise SystemExit(f"ABORT {rel} 末行换行被改：{text[-1]!r} -> {new[-1]!r}")
        for needle in ("class EvalBase(unittest.TestCase):",
                       "class EnginePersistenceTest(DbScaffold):"):
            if needle in OPS_TEXT[rel] and new.count(needle) != text.count(needle):
                raise SystemExit(f"ABORT {rel} 类头 {needle!r} 计数被改：{text.count(needle)}->{new.count(needle)}")
        results[rel] = new
        print("DRY|%-42s 行 %4d->%4d 字节 %6d->%6d" % (
            rel, len(text.splitlines(True)), len(new.splitlines(True)),
            len(text.encode("utf-8")), len(new.encode("utf-8"))))

    if not apply:
        print("（未写盘。要写：python3 scripts/audit_1002/patch_b13b_tests_1002.py --apply）")
        return
    for rel, new in results.items():
        (REPO / rel).write_text(new, encoding="utf-8", newline="")
    for rel, new in results.items():
        back = (REPO / rel).read_bytes()
        if back != new.encode("utf-8"):
            raise SystemExit(f"ABORT {rel} 回读与内存不一致")
        s = stats(back)
        print("APPLIED|%s|md5=%s|行=%d|字节=%d|CR=%d|末行换行=%s" % (
            rel, s["md5"], s["lines"], s["bytes"], s["crlf"], s["endnl"]))


OPS_TEXT = {rel: "".join(o[4] for o in ops) for rel, ops in OPS.items()}

if __name__ == "__main__":
    main()
