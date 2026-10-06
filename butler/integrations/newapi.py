"""new-api 运维封装：直接操作 SQLite 数据库（三表同步），不依赖 new-api 管理 API。

三表同步规则（新增/修改虚拟模型→渠道映射时必须同时更新）：
  1. channels.models       — JSON 数组字符串，渠道支持的模型列表
  2. channels.model_mapping — JSON 对象字符串，模型名映射（通常 model→model）
  3. abilities             — 虚拟模型→渠道路由表（group, model, channel_id, enabled, priority, weight）

只改前两处会 503（abilities 缺失导致路由失败）。

数据库路径：容器内 /app/data/new-api.db（挂载自宿主机 one-api.db）
写操作前自动备份到 /app/data/new-api.db.bak.YYYYMMDDHHMMSS
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from datetime import datetime

from butler.logging_setup import get_logger

logger = get_logger("butler.integrations.newapi")

DEFAULT_GROUP = "default"


class NewApiError(Exception):
    pass


class NewApiClient:
    """new-api 数据库直接操作客户端。所有方法返回 dict，失败时 ok=False + error。"""

    def __init__(self, db_path: str, backup_dir: str | None = None):
        self.db_path = db_path
        self.backup_dir = backup_dir or os.path.dirname(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _backup(self) -> str:
        """写操作前备份数据库。返回备份文件路径。"""
        os.makedirs(self.backup_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        bak = os.path.join(self.backup_dir, f"new-api.db.bak.{ts}")
        shutil.copy2(self.db_path, bak)
        logger.info("new-api DB backed up to %s", bak)
        return bak

    # ── 查询操作（只读） ────────────────────────────────────

    def list_channels(self) -> dict:
        """列出所有渠道（id/name/type/status/group/weight/priority/models 数量）。"""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    'SELECT id, name, type, status, "group", weight, priority, models, base_url '
                    'FROM channels ORDER BY id'
                ).fetchall()
            channels = []
            for r in rows:
                models = []
                try:
                    models = json.loads(r["models"] or "[]")
                except (json.JSONDecodeError, TypeError):
                    pass
                channels.append({
                    "id": r["id"],
                    "name": r["name"],
                    "type": r["type"],
                    "status": r["status"],  # 1=启用 0=禁用
                    "group": r["group"],
                    "weight": r["weight"],
                    "priority": r["priority"],
                    "model_count": len(models),
                    "base_url": r["base_url"],
                })
            return {"ok": True, "tool": "newapi_list_channels", "result": {"count": len(channels), "channels": channels}}
        except Exception as e:
            logger.warning("list_channels failed: %s", e)
            return {"ok": False, "tool": "newapi_list_channels", "error": "internal_error", "message": str(e)}

    def list_virtual_models(self) -> dict:
        """列出所有虚拟模型（从 abilities 表 GROUP BY model，含映射渠道数）。"""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    'SELECT model, count(*) as channel_count, sum(weight) as total_weight '
                    'FROM abilities WHERE "group" = ? AND enabled = 1 '
                    'GROUP BY model ORDER BY channel_count DESC, model',
                    (DEFAULT_GROUP,),
                ).fetchall()
            models = [{"model": r["model"], "channel_count": r["channel_count"],
                        "total_weight": r["total_weight"] or 0} for r in rows]
            return {"ok": True, "tool": "newapi_list_models", "result": {"count": len(models), "models": models}}
        except Exception as e:
            logger.warning("list_virtual_models failed: %s", e)
            return {"ok": False, "tool": "newapi_list_models", "error": "internal_error", "message": str(e)}

    def get_model_allocation(self, model: str) -> dict:
        """查询某个虚拟模型的渠道分配（渠道列表+权重+优先级）。"""
        model = (model or "").strip()
        if not model:
            return {"ok": False, "tool": "newapi_get_model_allocation", "error": "invalid_parameter", "message": "model 不能为空"}
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    'SELECT a.channel_id, a.enabled, a.priority, a.weight, a.tag, '
                    'c.name as channel_name, c.status as channel_status '
                    'FROM abilities a LEFT JOIN channels c ON a.channel_id = c.id '
                    'WHERE a."group" = ? AND a.model = ? '
                    'ORDER BY a.priority DESC, a.weight DESC',
                    (DEFAULT_GROUP, model),
                ).fetchall()
            allocations = [{
                "channel_id": r["channel_id"],
                "channel_name": r["channel_name"],
                "channel_status": r["channel_status"],
                "enabled": bool(r["enabled"]),
                "priority": r["priority"],
                "weight": r["weight"],
                "tag": r["tag"],
            } for r in rows]
            return {"ok": True, "tool": "newapi_get_model_allocation",
                    "result": {"model": model, "count": len(allocations), "allocations": allocations}}
        except Exception as e:
            logger.warning("get_model_allocation failed: %s", e)
            return {"ok": False, "tool": "newapi_get_model_allocation", "error": "internal_error", "message": str(e)}

    # ── 写操作（三表同步 + 备份） ───────────────────────────

    def set_model_allocation(self, model: str, allocations: list[dict]) -> dict:
        """全量设置某个虚拟模型的渠道分配（先删后插，三表同步）。

        allocations 格式：[{"channel_id": 1, "weight": 100, "priority": 0}, ...]
        会自动：
          1. 备份数据库
          2. 删除该 model 在 abilities 表中的所有旧记录
          3. 对每个渠道：更新 channels.models（加 model）+ channels.model_mapping（加映射）+ 插入 abilities
          4. 从不再分配的渠道中移除 model（channels.models）
        """
        model = (model or "").strip()
        if not model:
            return {"ok": False, "tool": "newapi_set_model_allocation", "error": "invalid_parameter", "message": "model 不能为空"}
        if not allocations:
            return {"ok": False, "tool": "newapi_set_model_allocation", "error": "invalid_parameter", "message": "allocations 不能为空（至少一个渠道）"}

        # 校验 channel_id 存在
        channel_ids = [int(a.get("channel_id", 0)) for a in allocations]
        if any(cid <= 0 for cid in channel_ids):
            return {"ok": False, "tool": "newapi_set_model_allocation", "error": "invalid_parameter", "message": "channel_id 必须为正整数"}

        try:
            bak = self._backup()
            with self._connect() as conn:
                # 1. 查所有现有渠道
                all_channels = conn.execute('SELECT id, models, model_mapping FROM channels').fetchall()

                # 2. 删除该 model 的旧 abilities
                conn.execute(
                    'DELETE FROM abilities WHERE "group" = ? AND model = ?',
                    (DEFAULT_GROUP, model),
                )

                # 3. 对每个渠道更新三表
                new_channel_ids = set(channel_ids)
                for ch in all_channels:
                    cid = ch["id"]
                    # 解析 models
                    try:
                        models_list = json.loads(ch["models"] or "[]")
                    except (json.JSONDecodeError, TypeError):
                        models_list = []
                    # 解析 model_mapping
                    try:
                        mapping = json.loads(ch["model_mapping"] or "{}")
                    except (json.JSONDecodeError, TypeError):
                        mapping = {}

                    if cid in new_channel_ids:
                        # 加 model 到 channels.models
                        if model not in models_list:
                            models_list.append(model)
                        # 加映射（model→model，即不重映射）
                        if model not in mapping:
                            mapping[model] = model
                        # 插入 abilities
                        alloc = next(a for a in allocations if int(a["channel_id"]) == cid)
                        conn.execute(
                            'INSERT INTO abilities ("group", model, channel_id, enabled, priority, weight, tag) '
                            'VALUES (?, ?, ?, 1, ?, ?, ?)',
                            (DEFAULT_GROUP, model, cid,
                             int(alloc.get("priority", 0)),
                             int(alloc.get("weight", 100)),
                             alloc.get("tag")),
                        )
                    else:
                        # 从 channels.models 移除 model
                        if model in models_list:
                            models_list.remove(model)
                        # 从 model_mapping 移除
                        if model in mapping:
                            del mapping[model]

                    # 写回 channels
                    conn.execute(
                        'UPDATE channels SET models = ?, model_mapping = ? WHERE id = ?',
                        (json.dumps(models_list, ensure_ascii=False),
                         json.dumps(mapping, ensure_ascii=False),
                         cid),
                    )

                conn.commit()

            logger.info("set_model_allocation: model=%s, channels=%s, backup=%s", model, channel_ids, bak)
            return {"ok": True, "tool": "newapi_set_model_allocation",
                    "result": {"model": model, "channel_count": len(channel_ids),
                               "channel_ids": channel_ids, "backup": os.path.basename(bak),
                               "note": "需重启 new-api 容器生效"}}
        except Exception as e:
            logger.warning("set_model_allocation failed: %s", e)
            return {"ok": False, "tool": "newapi_set_model_allocation", "error": "internal_error", "message": str(e)}

    def add_model_to_channel(self, model: str, channel_id: int, weight: int = 100, priority: int = 0) -> dict:
        """给单个渠道添加虚拟模型（增量，不影响其他渠道）。"""
        model = (model or "").strip()
        if not model or channel_id <= 0:
            return {"ok": False, "tool": "newapi_add_model_to_channel", "error": "invalid_parameter", "message": "model 和 channel_id 必填"}
        try:
            bak = self._backup()
            with self._connect() as conn:
                ch = conn.execute('SELECT id, models, model_mapping FROM channels WHERE id = ?', (channel_id,)).fetchone()
                if not ch:
                    return {"ok": False, "tool": "newapi_add_model_to_channel", "error": "not_found", "message": f"渠道 {channel_id} 不存在"}
                models_list = json.loads(ch["models"] or "[]")
                mapping = json.loads(ch["model_mapping"] or "{}")
                if model not in models_list:
                    models_list.append(model)
                if model not in mapping:
                    mapping[model] = model
                conn.execute('UPDATE channels SET models=?, model_mapping=? WHERE id=?',
                             (json.dumps(models_list, ensure_ascii=False), json.dumps(mapping, ensure_ascii=False), channel_id))
                # upsert abilities
                conn.execute(
                    'INSERT INTO abilities ("group", model, channel_id, enabled, priority, weight) '
                    'VALUES (?, ?, ?, 1, ?, ?) '
                    'ON CONFLICT("group", model, channel_id) DO UPDATE SET enabled=1, priority=excluded.priority, weight=excluded.weight',
                    (DEFAULT_GROUP, model, channel_id, priority, weight),
                )
                conn.commit()
            return {"ok": True, "tool": "newapi_add_model_to_channel",
                    "result": {"model": model, "channel_id": channel_id, "backup": os.path.basename(bak),
                               "note": "需重启 new-api 容器生效"}}
        except Exception as e:
            return {"ok": False, "tool": "newapi_add_model_to_channel", "error": "internal_error", "message": str(e)}
