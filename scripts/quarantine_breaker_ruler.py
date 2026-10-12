"""格3②＋格5 验收尺（2026-09-30，不依赖 pytest）。

腿：
  A 行为——真函数 `butler.triggers.engine._only_terminal` 跑 6 格（含必须咬住的负控）。
  B 结构——AST 证明 `_fail_count` 自增只在 `_only_terminal(...)` 那条 elif 的 **else** 里，
    elif 本体不碰计数、不写退避（⛔ 靠我读代码的印象）。
  C 结构——`butler/integrations/bark.py` 的 push_guard 异常腿：级别已升 warning，
    且 handler 本体只有打日志（⛔ return/raise）⇒ 裁定要的 fail-open 没被我改成 fail-close。
  D 现网日志面⛔ 在这把尺里：代码是「改盘上-未生效」，端到端要等与 R1 同批的那一次重启。
"""
from __future__ import annotations

import ast
import os
import sys

ROOT = os.environ.get("BUTLER_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from butler.triggers.engine import _only_terminal  # noqa: E402

fails = []
cases = {}


def case(name, fn):
    cases[name] = 0
    try:
        fn()
    except AssertionError as exc:
        fails.append("%s: %s" % (name, exc))
    except Exception as exc:  # noqa: BLE001
        fails.append("%s: unexpected %s: %s" % (name, type(exc).__name__, exc))
    else:
        cases[name] = 1


def eq(cond, want, msg):
    assert cond is want or bool(cond) == want, "%s got %r" % (msg, cond)


# ---------- A ----------
def a1_quarantined_only_is_terminal():
    eq(_only_terminal([{"ok": False, "status": "quarantined"}]), True, "隔离态单独失败")


def a2_mixed_success_and_quarantined():
    eq(_only_terminal([{"ok": True, "status": None}, {"ok": False, "status": "quarantined"}]),
       True, "成功＋隔离")


def a3_real_failure_is_not_terminal():
    eq(_only_terminal([{"ok": False, "error": "timeout"}]), False, "真失败被当成终态＝熔断从此失效")


def a4_quarantined_plus_real_failure():
    eq(_only_terminal([{"ok": False, "status": "quarantined"}, {"ok": False, "error": "boom"}]),
       False, "混着真失败还必须豁免＝把真故障藏起来")


def a5_nothing_failed():
    eq(_only_terminal([{"ok": True}]), False, "全成功不该走豁免分支")


def a6_empty_results():
    eq(_only_terminal([]), False, "空动作集不该走豁免分支（维持旧行为：计入失败）")


for n in ["a1_quarantined_only_is_terminal", "a2_mixed_success_and_quarantined",
          "a3_real_failure_is_not_terminal", "a4_quarantined_plus_real_failure",
          "a5_nothing_failed", "a6_empty_results"]:
    case(n, globals()[n])

# ---------- B / C ----------
INC_MARK = "_fail_count"


def assignments_names(body):
    """整条赋值语句的源码（含右值）——只取目标名的话 `+ 1` 永远匹配不到。"""
    out = []
    tree = ast.Module(body=list(body), type_ignores=[])
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            out.append(ast.unparse(node))
    return out


def b_gate4_structure():
    src = open(os.path.join(ROOT, "butler/triggers/engine.py"), encoding="utf-8-sig").read()
    tree = ast.parse(src)
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Call)
                and isinstance(node.test.func, ast.Name) and node.test.func.id == "_only_terminal"):
            found.append(node)
    assert len(found) == 1, "elif 锚点数量 %d（应恰好 1）" % len(found)
    node = found[0]
    elif_assigns = assignments_names(node.body)
    assert not any(INC_MARK in a for a in elif_assigns), \
        "elif 本体还在动熔断计数：%s" % [a for a in elif_assigns if INC_MARK in a]
    else_assigns = assignments_names(node.orelse)
    assert any(INC_MARK in a and "+ 1" in a for a in else_assigns), \
        "else 分支里找不到失败计数自增 ⇒ 真失败的熔断被一起删了：%s" % else_assigns
    warns = [ast.unparse(x) for x in node.body if isinstance(x, ast.Expr)]
    assert any("warning" in w for w in warns), "elif 分支没打 WARNING（裁定要可见）"
    assert src.count('logger.error("trigger %s action FAILED') == 1, \
        "真失败的 ERROR 条数变了（格3②只该降隔离态那一条）"
    print("STRUCT elif_body=%d stmts(计数字段 0) else_has_increment=True error_legs=1"
          % len(node.body))


def c_bark_fail_open():
    src = open(os.path.join(ROOT, "butler/integrations/bark.py"), encoding="utf-8-sig").read()
    tree = ast.parse(src)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            blob = ast.unparse(node)
            if "push_guard check failed" in blob:
                hits.append(node)
    assert len(hits) == 1, "push_guard 异常腿数量 %d（应恰好 1）" % len(hits)
    blob = ast.unparse(hits[0])
    assert "logger.warning" in blob, "还是 debug/info ⇒ 格5 没落地：%s" % blob[:80]
    assert "logger.debug" not in blob, "debug 与 warning 同时存在＝改了个新腿"
    assert "return" not in blob and "raise" not in blob, \
        "异常腿里出现了 return/raise ⇒ fail-open 被我改成 fail-close：%s" % blob
    print("BARK handler=%s" % blob.replace("\n", " ")[:150])


def b0_ruler_bites_on_mutant():
    """变异腿：把「elif 里也动计数」写成夹具，本尺必须能咬——否则上面那条 PASS 零信息。"""
    mutant = ("def f(_only_terminal, action_results, self, cd_key):\n"
              "    if x:\n        pass\n"
              "    elif _only_terminal(action_results):\n"
              "        self._fail_count[cd_key] = self._fail_count.get(cd_key, 0) + 1\n"
              "    else:\n        y = 1\n")
    node = None
    for n in ast.walk(ast.parse(mutant)):
        if (isinstance(n, ast.If) and isinstance(n.test, ast.Call)
                and isinstance(n.test.func, ast.Name) and n.test.func.id == "_only_terminal"):
            node = n
    assert node is not None, "夹具里没构造出 elif 锚点"
    bad = [a for a in assignments_names(node.body) if INC_MARK in a]
    assert bad, "夹具把自增放进 elif，本尺的抽取逻辑却没看见 ⇒ 判据是瞎的"
    print("MUTANT bites=%s" % (len(bad),))


case("b0_ruler_bites_on_mutant", b0_ruler_bites_on_mutant)
case("b_gate4_structure", b_gate4_structure)
case("c_bark_fail_open", c_bark_fail_open)

print("CASES_RUN=%d" % len(cases))
print("CASES_PASS=%d" % sum(cases.values()))
print("FAIL=%d" % len(fails))
for f in fails:
    print("  FAIL %s" % f)
print("VERDICT=%s" % ("GREEN" if not fails else "RED"))
sys.exit(1 if fails else 0)
