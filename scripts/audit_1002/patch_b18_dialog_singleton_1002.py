#!/usr/bin/env python3
"""批18 一次性补丁器 · dialog.py（P2-18 收敛 8 处 + P1-22 过期分支）＋ tts/singleton.py（P0-7 强引用）。

用法：
    python3 scripts/audit_1002/patch_b18_dialog_singleton_1002.py --dry-run
    python3 scripts/audit_1002/patch_b18_dialog_singleton_1002.py

纪律：ALREADY_SCAN 先于 BASELINE；行号只来自现读（19:07-19:09Z awk 那把尺），每条替换都先 assert
被替换行的 strip() 原文；只动前导空格与整行，⛔ 正则全文替换；写完 py_compile 语法门 + WRITE_VERIFY；
重跑必 raise。所有期望文本都从文件本身取（⛔ 在本文件里嵌中文字面量当锚点，除了新写的 helper 块本体）。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIALOG = ROOT / "butler" / "core" / "dialog.py"
SINGLETON = ROOT / "butler" / "tts" / "singleton.py"

BASE = {
    "butler/core/dialog.py": ("9d805fbffb838d5e89cadb9294ccab3c", 772, 43854, 0),
    "butler/tts/singleton.py": ("b6d5d646f6657ac247c66d41c27160c4", 72, 3122, 0),
}

SPEAK = "self.state.set_state(DialogState.SPEAKING)"
WAIT = "self.state.set_state(DialogState.WAITING)"
SPAWN = "asyncio.create_task(self._return_idle())"

HELPER_BLOCK = '''    async def _speak_transition(self, action):
        """SPEAKING → action → 无论成败都收尾（第六轮 P2-18）。

        旧形状＝set_state(SPEAKING) / await 发声 / set_state(WAITING) 三段平铺，发声一抛
        （HA 断连、合成失败、mqtt 抛错）后两段永不执行 ⇒ 状态机永久停在 SPEAKING，且日志里
        没有一句「卡住了」。收尾放进 finally；异常照原样上抛（吞异常＝另一枚缺陷）。
        """
        self.state.set_state(DialogState.SPEAKING)
        try:
            result = action()
            if inspect.isawaitable(result):
                result = await result
            return result
        finally:
            self.state.set_state(DialogState.WAITING)
            self._spawn_idle()

    def _spawn_idle(self) -> None:
        """起 _return_idle 并把 task 存进模块级强引用。

        第五轮 P0-7 同形状：裸 create_task 的返回值没人保存，loop 对任务只持弱引用
        ⇒ 可能在跑完前被回收；这里 add/discard 成对，注册表⛔ 越跑越涨。
        """
        task = asyncio.create_task(self._return_idle())
        _idle_tasks.add(task)
        task.add_done_callback(_idle_tasks.discard)
'''

IDLE_DECL = '''
_idle_tasks: set[asyncio.Task] = set()   # 收尾任务强引用注册表（第五轮 P0-7 同形状）
'''

WORKER_BLOCK = '''
_worker_tasks: set[asyncio.Task] = set()   # 长生命周期 worker 的强引用（第五轮 P0-7）


def _on_worker_done(task: asyncio.Task) -> None:
    """worker 退出：先从注册表摘掉；异常必须出声（旧码＝异常留在 task 对象里，没人 await＝⛔ 响）。"""
    _worker_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("TTSQueue worker died: %s: %s", type(exc).__name__, exc)
'''

WORKER_NEW_COMMENT = [
    "    # 启动后台 worker（主事件循环已在跑）。task 必须存进 _worker_tasks：CPython 对事件循环里的",
    "    # 任务只持弱引用，返回值不保存就可能在跑完前被 GC 回收＝队列静默停止消费（第五轮 P0-7）。",
]


def sha(d: bytes) -> str:
    return hashlib.md5(d).hexdigest()


def lines_of(p: Path) -> list[str]:
    raw = p.read_bytes()
    if raw.count(b"\r") != 0:
        raise SystemExit(f"BASELINE_CR|{p.name} CR={raw.count(chr(13).encode())} 期望 0（本批文件全是 LF 血统）")
    if not raw.endswith(b"\n"):
        raise SystemExit(f"BASELINE_ENDNL|{p.name} 缺尾换行")
    text = raw.decode("utf-8")
    parts = text.split("\n")
    assert parts[-1] == "", f"{p.name} 拆分尾部非空"
    return parts[:-1]


def measure(lines: list[str]) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


def lead(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def expect(lines: list[str], no: int, stripped: str, tag: str) -> str:
    got = lines[no - 1].strip()
    if got != stripped:
        raise SystemExit(f"ANCHOR|{tag} 第 {no} 行期望 strip()=={stripped!r} 实读 {got!r}")
    return lines[no - 1]


def expect_prefix(lines: list[str], no: int, prefix: str, tag: str) -> str:
    got = lines[no - 1].strip()
    if not got.startswith(prefix):
        raise SystemExit(f"ANCHOR|{tag} 第 {no} 行期望以 {prefix!r} 开头 实读 {got!r}")
    return lines[no - 1]


def transition_from_spoken(orig: str) -> str:
    """`spoken = await self.speak_as_role(...)` → `spoken = await self._speak_transition(lambda: self.speak_as_role(...))`"""
    body = orig.strip()
    head = "spoken = await "
    if not body.startswith(head):
        raise SystemExit(f"ANCHOR|spoken 行不以 {head!r} 开头：{body!r}")
    call = body[len(head):]
    if not call.endswith(")"):
        raise SystemExit(f"ANCHOR|spoken 调用没有右括号收尾：{call!r}")
    return lead(orig) + head + "self._speak_transition(lambda: " + call + ")"


def patch_dialog(lines: list[str]) -> list[str]:
    out = list(lines)

    def replace(start: int, end: int, new: list[str], tag: str) -> None:
        # 1-based inclusive；就地替换（长度可变）
        out[start - 1:end] = new

    # ---- 自底向上，行号始终指原文件 ----
    # 734：在 _return_idle 之前插入两个 helper
    expect(out, 734, "async def _return_idle(self) -> None:", "D-734")
    replace(734, 734, HELPER_BLOCK.rstrip("\n").split("\n") + ["", out[733]], "D-734-ins")

    # 724-731：speak() 尾部（publish 可抛 mqtt）
    expect(out, 724, SPEAK, "D-724")
    p1 = expect_prefix(out, 725, "self._publish_dialog(", "D-725")
    p2 = expect_prefix(out, 726, '"devices": dev_ids', "D-726")
    expect(out, 730, WAIT, "D-730")
    expect(out, 731, SPAWN, "D-731")
    expect_prefix(out, 732, "return {", "D-732")
    ind = lead(out[723])
    new724 = [out[726], out[727],
              ind + "await self._speak_transition(lambda: " + p1.strip(),
              ind + "        " + p2.strip() + ")"]
    # ⛔ 把这两行合成一个列表元素（元素内嵌 "\n"）：拼接后的字节一样，
    # 但 PLAN 的 len(new) 会少报一行——上一版就是这样，PLAN 776 / 盘上 777。
    # 724-731 收敛（含 :729 的 # WAITING → IDLE 注释，其代码已进 finally）；:732 的 return 不动
    replace(724, 731, new724, "D-724")

    # 502-507：tv_voice（_speak_tv_only 可抛）
    expect(out, 502, SPEAK, "D-502")
    expect(out, 505, "await self._speak_tv_only(reply, role)", "D-505")
    expect(out, 506, WAIT, "D-506")
    expect(out, 507, SPAWN, "D-507")
    ind = lead(out[501])
    replace(502, 507, [out[502], out[503],
                       ind + "await self._speak_transition(lambda: self._speak_tv_only(reply, role))"],
            "D-502")

    # 444-451：on_wakeup 主出口（445-448 的 trace 桥原样保留）
    expect(out, 444, SPEAK, "D-444")
    expect_prefix(out, 449, "spoken = await self.speak_as_role(", "D-449")
    expect(out, 450, WAIT, "D-450")
    expect(out, 451, SPAWN, "D-451")
    replace(444, 451, out[444:448] + [transition_from_spoken(out[448])], "D-444")

    # 410-413：简单命令出口
    expect(out, 410, SPEAK, "D-410")
    expect_prefix(out, 411, "spoken = await self.speak_as_role(", "D-411")
    expect(out, 412, WAIT, "D-412")
    expect(out, 413, SPAWN, "D-413")
    replace(410, 413, [transition_from_spoken(out[410])], "D-410")

    # 392-393：xiaoai 静默路径的裸 spawn（无 SPEAKING 入口，只并收尾）
    expect(out, 392, WAIT, "D-392")
    expect(out, 393, SPAWN, "D-393")
    replace(392, 393, [out[391], lead(out[391]) + "self._spawn_idle()"], "D-392")

    # 361-379：P1-22 整段缩进进 :335 的 else（⛔ 过期路径也进生成器）＋ 375-378 收敛
    expect(out, 361, "if creator:", "D-361")
    expect(out, 373, "else:", "D-373")
    expect(out, 375, SPEAK, "D-375")
    expect_prefix(out, 376, "spoken = await self.speak_as_role(", "D-376")
    expect(out, 377, WAIT, "D-377")
    expect(out, 378, SPAWN, "D-378")
    expect_prefix(out, 379, "return {", "D-379")
    reindented = ["    " + l if l.strip() else l for l in out[360:374]]
    conv = ["                " + transition_from_spoken(out[375]).strip()]
    tail = ["    " + out[378]]
    replace(361, 379, reindented + conv + tail, "D-361")

    # 353-356：pending 二次触发
    expect(out, 353, SPEAK, "D-353")
    expect_prefix(out, 354, "spoken = await self.speak_as_role(", "D-354")
    expect(out, 355, WAIT, "D-355")
    expect(out, 356, SPAWN, "D-356")
    replace(353, 356, [transition_from_spoken(out[353])], "D-353")

    # 341-344：pending 取消词
    expect(out, 341, SPEAK, "D-341")
    expect_prefix(out, 342, "spoken = await self.speak_as_role(", "D-342")
    expect(out, 343, WAIT, "D-343")
    expect(out, 344, SPAWN, "D-344")
    replace(341, 344, [transition_from_spoken(out[341])], "D-341")

    # 226-229：_quick_reply
    expect(out, 226, SPEAK, "D-226")
    expect_prefix(out, 227, "spoken = await self.speak_as_role(", "D-227")
    expect(out, 228, WAIT, "D-228")
    expect(out, 229, SPAWN, "D-229")
    replace(226, 229, [transition_from_spoken(out[226])], "D-226")

    # 27：logger 之后加注册表声明
    expect(out, 27, 'logger = get_logger("butler.dialog")', "D-27")
    replace(28, 27, IDLE_DECL.rstrip("\n").split("\n"), "D-28-ins")

    # 7：import asyncio 之后加 inspect
    expect(out, 7, "import asyncio", "D-7")
    replace(8, 7, ["import inspect"], "D-8-ins")
    return out


def patch_singleton(lines: list[str]) -> list[str]:
    out = list(lines)
    expect(out, 14, "_manager: TTSManager | None = None", "S-14")
    expect(out, 60, "# 启动后台 worker（fire-and-forget，主事件循环已在跑）", "S-60")
    expect(out, 63, "loop.create_task(_queue.run())", "S-63")
    expect(out, 64, 'logger.info("TTSQueue worker started")', "S-64")
    out[62:63] = ["        task = loop.create_task(_queue.run())",
                  "        _worker_tasks.add(task)",
                  "        task.add_done_callback(_on_worker_done)"]
    out[59:60] = WORKER_NEW_COMMENT
    out[14:14] = WORKER_BLOCK.rstrip("\n").split("\n")
    return out


def gate(rel: str, old_lines: list[str], new_lines: list[str], res: list[str]) -> None:
    txt = "\n".join(new_lines)
    if rel.endswith("dialog.py"):
        must = ["async def _speak_transition(self, action):", "def _spawn_idle(self) -> None:",
                "_idle_tasks: set[asyncio.Task] = set()", "import inspect",
                "self.state.set_state(DialogState.SPEAKING)"]
        for m in must:
            if m not in txt:
                raise SystemExit(f"MUST_PRESENT|dialog|{m}")
        counts = {
            "set_state(DialogState.SPEAKING)": 1,
            "create_task(self._return_idle())": 1,
            "self._speak_transition(": 8,
            "self._spawn_idle()": 2,
            "def _speak_transition": 1,
            "def _spawn_idle": 1,
            "if _pending_exp:": 1,
            "generate_skill_from_description": 1,
        }
        for k, v in counts.items():
            got = txt.count(k)
            if got != v:
                raise SystemExit(f"FUNC_COUNT|dialog|{k} 期望 {v} 实读 {got}")
        # P1-22：过期分支之后不许再有 12 空格的 if creator（必须缩进进 else＝16 空格）
        if "\n            if creator:" in txt:
            raise SystemExit("ABSENT|dialog|12 空格的 if creator: 还在＝过期分支仍会喂生成器")
        if "\n                if creator:" not in txt:
            raise SystemExit("MUST_PRESENT|dialog|if creator: 没缩进进 else（期望 16 空格）")
    else:
        for m in ["_worker_tasks: set[asyncio.Task] = set()", "def _on_worker_done(",
                  "task = loop.create_task(_queue.run())", "_worker_tasks.add(task)",
                  "task.add_done_callback(_on_worker_done)", 'logger.error("TTSQueue worker died']:
            if m not in txt:
                raise SystemExit(f"MUST_PRESENT|singleton|{m}")
        if "loop.create_task(_queue.run())\n        logger.info" in txt:
            raise SystemExit("ABSENT|singleton|裸 create_task 仍在（返回值没保存）")
        for k, v in {"_worker_tasks.add(task)": 1, "add_done_callback(_on_worker_done)": 1,
                     "def _on_worker_done": 1}.items():
            got = txt.count(k)
            if got != v:
                raise SystemExit(f"FUNC_COUNT|singleton|{k} 期望 {v} 实读 {got}")
    assert txt.count(chr(13)) == 0, f"EOL|{rel} 引入了 CR"


def main() -> int:
    dry = "--dry-run" in sys.argv
    plan: list[tuple[Path, list[str]]] = []
    stamp = sha(("".join(sorted([DIALOG.name, SINGLETON.name]))).encode())[:16]
    for p, rel in ((DIALOG, "butler/core/dialog.py"), (SINGLETON, "butler/tts/singleton.py")):
        raw = p.read_bytes()
        md5, ln, by, cr = BASE[rel]
        if not p.exists():
            raise SystemExit(f"PATH_ABSENT|{rel}")
        # ALREADY_SCAN 先于 BASELINE
        txt = raw.decode("utf-8")
        markers = ["_speak_transition", "_idle_tasks", "import inspect"] if rel.endswith("dialog.py") \
            else ["_worker_tasks", "_on_worker_done"]
        hits = [m for m in markers if m in txt]
        if hits:
            print(f"ALREADY_SCAN|{rel}|hit={','.join(hits)}")
            raise SystemExit(f"ALREADY_APPLIED|{rel} 已含 {hits}，⛔ 二次打补丁 rc=1")
        if sha(raw) != md5 or raw.count(bytes([10])) != ln or len(raw) != by or raw.count(bytes([13])) != cr:
            raise SystemExit(
                f"BASELINE|{rel} md5={sha(raw)[:12]} 期望 {md5[:12]} "
                f"lines={raw.count(bytes([10]))} 期望 {ln} "
                f"bytes={len(raw)} 期望 {by} "
                f"CR={raw.count(bytes([13]))} 期望 {cr}")
        lines = lines_of(p)
        new = patch_dialog(lines) if rel.endswith("dialog.py") else patch_singleton(lines)
        gate(rel, lines, new, plan)
        payload = measure(new)
        compile(payload.decode("utf-8"), str(p), "exec")
        print(f"PLAN|{rel}|{len(lines)}->{len(new)} lines|{len(raw)}->{len(payload)} bytes|"
              f"md5={sha(raw)[:12]}->{sha(payload)[:12]}")
        plan.append((p, new, payload))

    if dry:
        print(f"DRY|2 files|stamp={stamp}")
        return 0
    for p, new, payload in plan:
        p.write_bytes(payload)
        back = p.read_bytes()
        if back != payload:
            raise SystemExit(f"WRITE_VERIFY|{p.name} 回读不一致")
    print(f"APPLIED|2 files|stamp={stamp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
