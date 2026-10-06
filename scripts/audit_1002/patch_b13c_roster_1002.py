"""批13c 一次性落件器（**只动尺子/在册件，不动生产码**）：把批13 挪动的两处在册坐标刷新，并把三枚新台账接入点登记进去。

起因＝批13 落码后全量 discover 的三枚红灯（读数件 `workorders/readings/1002b13/discover_after_b13b.txt`）：

1. `tests/test_audit_1002_batch6_gate.py::ProductionRosterTest`（两枚）
   批6 的在册 30 枚按 `(文件, 行号, 末段名)` 钉死，行号漂了尺子自己报 DRIFT＝设计如此。
   批13 在 `butler/triggers/engine.py` 的第 58-125 行区间收口冷却，之后的行整体 +37：
     · 旧（`git show HEAD:…` → `/tmp/old_engine_b13c.py`，md5 8ad01292、493 行）
       `:175 write_failures.record(site="triggers/engine.add_trigger_evaluation")`
       `:434 write_failures.record(site="triggers/engine.add_trigger_run")`
     · 新（活树 AST 现读，530 行）`:212 add_trigger_evaluation`、`:471 add_trigger_run`
   ⇒ 这是**同两枚**调用点换了坐标，⛔ 新增缺陷。在册件按现读刷新，DRIFT 报警本体留着（下次再动行号还是会红）。

2. `tests/test_write_failures.py::WiringSourceTest`
   它的 `SITES` 是台账接入点的在册名单，判据是 `set(源码扫到的) == set(在册)`（双向相等）。
   批13 新加了三枚真接入点（`load_cooldowns` / `save_cooldowns` / `import_legacy_snapshot`，
   全部在各自的 `except` 里、首参是常量），扫得到而册上没有 ⇒ 红。补进册，
   并把「四条腿 / all_four_sites」这类**已经变成假的**计数措辞改成不数数的说法
   （判例：文书在装作在做——名字承诺四、盘上是八，比不写更坏）。仓内除本文件外 0 处引用旧腿名
   （`grep -rn all_four_sites` 只命中定义行），改名不留死链。

两枚文件（基线现读于 2026-10-02 15:46Z 的权威树，全 LF／CR 0／末行有换行）：
  scripts/audit_1002/classify_unawaited_1002.py b4bcd472db71e0299d6679d8f2cfed66  672 行
  tests/test_write_failures.py                  f28bed6ef115f3523a59f7b22e0dfd42  224 行

用法：
    python3 scripts/audit_1002/patch_b13c_roster_1002.py          # DRY
    python3 scripts/audit_1002/patch_b13c_roster_1002.py --apply  # 写盘（重跑必 raise）
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]

BASE = {
    "scripts/audit_1002/classify_unawaited_1002.py": ("b4bcd472db71e0299d6679d8f2cfed66", 672),
    "tests/test_write_failures.py": ("f28bed6ef115f3523a59f7b22e0dfd42", 224),
}

OPS = {
    "scripts/audit_1002/classify_unawaited_1002.py": [
        (84, 84, '    ("triggers/engine.py", 434, "record"),', None,
         '    ("triggers/engine.py", 471, "record"),   # 批13 冷却收口把 add_trigger_run 从 434 推到 471（旧码 `git show`→/tmp AST 现读）'),
        (83, 83, '    ("triggers/engine.py", 175, "record"),', None,
         '    ("triggers/engine.py", 212, "record"),   # 同上：add_trigger_evaluation 175→212（整段 +37，⛔ 手改名单＝按 AST 现读刷新）'),
    ],
    "tests/test_write_failures.py": [
        (205, 205, "    def test_all_four_sites_call_record_inside_their_except(self):", None,
         "    def test_every_registered_site_calls_record_inside_its_except(self):"),
        (184, 184, '    """四条腿的源码形状：record() 必须真的落在同一个 except 里，⛔ 只在散文里承诺。"""',
         None,
         '    """在册接入点的源码形状：record() 必须落在**自己那条** except 里，⛔ 只在散文里承诺。\n'
         '\n'
         '    批13（触发冷却进 SQLite）新增三枚接入点，本册按现读补齐：⛔ 让册子停在旧数上，\n'
         '    那等于用一条恒等式判据掩盖真实的写入面。\n'
         '    """'),
        (33, 33, '    "triggers/engine.add_trigger_evaluation": "butler/triggers/engine.py",', None,
         '    "triggers/engine.add_trigger_evaluation": "butler/triggers/engine.py",\n'
         '    "triggers/engine.load_cooldowns": "butler/triggers/engine.py",\n'
         '    "triggers/engine.save_cooldowns": "butler/triggers/engine.py",\n'
         '    "triggers/engine.import_legacy_snapshot": "butler/triggers/engine.py",'),
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
            raise SystemExit(f"ABORT {rel}:{end} 锚点末行不符：{body[-1]!r}")
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
            raise SystemExit(f"ABORT {rel} 末行换行被改")
        if "all_four_sites" in new or "四条腿" in new:
            raise SystemExit(f"ABORT {rel} 旧计数措辞还留着")
        if rel.endswith("classify_unawaited_1002.py"):
            for want in ('("triggers/engine.py", 212, "record")',
                         '("triggers/engine.py", 471, "record")'):
                if new.count(want) != 1:
                    raise SystemExit(f"ABORT {rel} 在册坐标未落成 1 处：{want}")
            if new.count('("triggers/engine.py", 175') or new.count('("triggers/engine.py", 434'):
                raise SystemExit(f"ABORT {rel} 旧坐标（175/434）还在册")
        else:
            for want in ('"triggers/engine.load_cooldowns"', '"triggers/engine.save_cooldowns"',
                         '"triggers/engine.import_legacy_snapshot"'):
                if new.count(want) != 1:
                    raise SystemExit(f"ABORT {rel} 新接入点未登记成 1 处：{want}")
            if new.count('"butler/triggers/engine.py",') != 5:
                raise SystemExit(f"ABORT {rel} engine.py 的在册条目不是 5 枚："
                                 f"{new.count(chr(34) + 'butler/triggers/engine.py' + chr(34) + ',')}")
        results[rel] = new
        print("DRY|%-46s 行 %4d->%4d 字节 %6d->%6d" % (
            rel, len(text.splitlines(True)), len(new.splitlines(True)),
            len(text.encode("utf-8")), len(new.encode("utf-8"))))

    if not apply:
        print("（未写盘。要写：python3 scripts/audit_1002/patch_b13c_roster_1002.py --apply）")
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


if __name__ == "__main__":
    main()
