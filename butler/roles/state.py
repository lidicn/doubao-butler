"""角色对话线程持久化：每个角色各自的 doubao2api conversation_id。

用于「跨端续聊」（R8）：手机豆包 App 能看到按角色隔离的连续 thread。
依赖 doubao2api 已落地的 keep_conversation 特性（见交接单_会话生命周期）；
该特性未实现时 conversation_id 恒为 None，退化为单次调用，不影响现有功能。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from butler.core.atomic_json import write_json_atomic
from butler.logging_setup import get_logger

logger = get_logger("butler.roles.state")


class RoleConversationStore:
    def __init__(self, data_dir: str):
        self.path = Path(data_dir) / "role_state.json"
        self._m: dict[str, dict] = {}

    def load(self) -> None:
        if self.path.exists():
            try:
                self._m = json.loads(self.path.read_text(encoding="utf-8")) or {}
            except Exception as e:
                logger.warning("role_state.json parse failed: %s", e)
                self._m = {}

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
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # 同一份 role_state.json 的第二个写手（第一个是 memory/feeder.py）：就地截断写会盖坏对方刚绑好的会话
            write_json_atomic(self.path, self._m)
        except Exception as e:
            logger.warning("save role_state failed: %s", e)
