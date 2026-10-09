"""角色对话线程持久化：每个角色各自的 doubao2api conversation_id。

用于「跨端续聊」（R8）：手机豆包 App 能看到按角色隔离的连续 thread。
依赖 doubao2api 已落地的 keep_conversation 特性（见交接单_会话生命周期）；
该特性未实现时 conversation_id 恒为 None，退化为单次调用，不影响现有功能。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from butler.core.atomic_json import read_json_object, write_json_atomic
from butler.logging_setup import get_logger

logger = get_logger("butler.roles.state")

_merge_lock = threading.Lock()


def read_role_state(path) -> dict:
    """整份读 role_state.json；缺档与坏档都兜底空 dict（⛔ 抛穿，批17 口径）。

    「读不出来≠没有」的判定走收敛尺子 read_json_object（批70b），调用方若要做整份覆盖写
    请直接用 merge_and_bind——它自带拒写闸。
    """
    data, _unreadable = read_json_object(path)
    return data


def merge_and_bind(path, role_id: str, conversation_id: str) -> dict:
    """锁内重读 → 并一条角色绑定 → 整份写回，返回并完的整份状态。

    D2（批70b）：坏档时重读拿到的是不完整内存态，直接写回＝其余角色的 conversation_id 全没
    （跨端续聊 R8）。读不出来就拒写、保住盘上现场；⛔ 抛异常（feeder 调用面依赖不抛），
    ERROR 日志是唯一拒绝出口。缺档＝真没有，首次绑定照常落盘。
    """
    p = Path(path)
    with _merge_lock:
        data, unreadable = read_json_object(p)
        if unreadable:
            logger.error("role_state.json unreadable, merge_and_bind refused to avoid "
                         "clobbering the rest of the bindings: %s", p)
            return {}
        data[role_id] = {
            "conversation_id": conversation_id,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        p.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(p, data)
        return data


class RoleConversationStore:
    def __init__(self, data_dir: str):
        self.path = Path(data_dir) / "role_state.json"
        self._m: dict[str, dict] = {}
        # 批70b D2 同款：坏档兜底空之后这份内存态不完整，_save 拿它整份覆盖＝其余绑定全没
        self._unreadable = False

    def load(self) -> None:
        self._m, self._unreadable = read_json_object(self.path)

    def get(self, role_id: str) -> str | None:
        return (self._m.get(role_id) or {}).get("conversation_id")

    def set(self, role_id: str, conversation_id: str) -> None:
        if not conversation_id:
            return
        self._m[role_id] = {
            "conversation_id": conversation_id,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._save()

    def _save(self) -> None:
        if self._unreadable:
            logger.error("role_state.json unreadable, refusing full overwrite from incomplete "
                         "in-memory state: %s", self.path)
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # 同一份 role_state.json 的第二个写手（第一个是 memory/feeder.py）：就地截断写会盖坏对方刚绑好的会话
            write_json_atomic(self.path, self._m)
        except Exception as e:
            logger.warning("save role_state failed: %s", e)
