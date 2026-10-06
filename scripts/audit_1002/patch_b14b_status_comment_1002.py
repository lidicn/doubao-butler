"""批14b：把批14 注释里「面板绿、门红」这句说过头的话改成现读结论。

现读（权威树，`grep -rn "\.status()" butler/ --include=*.py`）：生产侧只有
`audiobook_routes.py:57`、`tts_routes.py:394/413` 三处 `.status()`，`TriggerEngine.status()`
**零生产调用方**（台账 §14-6 写成「/api/status 恒显示没在冷却」＝我把「供 API/调试」的
注释当成接线事实，§15-2 置顶撤回）。修还是该修（接上它的那一刻就是假话），但⛔ 卖成现网面板事故。
"""
import hashlib
import os
import sys

TARGET = "butler/triggers/engine.py"
BASE_MD5 = "0da5478c51e1e0fcbf386d84c0ea9b21"
BASE_LINES = 539
BASE_BYTES = 27503

OLD = (
    "            # 读侧键形制必须＝写侧（`:244`/`:358`：`id` 或 `id:member`）。裸 id 只命中\n"
    "            # 无 member 的事件，带 member 的触发器会恒报「没在冷却」，而同一秒 `_match()`\n"
    "            # 真的在拦它＝面板绿、门红（台账 §14-6）。多枚 member 键取最新＝最保守：\n"
    "            # 宁可说「还在冷却」，⛔ 对刚火过的那位谎报「随时可再火」。\n"
)
NEW = (
    "            # 读侧键形制必须＝写侧（`:244`/`:358`：`id` 或 `id:member`）。裸 id 只命中无 member\n"
    "            # 的事件⇒带 member 的触发器恒报「没在冷却」，而同一秒 `_match()` 真的在拦它＝显示\n"
    "            # 与执行相反（台账 §14-6；§15-2 现读：本函数零生产调用方，修的是「接上它即拿到假话」）。\n"
    "            # 多枚 member 键取最新＝最保守：宁可说「还在冷却」，⛔ 谎报「随时可再火」。\n"
)


def sha(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def main():
    apply = "--apply" in sys.argv[1:]
    if not os.path.exists(TARGET):
        raise SystemExit("NO_TARGET|%s" % os.getcwd())
    raw = open(TARGET, "rb").read()
    if raw.count(b"\r"):
        raise SystemExit("BASELINE_CR|%d" % raw.count(b"\r"))
    text = raw.decode("utf-8")
    if NEW in text:
        raise SystemExit("ALREADY_APPLIED|批14b 的措辞已在盘上")
    if sha(text) != BASE_MD5:
        raise SystemExit("BASELINE_MD5|%s(期望%s)" % (sha(text), BASE_MD5))
    if len(text.splitlines()) != BASE_LINES or len(raw) != BASE_BYTES:
        raise SystemExit("BASELINE_SHAPE|行=%d 字节=%d" % (len(text.splitlines()), len(raw)))
    if text.count(OLD) != 1:
        raise SystemExit("ANCHOR|%d(期望1)" % text.count(OLD))

    new = text.replace(OLD, NEW, 1)
    if new.count(OLD) != 0 or new.count(NEW) != 1:
        raise SystemExit("SHAPE|%d/%d" % (new.count(OLD), new.count(NEW)))
    if "面板绿" in new:
        raise SystemExit("OVERSTATEMENT_LEFT")
    if len(new.splitlines()) != BASE_LINES:
        raise SystemExit("LINES_AFTER|%d(期望%d，注释改注释不许改行数)" % (len(new.splitlines()), BASE_LINES))
    if not new.endswith("\n") or new.count("\r"):
        raise SystemExit("EOL")

    payload = new.encode("utf-8")
    print("%s |%s 行 %d->%d 字节 %d->%d md5 %s->%s"
          % ("APPLY" if apply else "DRY", TARGET, len(text.splitlines()), len(new.splitlines()),
             len(raw), len(payload), BASE_MD5, sha(new)))
    if apply:
        with open(TARGET, "wb") as fh:
            fh.write(payload)
        if open(TARGET, "rb").read() != payload:
            raise SystemExit("WRITE_VERIFY")
        print("APPLIED|%s|%s|%d 行|%d 字节" % (TARGET, sha(new), len(new.splitlines()), len(payload)))
    else:
        print("DRY_ONLY|未落盘")


main()
