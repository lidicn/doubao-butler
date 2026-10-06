"""技能定义存储 v2：按来源分目录隔离 + 内存索引。

目录结构：
  data/skills/
    builtin/   内置默认技能（只读，随版本更新）
    user/      用户手动创建/修改的技能
    agent/     Agent 自动生成的技能（需审核）

文件写入采用 tmp+rename 原子替换；缺失的默认技能在 ensure_defaults 时播种。
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

from butler.logging_setup import get_logger
from butler.skills.schema import validate_skill

logger = get_logger("butler.skills.store")

_SOURCES = ("builtin", "user", "agent")


class SkillStore:
    def __init__(self, data_dir: str):
        self.root = Path(data_dir) / "skills"
        self._index: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.version_mgr = None  # 由 app.py 注入

    def _dir_for(self, source: str) -> Path:
        source = source if source in _SOURCES else "user"
        return self.root / source

    # ---- 生命周期 ----

    def load(self) -> int:
        """从所有子目录加载技能定义，返回数量。"""
        for src in _SOURCES:
            (self.root / src).mkdir(parents=True, exist_ok=True)
        count = 0
        with self._lock:
            self._index.clear()
        for src in _SOURCES:
            d = self.root / src
            for f in sorted(d.glob("*.json")):
                try:
                    raw = json.loads(f.read_text(encoding="utf-8"))
                except Exception as e:
                    logger.warning("skill file %s invalid: %s", f.name, e)
                    continue
                normalized, err = validate_skill(raw)
                if err:
                    logger.warning("skill %s schema error: %s", f.name, err)
                    continue
                # 确保 source 字段与目录一致
                if normalized.get("source") != src:
                    normalized["source"] = src
                with self._lock:
                    self._index[normalized["id"]] = normalized
                count += 1
        logger.info("skills loaded: %d (builtin/user/agent)", count)
        return count

    def reload(self) -> int:
        """热加载：重新从磁盘加载所有技能。"""
        return self.load()

    def ensure_defaults(self, defaults: list[dict]) -> list[str]:
        """播种缺失的默认技能到 builtin/ 目录（已存在的不覆盖）。"""
        created = []
        for raw in defaults:
            normalized, err = validate_skill(raw)
            if err:
                logger.warning("default skill %s invalid: %s", raw.get("id"), err)
                continue
            normalized["source"] = "builtin"
            sid = normalized["id"]
            existing = self.get(sid)
            if existing is None:
                self.save(normalized)
                created.append(sid)
            elif existing.get("source") == "builtin":
                # 内置技能更新：补齐缺失字段，不覆盖用户编辑
                changed = False
                for k, v in normalized.items():
                    if k not in existing and k not in ("updated_at",):
                        existing[k] = v
                        changed = True
                if changed:
                    self.save(existing)
                    created.append(sid)
        return created

    def migrate_flat_to_subdirs(self) -> int:
        """数据迁移：将 data/skills/*.json 迁移到 user/ 子目录。"""
        migrated = 0
        flat_files = list(self.root.glob("*.json"))
        if not flat_files:
            return 0
        user_dir = self._dir_for("user")
        user_dir.mkdir(parents=True, exist_ok=True)
        backup_dir = self.root / "_backup_flat"
        backup_dir.mkdir(exist_ok=True)
        for f in flat_files:
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
                normalized, err = validate_skill(raw)
                if err:
                    logger.warning("migrate skip %s: %s", f.name, err)
                    continue
                normalized["source"] = "user"
                target = user_dir / f.name
                if not target.exists():
                    target.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
                # 备份原文件
                shutil.move(str(f), str(backup_dir / f.name))
                migrated += 1
                logger.info("migrated skill %s -> user/", f.name)
            except Exception as e:
                logger.warning("migrate %s failed: %s", f.name, e)
        if migrated:
            logger.info("migration complete: %d skills moved to user/, backup in _backup_flat/", migrated)
        return migrated

    # ---- CRUD ----

    def list(self, source: str = "", status: str = "") -> list[dict]:
        """列出技能，可按来源/状态过滤。按优先级降序、id 升序。"""
        with self._lock:
            items = list(self._index.values())
        if source:
            items = [s for s in items if s.get("source") == source]
        if status:
            items = [s for s in items if s.get("status") == status]
        items.sort(key=lambda s: (-s.get("priority", 50), s["id"]))
        return items

    def get(self, skill_id: str) -> dict | None:
        with self._lock:
            return self._index.get(skill_id)

    def save(self, skill: dict) -> None:
        """保存（内存索引 + 落盘到对应 source 目录）。"""
        sid = skill["id"]
        source = skill.get("source", "user")
        if source not in _SOURCES:
            source = "user"
            skill["source"] = source
        with self._lock:
            self._index[sid] = skill
        d = self._dir_for(source)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{sid}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(skill, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        # 版本快照（v1.4）
        if self.version_mgr:
            try:
                self.version_mgr.snapshot(skill)
            except Exception as e:
                logger.debug("version snapshot failed: %s", e)

    def delete(self, skill_id: str) -> bool:
        with self._lock:
            skill = self._index.pop(skill_id, None)
        if skill is None:
            return False
        source = skill.get("source", "user")
        path = self._dir_for(source) / f"{skill_id}.json"
        if path.exists():
            path.unlink()
        # 清理版本历史（v1.4）
        if self.version_mgr:
            try:
                self.version_mgr.delete_versions(skill_id)
            except Exception as e:
                logger.debug("version cleanup failed: %s", e)
        return True

    def set_status(self, skill_id: str, status: str) -> dict | None:
        """修改技能状态。返回更新后的技能，不存在返回 None。"""
        skill = self.get(skill_id)
        if skill is None:
            return None
        skill["status"] = status
        skill["enabled"] = status == "enabled"
        skill["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.save(skill)
        return skill

    def set_approval(self, skill_id: str, approval: str) -> dict | None:
        """修改技能审批状态（v1.5）。返回更新后的技能，不存在返回 None。"""
        skill = self.get(skill_id)
        if skill is None:
            return None
        skill["approval"] = approval
        skill["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.save(skill)
        return skill

    def quarantine(self, skill_id: str, reason: str = "") -> dict | None:
        """隔离技能：状态改为 quarantined，记录原因。"""
        skill = self.get(skill_id)
        if skill is None:
            return None
        skill["status"] = "quarantined"
        skill["enabled"] = False
        skill["quarantine_reason"] = reason
        skill["quarantined_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        skill["updated_at"] = skill["quarantined_at"]
        self.save(skill)
        logger.warning("skill %s quarantined: %s", skill_id, reason)
        return skill

    def stats(self) -> dict:
        """统计各来源/状态的技能数量。"""
        result = {"total": 0, "by_source": {}, "by_status": {}}
        with self._lock:
            for s in self._index.values():
                result["total"] += 1
                src = s.get("source", "user")
                st = s.get("status", "enabled")
                result["by_source"][src] = result["by_source"].get(src, 0) + 1
                result["by_status"][st] = result["by_status"].get(st, 0) + 1
        return result
