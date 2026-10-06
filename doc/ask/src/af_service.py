"""AutoForge 服务层（只读 API 的后端逻辑，**无 Web 依赖**）。

对齐《AutoForge-UI 开工令》附录 A 契约：每个函数返回可直接 JSON 化的 dict，
HTTP 层（`af_api.py`）只做路由与错误码映射。

Round 1 只读：不连真实 HA、不写设备、不需要令牌。故障注入只作用于本地 FakeHA 仿真。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .af_adapters import DEFAULT_HA_URL, HAAdapter, HAStateProvider, HATransport
from .af_catalog import DeviceCatalog
from .af_conf import AUTO_MIN, SHADOW_LOW, ConfidenceStore, decision_for
from .af_expect import evaluate_graph_expects
from .af_executor import classify_answer
from .af_fault import FAULT_META, FOUR_FAILURES, FaultKind
from .af_ir import Graph, IRValidationError, load_graph
from .af_nl import render_graph
from .af_runtime import Runtime, build_runtime
from .af_scanner import DeviceGuardRegistry, Diagnostic, ScanResult, StaticScanner
from .af_spec import SpecError, compile_spec, graph_to_raw, render_spec
from .af_store import GraphStore, diff_graphs
from .af_vhass import FakeHAAdapter, seed_from_graph
from .af_pending import PendingLimitExceeded, PendingStore
from .af_error_knowledge import ErrorKnowledge
from .af_experience import ExperienceStore
from .af_telemetry import TelemetryStore, attach as attach_telemetry

logger = logging.getLogger("autoforge.service")

__all__ = [
    "CONTRACT_VERSION",
    "API_VERSION",
    "MILESTONES",
    "ServiceError",
    "health",
    "list_graphs",
    "get_graph",
    "save_graph",
    "build",
    "simulate",
    "get_conf",
    "intervene",
    "diff",
    "spec_of",
    "compile_text",
    "faults",
    "bootstrap_examples",
    # Round 2-A：会话（ask 审批的人机回路）
    "create_session",
    "list_sessions",
    "get_session",
    "answer_session",
    "tick_session",
    "cancel_session",
    "delete_session",
    "SESSION_TTL_S",
    # Round 2-B：真机下发
    "live_status",
    "live_run",
    "get_metrics",
    # v1.4.0 待批队列（部署前写操作先入队，人审后回放落盘）
    "submit_pending",
    "list_pending",
    "approve_pending",
    "reject_pending",
    "LIVE_TRANSPORT_FACTORY",
    # v0.7.0 模板导出与备份恢复
    "export_store",
    "import_store",
    # v1.1.0 实体事实内建（设备目录 + 解析）
    "catalog_refresh",
    "catalog_resolve",
    "catalog_list",
    "catalog_state",
    "catalog_snapshot",
    # v1.5.0 经验闭环
    "get_telemetry",
    "get_experience",
    "export_experience",
    "catalog_resolve_metrics",
    # v1.6.0 决策智能
    "catalog_set_alias",
    "catalog_list_aliases",
    "catalog_remove_alias",
    "bind_ir",
]


class ServiceError(Exception):
    """服务层错误。HTTP 层据 `status` 映射状态码。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status

CONTRACT_VERSION = "1.0"
API_VERSION = "0.1.0"
MILESTONES = ("G1", "G2", "G3", "G4", "G5", "真机接线", "G6", "G7")


# ─────────────────────────────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────────────────────────────


def _diag(d: Diagnostic) -> dict[str, Any]:
    return {
        "code": d.code,
        "level": d.level,
        "message": d.message,
        "automation_id": d.automation_id,
        "node_id": d.node_id,
        # v1.2.0：怎么改（Agent 据此一次往返自修正）
        "hint": d.hint,
    }


def _diagnostics(result: ScanResult) -> list[dict[str, Any]]:
    return [_diag(d) for d in result.diagnostics]


def _load_ir(ir: Mapping[str, Any]) -> Graph:
    """从请求体里的 IR（单条或 `{"automations": [...]}`）构造 Graph。"""
    return load_graph(dict(ir))


def _scan(graph: Graph, known: Iterable[str] | None = None) -> list[dict[str, Any]]:
    return _diagnostics(StaticScanner(graph, known_entities=known).scan())


def _ir_payload(graph: Graph) -> Any:
    raws = graph_to_raw(graph)
    return raws[0] if len(raws) == 1 else {"automations": raws}


def _entities_of(graph: Graph) -> set[str]:
    out: set[str] = set()
    for auto in graph:
        out |= auto.reads() | auto.writes()
    return out


# ─────────────────────────────────────────────────────────────────────
# 1. 健康
# ─────────────────────────────────────────────────────────────────────


def health() -> dict[str, Any]:
    return {
        "ok": True,
        "version": API_VERSION,
        "contract_version": CONTRACT_VERSION,
        "milestones": list(MILESTONES),
        "readonly": True,
    }


# ─────────────────────────────────────────────────────────────────────
# 2/3. 归档列表 / 某版本 Graph
# ─────────────────────────────────────────────────────────────────────


def _archive_names(store: GraphStore) -> list[str]:
    """已归档的名字（去重、保持出现顺序）。"""
    seen: list[str] = []
    for record in store.history():
        name = str(record.get("name", ""))
        if name and name not in seen:
            seen.append(name)
    return seen


def list_graphs(store: GraphStore) -> dict[str, Any]:
    latest: dict[str, dict[str, Any]] = {}
    for record in store.history():
        name = str(record.get("name", ""))
        version = int(record.get("version") or 0)
        if name not in latest or version > int(latest[name]["latest_version"]):
            latest[name] = {
                "name": name,
                "latest_version": version,
                "saved_at": record.get("saved_at", ""),
                "note": record.get("note", ""),
                "mode": "",
                "owner": record.get("owner", ""),  # v1.3.0 归档归属
                "automation_ids": [],
                }
    # 补上 mode / automation_ids / tags（供列表页把"归档名 ↔ 自动化 id / 置信度"对上）
    for name, item in latest.items():
        try:
            graph = store.load(name, int(item["latest_version"]))
        except (FileNotFoundError, IRValidationError, OSError):
            continue
        autos = list(graph)
        item["automation_ids"] = [a.id for a in autos]
        item["mode"] = autos[0].mode if len(autos) == 1 else "multi"
        item["tags"] = store.get_tags(name)
    return {"ok": True, "items": sorted(latest.values(), key=lambda x: x["name"])}


# ─────────────────────────────────────────────────────────────────────
# v0.6.0 标签体系 + 批量启停
# ─────────────────────────────────────────────────────────────────────


def set_graph_tags(store: GraphStore, name: str, tags: list[str]) -> dict[str, Any]:
    """设置某归档名字的标签（覆盖式）。"""
    store.set_tags(name, tags)
    return {"ok": True, "name": name, "tags": store.get_tags(name)}


def graphs_by_tag(store: GraphStore, tag: str) -> dict[str, Any]:
    """返回带某标签的所有归档名（及其最新版本信息）。"""
    names = [n for n, ts in store.all_tags().items() if tag in ts]
    items = []
    for name in names:
        try:
            latest_version = store.latest(name)
            if latest_version is None:
                continue
            graph = store.load(name, latest_version)
        except (FileNotFoundError, IRValidationError, OSError):
            continue
        items.append(
            {
                "name": name,
                "version": latest_version,
                "automation_ids": [a.id for a in graph],
                "tags": store.get_tags(name),
            }
        )
    return {"ok": True, "tag": tag, "items": sorted(items, key=lambda x: x["name"])}


def enable_by_tag(
    store: GraphStore, tag: str, enabled: bool, allow_bulk: bool = False
) -> dict[str, Any]:
    """批量启停：对某标签下所有归档的最新版本，翻转其中全部自动化的 `enabled` 并保存新版本。

    返回受影响（已落新版本）的归档名列表。未找到该标签 → 空列表。

    v1.3.0：默认受**爆炸半径**约束——先把受影响自动化条数算出来再决定要不要做，
    **绝不做一半才报错**。显式传 `allow_bulk=True` 绕过（真正想批量时用）。
    """
    names = [n for n, ts in store.all_tags().items() if tag in ts]
    # 先算清影响面（不落盘），超阈值直接拒——避免"改了一半才失败"
    planned: list[tuple[str, int, Any]] = []
    total = 0
    for name in names:
        try:
            version = store.latest(name)
            if version is None:
                continue
            graph = store.load(name, version)
        except (FileNotFoundError, IRValidationError, OSError):
            continue
        planned.append((name, int(version or 0), graph))
        total += len(list(graph))
    if not allow_bulk:
        _check_blast(total, f"按标签 {tag!r} 批量{'启用' if enabled else '禁用'}")

    affected: list[dict[str, Any]] = []
    for name, _version, graph in planned:
        for auto in graph:
            auto.enabled = enabled
            auto.raw["enabled"] = enabled  # 序列化形态同步翻转，保证 re-save 后 round-trip 一致
        new_version = store.save(
            graph,
            name,
            note=f"v0.6.0 批量{'启用' if enabled else '禁用'}（tag={tag}）",
        )
        affected.append({"name": name, "version": new_version, "enabled": enabled})
    return {
        "ok": True,
        "tag": tag,
        "enabled": enabled,
        "affected": affected,
        "blast_radius": {"affected": total, "limit": blast_radius_limit()},
    }


def get_graph(store: GraphStore, name: str, version: int | None = None) -> dict[str, Any]:
    record = store.load_record(name, version)  # 不存在 → FileNotFoundError
    graph = load_graph(record["graph"])
    return {
        "ok": True,
        "name": record.get("name", name),
        "version": record.get("version"),
        "saved_at": record.get("saved_at", ""),
        "note": record.get("note", ""),
        "ir": _ir_payload(graph),  # 单条自动化 → 单 dict；多条 → {"automations": [...]}
        "nl": render_graph(graph).text,
        "diagnostics": _scan(graph),
    }


def save_graph(
    store: GraphStore,
    ir: Mapping[str, Any],
    name: str,
    note: str = "",
    tags: list[str] | None = None,
    expect_version: int | None = None,
    owner: str = "",
    allow_bulk: bool = False,
) -> dict[str, Any]:
    """归档一个 Graph 版本（**先过第一道闸**，与 MCP/HTTP/CLI 同源）。

    安全约定：**拒绝归档未通过静态扫描的 IR**（`ok=false`）——避免把坏自动化落盘。
    `tags` 非空时一并写入标签元数据；`expect_version` 非空时做乐观锁校验（v0.9.0，
    不匹配抛 `WriteConflictError`）。同时写入置信度种子快照，与 `forge store save` 等价。

    v1.3.0：
    - `owner`：**归档归属**（谁建的），多 agent 接入后用于所有权隔离；
    - `allow_bulk=False`（默认）时受**爆炸半径**约束——一次归档的自动化条数超限即拒。
      显式传 `allow_bulk=True` 表示「我确认这是一次批量操作」，绕过该限制。
    """
    if not name or not str(name).strip():
        raise ServiceError("归档名不能为空", status=400)
    graph = _load_ir(ir)
    scan = StaticScanner(
        graph,
        device_guard=load_device_guard(store),
        entity_health=load_entity_health(store),
    ).scan()
    if not scan.ok:
        raise ServiceError(
            f"拒绝归档：IR 未通过静态扫描（{len(scan.errors)} 个错误）。先修 IR 或改用 af_build 查看诊断。",
            status=400,
        )
    count = len(list(graph))
    if not allow_bulk:
        _check_blast(count, f"本次归档 {str(name).strip()!r} 含 {count} 条自动化")
    # v1.3.0 所有权隔离：已归属他人的归档不能覆盖（归属为空视为公共，放行）
    prev_owner = _existing_owner(store, str(name).strip())
    if prev_owner and owner and prev_owner != owner:
        raise ServiceError(
            f"归档 {str(name).strip()!r} 归属 {prev_owner!r}，当前主体 {owner!r} 无权覆盖（所有权隔离）",
            status=403,
        )
    version = store.save(
        graph, str(name).strip(), note=note, tags=tags,
        expect_version=expect_version, owner=owner,
    )
    store.save_conf(ConfidenceStore().seed(graph), str(name).strip(), note)
    # v1.5.0：**仅在成功落盘后**采集实体共现经验（失败样本不污染）
    try:
        ExperienceStore(store.root).observe(graph, ok=True)
    except (OSError, ValueError, KeyError, AttributeError):
        logger.debug("EXPERIENCE_OBSERVE_SKIPPED", exc_info=True)
    return {
        "ok": True,
        "name": str(name).strip(),
        "version": version,
        "note": note,
        "owner": owner,
        "tags": store.get_tags(str(name).strip()),
        "blast_radius": {"affected": count, "limit": blast_radius_limit()},
        "warnings": [_diag(d) for d in scan.warnings],
    }


# ─────────────────────────────────────────────────────────────────────
# 3.1 待批队列（v1.4.0，调研 §2.6）
#
# 部署前写操作（如 af_save）先入队 `pending/`，人审（HTTP / CLI）后回放 payload 落盘。
# **MCP 面绝不注册 approve**（agent 不能自批）：提交在 MCP，批准只在服务层。
# ─────────────────────────────────────────────────────────────────────


def _blast_radius_of(ir: Mapping[str, Any]) -> dict[str, Any]:
    """复用 v1.3.0 的爆炸半径口径：影响面 = 本 IR 自动化条数。"""
    try:
        graph = _load_ir(ir)
        affected = len(list(graph))
    except (IRValidationError, ValueError, KeyError):
        affected = 0
    return {"affected": affected, "limit": blast_radius_limit()}


def submit_pending(
    store: GraphStore,
    tool: str,
    payload: Mapping[str, Any],
    *,
    summary: str | None = None,
    submitted_by: str = "",
    agent_key: str | None = None,
) -> dict[str, Any]:
    """把一条写操作入待批队列；per-agent 熔断达上限抛 `ServiceError(400)`。

    v1.4.0：提交前先过**第一道闸**（静态扫描）——未通过即拒绝入队（fail-fast），
    避免坏自动化占坑等人审；`approve` 回放时 `save_graph` 会**再校验一次**（双保险）。
    """
    ir = payload.get("ir")
    if ir is not None:
        try:
            graph = _load_ir(ir)
        except (IRValidationError, ValueError, KeyError) as exc:
            raise ServiceError(f"拒绝归档：IR 结构非法（{exc}）", status=400)
        scan = StaticScanner(
            graph,
            device_guard=load_device_guard(store),
            entity_health=load_entity_health(store),
        ).scan()
        if not scan.ok:
            raise ServiceError(
                f"拒绝归档：IR 未通过静态扫描（{len(scan.errors)} 个错误）。"
                f"先修 IR 或改用 af_build 查看诊断。",
                status=400,
            )
    ps = PendingStore(store.root)
    br = payload.get("blast_radius") or _blast_radius_of(payload.get("ir", {}))
    summary = summary or f"{tool}：{payload.get('name', '')}"
    try:
        op = ps.submit(
            tool, payload, summary=summary, blast_radius=br,
            submitted_by=submitted_by, agent_key=agent_key,
        )
    except PendingLimitExceeded as exc:
        raise ServiceError(str(exc), status=400)
    return {
        "ok": True,
        "pending": op.op_id,
        "summary": op.summary,
        "blast_radius": op.blast_radius,
        "submitted_by": op.submitted_by,
        "submitted_at": op.submitted_at,
        "agent": op.agent,
        "note": "待人工审批（approve 后才会落盘）",
    }


def list_pending(store: GraphStore, agent: str | None = None) -> dict[str, Any]:
    """列出待批（审批视图：含 summary / blast_radius / 完整 payload 供回放）。"""
    return {"ok": True, "items": PendingStore(store.root).list(agent)}


def approve_pending(store: GraphStore, op_id: str, reviewer: str = "human") -> dict[str, Any]:
    """批准并回放 payload 落盘（复用 `_t_save` 的落盘逻辑，无二次渲染）。

    reviewer 硬编码 `"human"`（服务层约束：agent 不能自批）；HTTP 层传令牌主体。
    """
    ps = PendingStore(store.root)
    op = ps.load(op_id)
    if op is None:
        raise ServiceError(f"未找到待批操作 {op_id!r}", status=404)
    payload = op["payload"]
    result = save_graph(
        store,
        payload.get("ir"),
        payload.get("name", ""),
        note=payload.get("note", ""),
        tags=payload.get("tags"),
        expect_version=payload.get("expect_version"),
        owner=payload.get("owner", ""),
        allow_bulk=bool(payload.get("allow_bulk", False)),
    )
    ps.delete(op_id)
    result["approved_by"] = reviewer
    result["pending"] = op_id
    return result


def reject_pending(store: GraphStore, op_id: str, reason: str = "") -> dict[str, Any]:
    """拒绝并删除待批操作（不落盘）。"""
    ps = PendingStore(store.root)
    op = ps.load(op_id)
    if op is None:
        raise ServiceError(f"未找到待批操作 {op_id!r}", status=404)
    ps.delete(op_id)
    logger.warning("PENDING_REJECTED op=%s reason=%s", op_id, reason)
    return {"ok": True, "rejected": op_id, "reason": reason}


# ─────────────────────────────────────────────────────────────────────
# 3.2 经验闭环出口（v1.5.0）：遥测 + 实体共现
# ─────────────────────────────────────────────────────────────────────


def get_telemetry(store: GraphStore, days: int = 30) -> dict[str, Any]:
    """token/结果遥测四维聚合 + 错误知识库类别分布（v1.5.0）。"""
    out = TelemetryStore(store.root).usage(days=days)
    out["knowledge"] = ErrorKnowledge(store.root).counts()
    return out


def get_experience(store: GraphStore, *, limit: int = 10) -> dict[str, Any]:
    """实体共现经验摘要（v1.5.0，喂 MA 的采集事实面）。"""
    return ExperienceStore(store.root).summary(limit=limit)


def export_experience(store: GraphStore, *, limit: int = 200) -> dict[str, Any]:
    """实体共现经验结构化导出（v1.5.0）。"""
    return ExperienceStore(store.root).export(limit=limit)


# ─────────────────────────────────────────────────────────────────────
# 4. 静态扫描（第一道闸）
# ─────────────────────────────────────────────────────────────────────

#: v1.4.0：设备保护注册表文件名（放 store 根；可入库、可热更新，无需重启）。
DEVICE_ACL_FILENAME = "device_acl.json"


#: 进程级缓存 `{catalog_path: (mtime, size, health_map)}`。
#: 审计发现：原先**每次** build/save_graph/submit_pending 都 new 一个 DeviceCatalog
#: 并全量解析目录（NAS 上约 2888 实体，数 MB JSON），而实例级 mtime 缓存跨请求完全不命中。
_ENTITY_HEALTH_CACHE: dict[str, tuple[float, int, dict[str, Any]]] = {}
_ENTITY_HEALTH_LOCK = threading.Lock()


def load_entity_health(store: GraphStore | None) -> dict[str, Any]:
    """v1.6.0 P2 联动验证闸：从设备目录取「此刻可用性」视图。

    目录为空 / 读取异常 → 空 dict（离线编写 IR 场景**零误报**；核心能力不受损）。
    按目录文件的 `(mtime, size)` 做**进程级缓存**，命中即复用，避免每次扫描全量重解析。
    """
    if store is None:
        return {}
    try:
        path = DeviceCatalog(store.root).catalog_path
        try:
            stat = path.stat()
        except OSError:
            return {}
        key = str(path)
        with _ENTITY_HEALTH_LOCK:
            cached = _ENTITY_HEALTH_CACHE.get(key)
        if cached is not None and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
            return cached[2]
        data = DeviceCatalog(store.root).health_map()
        with _ENTITY_HEALTH_LOCK:
            _ENTITY_HEALTH_CACHE[key] = (stat.st_mtime, stat.st_size, data)
        return data
    except (OSError, ValueError, KeyError, AttributeError):
        logger.debug("ENTITY_HEALTH_MAP_SKIPPED", exc_info=True)
        return {}


def load_device_guard(store: GraphStore | None) -> DeviceGuardRegistry | None:
    """从 `{store.root}/device_acl.json` 加载设备保护注册表（v1.4.0）。

    - 文件不存在 → 返回 None（不启用分级保护，向后兼容）；
    - 每次调用**重新读盘**：规则文件改动后下次 `scan()` 自动生效（热更新，无需重启）；
    - 读盘/解析失败 → 记日志并返回 None（不让坏策略文件把服务打挂）。

    规则文件形态见 `DeviceGuardRegistry.from_file`（规则数组 / 旧 ACL 对象 / `{"rules":[…]}`）。
    """
    if store is None:
        return None
    path = Path(store.root) / DEVICE_ACL_FILENAME
    if not path.is_file():
        return None
    try:
        return DeviceGuardRegistry.from_file(path)
    except (OSError, ValueError) as exc:
        logger.warning("DEVICE_ACL_LOAD_FAILED path=%s err=%s", path, exc)
        return None


def build(
    ir: Mapping[str, Any],
    known_entities: Iterable[str] | None = None,
    *,
    store: GraphStore | None = None,
    use_catalog: bool = False,
) -> dict[str, Any]:
    """第一道闸：静态扫描 + NL 渲染。

    v1.1.0：`use_catalog=True` 且调用方未显式给 `known_entities` 时，
    **自动取本机设备目录全集**，让「实体存在性校验」默认开启。
    目录为空时**静默退化**为不校验（避免把「没刷过目录」误判成「实体全不存在」）。
    """
    known = known_entities
    catalog_used = False
    if known is None and use_catalog and store is not None:
        ids = DeviceCatalog(store.root).known_entity_ids()
        if ids:
            known = ids
            catalog_used = True
    graph = _load_ir(ir)
    result = StaticScanner(
        graph,
        known_entities=known,
        device_guard=load_device_guard(store),
        entity_health=load_entity_health(store),
    ).scan()
    out = {
        "ok": result.ok,
        "errors": [_diag(d) for d in result.errors],
        "warnings": [_diag(d) for d in result.warnings],
        "diagnostics": _diagnostics(result),
        "nl": render_graph(graph).text,
        "entity_check": "catalog" if catalog_used else ("provided" if known is not None else "skipped"),
    }
    # v1.5.0：失败回执附归因 + 历史同类（写→读闭环）
    first_error = result.errors[0].message if result.errors else ""
    return attach_telemetry(
        out,
        tool="build",
        root=(store.root if store is not None else None),
        ok=result.ok,
        message=first_error,
    )


# ─────────────────────────────────────────────────────────────────────
# 5. 仿真回放（第二道闸）
# ─────────────────────────────────────────────────────────────────────


def _replay(runtime: Runtime, states: Any, events: Sequence[Mapping[str, Any]]) -> None:
    for item in events:
        advance = item.get("advance_s")
        if advance:
            runtime.advance(float(advance))
        entity_id = item.get("entity_id")
        if entity_id:
            state = str(item.get("state", ""))
            states.set(str(entity_id), state)
            runtime.emit(str(entity_id), state, last_changed=item.get("last_changed"))
        elif advance is None:
            runtime.tick()


def _expect_failure_message(report: Any) -> str:
    """从 `expect` 报告里取一条可归因的失败说明（没有失败则空串）。"""
    if not isinstance(report, dict):
        return ""
    for item in report.get("items", []) or []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", item.get("result", ""))).lower()
        if status in ("fail", "failed", "false"):
            target = item.get("target") or item.get("entity_id") or item.get("var") or ""
            detail = item.get("reason") or item.get("detail") or item.get("message") or ""
            return f"expect 断言失败：{target} {detail}".strip()
    if report.get("ok") is False:
        return "expect 断言失败（详见报告）"
    return ""


def simulate(
    ir: Mapping[str, Any],
    seed: Mapping[str, str] | None = None,
    events: Sequence[Mapping[str, Any]] | None = None,
    *,
    store: GraphStore | None = None,
) -> dict[str, Any]:
    """第二道闸：在仿真里回放，**并对 IR 声明的 `expect` 逐条断言**（v1.2.0）。

    返回里的 `expect` 是「跑**对**了吗」的答案：
    - `ok`：没有断言失败；
    - `fully_verified`：**声明过断言且全部验过**（无 fail 且无 unverified）——
      比 `ok` 更严；`ok=True` 只代表「没抓到反例」。
    """
    graph = _load_ir(ir)
    runtime = build_runtime(graph)
    states = seed_from_graph(graph, seed or {}, clock=runtime.clock)
    runtime.states = states
    runtime.instances.states = states
    runtime.scheduler.states = states
    runtime.executor.states = states  # canary 漂移检测读同一份状态源
    adapter = FakeHAAdapter(states)
    runtime.adapters.register(adapter)

    _replay(runtime, states, list(events or []))

    # v1.2.0：后置条件断言求值（变量形态在任一实例的 vars 里成立即通过）
    var_sources = [inst.ctx.vars for inst in runtime.instances.all()]
    expect_report = evaluate_graph_expects(
        graph, states, var_sources, unmodeled_actions=getattr(adapter, "unmodeled", ())
    )

    final_states = {e: states.get(e) for e in sorted(_entities_of(graph))}
    out = {
        "ok": True,
        "instances": [i.to_dict() for i in runtime.instances.all()],
        "audit": [e.to_dict() for e in runtime.audit],
        "bus": runtime.bus.stats(),
        "final_states": final_states,
        "expect": expect_report,
        "nl": render_graph(graph).text,
    }
    # v1.5.0：sim 的 `ok` 恒 True（失败以 `expect` 报告）；`_telemetry.ok` 反映**断言是否通过**。
    return attach_telemetry(
        out,
        tool="simulate",
        root=(store.root if store is not None else None),
        ok=bool(expect_report.get("ok", True)),
        message=_expect_failure_message(expect_report),
    )


# ─────────────────────────────────────────────────────────────────────
# 6/7. 置信度分级（G4）
# ─────────────────────────────────────────────────────────────────────


def _conf_of(store: GraphStore, name: str, graph: Graph) -> ConfidenceStore:
    try:
        return store.load_conf(name)
    except FileNotFoundError:
        return ConfidenceStore().seed(graph)


def _conf_items(graph: Graph, conf: ConfidenceStore) -> list[dict[str, Any]]:
    return [
        {"automation_id": a.id, "confidence": conf.get(a.id), "band": conf.band(a.id)}
        for a in graph
    ]


#: `_all` / `all` 视为"全部归档"（列表页与置信度面板的聚合视图）
_ALL_NAMES = frozenset({"_all", "all"})

#: v1.3.0 **爆炸半径**：一次写操作最多影响的自动化条数。
#:
#: 动机（调研 §B2.4）：AutoForge 此前没有「一次能改多少」的上限——
#: `import_store` 可一次导入整个 bundle，`enable_by_tag` 可一次翻转一个 tag 下全部。
#: 防「agent 一次手滑改完全部自动化」最有效的一招就是把它变成一等公民参数。
#: `0` 表示不限（导入 bundle 这类**显式批量**操作需单独用 `allow_bulk=true` 放开）。
DEFAULT_BLAST_RADIUS = 8


def blast_radius_limit() -> int:
    """当前爆炸半径上限（可用 `AUTOFORGE_BLAST_RADIUS` 覆盖；`0` = 不限）。"""
    try:
        return int(os.getenv("AUTOFORGE_BLAST_RADIUS", str(DEFAULT_BLAST_RADIUS)))
    except (TypeError, ValueError):
        return DEFAULT_BLAST_RADIUS


def _existing_owner(store: GraphStore, name: str) -> str:
    """某归档**最近一次**记录的归属主体（v1.3.0 所有权隔离用）。"""
    for record in reversed(list(store.history())):
        if str(record.get("name", "")) == name and record.get("owner"):
            return str(record["owner"])
    return ""


def _check_blast(count: int, what: str) -> None:
    """超过爆炸半径即拒绝——**提示「请拆分」而不是硬拒到无法工作**。"""
    limit = blast_radius_limit()
    if limit > 0 and count > limit:
        raise ServiceError(
            f"{what} 会影响 {count} 条自动化，超过爆炸半径上限 {limit}；"
            f"请拆分为更小的操作（或调高 AUTOFORGE_BLAST_RADIUS；置 0 表示不限）",
            status=400,
        )


def get_conf(store: GraphStore, name: str) -> dict[str, Any]:
    if name in _ALL_NAMES:
        merged: dict[str, dict[str, Any]] = {}
        for archive in _archive_names(store):
            try:
                graph = store.load(archive)
            except (FileNotFoundError, IRValidationError, OSError):
                continue
            conf = _conf_of(store, archive, graph)
            for item in _conf_items(graph, conf):
                merged.setdefault(item["automation_id"], item)
        return {
            "ok": True,
            "name": name,
            "version": None,
            "items": sorted(merged.values(), key=lambda x: x["automation_id"]),
            "thresholds": {"auto": AUTO_MIN, "shadow_low": SHADOW_LOW},
        }

    record = store.load_record(name)
    graph = load_graph(record["graph"])
    conf = _conf_of(store, name, graph)
    return {
        "ok": True,
        "name": name,
        "version": record.get("version"),
        "items": _conf_items(graph, conf),
        "thresholds": {"auto": AUTO_MIN, "shadow_low": SHADOW_LOW},
    }


def intervene(store: GraphStore, name: str, automation_id: str) -> dict[str, Any]:
    if name in _ALL_NAMES:
        # 找到包含该 automation 的归档，干预落实到那个归档
        for archive in _archive_names(store):
            try:
                graph = store.load(archive)
            except (FileNotFoundError, IRValidationError, OSError):
                continue
            if automation_id in {a.id for a in graph}:
                name = archive
                break
        else:
            raise KeyError(automation_id)

    record = store.load_record(name)
    graph = load_graph(record["graph"])
    if automation_id not in {a.id for a in graph}:
        raise KeyError(automation_id)
    conf = _conf_of(store, name, graph)
    conf.record_negative(automation_id)
    store.save_conf(conf, name, note=f"manual intervene: {automation_id}")
    return {
        "ok": True,
        "name": name,
        "items": _conf_items(graph, conf),
        "thresholds": {"auto": AUTO_MIN, "shadow_low": SHADOW_LOW},
    }


# ─────────────────────────────────────────────────────────────────────
# 8. 版本 diff（G6）
# ─────────────────────────────────────────────────────────────────────


def _node_obj(address: str) -> dict[str, Any]:
    """`{automation_id}:{node_id}` → 结构化对象（供前端渲染）。"""
    automation_id, _, node_id = address.partition(":")
    return {"node_id": address, "address": address, "automation_id": automation_id, "node": node_id}


def _edge_obj(text: str) -> dict[str, Any]:
    """`{automation_id}:{from}->{to}:{kind}` → 结构化对象。"""
    automation_id, _, rest = text.partition(":")
    from_, _, tail = rest.partition("->")
    to, _, kind = tail.partition(":")
    return {"automation_id": automation_id, "from": from_, "to": to, "kind": kind, "edge": text}


def _record_note(store: GraphStore, name: str, version: int) -> str:
    """读取某版本的**归档备注**。

    `note` 属于归档记录元数据（`GraphStore.save(note=...)`），**不在 Graph 内**，
    因此不参与 `diff_graphs` 的图内容比对；此处单独回传，供前端在
    "内容无差异" 时说明"只是归档备注不同"。
    """
    try:
        return str(store.load_record(name, version).get("note", "") or "")
    except (OSError, ValueError, KeyError):
        return ""


def diff(store: GraphStore, name: str, old: int, new: int) -> dict[str, Any]:
    old_graph = store.load(name, old)
    new_graph = store.load(name, new)
    result = diff_graphs(old_graph, new_graph)
    return {
        "ok": True,
        "name": name,
        "old": old,
        "new": new,
        "render": result.render(),
        "notes": {
            "old": _record_note(store, name, old),
            "new": _record_note(store, name, new),
        },
        "structured": {
            "added_automations": result.added_automations,
            "removed_automations": result.removed_automations,
            "added_nodes": [_node_obj(a) for a in result.added_nodes],
            "removed_nodes": [_node_obj(a) for a in result.removed_nodes],
            "changed_nodes": [
                {"node_id": a, "address": a, "changes": f} for a, f in result.changed_nodes
            ],
            # v1.3.0：id 重命名（签名兜底配对），避免 Agent 改个 node id 就被误报成删+加
            "renamed_nodes": [
                {"from": o, "to": n, "matched_by": "signature"} for o, n in result.renamed_nodes
            ],
            "added_edges": [_edge_obj(e) for e in result.added_edges],
            "removed_edges": [_edge_obj(e) for e in result.removed_edges],
            "meta_changes": [
                {"key": f"{a}.{f}", "automation_id": a, "field": f, "old_value": o, "new_value": n}
                for a, f, o, n in result.meta_changes
            ],
        },
    }


# ─────────────────────────────────────────────────────────────────────
# 9/10. AF-Spec（G7）
# ─────────────────────────────────────────────────────────────────────


def spec_of(store: GraphStore, name: str, version: int | None = None) -> dict[str, Any]:
    graph = store.load(name, version)
    return {
        "ok": True,
        "name": name,
        "version": version if version is not None else store.latest(name),
        "spec": render_spec(graph),
    }


def compile_text(text: str) -> dict[str, Any]:
    try:
        graph = compile_spec(text)
    except (SpecError, IRValidationError) as exc:
        return {
            "ok": False,
            "ir": None,
            "nl": "",
            "diagnostics": [],
            "error": {"code": type(exc).__name__, "message": str(exc)},
        }
    result = StaticScanner(graph).scan()
    return {
        "ok": result.ok,
        "ir": _ir_payload(graph),
        "nl": render_graph(graph).text,
        "diagnostics": _diagnostics(result),
    }


# ─────────────────────────────────────────────────────────────────────
# 11. 故障注入图鉴（G5，静态元数据）
# ─────────────────────────────────────────────────────────────────────


def faults() -> dict[str, Any]:
    kinds = []
    for kind in FaultKind:
        meta = FAULT_META.get(kind.value, {})
        kinds.append(
            {
                "value": kind.value,
                "label": meta.get("label", kind.value),
                "inject_layer": meta.get("inject_layer", ""),
                "expected_handler": meta.get("expected_handler", ""),
            }
        )
    failures = [
        {
            "key": key,
            "label": info.get("label", key),
            "edge": info.get("edge", ""),
            "audit": info.get("audit", ""),
            "recoverable": info.get("recoverable", "") == "yes",  # 布尔，供前端直接进 v-if
        }
        for key, info in FOUR_FAILURES.items()
    ]
    return {"ok": True, "kinds": kinds, "failures": failures}


# ─────────────────────────────────────────────────────────────────────
# 启动引导：把 examples/ir 灌入归档（让只读控制台一开就有数据）
# ─────────────────────────────────────────────────────────────────────


def bootstrap_examples(store: GraphStore, examples_dir: str | Path) -> list[str]:
    """把样例 IR 归档进 store（幂等：同名已有版本则跳过）。返回新归档的名字。

    版本化样例：除主文件 `<name>.json`（存为 v1）外，若存在同级
    `<name>.v2.json` / `<name>.v3.json` …，会依次保存为 v2 / v3 …，
    让控制台「版本与 Diff」页开箱即可演示（R5：依赖 ≥2 版本）。
    """
    directory = Path(examples_dir)
    if not directory.is_dir():
        return []
    created: list[str] = []
    for path in sorted(directory.glob("*.json")):
        stem = path.stem
        # 跳过版本化样例（`<name>.v2.json` 等），它们由主样例 `<name>.json` 触发保存
        if ".v" in stem and stem.rsplit(".v", 1)[1].isdigit():
            continue
        name = stem
        if store.versions(name):
            continue
        try:
            graph = load_graph(path)
        except (IRValidationError, json.JSONDecodeError, OSError):
            continue
        store.save(graph, name, note="bootstrap: examples/ir")
        created.append(name)
        # 版本化样例：若存在 `<name>.v2.json` / `.v3.json` … 依次存为 v2 / v3 …
        v = 2
        while True:
            vp = directory / f"{name}.v{v}.json"
            if not vp.exists():
                break
            try:
                vg = load_graph(vp)
            except (IRValidationError, json.JSONDecodeError, OSError):
                break
            store.save(vg, name, note=f"bootstrap: examples/ir v{v}")
            v += 1
    return created


# ─────────────────────────────────────────────────────────────────────
# Round 2-A：会话（支撑 ask 审批的"人机回路"）
#
# `/api/sim` 是一次性回放；`ask` 需要"挂起 → 人工应答 → 继续"跨请求的状态，
# 因此引入**进程内会话**：创建后运行到挂起/终态，之后可反复 answer / tick。
# ⚠️ 会话在内存中（单进程、可配置 TTL），服务重启即丢失——原型期足够。
# ─────────────────────────────────────────────────────────────────────

#: 会话存活上限（秒），可用环境变量覆盖
SESSION_TTL_S = float(os.getenv("AUTOFORGE_SESSION_TTL_S", "3600"))

_SESSIONS: dict[str, dict[str, Any]] = {}
_SESSIONS_LOCK = threading.Lock()


def _build_sim_runtime(graph: Graph, seed: Mapping[str, str] | None):
    """装配"仿真运行态"：FakeHA 状态源 + 会真改状态的 ha 适配器。"""
    runtime = build_runtime(graph)
    states = seed_from_graph(graph, seed or {}, clock=runtime.clock)
    runtime.states = states
    runtime.instances.states = states
    runtime.scheduler.states = states
    runtime.executor.states = states  # canary 漂移检测读同一份状态源
    runtime.adapters.register(FakeHAAdapter(states))
    return runtime, states


def _asks_of(runtime: Runtime) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for session in runtime.executor.pending_asks.values():
        try:
            automation_id = runtime.instances.get(session.instance_id).ctx.automation_id
        except KeyError:  # pragma: no cover - 防御
            automation_id = ""
        out.append(
            {
                "ask_id": session.instance_id,  # 一个实例同一时刻只有一个挂起 ask
                "instance_id": session.instance_id,
                "node_id": session.node_id,
                "room": session.room,
                "prompt": session.prompt,
                "automation_id": automation_id,
            }
        )
    return out


def _session_view(sess: Mapping[str, Any]) -> dict[str, Any]:
    runtime: Runtime = sess["runtime"]
    return {
        "ok": True,
        "session_id": sess["id"],
        "mode": sess.get("mode", "sim"),
        "instances": [i.to_dict() for i in runtime.instances.all()],
        "audit": [e.to_dict() for e in runtime.audit],
        "bus": runtime.bus.stats(),
        "final_states": {e: sess["states"].get(e) for e in sorted(sess["entities"])},
        "nl": render_graph(sess["graph"]).text,
        "asks": _asks_of(runtime),
    }


def _purge_sessions() -> None:
    now = time.monotonic()
    with _SESSIONS_LOCK:
        for sid in [k for k, v in _SESSIONS.items() if now - float(v["created"]) > SESSION_TTL_S]:
            _SESSIONS.pop(sid, None)


def _get_session(session_id: str) -> dict[str, Any]:
    _purge_sessions()
    sess = _SESSIONS.get(session_id)
    if sess is None:
        raise ServiceError(f"未找到会话 {session_id!r}（可能已过期或服务已重启）", status=404)
    return sess


def create_session(
    ir: Mapping[str, Any],
    seed: Mapping[str, str] | None = None,
    events: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """创建会话：回放事件并停在挂起点/终态，返回含 `asks` 的状态视图。"""
    graph = _load_ir(ir)
    runtime, states = _build_sim_runtime(graph, seed)
    _replay(runtime, states, list(events or []))
    session_id = uuid.uuid4().hex[:12]
    sess = {
        "id": session_id,
        "runtime": runtime,
        "states": states,
        "graph": graph,
        "entities": _entities_of(graph),
        "created": time.monotonic(),
        "mode": "sim",
    }
    with _SESSIONS_LOCK:
        _SESSIONS[session_id] = sess
    return _session_view(sess)


def list_sessions() -> dict[str, Any]:
    _purge_sessions()
    with _SESSIONS_LOCK:
        ids = sorted(_SESSIONS)
    return {"ok": True, "session_ids": ids, "ttl_s": SESSION_TTL_S}


def get_session(session_id: str) -> dict[str, Any]:
    return _session_view(_get_session(session_id))


def answer_session(
    session_id: str,
    text: str,
    ask_id: str | None = None,
    room: str | None = None,
) -> dict[str, Any]:
    """人工应答：按 `ask_id`（优先）或 `room` 匹配挂起 ask，唤醒后继续执行。"""
    sess = _get_session(session_id)
    runtime: Runtime = sess["runtime"]
    pending = runtime.executor.pending_asks

    ask = pending.get(ask_id) if ask_id else None
    if ask is None:
        candidates = [
            s for s in pending.values() if room is None or s.room == room
        ]
        ask = min(candidates, key=lambda s: s.created_at) if candidates else None
    if ask is None:
        raise ServiceError("没有匹配的待应答 ask（可能已超时或被取消）", status=404)

    instance = runtime.instances.get(ask.instance_id)
    runtime.executor.resume(instance, classify_answer(text))
    return _session_view(sess)


def tick_session(session_id: str, advance_s: float) -> dict[str, Any]:
    """推进虚拟时钟（用于演示 `on_timeout`）。"""
    sess = _get_session(session_id)
    sess["runtime"].advance(float(advance_s))
    return _session_view(sess)


def cancel_session(session_id: str, reason: str = "") -> dict[str, Any]:
    sess = _get_session(session_id)
    runtime: Runtime = sess["runtime"]
    for instance in [i for i in runtime.instances.all() if not i.is_terminal]:
        runtime.cancel(instance, reason or "人工取消")
    return _session_view(sess)


def delete_session(session_id: str) -> dict[str, Any]:
    with _SESSIONS_LOCK:
        existed = _SESSIONS.pop(session_id, None) is not None
    if not existed:
        raise ServiceError(f"未找到会话 {session_id!r}", status=404)
    return {"ok": True, "session_id": session_id, "deleted": True}


# ─────────────────────────────────────────────────────────────────────
# Round 2-B：真机下发（受闸门保护的真实设备写入）
#
# 安全模型（三重闸，缺一不可）：
#   1. 服务端开关 `AUTOFORGE_LIVE_ENABLED=1`（默认**关闭**）
#   2. 服务端令牌 `AUTOFORGE_HA_TOKEN`（**绝不从请求体接收令牌**）
#   3. 请求体 `confirm=true` + 非空 `live_allow` 白名单，且 IR 的写目标 ⊆ 白名单
# 复用 `af_scanner.live_preflight` 的语义（与 CLI `forge run --live` 一致口径）。
# ─────────────────────────────────────────────────────────────────────

#: 测试可注入的 transport 工厂：`(ha_url, token) -> transport`；默认 None 表示用真实 HATransport
LIVE_TRANSPORT_FACTORY: Callable[[str, str], Any] | None = None


def _live_config() -> dict[str, Any]:
    return {
        "enabled": os.getenv("AUTOFORGE_LIVE_ENABLED", "").strip().lower() in ("1", "true", "yes"),
        "ha_url": os.getenv("AUTOFORGE_HA_URL", DEFAULT_HA_URL),
        "has_token": bool(os.getenv("AUTOFORGE_HA_TOKEN", "")),
    }


def live_status() -> dict[str, Any]:
    """真机下发的可用性（供前端渲染开关/禁用原因）。"""
    cfg = _live_config()
    reasons: list[str] = []
    if not cfg["enabled"]:
        reasons.append("服务端未启用真机下发（需设置 AUTOFORGE_LIVE_ENABLED=1）")
    if not cfg["has_token"]:
        reasons.append("服务端缺少 HA 令牌（需设置 AUTOFORGE_HA_TOKEN）")
    return {
        "ok": True,
        "enabled": cfg["enabled"] and cfg["has_token"],
        "ha_url": cfg["ha_url"],
        "has_token": cfg["has_token"],
        "allowlist_required": True,
        "confirm_required": True,
        "reasons": reasons,
    }


def _replay_live(runtime: Runtime, events: Sequence[Mapping[str, Any]]) -> None:
    """真机回放：只发布事件/推进时间，**不写本地状态**（真实状态由 HA 提供）。"""
    for item in events:
        advance = item.get("advance_s")
        if advance:
            runtime.advance(float(advance))
        entity_id = item.get("entity_id")
        if entity_id:
            runtime.emit(str(entity_id), str(item.get("state", "")), last_changed=item.get("last_changed"))
        elif advance is None:
            runtime.tick()


def live_run(
    ir: Mapping[str, Any],
    live_allow: Sequence[str],
    events: Sequence[Mapping[str, Any]] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """受闸门的真机下发：三重闸全通过才执行。"""
    cfg = _live_config()
    if not cfg["enabled"]:
        raise ServiceError("真机下发未启用：服务端需设置 AUTOFORGE_LIVE_ENABLED=1", status=403)
    if not cfg["has_token"]:
        raise ServiceError("真机下发缺少 HA 令牌：服务端需设置 AUTOFORGE_HA_TOKEN", status=403)
    if not confirm:
        raise ServiceError("真机下发必须显式二次确认（confirm=true）", status=403)

    allow = {str(x) for x in (live_allow or []) if str(x)}
    if not allow:
        raise ServiceError("真机下发必须提供可写白名单（live_allow），先不开放全量", status=400)

    graph = _load_ir(ir)
    targets: set[str] = set()
    for auto in graph:
        targets |= auto.writes()
    outside = sorted(targets - allow)
    if outside:
        raise ServiceError(f"写目标不在白名单内：{outside}", status=400)

    token = os.getenv("AUTOFORGE_HA_TOKEN", "")
    ha_url = str(cfg["ha_url"])
    factory = LIVE_TRANSPORT_FACTORY
    if factory is not None:
        transport = factory(ha_url, token)
        provider = HAStateProvider(transport=transport)
    else:
        transport = HATransport(base_url=ha_url, token=token)
        provider = HAStateProvider(transport=transport)

    runtime = build_runtime(graph)
    runtime.states = provider
    runtime.instances.states = provider
    runtime.scheduler.states = provider
    runtime.executor.states = provider
    runtime.adapters.register(HAAdapter(transport=transport, dry_run=False))

    _replay_live(runtime, list(events or []))

    entities = sorted(_entities_of(graph))
    snapshot = provider.snapshot(entities)
    logger.warning(
        "LIVE RUN：automation(s)=%s writes=%s events=%d",
        [a.id for a in graph],
        sorted(targets),
        len(events or []),
    )
    return {
        "ok": True,
        "mode": "live",
        "live": True,
        "ha_url": ha_url,
        "allowlist": sorted(allow),
        "writes": sorted(targets),
        "instances": [i.to_dict() for i in runtime.instances.all()],
        "audit": [e.to_dict() for e in runtime.audit],
        "bus": runtime.bus.stats(),
        "final_states": {e: snapshot.values.get(e) for e in entities},
        "nl": render_graph(graph).text,
        "asks": _asks_of(runtime),
    }


# ─────────────────────────────────────────────────────────────────────
# v0.7.0 模板导出与备份恢复
# ─────────────────────────────────────────────────────────────────────


def export_store(store: GraphStore) -> dict[str, Any]:
    """导出整个 store 为可携带 bundle（含 tags，依赖 v0.6.0）。

    返回可直接 JSON 化的 dict（GraphStore.export_bundle 的形态）。
    """
    bundle = store.export_bundle()
    return {
        "ok": True,
        "format": bundle.get("format"),
        "version": bundle.get("version"),
        "checksum": bundle.get("checksum"),
        "exported_at": bundle.get("exported_at"),
        "names": [e["name"] for e in bundle.get("entries", [])],
        "bundle": bundle,
    }


def _bundle_automation_count(bundle: Mapping[str, Any]) -> int:
    """估算 bundle 会带来多少条自动化（取每个归档的**最新版本**计一次）。"""
    total = 0
    for entry in bundle.get("entries") or ():
        versions = entry.get("versions") or ()
        if not versions:
            continue
        graph = versions[-1].get("graph") or {}
        if isinstance(graph, Mapping) and isinstance(graph.get("automations"), list):
            total += len(graph["automations"])
        elif isinstance(graph, Mapping) and graph.get("id"):
            total += 1
    return total


def import_store(
    store: GraphStore,
    bundle: Mapping[str, Any],
    strategy: str = "skip",
    allow_bulk: bool = False,
) -> dict[str, Any]:
    """导入 bundle（v0.7.0）。冲突策略 skip/overwrite/rename，见 GraphStore.import_bundle。

    返回导入报告 {imported, skipped, renamed, errors}。

    v1.3.0：默认受**爆炸半径**约束。导入天然是批量动作，故**常见做法是显式传
    `allow_bulk=True`**——但默认关着，能让「误导入整个 bundle」这类事故先被拦一下。
    """
    count = _bundle_automation_count(bundle)
    if not allow_bulk:
        _check_blast(count, f"本次导入含约 {count} 条自动化")
    report = store.import_bundle(bundle, strategy)
    if isinstance(report, dict):
        report.setdefault("blast_radius", {"affected": count, "limit": blast_radius_limit()})
    return report


# ─────────────────────────────────────────────────────────────────────
# v0.5.0 运行指标（回灌 MA 的数据源）
# ─────────────────────────────────────────────────────────────────────


def get_metrics(store: GraphStore) -> dict[str, Any]:
    """聚合运行指标，输出与 `MetricsAggregator.snapshot` 同构的 dict。

    原型期服务层为无状态（每次请求建临时 runtime），故此处以**持久化置信度**为正源
    （conf 维度真实），执行次数 / 成功率 / 审计分布需常驻进程（forge serve / watch）
    才累计，此处暂置 0/空。`GET /api/metrics` 与 CLI `forge metrics push` 共用此结构。
    """
    per_auto: dict[str, Any] = {}
    for record in store.history():
        name = record.get("name", "")
        if not name:
            continue
        try:
            conf = store.load_conf(name)
        except FileNotFoundError:
            continue
        for aid, val in conf.values.items():
            band = decision_for(val)
            per_auto[aid] = {
                "id": aid,
                "runs": 0,
                "success": 0,
                "failed": 0,
                "success_rate": None,
                "audit_distribution": {},
                "confidence": {"value": val, "band": band},
                "last_event_at": None,
            }
    return {
        "generated_at": datetime.now().isoformat(),
        "source": "conf-store",
        "overall": {
            "total_runs": 0,
            "total_success": 0,
            "total_failed": 0,
            "overall_success_rate": None,
            "audit_distribution": {},
        },
        "automations": per_auto,
    }


# ─────────────────────────────────────────────────────────────────────
# v1.1.0 实体事实内建（设备目录 + 解析）
#
# 目的：把「自然语言设备名 → 真实 entity_id」做进平台自身，**切断对 MA 的硬依赖**。
# 与 MA 分工不重叠：AF 回答「执行事实」（ID/域/可能状态/当前状态），
# MA 回答「语义身份」（谁在家/习惯/历史）。
# ─────────────────────────────────────────────────────────────────────


def _catalog(store: GraphStore, **kwargs: Any) -> DeviceCatalog:
    """从 store 根目录构造设备目录（缓存与归档同根，便于整体搬运）。"""
    return DeviceCatalog(store.root, **kwargs)


def catalog_refresh(
    store: GraphStore,
    *,
    full: bool = True,
    domain: str = "",
    area: str = "",
) -> dict[str, Any]:
    """从 HA 重新快照全屋设备进本地缓存（之后解析毫秒级返回）。"""
    return _catalog(store).refresh(full=full, domain=domain, area=area)


def catalog_resolve(
    store: GraphStore,
    name: str,
    *,
    area: str = "",
    domain: str = "",
    top_n: int = 8,
) -> dict[str, Any]:
    """自然语言设备名 → Top-N 候选 `entity_id`（写 IR 前必调）。"""
    return _catalog(store).resolve(name, area=area, domain=domain, top_n=top_n)


def catalog_list(
    store: GraphStore,
    *,
    domain: str = "",
    area: str = "",
    keyword: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """全屋实体目录·过滤浏览（强制分页 + 透明截断回报）。"""
    return _catalog(store).list_entities(domain=domain, area=area, keyword=keyword, limit=limit, offset=offset)


def catalog_state(store: GraphStore, entity_id: str) -> dict[str, Any]:
    """单实体状态（实时优先，失败回退目录缓存并标注）。"""
    return _catalog(store).get_state(entity_id)


def catalog_snapshot(store: GraphStore) -> dict[str, Any]:
    """目录摘要（按域统计 + 区域列表 + 新鲜度；不 dump 全量实体）。"""
    return _catalog(store).snapshot()


def catalog_resolve_metrics(store: GraphStore) -> dict[str, Any]:
    """v1.5.0：解析成功率漏斗（五档 exact/medium/low/ambiguous/none）。"""
    return _catalog(store).resolve_metrics()


def catalog_set_alias(store: GraphStore, name: str, entity_id: str) -> dict[str, Any]:
    """v1.6.0 P0：沉淀「设备名 → entity_id」精确映射（下次解析直中，high 置信）。"""
    return _catalog(store).set_alias(name, entity_id)


def catalog_list_aliases(store: GraphStore) -> dict[str, Any]:
    """v1.6.0 P0：列出已沉淀的别名映射。"""
    return _catalog(store).list_aliases()


def catalog_remove_alias(store: GraphStore, name: str) -> dict[str, Any]:
    """v1.6.0 P0：删除某条别名映射。"""
    return _catalog(store).remove_alias(name)


# ─────────────────────────────────────────────────────────────────────
# v1.6.0（决策 3）：可选 binding —— 设备描述占位符 → 真实 entity_id
# ─────────────────────────────────────────────────────────────────────

#: 占位符前缀：`entity_id` 以 `?` 开头表示「待绑定的设备描述」。
BIND_PREFIX = "?"
#: 占位符形态：`?书房吊灯` 或 `?light:书房吊灯`（可选域收窄）
_PLACEHOLDER_RE = re.compile(r"^\?(?:(?P<domain>[a-z_][a-z0-9_]*):)?(?P<name>.+)$")


def _bind_target(catalog: Any, placeholder: str) -> tuple[str, str, str]:
    """解析一个占位符 → `(entity_id, matched_by, reason)`；未定 → `("", "", reason)`。"""
    match = _PLACEHOLDER_RE.match(str(placeholder).strip())
    if not match:
        return "", "", "占位符格式非法（应为 ?设备名 或 ?domain:设备名）"
    domain = match.group("domain") or ""
    name = (match.group("name") or "").strip()
    if not name:
        return "", "", "占位符缺设备名"
    res = catalog.resolve(name, domain=domain)
    cands = res.get("candidates", []) or []
    if not cands:
        return "", "", "无候选（目录未刷新或设备名有误）"
    # fail-closed 纪律（同 resolve_best）：唯一候选 / top=high 才自动采纳
    if len(cands) == 1 or cands[0].get("confidence") == "high":
        return str(cands[0]["entity_id"]), str(cands[0].get("matched_by", "")), ""
    return "", "", (
        "歧义（{} 个候选：{}…）需明确指定".format(
            len(cands), ", ".join(str(c.get("entity_id")) for c in cands[:3])
        )
    )


def bind_ir(store: GraphStore, ir: Mapping[str, Any]) -> dict[str, Any]:
    """v1.6.0（决策 3）：把 IR 里的「设备描述占位符」解析回填为真实 entity_id。

    - **可选**（不强制）：不启用时 IR 必须写精确 entity_id（行为与 v1.1.0 一致，
      保住「离线编写 IR」与 G7 AF-Spec 零有损往返）；
    - 占位符约定：`entity_id` 以 `?` 开头，形如 `?书房吊灯` / `?light:书房吊灯`；
    - **fail-closed 纪律不变**：歧义 / 无候选 → **不回填**（保留占位符），
      交由既有安全闸（未知实体）拒编译；**绝不静默猜域/猜实体**。
    """
    catalog = _catalog(store)
    payload = json.loads(json.dumps(dict(ir), ensure_ascii=False, default=str))
    bound: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    def _try(node_id: str, field: str, value: Any) -> Any:
        if not isinstance(value, str) or not value.startswith(BIND_PREFIX):
            return value
        eid, matched, reason = _bind_target(catalog, value)
        if eid:
            bound.append(
                {"node": node_id, "field": field, "from": value, "to": eid, "matched_by": matched}
            )
            return eid
        unresolved.append({"node": node_id, "field": field, "from": value, "reason": reason})
        return value

    items = payload.get("automations")
    if not isinstance(items, list):
        items = [payload]
    for auto in items:
        if not isinstance(auto, dict):
            continue
        for node in auto.get("nodes", []) or []:
            if not isinstance(node, dict):
                continue
            nid = str(node.get("id", ""))
            trigger = node.get("trigger")
            if isinstance(trigger, dict) and "entity_id" in trigger:
                trigger["entity_id"] = _try(nid, "trigger.entity_id", trigger["entity_id"])
            params = node.get("params")
            if isinstance(params, dict) and "entity_id" in params:
                raw = params["entity_id"]
                if isinstance(raw, list):
                    params["entity_id"] = [_try(nid, "params.entity_id", r) for r in raw]
                else:
                    params["entity_id"] = _try(nid, "params.entity_id", raw)
        for exp in auto.get("expect", []) or []:
            if isinstance(exp, dict) and "entity_id" in exp:
                exp["entity_id"] = _try("", "expect.entity_id", exp["entity_id"])

    return {
        "ok": not unresolved,
        "ir": payload,
        "bound": bound,
        "unresolved": unresolved,
        "total": len(bound) + len(unresolved),
        "note": (
            "binding 完成：全部占位符已回填"
            if not unresolved
            else f"binding 未完全：{len(unresolved)} 个占位符因歧义/无候选未回填"
                 f"（fail-closed，保留占位符交由安全闸拒编译）"
        ),
    }



def _graph_summary(graph_path: str) -> dict[str, Any]:
    """从 IR 文件提取名称/触发源/动作目标，供 UI 展示。"""
    from pathlib import Path
    gp = Path(graph_path)
    if not gp.is_absolute():
        # 容器内 graph 路径是 /app/examples/...，相对路径无法定位时返回空
        gp = Path("/app") / graph_path.lstrip("/")
    try:
        data = json.loads(gp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    auto = data
    if isinstance(data, dict) and "automations" in data:
        autos = data["automations"]
        auto = autos[0] if autos else {}
    triggers: list[str] = []
    actions: list[str] = []
    for node in (auto.get("nodes") or []):
        kind = node.get("kind", "")
        if kind == "on":
            trg = node.get("trigger") or {}
            eid = trg.get("entity_id", "")
            if eid:
                triggers.append(f"{eid} → {trg.get('to', '')}")
        elif kind == "do":
            params = node.get("params") or {}
            eid = params.get("entity_id", "")
            if eid:
                actions.append(f"{node.get('action', '')} {eid}")
    return {
        "name": auto.get("name", auto.get("id", "")),
        "automation_id": auto.get("id", ""),
        "triggers": triggers,
        "actions": actions,
        "node_count": len(auto.get("nodes") or []),
    }


def list_watches(store_root: str | None = None) -> dict[str, Any]:
    """列出正在跑的 watch 实例（读 persist dir 下的 watch.lock.info sidecar）。

    watch 是独立 CLI 进程，API server 不持有其 Runtime；这里只读 sidecar 文件
    列出当前在跑的自动化，供 UI「运行中」页展示。
    """
    from pathlib import Path
    root = Path(store_root) if store_root else Path(".forge")
    watches: list[dict[str, Any]] = []
    candidates = [root] + list(root.glob("*/watch.lock.info"))
    seen: set[str] = set()
    for path in candidates:
        if path.is_dir():
            path = path / "watch.lock.info"
        if not path.is_file() or str(path) in seen:
            continue
        seen.add(str(path))
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        graph_path = data.get("graph", "")
        summary = _graph_summary(graph_path) if graph_path else {}
        watches.append({
            "owner": data.get("owner", ""),
            "acquired_at": data.get("acquired_at", ""),
            "graph": graph_path,
            "ha_url": data.get("ha_url", ""),
            "sidecar": str(path),
            "name": summary.get("name", ""),
            "automation_id": summary.get("automation_id", ""),
            "triggers": summary.get("triggers", []),
            "actions": summary.get("actions", []),
            "node_count": summary.get("node_count", 0),
        })
    return {"ok": True, "watches": watches, "total": len(watches)}


def stop_watch(owner: str | None = None, store_root: str | None = None) -> dict[str, Any]:
    """停止正在跑的 watch 进程。

    通过 pkill 终止容器内 forge watch 进程；owner 为空则停全部。
    """
    import subprocess
    root = Path(store_root) if store_root else Path(".forge")
    lock = root / "watch.lock"
    info = root / "watch.lock.info"
    # 读当前持有者
    holder = {}
    try:
        holder = json.loads(info.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if owner and holder.get("owner") and holder["owner"] != owner:
        return {"ok": False, "error": f"当前持有者 {holder['owner']} 与请求 {owner} 不符"}
    # pkill forge watch
    try:
        r = subprocess.run(
            ["pkill", "-f", "forge watch"],
            capture_output=True, text=True, timeout=10,
        )
        killed = r.returncode == 0
    except Exception as e:
        return {"ok": False, "error": f"pkill 失败: {e}"}
    # 清 sidecar
    try:
        if info.exists():
            info.unlink()
        if lock.exists():
            lock.unlink()
    except OSError:
        pass
    return {"ok": killed, "stopped": holder.get("owner", ""), "graph": holder.get("graph", "")}
