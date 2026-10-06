"""技能版本管理：保存每次修改的版本快照，支持回滚和对比。

版本存储：data/skills/_versions/{skill_id}/{timestamp}.json
每次 save 时自动保存当前版本（如果与上一版本不同）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from butler.logging_setup import get_logger

logger = get_logger("butler.skills.versions")

MAX_VERSIONS_PER_SKILL = 20  # 每个技能最多保留20个版本


class SkillVersionManager:
    """技能版本管理器。"""

    def __init__(self, data_dir: str = "/app/data"):
        self.versions_dir = Path(data_dir) / "skills" / "_versions"
        self.versions_dir.mkdir(parents=True, exist_ok=True)

    def _skill_dir(self, skill_id: str) -> Path:
        d = self.versions_dir / skill_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def snapshot(self, skill: dict) -> str | None:
        """保存技能版本快照。如果与上一版本相同则不保存。返回版本ID（timestamp）。"""
        skill_id = skill.get("id", "")
        if not skill_id:
            return None
        # 不保存运行时字段
        snapshot_data = {k: v for k, v in skill.items() if k not in ("_runtime",)}
        snapshot_data["_version_saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

        # 检查是否与上一版本相同
        versions = self.list_versions(skill_id)
        if versions:
            last = versions[0]
            last_data = self._load_version(skill_id, last["version"])
            if last_data and self._compare_skills(last_data, snapshot_data):
                logger.debug("skill %s unchanged, skipping version snapshot", skill_id)
                return last["version"]

        version_id = str(int(time.time() * 1000))
        path = self._skill_dir(skill_id) / f"{version_id}.json"
        path.write_text(json.dumps(snapshot_data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("skill version saved: %s@%s", skill_id, version_id)

        # 清理旧版本
        self._cleanup_old_versions(skill_id)
        return version_id

    def list_versions(self, skill_id: str) -> list[dict]:
        """列出技能的所有版本（按时间倒序）。"""
        d = self._skill_dir(skill_id)
        versions = []
        for f in sorted(d.glob("*.json"), reverse=True):
            version_id = f.stem
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                versions.append({
                    "version": version_id,
                    "name": data.get("name", ""),
                    "status": data.get("status", "enabled"),
                    "enabled": data.get("enabled", True),
                    "saved_at": data.get("_version_saved_at", ""),
                    "trigger": (data.get("trigger") or {}).get("entry", ""),
                    "engine": (data.get("brain") or {}).get("engine", ""),
                })
            except Exception:
                pass
        return versions

    def _load_version(self, skill_id: str, version_id: str) -> dict | None:
        """加载指定版本的技能数据。"""
        path = self._skill_dir(skill_id) / f"{version_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def get_version(self, skill_id: str, version_id: str) -> dict | None:
        """获取指定版本的完整技能数据。"""
        data = self._load_version(skill_id, version_id)
        if data:
            data.pop("_version_saved_at", None)
        return data

    def rollback(self, skill_id: str, version_id: str, store) -> dict | None:
        """回滚到指定版本。返回回滚后的技能数据。"""
        data = self.get_version(skill_id, version_id)
        if data is None:
            return None
        # 保存当前版本（回滚前的快照）
        current = store.get(skill_id)
        if current:
            self.snapshot(current)
        # 恢复到指定版本
        data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        store.save(data)
        logger.info("skill %s rolled back to version %s", skill_id, version_id)
        return data

    def diff(self, skill_id: str, version_a: str, version_b: str) -> dict:
        """对比两个版本的差异。"""
        a = self.get_version(skill_id, version_a)
        b = self.get_version(skill_id, version_b)
        if a is None or b is None:
            return {"error": "version not found"}

        changes = []
        all_keys = set(list(a.keys()) + list(b.keys()))
        for key in sorted(all_keys):
            va = a.get(key)
            vb = b.get(key)
            if va != vb:
                changes.append({
                    "field": key,
                    "old": va,
                    "new": vb,
                })
        return {
            "skill_id": skill_id,
            "version_a": version_a,
            "version_b": version_b,
            "changes": changes,
            "change_count": len(changes),
        }

    def _compare_skills(self, a: dict, b: dict) -> bool:
        """比较两个技能数据是否相同（忽略版本保存时间字段）。"""
        a_clean = {k: v for k, v in a.items() if k != "_version_saved_at"}
        b_clean = {k: v for k, v in b.items() if k != "_version_saved_at"}
        return a_clean == b_clean

    def _cleanup_old_versions(self, skill_id: str) -> None:
        """清理超过最大版本数的旧版本。"""
        d = self._skill_dir(skill_id)
        files = sorted(d.glob("*.json"), reverse=True)
        if len(files) > MAX_VERSIONS_PER_SKILL:
            for f in files[MAX_VERSIONS_PER_SKILL:]:
                try:
                    f.unlink()
                except Exception:
                    pass

    def delete_versions(self, skill_id: str) -> None:
        """删除技能的所有版本（技能删除时调用）。"""
        d = self._skill_dir(skill_id)
        if d.exists():
            for f in d.glob("*.json"):
                try:
                    f.unlink()
                except Exception:
                    pass
            try:
                d.rmdir()
            except Exception:
                pass
