import pathlib
import sys

P = pathlib.Path("butler/core/event_stream.py")
raw = P.read_bytes()
assert raw.count(b"\r\n") == raw.count(b"\n"), "血统不是纯 CRLF，停手"


def blk(text):
    return text.replace("\n", "\r\n").encode("utf-8")


OLD_NEW = """        new_state = data.get("new_state")
        if not new_state:
            return
"""

NEW_NEW = """        new_state = data.get("new_state")
        if not isinstance(new_state, dict):
            # 批165：载荷形状不符契约 ⇒ 这条没法处理，但⛔ 静默 return（丢了要能查到）
            if new_state is not None:
                warn_throttled(logger, "es.new_state_shape",
                               "event_stream new_state 非对象（类型=%s），该帧丢弃: %s",
                               type(new_state).__name__, entity_id, exc=False)
            return
"""

OLD_OLD = """        # 跳过未变化的
        old_state = data.get("old_state") or {}
        old_s = (old_state.get("state") or "").strip()
"""

NEW_OLD = """        # 跳过未变化的
        old_state = data.get("old_state")
        if old_state is None:
            old_s = ""  # HA 首次见到该实体时 old_state 本就是 null ⇒ 算「有变化」
        elif not isinstance(old_state, dict):
            # 批165：投递方把 old_state 打成裸字符串＝现网 277 次/24h 的崩点（traceback 见批165 门件头）。
            # 「证明不了未变化」就当作变化，⛔ 因为载荷形状坏掉就吞掉这一次状态、连带不写 home_events。
            old_s = ""
            warn_throttled(logger, "es.old_state_shape",
                           "event_stream old_state 非对象（类型=%s），按未证明未变化放行该次: %s",
                           type(old_state).__name__, entity_id, exc=False)
        else:
            old_s = (old_state.get("state") or "").strip()
"""

for old, new in ((OLD_NEW, NEW_NEW), (OLD_OLD, NEW_OLD)):
    o, n = blk(old), blk(new)
    cnt = raw.count(o)
    if cnt != 1:
        print("ABORT: 期望 1 处命中，实得 %d（%r）" % (cnt, old[:40]))
        sys.exit(1)
    raw = raw.replace(o, n)

P.write_bytes(raw)
after = P.read_bytes()
print("done CRLF=%d LF=%d" % (after.count(b"\r\n"), after.count(b"\n")))
