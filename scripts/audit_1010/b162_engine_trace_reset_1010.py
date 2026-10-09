#!/usr/bin/env python3
"""批151 装机腿：`butler/triggers/engine.py` 的 trace「生完即还」。

三段替换各带锚点计数断言（每段必须恰好命中 1 次），任何一段不对就整件不落盘、rc=1。
字节级读写（rb/wb）：⛔ 让文本模式或 patch 工具把 LF 目标抹成 CRLF（09-21 R10 那条反过来的坑）。

用法：
  python3 scripts/audit_1010/b162_engine_trace_reset_1010.py            # 干跑：只报三段命中数
  python3 scripts/audit_1010/b162_engine_trace_reset_1010.py --apply     # 落盘 + compile 复检
"""
import pathlib
import py_compile
import sys

TREE = pathlib.Path(__file__).resolve().parents[2]
TARGET = TREE / "butler" / "triggers" / "engine.py"

IMPORT_OLD = "from butler.logging_setup import get_logger\n"
IMPORT_NEW = ("from butler.core.trace_ctx import current_trace_id\n"
              "from butler.logging_setup import get_logger\n")

ENTRY_OLD = '''    async def handle_event(self, event_type: str, payload: dict | None = None,
                           dry_run: bool = False) -> list[dict]:
        """处理一个事件，返回匹配并执行的 trigger 结果列表。

        payload 统一字段：event, member, room, confidence, timestamp, 及事件特有字段。

        dry_run=True（DCD 裁定 C）＝只评估不执行：闸门照过、动作不跑、冷却与熔断不写、
        事件防抖窗口不占、trigger_runs 执行流水不落。默认 False＝既有行为一字不变。
        """
        payload = dict(payload or {})
        payload.setdefault("event", event_type)
        payload.setdefault("timestamp", datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))

        # v2.6: 事件入口写入 trace 上下文，trigger 驱动的技能/发声与上游同链
        trace_id = self._trace_id()
        if not trace_id:
            trace_id = uuid.uuid4().hex[:16]
            try:
                from butler.core.agent import _current_trace_id
                _current_trace_id.set(trace_id)
            except Exception:
                pass

'''

ENTRY_NEW = '''    async def handle_event(self, event_type: str, payload: dict | None = None,
                           dry_run: bool = False) -> list[dict]:
        """处理一个事件，返回匹配并执行的 trigger 结果列表。

        payload 统一字段：event, member, room, confidence, timestamp, 及事件特有字段。

        dry_run=True（DCD 裁定 C）＝只评估不执行：闸门照过、动作不跑、冷却与熔断不写、
        事件防抖窗口不占、trigger_runs 执行流水不落。默认 False＝既有行为一字不变。

        批151：trace 号在事件边界上生、在边界上还。事件流跑在长驻 task 上
        （`event_stream` / `xiaomi_ear` 的队列循环），set 而不 reset 会把上一条事件的号
        发给下一条——现网 4 次 face_detected 共用 1 个号就是这条。
        """
        inherited = self._trace_id()
        trace_id = inherited or uuid.uuid4().hex[:16]
        token = None if inherited else current_trace_id.set(trace_id)
        try:
            return await self._handle_event_inner(event_type, payload, dry_run, trace_id)
        finally:
            if token is not None:
                current_trace_id.reset(token)

    async def _handle_event_inner(self, event_type: str, payload: dict | None,
                                  dry_run: bool, trace_id: str) -> list[dict]:
        payload = dict(payload or {})
        payload.setdefault("event", event_type)
        payload.setdefault("timestamp", datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))

'''

READER_OLD = '''    @staticmethod
    def _trace_id() -> str:
        """当前链路 trace_id；未设返回空串，不抛。"""
        try:
            from butler.core.agent import _current_trace_id
            return _current_trace_id.get() or ""
        except Exception:
            return ""
'''

READER_NEW = '''    @staticmethod
    def _trace_id() -> str:
        """当前链路 trace_id；未设返回空串，不抛。

        批151：改读叶子 `butler.core.trace_ctx`（与 `agent` 里那枚是同一个对象）。
        原先走 `butler.core.agent` 的 import 在宿主（无 homesdk）会抛，而外面那圈
        `except Exception: pass` 把它静静吃掉＝整条 trace 腿空转、读数看着却像跑过了。
        """
        return current_trace_id.get() or ""
'''

EDITS = (("import 腿", IMPORT_OLD, IMPORT_NEW),
         ("事件入口", ENTRY_OLD, ENTRY_NEW),
         ("读取腿", READER_OLD, READER_NEW))


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    raw = TARGET.read_bytes()
    cr = raw.count(b"\r\n")
    src = raw.decode("utf-8")
    print("target=%s bytes=%d lines=%d CRLF=%d" %
          (TARGET.name, len(raw), src.count("\n"), cr))
    counts = []
    for name, old, _new in EDITS:
        counts.append((name, src.count(old)))
    for name, n in counts:
        print("anchor %-12s hits=%d" % (name, n))
    bad = [name for name, n in counts if n != 1]
    if bad:
        print("RED anchors_not_unique=%s — nothing written" % ",".join(bad))
        return 1
    if not apply:
        print("DRYRUN ok=3/3 — pass --apply to land")
        return 0

    out = src
    for _name, old, new in EDITS:
        out = out.replace(old, new, 1)
    data = out.encode("utf-8")
    assert data.count(b"\r\n") == cr, "换行形制被改动"
    TARGET.write_bytes(data)
    print("APPLIED bytes=%d lines=%d CRLF=%d" % (len(data), out.count("\n"), cr))
    py_compile.compile(str(TARGET), doraise=True)
    print("COMPILE PASS")
    for probe in ("current_trace_id.set(trace_id)",
                  "current_trace_id.reset(token)",
                  "_handle_event_inner",
                  "return current_trace_id.get() or \"\""):
        print("probe %-42s hits=%d" % (probe, out.count(probe)))
    for gone in ("from butler.core.agent import _current_trace_id",):
        print("absent %-42s hits=%d" % (gone, out.count(gone)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
