"""批197 自更新的纯判定件：⛔ I/O、⛔ 依赖 butler，容器侧与宿主侧共用同一把尺。

宿主侧 `scripts/host_update.sh` 用 `importlib` 按**文件路径**装载本模块（⛔ `import butler`：
包的 import 会带上 `__init__`，本仓判例「二次 import＝在停摆库上开连接」）。

裁点（出资人 2026-10-11 三条）：
 ① 发布源＝现在的公开 GitHub 仓的 `main`；
 ② **只有版本 tag 才自动重启**，日常更新只落码；
 ③ 部署树有未收编的本地改动 ⇒ 拒绝执行并回报「有未收编的本地改动」。
优先级按此排：未启用 > 脏树 > 非快进 > tag/日常（脏树即便目标是 tag 也先拒）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

RELEASE_REF_RE = re.compile(r"^v\d+\.\d+")


def is_release_ref(ref: str) -> bool:
    """版本 tag 形制＝`v<主>.<次>…`。`main`／`latest`／裸 `v`／`ver1.2`／`2.1.0` 一律不算。"""
    return bool(RELEASE_REF_RE.match(ref or ""))


def decide_action(*, enabled: bool, dirty: int, fast_forward: bool, ref: str) -> tuple[str, str]:
    """返回 `(action, reason)`，action ∈ refuse / apply_only / apply_and_restart。"""
    if not enabled:
        return "refuse", "自更新未启用（SELF_UPDATE_ENABLED=false）"
    if dirty:
        return "refuse", "有未收编的本地改动（%d 枚跟踪文件被改）" % dirty
    if not fast_forward:
        return "refuse", "目标不是当前码的后代（⛔ 覆盖：先把本地历史收编或推到发布源）"
    if is_release_ref(ref):
        return "apply_and_restart", "版本 tag（%s）⇒ 落码并重启" % ref
    return "apply_only", "日常更新（%s）⇒ 只落码，⛔ 自动重启（重启只在版本 tag）" % ref


def read_state(path) -> dict:
    """三态读数：没有／读到／读坏。

    ⛔ 把「读不出来」当「没有」（本仓判例「读不出来≠没有」）——present 与 data 分两格交出去。
    """
    p = Path(path)
    if not p.exists():
        return {"present": False, "data": None, "error": None}
    try:
        return {"present": True, "data": json.loads(p.read_text(encoding="utf-8")), "error": None}
    except Exception as exc:  # JSON 半截／权限／编码
        return {"present": True, "data": None,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:120])}
