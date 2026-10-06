"""trigger 定义存储：data/triggers/*.json 文件 + 内存索引。

与 skills/store.py 同模式：tmp+rename 原子写入，缺失默认规则在 ensure_defaults 时播种。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from butler.logging_setup import get_logger
from butler.triggers.schema import validate_trigger

logger = get_logger("butler.triggers.store")


class TriggerStore:
    def __init__(self, data_dir: str):
        self.dir = Path(data_dir) / "triggers"
        self._index: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ---- 生命周期 ----

    def load(self) -> int:
        """从磁盘加载全部 trigger 定义，返回数量。"""
        self.dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for f in sorted(self.dir.glob("*.json")):
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("trigger file %s invalid: %s", f.name, e)
                continue
            normalized, err = validate_trigger(raw)
            if err:
                logger.warning("trigger %s schema error: %s", f.name, err)
                continue
            with self._lock:
                self._index[normalized["id"]] = normalized
            count += 1
        logger.info("triggers loaded: %d", count)
        return count

    def ensure_defaults(self, defaults: list[dict]) -> list[str]:
        """播种缺失的默认 trigger（已存在的不覆盖），返回新建 id 列表。"""
        created = []
        for raw in defaults:
            normalized, err = validate_trigger(raw)
            if err:
                logger.warning("default trigger %s invalid: %s", raw.get("id"), err)
                continue
            tid = normalized["id"]
            if self.get(tid) is None:
                self.save(normalized)
                created.append(tid)
        return created

    def reload(self) -> int:
        """热加载：清空索引后重新从磁盘加载。"""
        with self._lock:
            self._index.clear()
        return self.load()

    # ---- CRUD ----

    def list(self) -> list[dict]:
        with self._lock:
            return sorted(self._index.values(), key=lambda t: (-t.get("priority", 50), t.get("id", "")))

    def get(self, trigger_id: str) -> dict | None:
        with self._lock:
            return self._index.get(trigger_id)

    def save(self, trigger: dict) -> None:
        """保存（内存索引 + 落盘）。校验在本函数收口：不通过 schema 的定义既不进出索引也不落盘。

        原注释把"先校验"当成调用方的自觉，而唯一没校验的调用方已经往磁盘上写过
        只带 id 的空壳 trigger（load() 下次启动直接跳过，用户侧表现为"设了不管用"）。
        落盘写 normalized，保证磁盘与索引是同一份形状。
        """
        normalized, err = validate_trigger(trigger)
        if err:
            raise ValueError("trigger 校验失败: " + err)
        tid = normalized["id"]
        with self._lock:
            self._index[tid] = normalized
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{tid}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def delete(self, trigger_id: str) -> bool:
        with self._lock:
            existed = self._index.pop(trigger_id, None) is not None
        path = self.dir / f"{trigger_id}.json"
        if path.exists():
            path.unlink()
        return existed
