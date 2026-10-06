"""批14 一次性落码器：`engine.py` 的 `status()` 读冷却改成与 fire 侧同形制的键聚合。

用法（在权威树根跑）：
    python3 -B scripts/audit_1002/patch_b14_status_key_1002.py          # DRY
    python3 -B scripts/audit_1002/patch_b14_status_key_1002.py --apply  # 落一次
    再跑第二遍必 raise ALREADY_APPLIED。

闸门都是现读的：基底 md5/行/字节/CR、锚点唯一性、AST 可解析、函数数不变（本批不产新函数）、
旧形状清零、新块唯一、换行符血统、末行换行。任一不过＝不落盘。
"""
import ast
import hashlib
import io
import os
import sys

TARGET = "butler/triggers/engine.py"
BASE_MD5 = "307424a3555ecffb49379dfd0973b9b7"
BASE_LINES = 533
BASE_BYTES = 26960
BASE_FUNCS = 17
BASE_CR = 0

OLD = '            last = self._last_fired.get(trig["id"], 0)\n'
NEW = (
    "            # 读侧键形制必须＝写侧（`:244`/`:358`：`id` 或 `id:member`）。裸 id 只命中\n"
    '            # 无 member 的事件，带 member 的触发器会恒报「没在冷却」，而同一秒 `_match()`\n'
    "            # 真的在拦它＝面板绿、门红（台账 §14-6）。多枚 member 键取最新＝最保守：\n"
    "            # 宁可说「还在冷却」，⛔ 对刚火过的那位谎报「随时可再火」。\n"
    '            tid = trig["id"]\n'
    "            last = max([v for k, v in self._last_fired.items()\n"
    '                        if k == tid or k.startswith(tid + ":")] or [0])\n'
)
NEW_FIRST = "            tid = trig[\"id\"]\n"
LINE_DELTA = NEW.count("\n") - OLD.count("\n")


def sha(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def funcs(text):
    return sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               for n in ast.walk(ast.parse(text)))


def main():
    apply = "--apply" in sys.argv[1:]
    if not os.path.exists(TARGET):
        raise SystemExit("NO_TARGET|%s" % os.getcwd())
    raw = open(TARGET, "rb").read()
    cr = raw.count(b"\r")
    if cr != BASE_CR:
        raise SystemExit("BASELINE_CR|%d(期望%d)——先回权威树重读换行血统，⛔ 硬改" % (cr, BASE_CR))
    text = raw.decode("utf-8").replace("\r\n", "\n")
    # 一次性证明放在基底校验之前：二次施加时这条先说话（放 md5 之后就是死支，永远轮不到）
    if NEW_FIRST in text:
        raise SystemExit("ALREADY_APPLIED|批14 的 tid 聚合已在盘上，本单收工，⛔ 二次施加")
    if sha(text) != BASE_MD5:
        raise SystemExit("BASELINE_MD5|%s(期望%s)" % (sha(text), BASE_MD5))
    lines = text.splitlines()
    if len(lines) != BASE_LINES or len(raw) != BASE_BYTES:
        raise SystemExit("BASELINE_SHAPE|行=%d(期望%d) 字节=%d(期望%d)"
                         % (len(lines), BASE_LINES, len(raw), BASE_BYTES))
    if text.count(OLD) != 1:
        raise SystemExit("ANCHOR|%d(期望1)" % text.count(OLD))
    before_funcs = funcs(text)
    if before_funcs != BASE_FUNCS:
        raise SystemExit("FUNCS|%d(期望%d)" % (before_funcs, BASE_FUNCS))

    new = text.replace(OLD, NEW, 1)
    if new.count(NEW) != 1:
        raise SystemExit("NEW_NOT_UNIQUE|%d" % new.count(NEW))
    if new.count(OLD) != 0:
        raise SystemExit("OLD_NOT_CLEARED|%d" % new.count(OLD))
    if 'self._last_fired.get(trig["id"]' in new:
        raise SystemExit("BARE_ID_READ_LEFT")
    # 符号引用数守恒：OLD 里 1 枚、NEW 里 1 枚 ⇒ 总数不变；变了就是我在别处动了冷却读写
    if new.count("self._last_fired") != text.count("self._last_fired"):
        raise SystemExit("SYMBOL_USES|%d->%d" % (text.count("self._last_fired"),
                                                 new.count("self._last_fired")))
    after_funcs = funcs(new)
    if after_funcs != before_funcs:
        raise SystemExit("FUNCS_CHANGED|%d->%d（本批不产新函数）" % (before_funcs, after_funcs))
    if new.count("\r"):
        raise SystemExit("CR_INTRODUCED|%d" % new.count("\r"))
    if not new.endswith("\n"):
        raise SystemExit("ENDNL_LOST")
    if len(new.splitlines()) != BASE_LINES + LINE_DELTA:
        raise SystemExit("LINES_AFTER|%d(期望%d)" % (len(new.splitlines()), BASE_LINES + LINE_DELTA))
    # status() 之外的一切⛔ 被本批改动：只有 525 那一枚读法换掉
    for probe in ("def _match", "def _fire", "def _save_cooldowns", "def _load_cooldowns",
                  "def _import_legacy_snapshot", "def set_runtime", "def handle_event"):
        if new.count(probe) != text.count(probe):
            raise SystemExit("COLLATERAL|%s" % probe)

    payload = new.encode("utf-8")
    print("%s |%s 行 %d->%d 字节 %d->%d CR 0->%d md5 %s->%s"
          % ("APPLY" if apply else "DRY", TARGET, len(lines), len(new.splitlines()),
             len(raw), len(payload), new.count("\r"), BASE_MD5, sha(new)))
    if apply:
        with open(TARGET, "wb") as fh:
            fh.write(payload)
        back = open(TARGET, "rb").read()
        if back != payload:
            raise SystemExit("WRITE_VERIFY|%d!=%d" % (len(back), len(payload)))
        print("APPLIED|%s|%s|%d 行|%d 字节"
              % (TARGET, sha(new), len(new.splitlines()), len(payload)))
    else:
        print("DRY_ONLY|未落盘")


main()
