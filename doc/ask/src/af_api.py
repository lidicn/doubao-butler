"""AutoForge 只读 HTTP 服务层（FastAPI）。

对齐《AutoForge-UI 开工令》附录 A 契约（Round 1，只读）：
    GET  /api/health
    GET  /api/graphs?tag=           # v0.6.0 支持按标签过滤
    GET  /api/graphs/{name}?version=
    POST /api/build
    POST /api/sim
    GET  /api/conf/{name}
    POST /api/conf/{name}/intervene
    GET  /api/diff?name=&old=&new=
    GET  /api/spec/{name}?version=
    POST /api/spec/compile
    GET  /api/faults

v0.6.0 标签体系 + 批量启停（写操作，需服务端设置 AUTOFORGE_API_TOKEN 才强制鉴权）：
    POST /api/graphs/tags          # {name, tags[]} 设置某归档标签
    POST /api/graphs/enable        # {tag} 按标签批量启用其中全部自动化
    POST /api/graphs/disable       # {tag} 按标签批量禁用其中全部自动化

v0.7.0 模板导出与备份恢复：
    GET  /api/store/export         # 导出整个 store 为 bundle（含 tags + 校验和，读端点）
    POST /api/store/import         # {bundle, strategy} 导入 bundle（写操作，需鉴权）

v0.8.0 服务层鉴权升级（多令牌主体模型 + 撤销 + 限速 + scope 分级）：
    - 令牌来源：`AUTOFORGE_API_TOKEN`（旧单密钥，等价 subject=shared、scopes=read+write+live）
      或 `AUTOFORGE_TOKENS`（JSON 对象，每条令牌自报 subject + scopes）。
    - 端点 scope 分级：
        read  ：公开（健康/图表/配置/指标/审计/故障图鉴/store/export/sessions 列表与详情）
        write ：标签/批量启停/store/import/会话写/干预/`auth/*` 管理
        live  ：真机下发（`/api/live/*`）
    - 撤销：`POST /api/auth/revoke {token}` 即时生效（落盘 `{store_root}/.auth/revoked.json`）。
    - 限速：IP + 主体双维度固定窗口（默认 1000/min，可配 `AUTOFORGE_RATE_LIMIT_PER_MIN`）。
    - 自检：`GET /api/auth/whoami`、`GET /api/auth/subjects`。
    - 未配置任何令牌时全站公开（向后兼容原型期局域网使用）。

    v1.1.0 实体事实内建（设备目录 + 解析，切断对 MA 的硬依赖）：
    GET  /api/catalog                # 目录摘要（按域统计 + 区域 + freshness）
    POST /api/catalog/refresh        # 拉 HA 全屋设备目录进本地缓存（写端点）
    GET  /api/entities/resolve       # 自然语言设备名 → 候选 entity_id（写 IR 前必调）
    GET  /api/entities               # 全屋实体目录·过滤浏览（强制分页）
    GET  /api/entities/{id}/state    # 单实体当前状态（实时优先 + 缓存兜底）

FastAPI 自带 `/docs`（Swagger UI）与 `/openapi.json`，可直接作为前端联调依据。
业务逻辑全在 `af_service.py`，本层只做路由与错误码映射。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from . import af_service as svc
from .af_config import get_config
from .af_auth import RateLimitExceeded, RateLimiter, TokenExpired, TokenInfo, TokenRegistry
from .af_ir import IRValidationError
from .af_store import GraphStore

__all__ = ["build_app"]

#: Bearer 令牌提取（auto_error=False：缺失时不报错，交由鉴权依赖决定）
_bearer = HTTPBearer(auto_error=False)



class BuildBody(BaseModel):
    ir: dict[str, Any]
    known_entities: list[str] | None = None


class SimBody(BaseModel):
    ir: dict[str, Any]
    seed: dict[str, str] | None = None
    events: list[dict[str, Any]] | None = None


class InterveneBody(BaseModel):
    automation_id: str


class SpecBody(BaseModel):
    text: str


# ── v1.4.0 治理面：待批队列 + 凭据热重载 请求体 ──
class PendingListBody(BaseModel):
    agent: str | None = None


class PendingApproveBody(BaseModel):
    op_id: str


class PendingRejectBody(BaseModel):
    op_id: str
    reason: str = ""


class CredentialsUpdateBody(BaseModel):
    ha_token: str | None = None
    api_token: str | None = None


# ── Round 2-A：会话（ask 审批的人机回路）──
class SessionBody(BaseModel):
    ir: dict[str, Any]
    seed: dict[str, str] | None = None
    events: list[dict[str, Any]] | None = None


class AnswerBody(BaseModel):
    text: str
    ask_id: str | None = None
    room: str | None = None


class TickBody(BaseModel):
    advance_s: float


class CancelBody(BaseModel):
    reason: str = ""


# ── Round 2-B：真机下发 ──
class LiveRunBody(BaseModel):
    ir: dict[str, Any]
    events: list[dict[str, Any]] | None = None
    live_allow: list[str] = []
    confirm: bool = False


# ── v0.6.0 标签体系 + 批量启停 ──
class TagBody(BaseModel):
    name: str
    tags: list[str] = []


class TagEnableBody(BaseModel):
    tag: str


# ── v0.7.0 模板导出与备份恢复 ──
class ImportBody(BaseModel):
    bundle: dict[str, Any]
    strategy: str = "skip"  # skip | overwrite | rename


# ── v0.8.0 令牌管理 ──
class RevokeBody(BaseModel):
    token: str


# ── v1.1.0 实体事实内建 ──
class CatalogRefreshBody(BaseModel):
    full: bool = True
    domain: str = ""
    area: str = ""


class AliasBody(BaseModel):
    """v1.6.0 P0：别名沉淀请求体（`entity_id` 在删除时可省）。"""

    name: str
    entity_id: str = ""


def build_app(
    store_root: str = ".forge",
    examples_dir: str | None = None,
    ui_dir: str | None = None,
) -> FastAPI:
    """构造 FastAPI 应用。

    `store_root`：G6 归档目录（`{root}/{name}/v{n}.json`）。
    `examples_dir`：非空时把样例 IR 幂等灌入归档，让控制台一开就有数据。
    `ui_dir`：非空且为已存在的目录时，把前端构建产物（dist）一并托管，
        支持 SPA fallback（HTML5 history 模式深链刷新返回 index.html）。
        前端同源访问 `/api`，无需额外 CORS / 反向代理。
    """
    store = GraphStore(store_root)
    if examples_dir:
        svc.bootstrap_examples(store, examples_dir)

    # ── v0.8.0 鉴权引擎装配（每 app 实例独立，测试间互不串扰）──
    registry = TokenRegistry(Path(store_root) / ".auth" / "revoked.json")
    limiter = RateLimiter(
        per_minute=int(os.getenv("AUTOFORGE_RATE_LIMIT_PER_MIN", "1000"))
    )

    def _client_ip(request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _rate_limit_dep(
        request: Request,
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> None:
        """全局依赖：IP + 主体双维度限速（不鉴权，鉴权由 requires(scope) 负责）。"""
        try:
            limiter.check(f"ip:{_client_ip(request)}")
            if creds and registry.enabled:
                try:
                    info = registry.authenticate(creds.credentials)
                except TokenExpired:
                    info = None  # 过期令牌交 requires/authenticated 定夺（此处不重复报错）
                if info:
                    limiter.check(f"subj:{info.subject}")
        except RateLimitExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc

    def requires(scope: str):
        """端点分级依赖：write / live。未配置令牌时全站公开（向后兼容）。"""
        def dep(
            creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
        ) -> TokenInfo | None:
            if not registry.enabled:
                return None
            if creds is None:
                raise HTTPException(status_code=403, detail="缺少 API 令牌")
            try:
                info = registry.authenticate(creds.credentials)
            except TokenExpired as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            if info is None:
                raise HTTPException(status_code=403, detail="无效或已撤销的 API 令牌")
            if scope not in info.scopes:
                raise HTTPException(
                    status_code=403,
                    detail=f"令牌缺少 '{scope}' 权限（当前 scopes：{sorted(info.scopes)}）",
                )
            return info

        return dep

    def authenticated():
        """可选认证：返回令牌主体（未配置鉴权/未携带/无效均返回 None）。"""
        def dep(
            creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
        ) -> TokenInfo | None:
            if not registry.enabled or creds is None:
                return None
            try:
                return registry.authenticate(creds.credentials)
            except TokenExpired:
                return None  # 过期令牌视为未认证（如 /api/auth/whoami → 401）

        return dep

    _write = requires("write")
    _live = requires("live")

    app = FastAPI(
        title="AutoForge API",
        version=svc.API_VERSION,
        description="AutoForge 只读服务层（Round 1 控制台后端，对齐 UI 开工令附录 A）",
        dependencies=[Depends(_rate_limit_dep)],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 原型期：供本地前端（Vite dev）跨域联调
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        return svc.health()

    # ── v1.4.0 治理面：待批队列（部署前写操作先入队，人审后回放）──
    # 注意：approve / reject **只在服务层**（此处 + CLI），MCP 面绝不注册。
    @app.post("/api/pending/list", dependencies=[Depends(_write)])
    def api_pending_list(body: PendingListBody) -> dict[str, Any]:
        return svc.list_pending(store, body.agent)

    @app.post("/api/pending/approve", dependencies=[Depends(_write)])
    def api_pending_approve(
        body: PendingApproveBody, info: TokenInfo | None = Depends(_write)
    ) -> dict[str, Any]:
        reviewer = info.subject if info else "human"
        return _svc(svc.approve_pending, store, body.op_id, reviewer)

    @app.post("/api/pending/reject", dependencies=[Depends(_write)])
    def api_pending_reject(
        body: PendingRejectBody, info: TokenInfo | None = Depends(_write)
    ) -> dict[str, Any]:
        return _svc(svc.reject_pending, store, body.op_id, body.reason)

    # ── v1.4.0 治理面：凭据热重载（connection_revision 代数，免重启）──
    @app.get("/api/credentials", dependencies=[Depends(_write)])
    def api_credentials_show() -> dict[str, Any]:
        return get_config(store.root).describe()

    @app.post("/api/credentials/update", dependencies=[Depends(_write)])
    def api_credentials_update(body: CredentialsUpdateBody) -> dict[str, Any]:
        return _svc(
            get_config(store.root).update_credentials,
            ha_token=body.ha_token,
            api_token=body.api_token,
        )

    @app.get("/api/graphs")
    def api_graphs(tag: str | None = Query(default=None)) -> dict[str, Any]:
        result = svc.list_graphs(store)
        if tag:
            result["items"] = [it for it in result["items"] if tag in it.get("tags", [])]
        return result

    @app.post("/api/graphs/tags", dependencies=[Depends(_write)])
    def api_graph_tags(body: TagBody) -> dict[str, Any]:
        return svc.set_graph_tags(store, body.name, body.tags)

    @app.post("/api/graphs/enable", dependencies=[Depends(_write)])
    def api_graph_enable(body: TagEnableBody) -> dict[str, Any]:
        return svc.enable_by_tag(store, body.tag, True)

    @app.post("/api/graphs/disable", dependencies=[Depends(_write)])
    def api_graph_disable(body: TagEnableBody) -> dict[str, Any]:
        return svc.enable_by_tag(store, body.tag, False)

    # ── v0.7.0 模板导出与备份恢复 ──
    @app.get("/api/store/export")
    def api_store_export() -> dict[str, Any]:
        """导出整个 store 为 bundle（含 tags + 校验和）。读端点，不强制鉴权。"""
        return svc.export_store(store)

    @app.post("/api/store/import", dependencies=[Depends(_write)])
    def api_store_import(body: ImportBody) -> dict[str, Any]:
        """导入 bundle（写操作，需鉴权）。冲突策略 skip/overwrite/rename。"""
        return _svc(svc.import_store, store, body.bundle, body.strategy)

    @app.get("/api/graphs/{name}")
    def api_graph(name: str, version: int | None = Query(default=None)) -> dict[str, Any]:
        try:
            return svc.get_graph(store, name, version)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/build")
    def api_build(body: BuildBody) -> dict[str, Any]:
        try:
            # v1.4.0：传 store → 自动加载 {store}/device_acl.json（设备保护分级生效）
            return svc.build(body.ir, body.known_entities, store=store)
        except IRValidationError as exc:
            raise HTTPException(status_code=400, detail=f"IR 校验失败：{exc}") from exc

    @app.post("/api/bind")
    def api_bind(body: BuildBody) -> dict[str, Any]:
        """v1.6.0：可选 binding——把 IR 里的设备描述占位符（`?书房吊灯`）回填为真实 entity_id。

        fail-closed：歧义/无候选 → **不回填**，保留占位符交由安全闸拒编译。
        """
        return svc.bind_ir(store, body.ir)

    @app.post("/api/sim")
    def api_sim(body: SimBody) -> dict[str, Any]:
        try:
            # v1.5.0：传 store → `_telemetry` 落遥测
            return svc.simulate(body.ir, body.seed, body.events, store=store)
        except IRValidationError as exc:
            raise HTTPException(status_code=400, detail=f"IR 校验失败：{exc}") from exc

    @app.get("/api/conf/{name}")
    def api_conf(name: str) -> dict[str, Any]:
        try:
            return svc.get_conf(store, name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/metrics")
    def api_metrics() -> dict[str, Any]:
        """运行指标（执行/审计/置信度），读端点不强制鉴权。"""
        return svc.get_metrics(store)

    # ── v1.5.0 经验闭环：遥测 + 实体共现（读端点）──
    @app.get("/api/experience")
    def api_experience(limit: int = Query(default=10)) -> dict[str, Any]:
        """实体共现经验摘要（**只在成功落盘后采集**，失败样本不污染）。"""
        return svc.get_experience(store, limit=int(limit))

    @app.get("/api/experience/export")
    def api_experience_export(limit: int = Query(default=200)) -> dict[str, Any]:
        """实体共现经验结构化导出（喂 MA：AF 采集事实 → MA 生成假设）。"""
        return svc.export_experience(store, limit=int(limit))

    @app.get("/api/telemetry")
    def api_telemetry(days: int = Query(default=30)) -> dict[str, Any]:
        """token/结果遥测 + 错误类别分布（四维：tool/category/ok/day）。"""
        return svc.get_telemetry(store, days=int(days))

    @app.post("/api/conf/{name}/intervene", dependencies=[Depends(_write)])
    def api_intervene(name: str, body: InterveneBody) -> dict[str, Any]:
        try:
            return svc.intervene(store, name, body.automation_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"未找到自动化 {body.automation_id!r}") from exc

    @app.get("/api/diff")
    def api_diff(
        name: str = Query(...),
        old: int = Query(...),
        new: int = Query(...),
    ) -> dict[str, Any]:
        try:
            return svc.diff(store, name, old, new)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/spec/{name}")
    def api_spec(name: str, version: int | None = Query(default=None)) -> dict[str, Any]:
        try:
            return svc.spec_of(store, name, version)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/spec/compile")
    def api_spec_compile(body: SpecBody) -> dict[str, Any]:
        return svc.compile_text(body.text)

    @app.get("/api/faults")
    def api_faults() -> dict[str, Any]:
        return svc.faults()

    # ── v1.1.0 实体事实内建（设备目录 + 解析）──────────────────────────
    @app.get("/api/catalog")
    def api_catalog() -> dict[str, Any]:
        """设备目录摘要（按域统计 + 区域列表 + 新鲜度），读端点不强制鉴权。"""
        return svc.catalog_snapshot(store)

    @app.get("/api/catalog/resolve-metrics")
    def api_catalog_resolve_metrics() -> dict[str, Any]:
        """v1.5.0：解析成功率漏斗（五档 exact/medium/low/ambiguous/none）。"""
        return svc.catalog_resolve_metrics(store)

    # ── v1.6.0 P0：别名沉淀（人工/agent 选对后写精确映射，下次直中）──
    @app.get("/api/catalog/aliases")
    def api_catalog_aliases() -> dict[str, Any]:
        """列出已沉淀的「设备名 → entity_id」映射。"""
        return svc.catalog_list_aliases(store)

    @app.post("/api/catalog/alias", dependencies=[Depends(_write)])
    def api_catalog_set_alias(body: AliasBody) -> dict[str, Any]:
        """沉淀一条别名映射（下次 `resolve` 直中，high 置信）。"""
        return svc.catalog_set_alias(store, body.name, body.entity_id)

    @app.post("/api/catalog/alias/remove", dependencies=[Depends(_write)])
    def api_catalog_remove_alias(body: AliasBody) -> dict[str, Any]:
        """删除一条别名映射。"""
        return svc.catalog_remove_alias(store, body.name)

    @app.post("/api/catalog/refresh", dependencies=[Depends(_write)])
    def api_catalog_refresh(body: CatalogRefreshBody | None = None) -> dict[str, Any]:
        """拉取 HA 全屋设备目录进本地缓存（会发起出站 HA 请求，按写端点保护）。"""
        body = body or CatalogRefreshBody()
        return svc.catalog_refresh(store, full=body.full, domain=body.domain, area=body.area)

    @app.get("/api/entities/resolve")
    def api_entities_resolve(
        name: str = Query(..., description="自然语言设备名，如「书房吊灯」"),
        area: str = Query(default=""),
        domain: str = Query(default=""),
        top_n: int = Query(default=8, ge=1, le=50),
    ) -> dict[str, Any]:
        """自然语言设备名 → Top-N 候选 entity_id（写 IR 前必调）。读端点。"""
        return svc.catalog_resolve(store, name, area=area, domain=domain, top_n=top_n)

    @app.get("/api/entities")
    def api_entities(
        domain: str = Query(default=""),
        area: str = Query(default=""),
        keyword: str = Query(default=""),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        """全屋实体目录·过滤浏览（强制分页 + 透明截断回报）。读端点。"""
        return svc.catalog_list(store, domain=domain, area=area, keyword=keyword, limit=limit, offset=offset)

    @app.get("/api/entities/{entity_id}/state")
    def api_entity_state(entity_id: str) -> dict[str, Any]:
        """单实体当前状态（实时优先，失败回退目录缓存并标注 source）。读端点。"""
        return svc.catalog_state(store, entity_id)

    # ── Round 2-A：会话（ask 审批）────────────────────────────────────
    def _svc(fn, *args, **kwargs):
        """统一错误映射：ServiceError → 其自带 status；IR 校验失败 → 400。"""
        try:
            return fn(*args, **kwargs)
        except svc.ServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        except IRValidationError as exc:
            raise HTTPException(status_code=400, detail=f"IR 校验失败：{exc}") from exc

    @app.post("/api/sessions", dependencies=[Depends(_write)])
    def api_session_create(body: SessionBody) -> dict[str, Any]:
        return _svc(svc.create_session, body.ir, body.seed, body.events)

    @app.get("/api/sessions")
    def api_session_list() -> dict[str, Any]:
        return svc.list_sessions()

    @app.get("/api/sessions/{session_id}")
    def api_session_get(session_id: str) -> dict[str, Any]:
        return _svc(svc.get_session, session_id)

    @app.post("/api/sessions/{session_id}/answer", dependencies=[Depends(_write)])
    def api_session_answer(session_id: str, body: AnswerBody) -> dict[str, Any]:
        return _svc(svc.answer_session, session_id, body.text, body.ask_id, body.room)

    @app.post("/api/sessions/{session_id}/tick", dependencies=[Depends(_write)])
    def api_session_tick(session_id: str, body: TickBody) -> dict[str, Any]:
        return _svc(svc.tick_session, session_id, body.advance_s)

    @app.post("/api/sessions/{session_id}/cancel", dependencies=[Depends(_write)])
    def api_session_cancel(session_id: str, body: CancelBody | None = None) -> dict[str, Any]:
        return _svc(svc.cancel_session, session_id, (body.reason if body else ""))

    @app.delete("/api/sessions/{session_id}", dependencies=[Depends(_write)])
    def api_session_delete(session_id: str) -> dict[str, Any]:
        return _svc(svc.delete_session, session_id)

    # ── Round 2-B：真机下发（三重闸 + live scope）──
    @app.get("/api/live/status", dependencies=[Depends(_live)])
    def api_live_status() -> dict[str, Any]:
        return svc.live_status()

    @app.post("/api/live/run", dependencies=[Depends(_live)])
    def api_live_run(body: LiveRunBody) -> dict[str, Any]:
        return _svc(svc.live_run, body.ir, body.live_allow, body.events, body.confirm)

    # ── v1.7.3：运行中 watch 实例列表（只读）──
    @app.get("/api/watch/list")
    def api_watch_list() -> dict[str, Any]:
        """列出当前在跑的 watch 实例（读 persist dir 的 watch.lock.info）。"""
        return svc.list_watches(store.root if store else None)

    @app.post("/api/watch/stop", dependencies=[Depends(_write)])
    def api_watch_stop(body: dict[str, Any] | None = None) -> dict[str, Any]:
        """停止运行中的 watch 进程。"""
        owner = (body or {}).get("owner", "")
        return svc.stop_watch(owner=owner, store_root=store.root if store else None)

    # ── v0.8.0：令牌自检与管理 ──
    @app.get("/api/auth/whoami")
    def api_auth_whoami(info: TokenInfo | None = Depends(authenticated())) -> dict[str, Any]:
        """令牌自检：返回当前令牌的 subject + scopes；未认证返回 401。"""
        if info is None:
            raise HTTPException(status_code=401, detail="未携带有效令牌（或未启用鉴权）")
        return {"ok": True, "subject": info.subject, "scopes": sorted(info.scopes)}

    @app.get("/api/auth/subjects", dependencies=[Depends(_write)])
    def api_auth_subjects() -> dict[str, Any]:
        """已注册令牌的主体摘要（不含明文令牌），供审计/管理用。"""
        return {"ok": True, "subjects": registry.subjects()}

    @app.post("/api/auth/revoke", dependencies=[Depends(_write)])
    def api_auth_revoke(body: RevokeBody) -> dict[str, Any]:
        """撤销令牌（即时生效并落盘 `{store_root}/.auth/revoked.json`）。"""
        revoked = registry.revoke(body.token)
        return {"ok": True, "revoked": revoked}

    # ── 可选：前端静态托管（SPA fallback）──────────────────────────────
    # 仅当显式传入已存在的 ui_dir 时挂载；默认不托管，保持只读 API 纯净。
    if ui_dir and Path(ui_dir).is_dir():
        dist = Path(ui_dir).resolve()

        @app.get("/{full_path:path}")
        def spa_fallback(full_path: str) -> FileResponse:
            # /api/* 由上方显式路由处理；未知 /api 路径应 404 而非回 index.html
            if full_path.startswith("api/") or full_path in ("docs", "openapi.json"):
                raise HTTPException(status_code=404, detail="Not Found")
            candidate = (dist / full_path).resolve()
            # 防目录穿越：只服务 dist 内的真实文件
            if full_path and candidate.is_file() and str(candidate).startswith(str(dist)):
                return FileResponse(str(candidate))
            return FileResponse(str(dist / "index.html"))

    return app
