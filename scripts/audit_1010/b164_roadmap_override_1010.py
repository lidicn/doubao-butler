"""批151 文书腿：roadmap §五 加注块里插一条「上一条口径本条覆盖」。

锚点计数断言＝1 才落盘；字节读写，⛔ 文本模式改行尾形制。干跑打印锚点/CR/md5。
"""
import hashlib
import pathlib
import sys

TREE = pathlib.Path(__file__).resolve().parents[2]
DOC = TREE / "doc" / "00-路线图" / "豆包管家版本开发路线图_v2.5_DCD版.md"

ANCHOR = "> - **本行⛔ 就此闭合**：`skill` 段的 trace 归属（#3 触发日志页要用它）还没落"

NEW_LINE = ("> - **上一条的口径由本条覆盖（2026-10-10，批151）**：那句「`skill` 段的 trace "
            "归属还没落」不准，现读为已落——`butler/skills/runner.py` 的 `_persist_run` "
            "从 contextvar 取 trace 随 `repo.add_skill_run` 入库，"
            "`butler/api/trigger_routes.py` 的 `trigger_logs`（docstring 原文："
            "「v2.6#3 技能触发日志页数据接口：trigger/skill/action/trace_id 一次拉平」）"
            "就是 #3 那张页的数据接口。真正没落的是**号的生命周期**："
            "`TriggerEngine.handle_event` 只 `set` 不 `reset`，而事件流跑在长驻 task 上"
            "（`event_stream` / `xiaomi_ear` 的队列循环、`app.py` 的 heartbeat/scheduled 也走它）"
            "⇒ 第一条事件的号发给其后每一条：88h 冻结样本里 `face_detected` 4 次共用 1 枚号，"
            "同样本 heartbeat 525 次 525 枚、scheduled 8 次 8 枚各自唯一 ⇒ 只有 trigger 链会串。"
            "批151 补的就是这条生命周期：号在事件边界上生、`finally` 还，继承到号就沿用不还；"
            "尺子 `tests/test_trigger_trace_reset_1010.py`（6 格：容器内拿 HEAD 旧件复现 3 红，"
            "新件在容器/宿主/镜像三面全绿，其中机理守卫那格保证其余几格不是空转的绿色）。"
            "**本行⛔ 就此闭合**：装机窗收口尺在批150 那两格外再加一格——同一 event 相邻两次触发，"
            "` trace=` 必须不同；⛔ 拿「码已落盘」当「已生效」（`.py` 要 restart 才装载）。"
            "`runner._persist_run` 那圈 `from butler.core.agent import` 的脆弱腿本批⛔ 一起改："
            "它在现网容器可用、无泄漏读数，只登记（台账 §124）。\n")


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    raw = DOC.read_bytes()
    src = raw.decode("utf-8")
    cr = raw.count(b"\r\n")
    hits = src.count(ANCHOR)
    print("doc=%s bytes=%d lines=%d CRLF=%d anchor_hits=%d already=%d"
          % (DOC.name, len(raw), src.count("\n"), cr, hits,
             src.count("上一条的口径由本条覆盖")))
    if hits != 1:
        print("RED anchor — nothing written")
        return 1
    if src.count("上一条的口径由本条覆盖"):
        print("RED 覆盖条已存在，⛔ 重复追加")
        return 1
    if not apply:
        print("DRYRUN ok — pass --apply to land")
        return 0
    idx = src.index(ANCHOR)
    end = src.index("\n", idx) + 1
    out = src[:end] + NEW_LINE + src[end:]
    data = out.encode("utf-8")
    assert data.count(b"\r\n") == cr, "行尾形制被改动"
    DOC.write_bytes(data)
    print("APPLIED bytes=%d lines=%d CRLF=%d md5=%s"
          % (len(data), out.count("\n"), cr, hashlib.md5(data).hexdigest()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
