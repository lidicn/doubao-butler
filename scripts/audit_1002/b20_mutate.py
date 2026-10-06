#!/usr/bin/env python3
# 批20 变异尺（跑在容器里的 /tmp/b20_mut 副本树上，⛔ 碰部署树）。
# 目的不是「再证一次绿」，而是证这把尺会咬：每枚变异只改一处真实缺陷形状，
#   KILL 类＝把修好的东西改回坏形状 ⇒ 指名的那几腿必须红（没咬＝尺有洞，逐腿报 HOLE）；
#   CTRL 类＝语义等价的写法改动（换 logger 方法、60.0 改 60、换语句顺序）⇒ 必须继续绿，
#            红了就说明尺在匹配字面量⛔ 匹配行为，那是要登记的尺缺陷。
# 落码前每枚锚点都唯一且能 ast.parse 才写盘；写盘后回读复验 md5/行数/换行血统。
import ast
import hashlib
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent
PRISTINE = ROOT / ".pristine"
TARGETS = ["butler/app.py", "butler/runtime.py", "butler/bus/mqtt_client.py",
           "butler/triggers/registry.py"]


def L(*lines):
    return "\n".join(lines)


# id -> (kind, 文件, [(旧行块, 新行块)], 必须红的腿名片段)
MUTANTS = {
    "M1_mqtt_no_counter": ("KILL", "butler/bus/mqtt_client.py", [
        (L("        self.dropped += 1",
           "        now = time.time()"),
         L("        now = time.time()          # MUT M1：计数动作被摘掉")),
     ], ["test_queue_full_is_counted_by_the_loop_callback"]),

    "M2_mqtt_dead_guard": ("KILL", "butler/bus/mqtt_client.py", [
        (L("        try:",
           "            self.queue.put_nowait((topic, payload))",
           "        except asyncio.QueueFull:",
           "            self._note_dropped(topic)"),
         L("        self.queue.put_nowait((topic, payload))   # MUT M2：回到网络线程里的死闸")),
     ], ["test_queue_full_is_counted_by_the_loop_callback",
         "test_queue_full_does_not_surface_as_unhandled_callback_error"]),

    "M3_registry_no_attach": ("KILL", "butler/triggers/registry.py", [
        (L("                self._watch_future(source_id, result)"),
         L("                pass   # MUT M3：Future 挂上不回收")),
     ], ["test_failed_future_marks_error",
         "test_failed_future_is_logged_at_error",
         "test_dispatch_row_is_kept_and_error_row_is_added",
         "test_real_cross_thread_dispatch_failure_is_recorded"]),

    "M4_registry_log_only": ("KILL", "butler/triggers/registry.py", [
        (L('                    self.mark_error(source_id, "%s: %s" % (type(e).__name__, e))'),
         L("                    pass   # MUT M4：只留日志，台账不动")),
     ], ["test_failed_future_marks_error",
         "test_dispatch_row_is_kept_and_error_row_is_added",
         "test_real_cross_thread_dispatch_failure_is_recorded"]),

    "M5_presence_sleep": ("KILL", "butler/app.py", [
        (L("        try:",
           "            await _asyncio.wait_for(stop.wait(), timeout=interval)",
           "        except _asyncio.TimeoutError:",
           "            pass"),
         L("        await _asyncio.sleep(interval)   # MUT M5：等满周期，⛔ 等事件")),
     ], ["test_loop_wakes_promptly_when_stop_is_set_during_sleep"]),

    "M6_stop_event_after_task": ("KILL", "butler/app.py", [
        (L("        rt._stop_event = asyncio.Event()",
           "        # 启动定位轮询后台任务（保持引用防止 GC 回收）",
           "        rt._presence_poll_task = asyncio.create_task(_presence_poll_loop(rt))"),
         L("        # 启动定位轮询后台任务（保持引用防止 GC 回收）",
           "        rt._presence_poll_task = asyncio.create_task(_presence_poll_loop(rt))",
           "        rt._stop_event = asyncio.Event()   # MUT M6：闩晚于任务出生")),
     ], ["test_stop_event_is_created_before_the_poll_task_starts"]),

    "M7_close_before_cleanup": ("KILL", "butler/app.py", [
        (L("        finally:",
           "            consumer.cancel()"),
         L("        finally:",
           "            import butler.store.db as _dbmod",
           "            _dbmod.close()   # MUT M7：先关连接，再收轮询任务",
           "            consumer.cancel()")),
        (L("            from butler.store.db import close",
           "",
           "            close()"),
         L("            pass")),
     ], ["test_shutdown_cancels_presence_task_before_closing_db"]),

    "M8_sse_swallow": ("KILL", "butler/app.py", [
        (L('                        rt.sse_dropped = getattr(rt, "sse_dropped", 0) + 1',
           '                        logger.warning("SSE_SUBSCRIBER_QUEUE_FULL 订阅者队列满，已丢弃 1 件：累计=%d",',
           "                                       rt.sse_dropped)"),
         L("                        pass   # MUT M8：丢件回到无声吞掉")),
     ], ["test_full_subscriber_drop_is_counted",
         "test_full_subscriber_drop_is_logged"]),

    # ---- 语义等价对照：改了写法但行为没改，尺子必须⛔ 咬 ----
    "E1_exception_to_error": ("CTRL", "butler/triggers/registry.py", [
        ("logger.exception(", "logger.error("),
     ], []),
    "E2_throttle_int": ("CTRL", "butler/bus/mqtt_client.py", [
        ('get("_drop_logged_at", 0.0) < 60.0', 'get("_drop_logged_at", 0.0) < 60'),
     ], []),
    "E3_swap_counter_time": ("CTRL", "butler/bus/mqtt_client.py", [
        (L("        self.dropped += 1",
           "        now = time.time()"),
         L("        now = time.time()",
           "        self.dropped += 1")),
     ], []),
    "E4_timeout_float": ("CTRL", "butler/app.py", [
        ("timeout=interval", "timeout=float(interval)"),
     ], []),
}


def read_norm(rel):
    raw = (ROOT / rel).read_bytes()
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n")
    if crlf and crlf != lf:
        raise SystemExit("EOL_MIXED|%s|crlf=%d|lf=%d" % (rel, crlf, lf))
    return raw.decode("utf-8").replace("\r\n", "\n"), bool(crlf)


def write_norm(rel, text, crlf):
    out = text.replace("\n", "\r\n") if crlf else text
    data = out.encode("utf-8")
    path = ROOT / rel
    path.write_bytes(data)
    back = path.read_bytes()
    if back != data:
        raise SystemExit("WRITE_VERIFY|%s" % rel)
    return hashlib.md5(data).hexdigest(), out.count("\n"), back.count(b"\r\n")


def md5_of(rel):
    return hashlib.md5((ROOT / rel).read_bytes()).hexdigest()


def do_list():
    for mid, (kind, rel, _edits, expect) in sorted(MUTANTS.items()):
        print("MUT|%s|kind=%s|file=%s|expect_fail=%s"
              % (mid, kind, rel, ",".join(expect) if expect else "-"))
    print("mutants=%d kill=%d ctrl=%d"
          % (len(MUTANTS),
             sum(1 for v in MUTANTS.values() if v[0] == "KILL"),
             sum(1 for v in MUTANTS.values() if v[0] == "CTRL")))


def do_snapshot():
    PRISTINE.mkdir(parents=True, exist_ok=True)
    for rel in TARGETS:
        dst = PRISTINE / rel.replace("/", "__")
        shutil.copyfile(ROOT / rel, dst)
        print("SNAPSHOT|%s|%s|%d" % (rel, md5_of(rel), (ROOT / rel).read_bytes().__len__()))


def do_restore():
    for rel in TARGETS:
        src = PRISTINE / rel.replace("/", "__")
        if not src.exists():
            print("RESTORE_SKIP|%s|no_pristine" % rel)
            continue
        before = md5_of(rel)
        shutil.copyfile(src, ROOT / rel)
        after = md5_of(rel)
        want = hashlib.md5(src.read_bytes()).hexdigest()
        print("RESTORE|%s|from=%s|to=%s|want=%s|%s"
              % (rel, before, after, want, "OK" if after == want else "MISMATCH"))
        if after != want:
            raise SystemExit("RESTORE_MISMATCH|%s" % rel)


def do_apply(mid):
    kind, rel, edits, expect = MUTANTS[mid]
    text, crlf = read_norm(rel)
    before = md5_of(rel)
    for old, new in edits:
        n = text.count(old)
        if n != 1:
            raise SystemExit("ANCHOR_%s|%s|count=%d" % ("MISS" if n == 0 else "AMBIGUOUS", mid, n))
        text = text.replace(old, new, 1)
    if text == (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n"):
        raise SystemExit("NO_OP|%s" % mid)
    try:
        ast.parse(text)
    except SyntaxError as e:
        raise SystemExit("SYNTAX|%s|%s" % (mid, e))
    after, lines, crlf_after = write_norm(rel, text, crlf)
    if after == before:
        raise SystemExit("MD5_UNCHANGED|%s" % mid)
    if crlf_after != (crlf * lines):
        raise SystemExit("EOL_BROKEN|%s|crlf=%d|lines=%d" % (mid, crlf_after, lines))
    print("APPLIED|%s|kind=%s|file=%s|md5_before=%s|md5_after=%s|lines=%d|crlf=%d|expect_fail=%s"
          % (mid, kind, rel, before, after, lines, crlf_after, ",".join(expect) if expect else "-"))


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--list":
        do_list()
    elif args[0] == "--snapshot":
        do_snapshot()
    elif args[0] == "--restore":
        do_restore()
    elif args[0] == "--apply":
        do_apply(args[1])
    else:
        raise SystemExit("USAGE|b20_mutate.py [--list|--snapshot|--restore|--prove|--apply ID]")


if __name__ == "__main__":
    main()
