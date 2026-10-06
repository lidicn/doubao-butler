"""启动期静态资产生成（P2-5）。

这里的东西跑在 `app.py` 的 `lifespan` 里，所以⛔ 任何阻塞式系统调用：10-02 稳定性报告点名
`subprocess.run(..., timeout=10)` 最多冻结事件循环 10 秒。本模块只用标准库（`butler/config.py`
之外的一串重量级 import 一概不碰），这样宿主机 python3.11 能直接跑验收，⛔ 装 starlette。

`ensure_wake_ding` 的返回约定是 `(ok, detail)` 且⛔ 向调用方抛异常：启动序不该被一枚提示音的
生成失败打断，但失败必须出声（原码 `app.py:275` 的 `print("generated")` 在⛔ 看返回码的情况下
也会执行＝假绿，本模块把「返回码 0」与「文件真落盘」两件事分别验）。
"""
from __future__ import annotations

import asyncio
import asyncio.subprocess
import contextlib
import pathlib

WAKE_DING_NAME = "wake_ding.mp3"

# 与改码前 app.py:271-273 那 14 项字面量逐枚相等（验收腿 test_03 钉着它）
FFMPEG_ARGV = (
    "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=880:duration=0.2",
    "-af", "afade=t=in:st=0:d=0.01,afade=t=out:st=0.15:d=0.05,volume=0.4",
    "-ar", "24000", "-ac", "1", "-b:a", "24k",
)
DEFAULT_TIMEOUT_S = 10


async def ensure_wake_ding(tts_dir, *, timeout_s: int = DEFAULT_TIMEOUT_S, spawn=None):
    """确保 tts_dir/wake_ding.mp3 存在。返回 (ok, detail)。

    `spawn` 是注入点（验收用它换假进程），默认 `asyncio.create_subprocess_exec`。
    """
    ding_path = pathlib.Path(tts_dir) / WAKE_DING_NAME
    if ding_path.exists():
        return True, "已存在"

    if spawn is None:
        spawn = asyncio.create_subprocess_exec
    try:
        proc = await spawn(
            *FFMPEG_ARGV, str(ding_path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return False, "ffmpeg 启动失败：%s" % exc

    try:
        await asyncio.wait_for(proc.communicate(), timeout_s)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        return False, "ffmpeg 超时（%ss）已终止" % timeout_s

    rc = await proc.wait()
    if rc != 0:
        return False, "ffmpeg 退出码 %s" % rc
    if not ding_path.exists():
        return False, "ffmpeg 退出码 0 但 %s 未落盘" % WAKE_DING_NAME
    return True, "已生成"
