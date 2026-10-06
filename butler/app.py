"""Starlette 应用装配：lifespan、MQTT 消费者、静态挂载、全局异常兜底、登录。"""
from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, FileResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from pathlib import Path as _Path

from butler.api import (
    agent_routes,
    audiobook_routes,
    config_routes,
    cron_task_routes,
    device_routes,
    dialog_routes,
    hello_routes,
    memory_routes,
    notify_routes,
    openai_routes,
    profile_routes,
    role_routes,
    skill_routes,
    stream_routes,
    trigger_routes,
    doubao_webhook,
    llm_chat,
    decision_routes,
    vibe_routes,
    system_routes,
    tts_routes,
    bark_routes,
    ha_device_routes,
    task_routes,
    dashboard_routes,
    notification_routes,
    pwa_chat_routes,
    command_routes,
    scene_routes,
    persona_routes,
    security_routes,
    audiobook_routes,
)
from butler.presence import api as presence_routes
from butler.timeseries import api as timeseries_routes
from butler.modes import api as modes_routes
from butler.ha_tools import api as ha_tools_routes
from butler.proactive import api as proactive_routes
from butler.guard import api as guard_routes
from butler.triggers import health_api as trigger_health_routes
from butler.mcp import http_transport as mcp_routes
from butler.integrations.ilink import api as ilink_routes
from butler.api.deps import err, is_login_locked, login, ok
from butler.bus.mqtt_client import MQTTClient
from butler.bus.topics import (PUB_DIALOG, SUB_MA_PRESENCE, SUB_MA_DEVICE_HEALTH,
                        SUB_MA_INSIGHTS, adm_status_is_online)
from butler.config import get_settings, set_settings
from butler.core.dedup import DedupChecker
from butler.core.dialog import DialogManager
from butler.core.persona import PersonaEngine
from butler.core.state import RuntimeState
from butler.core.wakeup import WakeupEngine
from butler.integrations.bark import Bark
from butler.integrations.ha import HAClient
from butler.integrations.llm import LLMClient
from butler.integrations.memory_agent import MemoryAgentClient
from butler.integrations.tv import TVClient
from butler.integrations.tvpilot import TVPilotClient
from butler.integrations.deskpilot import DeskPilotClient
from butler.integrations.newapi import NewApiClient
from butler.integrations.docker_tools import DockerClient
from butler.integrations.doubao import DoubaoClient
from butler.core.agent import Agent
from butler.logging_setup import get_logger, setup_logging
from butler.runtime import get_runtime, safe_add_job
from butler.store import repo
from butler.tts.manager import TTSManager
from butler.tts.singleton import init_queue as init_tts_queue, get_queue as get_tts_queue
from butler.notify.singleton import init_router as init_notify_router
from butler.devices import DeviceRegistry
from butler.roles.store import RoleRegistry
from butler.roles.state import RoleConversationStore

logger = get_logger("butler.app")
STATIC_DIR = Path(__file__).parent / "static"


async def _consume(rt) -> None:
    """MQTT 队列消费者：分发事件 + 把对话事件推给 SSE 订阅者。"""
    while True:
        try:
            topic, payload = await rt.mqtt.queue.get()
        except asyncio.CancelledError:
            break
        try:
            if topic == PUB_DIALOG:
                for q in list(rt.sse_subscribers):
                    try:
                        q.put_nowait(payload)
                    except asyncio.QueueFull:
                        # 表行 21 后半：慢订阅者丢件⛔ 无声吞掉，先记数再留痕
                        rt.sse_dropped = getattr(rt, "sse_dropped", 0) + 1
                        logger.warning("SSE_SUBSCRIBER_QUEUE_FULL 订阅者队列满，已丢弃 1 件：累计=%d",
                                       rt.sse_dropped)
            elif topic == SUB_MA_PRESENCE:
                # v1.5 P0-4：MA 成员在场快照 → 定位引擎 arcface_recognized 信号
                ma_source = getattr(rt, "_presence_ma_source", None)
                if ma_source and ma_source.enabled:
                    ma_source.handle_message(payload)
            elif topic == SUB_MA_DEVICE_HEALTH:
                # v1.6 P2-2：MA 设备健康变化（预留，当前仅日志）
                logger.info("MA device-health: %s", payload)
            elif topic == SUB_MA_INSIGHTS:
                # 契约 v2.0 §D：ma/insights {trace_id, ts, insight_id, kind, persons[],
                # room?, summary, evidence[], snapshot_url?, conf?, intent?}
                # fail-closed：schema 缺 trace_id/kind → 丢弃 + ADM_ERR_PAYLOAD_INVALID + 审计
                if not isinstance(payload, dict):
                    logger.error("ADM_ERR_PAYLOAD_INVALID ma/insights payload 非对象")
                    continue
                trace_id = str(payload.get("trace_id", "")).strip()
                kind = str(payload.get("kind") or "unknown").strip()
                if not trace_id:
                    logger.error("ADM_ERR_PAYLOAD_INVALID ma/insights trace_id 缺失 kind=%s", kind)
                    continue
                summary = str(payload.get("summary") or "")[:500]
                conf = payload.get("conf")
                # conf 封顶：契约要求 conf 封顶（>0.95 按 0.95 计），防止过拟合
                if isinstance(conf, (int, float)) and conf > 0.95:
                    conf = 0.95
                logger.info("MA insights: kind=%s trace=%s conf=%s summary=%s",
                            kind, trace_id, conf, summary[:100])
                # 安全类告警（security/anomaly/intrusion）走 Bark 通知，其余仅日志
                if kind in ("security", "anomaly", "intrusion"):
                    try:
                        from butler.notifier.router import get_router as get_notify_router
                        nr = get_notify_router()
                        if nr and nr.bark:
                            nr.bark.send(f"安全告警[{kind}]", summary)
                    except Exception as e:
                        logger.warning("MA insights bark notify failed: %s", e)
            elif topic.startswith("butler/inbox/"):
                # §13.4 公共收件箱：任何仓投递 → DB 过闸（schema/限流/冷却/预算）后播出/分发
                if getattr(rt, "inbox", None) is not None:
                    await rt.inbox.handle(topic, payload)
            elif topic in ("adm/memory-agent/status", "adm/autoforge/status"):
                # 契约表 §1.1：这条主题的载荷是字面量，判读走 topics 的单点 helper
                online = adm_status_is_online(payload)
                peers = getattr(rt, "adm_peers", None)
                if isinstance(peers, dict):
                    prev = peers.get(topic)
                    peers[topic] = online
                    # 离线边沿（online→offline）记录 ADM_ERR_PEER_OFFLINE，禁止静默降级
                    if prev is True and online is False:
                        logger.error("ADM_ERR_PEER_OFFLINE peer=%s (LWT/心跳超时)", topic)
                logger.info("ADM peer status %s online=%s", topic, online)
            elif topic.startswith("butler/trigger/"):
                # 技能 MQTT 触发入口（兼容通道）：butler/trigger/{skill_id}
                if rt.runner:
                    rt.runner.on_mqtt_trigger(topic, payload)
            else:
                await rt.dialog.on_event(topic, payload)
        except Exception:
            logger.exception("consume error")


async def _login(request: Request):
    try:
        body = await request.json()
    except Exception:
        return err("invalid json")
    client_ip = request.client.host if request.client else "unknown"
    if is_login_locked(client_ip):
        return err("登录尝试过多，请 15 分钟后再试", 429)
    tok = login(str(body.get("user", "")), str(body.get("password", "")), client_ip=client_ip)
    if not tok:
        return err("用户名或密码错误", 401)
    r = ok({"authed": True})
    r.set_cookie("butler_auth", tok, httponly=True, max_age=60 * 60 * 24 * 7)
    return r


async def _login_status(request: Request):
    from butler.api.deps import auth_enabled, check_auth

    return ok({"auth_required": auth_enabled(), "authed": check_auth(request)})


async def _logout(request: Request):
    """P0-8：登出端点，清除会话 cookie。"""
    from butler.api.deps import logout as do_logout
    do_logout(request)
    r = ok({"logged_out": True})
    r.delete_cookie("butler_auth")
    return r


import re as _re

_AVATAR_RE = _re.compile(r"^[A-Za-z0-9_\-]+$")


async def _avatar(request: Request):
    """成员头像：/avatars/{name}.png，缺失回退默认管家头像。"""
    name = request.path_params.get("name", "")
    name = name.removesuffix(".png") if name.endswith(".png") else name
    if not _AVATAR_RE.match(name):
        return JSONResponse({"ok": False, "error": "bad name"}, status_code=400)
    s = get_runtime().settings
    custom = _Path(s.data_dir) / "avatars" / f"{name}.png"
    default = STATIC_DIR / "images" / "doubao.png"
    target = custom if custom.exists() and custom.stat().st_size > 0 else default
    return FileResponse(str(target), media_type="image/png")


async def _global_exc(request, exc):
    logger.exception("unhandled: %s", exc)
    return JSONResponse({"ok": False, "error": "internal_error"}, status_code=500)


def _build_app() -> Starlette:
    rt = get_runtime()
    if rt.settings is None:
        rt.settings = get_settings()
    s = rt.settings

    routes = [
        Route("/api/login", _login, methods=["POST"]),
        Route("/api/login/status", _login_status, methods=["GET"]),
        Route("/api/logout", _logout, methods=["POST"]),
        Route("/avatars/{name}", _avatar, methods=["GET"]),
        *system_routes.routes(),
        *dialog_routes.routes(),
        *config_routes.routes(),
        *memory_routes.routes(),
        *tts_routes.routes(),
        *bark_routes.routes(),
        *ha_device_routes.routes(),
        *audiobook_routes.routes(),
        *notify_routes.routes(),
        *skill_routes.routes(),
        *trigger_routes.routes(),
        *doubao_webhook.routes(),
        *llm_chat.routes(),
        *decision_routes.routes(),
        *vibe_routes.routes(),
        *device_routes.routes(),
        *role_routes.routes(),
        *profile_routes.routes(),
        *hello_routes.routes(),
        *stream_routes.routes(),
        *agent_routes.routes(),
        *openai_routes.routes(),
        *task_routes.routes(),
        *dashboard_routes.routes(),
        *notification_routes.routes(),
        *pwa_chat_routes.routes(),
        *command_routes.routes(),
        *scene_routes.routes(),
        *presence_routes.routes(),
        *timeseries_routes.routes(),
        *modes_routes.routes(),
        *ha_tools_routes.routes(),
        *proactive_routes.routes(),
        *guard_routes.routes(),
        *trigger_health_routes.routes(),
        *mcp_routes.routes(),
        *ilink_routes.routes(),
        *persona_routes.routes(),
        *security_routes.routes(),
        *cron_task_routes.routes(),
        Mount("/tts", app=StaticFiles(directory=s.tts_dir, html=False), name="tts"),
        Mount("/dashboard", app=StaticFiles(directory=str(Path(__file__).parent / "dashboard_pwa"), html=True), name="dashboard"),
        Mount("/", app=StaticFiles(directory=str(STATIC_DIR), html=True), name="static"),
    ]

    @asynccontextmanager
    async def lifespan(app: Starlette):
        setup_logging()
        s = rt.settings
        set_settings(s)
        # 初始化统一指令中心表
        try:
            from butler.store import command_store
            command_store.init_table()
            print("[startup] command_queue table initialized")
        except Exception as e:
            print(f"[startup] command_store init failed: {e}")
        # 固化唤醒提示音（叮）：容器重建后自动生成，X08A 打断用
        # P2-5：改走 async helper，⛔ 在事件循环线程上 subprocess.run（原码最多冻 10 秒）
        try:
            from butler.startup_assets import ensure_wake_ding
            ding_ok, ding_detail = await ensure_wake_ding(s.tts_dir)
            if ding_ok:
                print(f"[startup] wake_ding.mp3 {ding_detail}")
            else:
                print(f"[startup] wake_ding.mp3 gen failed: {ding_detail}")
        except Exception as e:
            print(f"[startup] wake_ding.mp3 gen failed (unexpected): {e}")
        # 装配依赖
        state = RuntimeState()
        _main_loop = asyncio.get_running_loop()  # 保存主事件循环，供 APScheduler 线程调度协程
        mqtt = MQTTClient(s, _main_loop)
        llm = LLMClient(s)
        bark = Bark(s)
        tts = TTSManager(s, bark)
        tv = TVClient(s)
        tvpilot = TVPilotClient(s)
        deskpilot = DeskPilotClient(s.deskpilot_http_url, s.deskpilot_api_token)
        newapi = NewApiClient(s.newapi_db_path)
        docker = DockerClient(s.docker_socket_path)
        ha = HAClient(s)
        tts.ha = ha  # TTSManager 需要 ha 来播放到小爱音箱
        tts.queue._ha = ha  # 同步给 PlaybackQueue（__init__ 时 ha 还是 None）
        # 启动小米 token 自动刷新：从 HA 配置文件每小时读取最新 token
        init_tts_queue(tts)  # v2.5: initialize TTS priority queue
        init_notify_router(bark=bark, ha=ha, tv=tv, tts_queue=get_tts_queue())  # v2.5: NotifyRouter 绑定 bark/ha/tv/tts_queue
        await ha.start_xiaomi_token_refresh()  # v2.6: 小米 service_token 自动刷新（每2h，热更新无需重启）
        memory = MemoryAgentClient(s)
        persona = PersonaEngine(s)
        dedup = DedupChecker(s)
        wakeup = WakeupEngine(s, state)
        doubao = DoubaoClient(s)
        # 技能存储先于 Agent 创建（Agent 需要 skill_store 做操作模板匹配）
        from butler.skills.defaults import DEFAULT_SKILLS
        from butler.skills.store import SkillStore
        store = SkillStore(s.data_dir)
        store.load()
        created = store.ensure_defaults(DEFAULT_SKILLS)
        if created:
            logger.info("default skills seeded: %s", created)
        agent = Agent(s, llm, ha, memory, tv, doubao, scheduler=None, tvpilot=tvpilot, deskpilot=deskpilot, newapi=newapi, docker=docker, skill_store=store)
        dialog = DialogManager(s, state, mqtt, llm, tts, tv, ha, bark, memory, persona, dedup, wakeup, agent)
        tv.set_mqtt(mqtt)
        mqtt.on_result = tv.on_result
        # 技能子系统：引擎注册表 + 调度器
        from butler.skills.plugins import EngineRegistry
        from butler.skills.runner import SkillRunner
        registry = EngineRegistry(str(Path(s.data_dir) / "plugins"))
        registry.load()
        runner = SkillRunner(s, store, registry, dedup=dedup)
        rt.state = state
        rt.mqtt = mqtt
        rt.llm = llm
        rt.tts = tts
        rt.tv = tv
        rt.tvpilot = tvpilot
        rt.deskpilot = deskpilot
        rt.newapi = newapi
        rt.docker = docker
        rt.ha = ha
        rt.bark = bark
        # 百度 TTS：无凭证就不建对象，别把 None 翻成空钥匙对象
        _baidu_key = os.environ.get("BAIDU_TTS_API_KEY", "")
        _baidu_secret = os.environ.get("BAIDU_TTS_SECRET_KEY", "")
        if _baidu_key and _baidu_secret:
            try:
                from butler.core.tts_baidu import BaiduTTS
                rt.baidu_tts = BaiduTTS(api_key=_baidu_key, secret_key=_baidu_secret)
                logger.info("baidu tts initialized")
            except Exception as e:
                logger.warning("baidu tts init failed: %s", e)
                rt.baidu_tts = None
        else:
            rt.baidu_tts = None
            logger.info("baidu tts 未启用：环境缺 BAIDU_TTS_API_KEY/BAIDU_TTS_SECRET_KEY")
        rt.memory = memory
        rt.persona = persona
        rt.dedup = dedup
        rt.wakeup = wakeup
        rt.dialog = dialog
        rt.agent = agent
        
        # v1.9 Koin Action：Cron Task 执行引擎
        from butler.core.cron_task import CronTaskExecutor
        rt.cron_task_executor = CronTaskExecutor(s, ha=ha, xiaomi=None, bark=bark, scheduler=rt.scheduler)
        # 从技能存储初始化所有启用的 cron_task
        rt.cron_task_executor.init_from_store(store)
        logger.info("cron_task executor initialized")
        runner.rt = rt                 # 引擎经 ctx.rt 取服务
        rt.runner = runner
        rt.doubao = doubao
        # 技能生成器（v1.2 对话式创建）
        from butler.skills.creator import SkillCreator
        rt.skill_creator = SkillCreator(store, s.data_dir)
        logger.info("skill_creator initialized")
        # 技能隔离管理器（v1.3 自动隔离）
        from butler.skills.quarantine import QuarantineManager
        rt.quarantine_mgr = QuarantineManager(store, s.data_dir)
        runner.quarantine_mgr = rt.quarantine_mgr
        logger.info("quarantine_manager initialized")
        # 技能版本管理器（v1.4）
        from butler.skills.versions import SkillVersionManager
        rt.skill_version_mgr = SkillVersionManager(s.data_dir)
        store.version_mgr = rt.skill_version_mgr
        logger.info("skill_version_manager initialized")
        # 技能沙箱管理器（v1.5）
        from butler.skills.sandbox import SandboxManager
        rt.sandbox_mgr = SandboxManager(runner, s.data_dir)
        logger.info("sandbox_manager initialized")
        # 冲突检测器（v1.6）
        from butler.skills.conflict import ConflictDetector
        rt.conflict_detector = ConflictDetector(store, s.data_dir)
        logger.info("conflict_detector initialized")
        # 技能模板管理器（v1.7）
        from butler.skills.templates import SkillTemplateManager
        rt.template_mgr = SkillTemplateManager(store, s.data_dir)
        logger.info("template_manager initialized")
        # 性能监控器（v1.8）
        from butler.performance import PerformanceMonitor
        rt.perf_monitor = PerformanceMonitor(s.data_dir)
        logger.info("performance_monitor initialized")
        # 多Agent协作管理器（v1.9）
        from butler.agent_collab import AgentCollaborationManager
        rt.collab_mgr = AgentCollaborationManager(s.data_dir)
        rt.collab_mgr.load_state()
        # 注册内置角色
        rt.collab_mgr.register_builtin_agents()
        logger.info("collaboration_manager initialized")
        # 自进化引擎（v2.0）
        from butler.self_evolution import SelfEvolutionEngine
        rt.self_evolution = SelfEvolutionEngine(rt, s.data_dir)
        rt.self_evolution.load_state()
        logger.info("self_evolution_engine initialized (mode=%s)", rt.self_evolution.mode)
        # 启动定时自进化分析（每天凌晨3点）
        _start_evolution_scheduler(rt)
        # v1.5：多源融合定位引擎
        from butler.presence import PresenceEngine
        from butler.presence.sources import HASource, MaPresenceSource
        from butler.presence.fusion import FusionEngine
        from butler.presence.inference import InferenceEngine
        from butler.presence.store import PresenceStore
        rt.presence_engine = PresenceEngine(s.data_dir)
        rt.presence_engine.load_config()
        rt.presence_store = PresenceStore(s.data_dir)
        rt._presence_ha_source = HASource(ha, rt.presence_engine)
        rt._presence_ma_source = MaPresenceSource(rt.presence_engine)
        rt.presence_engine._ma_source = rt._presence_ma_source  # FusionEngine 降级检测用
        rt._presence_fusion = FusionEngine(rt.presence_engine)
        rt._presence_inference = InferenceEngine(rt.presence_engine)
        logger.info("presence_engine initialized (users=%d, rooms=%d, ma_presence=%s)",
                    len(rt.presence_engine.get_all_users()),
                    len(rt.presence_engine.config.get("rooms", [])),
                    rt._presence_ma_source.enabled)
        # v1.7 P0-2 时序数据引擎（模式切换/设备异常/家电运行）
        from butler.timeseries import TimeSeriesEngine
        rt.timeseries = TimeSeriesEngine()
        logger.info("timeseries engine initialized")

        # v1.6 P0-1 模式引擎（5 种互斥情景模式 + 行为规则）
        from butler.modes import ModeEngine
        rt.mode_engine = ModeEngine(rt=rt)
        logger.info("mode engine initialized: %s", rt.mode_engine.current_name)

        # v1.6 P0-4 HA 动态实体发现引擎
        from butler.ha_tools import HADiscovery
        rt.ha_discovery = HADiscovery(rt=rt)
        logger.info("ha discovery engine initialized")

        # v1.6 P0-5 HA 实体白名单管理器
        from butler.ha_tools.whitelist import HAWhitelist
        rt.ha_whitelist = HAWhitelist(rt=rt)
        logger.info("ha whitelist manager initialized")

        # v1.6 P0-3 主动问询引擎
        from butler.proactive import ProactiveEngine
        rt.proactive_engine = ProactiveEngine(rt=rt)
        # 启动时先把过期问询落一次状态：否则只有 get_pending() 轮询会写 timeout
        rt.proactive_engine.sweep_expired()

        logger.info("proactive engine initialized")

        # v1.8 推送风控层（PushGuard）：所有 Bark/TTS 推送经过风控
        from butler.guard import PushGuard
        rt.push_guard = PushGuard(data_dir=str(s.data_dir))
        logger.info("push_guard initialized")

        # v1.9 iLink 微信集成（可选，ILINK_ENABLED=true 启用）
        import os as _os
        if _os.environ.get("ILINK_ENABLED", "").lower() in ("1", "true", "yes"):
            try:
                from butler.integrations.ilink.client import ILinkClient
                from butler.integrations.ilink.bridge import ILinkBridge
                rt.ilink_client = ILinkClient(session_dir=_os.path.join(str(s.data_dir), "ilink"))
                rt.ilink_bridge = ILinkBridge(rt.ilink_client, runtime=rt)
                logger.info("ilink wechat initialized (logged_in=%s)", rt.ilink_client.is_logged_in)
                # 如果已登录，自动启动桥接
                if rt.ilink_client.is_logged_in:
                    rt.ilink_bridge.start()
                    logger.info("ilink bridge auto-started")
            except Exception as e:
                logger.warning("ilink wechat init failed: %s", e)
                rt.ilink_client = None
                rt.ilink_bridge = None
        else:
            rt.ilink_client = None
            rt.ilink_bridge = None
        # P1-5 前半：退出闩必须先于轮询任务出生，否则循环只能去读一个不存在的东西
        rt._stop_event = asyncio.Event()
        # 启动定位轮询后台任务（保持引用防止 GC 回收）
        rt._presence_poll_task = asyncio.create_task(_presence_poll_loop(rt))
        # trigger 规则层（v0.1）：事件匹配 → 条件 → 冷却 → 动作执行
        from butler.triggers.defaults import DEFAULT_TRIGGERS
        from butler.triggers.engine import TriggerEngine
        from butler.triggers.store import TriggerStore
        tstore = TriggerStore(s.data_dir)
        tstore.load()
        tcreated = tstore.ensure_defaults(DEFAULT_TRIGGERS)
        if tcreated:
            logger.info("default triggers seeded: %s", tcreated)
        trigger_engine = TriggerEngine(tstore)
        trigger_engine.set_runtime(rt)
        rt.trigger_engine = trigger_engine
        dialog.trigger_engine = trigger_engine   # on_face 等事件走 trigger 规则层
        # AF ask 桥：轮询 AutoForge 挂起 ask → TTS 播报 → 用户回答注回
        try:
            from butler import af_bridge
            af_base = getattr(s, 'autoforge_base_url', '')  # WO-BUT-012: 空串→af_bridge 模块默认
            af_token = getattr(s, 'autoforge_api_token', '')
            af_inbox_key = getattr(s, 'autoforge_inbox_key', '')
            af_window = getattr(s, 'autoforge_answer_window_s', 900) or 900
            af_bridge.init(rt, base_url=af_base, api_token=af_token, inbox_key=af_inbox_key,
                           answer_window_s=af_window)
            await af_bridge.start()
            print('[startup] af_bridge started, polling', af_base)
        except Exception as e:
            print(f'[startup] af_bridge start failed: {e}')
        # 设备登记表 + 角色登记表（多角色对话 / 全屋任意小爱）
        devices = DeviceRegistry(s)
        devices.load()
        roles = RoleRegistry(s.data_dir)
        roles.load()
        rt.devices = devices
        agent.devices = devices   # 审计 MA-05：play_music 按房间选箱要真拿得到登记表
        rt.roles = roles
        role_state = RoleConversationStore(s.data_dir)
        role_state.load()
        rt.role_state = role_state
        # v0.4：成员定位模块（大脑触发全屋找人）
        from butler.locator import MemberLocator

        rt.locator = MemberLocator(s, memory, devices)
        # v0.5：主动推送通道（豆包app多角色对话通知展示层）
        from butler.notifier import AppNotifier
        rt.notifier = AppNotifier(rt)
        # v2.5 三路由合并：统一 NotifyRouter 承接 App 通道；旧 NotificationRouter 停止接线（文件保留，删除待 DCD 裁定）
        from butler.notify.singleton import get_router as _get_notify_router
        _notify_router = _get_notify_router()
        if _notify_router is not None:
            _notify_router.app = rt.notifier
        # §13.4：公共收件箱闸
        from butler.bus.inbox import InboxGate
        rt.inbox = InboxGate(rt)
        rt.adm_peers = {}
        # v0.9：决策层/心跳（多源数据聚合 → LLM 推理 → 白名单行动）
        from butler.decision.config import DecisionConfig
        from butler.decision.aggregator import DecisionAggregator
        from butler.decision.action_router import ActionRouter
        from butler.decision.engine import DecisionEngine

        decision_cfg = DecisionConfig(s.data_dir)
        aggregator = DecisionAggregator(rt, decision_cfg)
        router = ActionRouter(rt, decision_cfg)
        rt.decision = DecisionEngine(rt, decision_cfg, aggregator, router)

        mqtt.start()
        connected = await mqtt.wait_connected(15)
        logger.info("mqtt connected=%s", connected)
        consumer = asyncio.create_task(_consume(rt))

        # v1.8 P0-5 触发统一注册中心：初始化
        from butler.triggers.registry import TriggerRegistry
        rt.trigger_registry = TriggerRegistry()
        reg = rt.trigger_registry

        # 定时任务：清理音频与指纹
        # V32（审计 2026-10-04）：`sched` 必须在本 try 之前就有名字。否则注册阶段一抛错
        # （import apscheduler 失败／构造失败），下面 `sched.start()` 抛的是 UnboundLocalError，
        # 被本段 except 吞掉后写成 "scheduler start FAILED"——把「没对象可启动」报成「启动失败」。
        sched = None
        scheduler_job_failures: list[str] = []
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler

            sched = AsyncIOScheduler(timezone="Asia/Shanghai")
            _tts_cleanup_job = reg.wrap_scheduler_job("sched:tts_cleanup", lambda: asyncio.run_coroutine_threadsafe(tts.cleanup_old(7), _main_loop))
            reg.register("sched:tts_cleanup", "scheduler", "TTS 旧音频清理", expected_interval_sec=86400, description="每天 04:10 清理 7 天前的 TTS 缓存")
            safe_add_job(sched, scheduler_job_failures, _tts_cleanup_job, "cron", hour=4, minute=10, id="tts_cleanup")

            _fp_cleanup_job = reg.wrap_scheduler_job("sched:fp_cleanup", lambda: asyncio.run_coroutine_threadsafe(_cleanup_fp(s), _main_loop))
            reg.register("sched:fp_cleanup", "scheduler", "指纹缓存清理", expected_interval_sec=86400, description="每天 04:20 清理旧指纹数据")
            safe_add_job(sched, scheduler_job_failures, _fp_cleanup_job, "cron", hour=4, minute=20, id="fp_cleanup")
            # v0.9 决策层心跳：interval 周期触发 heartbeat 事件（trigger 规则层处理）
            if decision_cfg.enabled:
                _heartbeat_job = reg.wrap_scheduler_job("sched:decision_heartbeat", lambda: asyncio.run_coroutine_threadsafe(_heartbeat_tick(rt), _main_loop))
                reg.register("sched:decision_heartbeat", "scheduler", "决策层心跳", expected_interval_sec=decision_cfg.interval_minutes * 60, description="定期触发 heartbeat 事件供 trigger 规则层处理")
                safe_add_job(sched, scheduler_job_failures, 
                    _heartbeat_job,
                    "interval",
                    minutes=decision_cfg.interval_minutes,
                    id="decision_heartbeat",
                )
                logger.info("decision heartbeat scheduled every %d min", decision_cfg.interval_minutes)
            # v1.2 Vibe Coding：决策超时检测（每30秒扫描过期决策）
            def _check_decision_timeouts():
                try:
                    from butler.core import decision_store
                    if not hasattr(decision_store, "check_timeouts"):
                        return  # 功能未实现，静默跳过
                    timed_out = decision_store.check_timeouts()
                    if timed_out:
                        logger.info("decision timeouts: %d expired", len(timed_out))
                except Exception as e:
                    logger.debug("check_decision_timeouts error: %s", e)

            _timeout_job = reg.wrap_scheduler_job("sched:vibe_decision_timeout", _check_decision_timeouts)
            reg.register("sched:vibe_decision_timeout", "scheduler", "Vibe Coding 决策超时检测", expected_interval_sec=30, description="每 30 秒扫描过期决策，自动超时关闭")
            safe_add_job(sched, scheduler_job_failures, 
                _timeout_job,
                "interval",
                seconds=30,
                id="vibe_decision_timeout",
                max_instances=1,
            )
            logger.info("vibe decision timeout checker scheduled every 30s")

            # v1.5 P1-1 设备巡检定时化：每天 9:00 自动执行，结果 Bark 推送（v2.5 改为一天1次）
            _di_morning_job = reg.wrap_scheduler_job("sched:device_inspection", lambda: asyncio.run_coroutine_threadsafe(_scheduled_device_inspection(rt), _main_loop))
            reg.register("sched:device_inspection", "scheduler", "设备巡检", expected_interval_sec=86400, description="每天 09:00 自动巡检全屋设备，结果 Bark 推送（一天1次）")
            safe_add_job(sched, scheduler_job_failures, _di_morning_job, "cron", hour=9, minute=0, id="device_inspection", max_instances=1)
            logger.info("device inspection scheduled at 09:00 daily (once per day)")

            # WO-DB-104 早报/晚报定时触发：到点 fire scheduled 事件，trigger 引擎按 time_range 匹配
            async def _scheduled_report_event(rt, label):
                try:
                    te = getattr(rt, "trigger_engine", None)
                    if te:
                        await te.handle_event("scheduled", {})
                        logger.info("scheduled report fired: %s", label)
                    else:
                        logger.warning("trigger_engine not ready for %s", label)
                except Exception as e:
                    logger.warning("scheduled report %s failed: %s", label, e)

            _mr_job = reg.wrap_scheduler_job("sched:morning_report", lambda: asyncio.run_coroutine_threadsafe(_scheduled_report_event(rt, "morning"), _main_loop))
            reg.register("sched:morning_report", "scheduler", "早报定时播报", expected_interval_sec=86400, description="每天 07:20 fire scheduled 事件，trigger morning_report(time_range 07:00-08:00) 匹配执行")
            safe_add_job(sched, scheduler_job_failures, _mr_job, "cron", hour=7, minute=20, id="morning_report", max_instances=1)

            _er_job = reg.wrap_scheduler_job("sched:evening_report", lambda: asyncio.run_coroutine_threadsafe(_scheduled_report_event(rt, "evening"), _main_loop))
            reg.register("sched:evening_report", "scheduler", "晚报定时播报", expected_interval_sec=86400, description="每天 21:30 fire scheduled 事件，trigger evening_report(time_range 21:00-22:00) 匹配执行")
            safe_add_job(sched, scheduler_job_failures, _er_job, "cron", hour=21, minute=30, id="evening_report", max_instances=1)
            logger.info("morning/evening report scheduled at 07:20 and 21:30 daily")

            # v1.7 P0-3 异常检测：每天 8:30 检查（v2.5 改为一天1次，汇总为一条 Bark 推送）
            _anomaly_job = reg.wrap_scheduler_job("sched:anomaly_detector", lambda: asyncio.run_coroutine_threadsafe(_scheduled_anomaly_check(rt), _main_loop))
            reg.register("sched:anomaly_detector", "scheduler", "异常检测", expected_interval_sec=86400, description="每天 08:30 检测设备异常，汇总为一条 Bark 推送（一天1次）")
            safe_add_job(sched, scheduler_job_failures, _anomaly_job, "cron", hour=8, minute=30, id="anomaly_detector", max_instances=1)
            logger.info("anomaly detector scheduled at 08:30 daily (once per day, summary push)")

            # v1.7 P0-1 晨起场景：每 5 分钟检查（6:00-10:00 时段内才会实际触发）
            _morning_job = reg.wrap_scheduler_job("sched:morning_routine", lambda: asyncio.run_coroutine_threadsafe(_scheduled_morning_routine(rt), _main_loop))
            reg.register("sched:morning_routine", "scheduler", "晨起场景检测", expected_interval_sec=300, description="每 5 分钟检测晨起条件（6:00-10:00 时段内触发）")
            safe_add_job(sched, scheduler_job_failures, _morning_job, "interval", minutes=5, id="morning_routine", max_instances=1)
            logger.info("morning routine scheduled every 5 minutes")

            # v1.6 P0-2 定位驱动模式自动切换：每 5 分钟检查
            _mode_job = reg.wrap_scheduler_job("sched:mode_auto_switch", lambda: asyncio.run_coroutine_threadsafe(_scheduled_mode_auto_switch(rt), _main_loop))
            reg.register("sched:mode_auto_switch", "scheduler", "定位驱动模式自动切换", expected_interval_sec=300, description="每 5 分钟根据用户位置自动切换全屋模式")
            safe_add_job(sched, scheduler_job_failures, _mode_job, "interval", minutes=5, id="mode_auto_switch", max_instances=1)
            logger.info("mode auto switch scheduled every 5 minutes")

            # v1.6 P1-2 主动问询场景库：每 15 分钟检查场景触发条件
            _proactive_job = reg.wrap_scheduler_job("sched:proactive_scenes", lambda: asyncio.run_coroutine_threadsafe(_scheduled_proactive_scenes(rt), _main_loop))
            reg.register("sched:proactive_scenes", "scheduler", "主动问询场景库", expected_interval_sec=900, description="每 15 分钟检查主动问询场景触发条件")
            safe_add_job(sched, scheduler_job_failures, _proactive_job, "interval", minutes=15, id="proactive_scenes", max_instances=1)
            logger.info("proactive scenes checker scheduled every 15 minutes")

            # v1.8 P1-5 Bark 合并消息定期批量推送（每5分钟）
            async def _flush_bark_merged():
                bark = getattr(rt, "bark", None)
                if bark and hasattr(bark, "flush_merged"):
                    try:
                        sent = await bark.flush_merged()
                        if sent > 0:
                            logger.info("bark merged flush: %d summary sent", sent)
                    except Exception as e:
                        logger.debug("bark merged flush error: %s", e)

            _bark_flush_job = reg.wrap_scheduler_job("sched:bark_merged_flush", lambda: asyncio.run_coroutine_threadsafe(_flush_bark_merged(), _main_loop))
            reg.register("sched:bark_merged_flush", "scheduler", "Bark 合并消息批量推送", expected_interval_sec=300, description="每5分钟把风控过载期间合并的 Bark 消息批量推送为摘要")
            safe_add_job(sched, scheduler_job_failures, _bark_flush_job, "interval", minutes=5, id="bark_merged_flush", max_instances=1)
            logger.info("bark merged flush scheduled every 5 minutes")

            # v1.1 记忆提取：每天 03:15 分析最近7天对话（错开 3:00 自进化任务）
            _mem_extract_job = reg.wrap_scheduler_job(
                "sched:memory_extract",
                lambda: asyncio.run_coroutine_threadsafe(_scheduled_memory_extract(rt), _main_loop),
            )
            reg.register(
                "sched:memory_extract", "scheduler", "记忆提取",
                expected_interval_sec=86400,
                description="每天 03:15 分析最近7天对话，提取偏好/家庭/事件/习惯到 memory_facts",
            )
            safe_add_job(sched, scheduler_job_failures, _mem_extract_job, "cron", hour=3, minute=15, id="memory_extract", max_instances=1)
            logger.info("memory extract scheduled at 03:15 daily")

            # v1.6 自进化自动分析：每天凌晨 3:45 跑一次快速路由分析 + 失败模式分析
            async def _self_evolve_job():
                try:
                    logger.info("[self-evolve] daily auto analysis started")
                    from butler.core.self_evolve import run_daily_analysis
                    run_daily_analysis()
                except Exception as e:
                    logger.error("SELF_EVOLVE_JOB_FAILED: %r", e)

            safe_add_job(sched, scheduler_job_failures, _self_evolve_job, "cron", hour=3, minute=45, id="self_evolve_daily", max_instances=1)
            logger.info("self-evolve daily analysis scheduled at 03:45 daily")

            # v1.7 LLM 智能简报：7:00 早报，22:00 晚报
            async def _morning_briefing():
                from butler.core.briefing import push_briefing
                await push_briefing(rt, "morning")
            async def _evening_briefing():
                from butler.core.briefing import push_briefing
                await push_briefing(rt, "evening")
            safe_add_job(sched, scheduler_job_failures, _morning_briefing, "cron", hour=7, minute=10, id="morning_briefing", max_instances=1)
            safe_add_job(sched, scheduler_job_failures, _evening_briefing, "cron", hour=22, minute=0, id="evening_briefing", max_instances=1)
            logger.info("LLM briefing scheduled: 7:10 morning, 22:00 evening (Asia/Shanghai)")

            # v1.7 感知自进化：凌晨 4:00 分析事件流发现行为模式
            async def _perception_learn_job():
                from butler.core.perception_learn import analyze_patterns
                try:
                    result = analyze_patterns(days=7)
                    logger.info("perception learn daily: %d events, %d patterns",
                                result.get("total_events", 0), len(result.get("top_entities", [])))
                except Exception as e:
                    logger.warning("[perception-learn] auto analysis failed: %s", e)
            safe_add_job(sched, scheduler_job_failures, _perception_learn_job, "cron", hour=4, minute=0, id="perception_learn_daily", max_instances=1)
            logger.info("perception learn daily analysis scheduled at 04:00 daily")

            # v1.7 安全监控：每15分钟检查一次
            async def _security_monitor_job():
                from butler.core.security_monitor import SecurityMonitor, push_alert
                # 单例化：冷却状态需跨调用保留，否则每15分钟重置
                if not hasattr(rt, "_security_monitor"):
                    rt._security_monitor = SecurityMonitor(rt)
                sm = rt._security_monitor
                alerts = sm.check()
                for alert in alerts:
                    # 审计 MA-01：出口抽成 push_alert，只有 Bark 真回执才算送达
                    await push_alert(rt, alert)
                    # v2.5: 安全告警也走 TTS 队列（P1 最高优先级）
                    try:
                        from butler.tts.helper import enqueue_tts
                        enqueue_tts(f"安全提醒：{alert['message']}", priority=1,
                                   room="客厅", member="系统", override_quiet=True)
                    except Exception as e:
                        logger.warning("security alert TTS enqueue failed: %s", e)
            safe_add_job(sched, scheduler_job_failures, _security_monitor_job, "interval", minutes=15, id="security_monitor", max_instances=1)
            logger.info("security monitor scheduled every 15 minutes")

        except Exception as e:
            logger.warning("scheduler job registration failed: %s", e)

        rt.scheduler_job_failures = list(scheduler_job_failures)
        if scheduler_job_failures:
            logger.error("SCHEDULER_JOBS_MISSING n=%d ids=%s",
                         len(scheduler_job_failures), ",".join(scheduler_job_failures))

        # P0-5: sched.start() 独立 try，避免作业注册失败连带调度器不启动
        # V32：⛔ 在这里 `return` 早退——lifespan 后面每条启动腿都会被一起跳过
        # （`rt.xiaomi_ear.start()`、`rt.event_stream.start()`），那是用一个坏掉的调度器
        # 换一桌更坏的静默。判空只跳过调度器这一段。
        if sched is None:
            logger.error("scheduler unavailable: registration phase failed, start skipped")
        else:
            try:
                sched.start()
                rt._sched = sched
                rt.scheduler = sched
                agent.scheduler = sched
                # v1.9: scheduler 就绪后，把 cron_task 执行器挂上并初始化任务
                if getattr(rt, "cron_task_executor", None):
                    rt.cron_task_executor.scheduler = sched
                    rt.cron_task_executor.init_from_store(store)
            except Exception as e:
                logger.error("scheduler start FAILED: %s", e, exc_info=True)

        # v0.9.2 小爱耳朵：HA websocket 直连订阅 conversation 传感器，脱离 NR
        try:
            from butler.core.xiaomi_ear import XiaomiEar
            rt.xiaomi_ear = XiaomiEar(rt)
            rt.xiaomi_ear.start()
        except Exception as e:
            logger.warning("xiaomi_ear start failed: %s", e)

        # v1.7 事件流监听器：订阅所有设备状态变化，滑动窗口+SQLite持久化
        try:
            from butler.core.event_stream import EventStream
            rt.event_stream = EventStream(rt)
            rt.event_stream.start()
        except Exception as e:
            logger.warning("event_stream start failed: %s", e)

        # v1.1 电子书播放：EPUB → TTS → 小爱音箱连续播放
        try:
            from butler.audiobook.manager import AudiobookManager
            books_dir = getattr(s, "audiobook_books_dir", "") or "/nas/books"
            rt.audiobook = AudiobookManager(rt, s.data_dir, books_dir)
            app.state.audiobook_mgr = rt.audiobook
            logger.info("audiobook manager ready, books_dir=%s", books_dir)
        except Exception as e:
            logger.warning("audiobook manager init failed: %s", e)

        try:
            yield
        finally:
            consumer.cancel()
            # P1-5 后半：定位轮询⛔ 是 consumer 的下属，它有独立的退出闩与收尾，
            # 且必须排在 close() 之前（晚一步就是往已关闭的连接里写）。
            _stop = getattr(rt, "_stop_event", None)
            if _stop is not None:
                _stop.set()
            _pt = getattr(rt, "_presence_poll_task", None)
            if _pt is not None:
                _pt.cancel()
                try:
                    await asyncio.wait_for(_pt, timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    _pt.cancel()          # 超时没收回＝再钉一次取消标志，⛔ 让收尾卡在这里
            mqtt.stop()
            try:
                if hasattr(rt, "xiaomi_ear"):
                    rt.xiaomi_ear.stop()
            except Exception:
                pass
            try:
                rt._sched.shutdown(False)
            except Exception:
                pass
            from butler.store.db import close

            close()

    app = Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=[Middleware(
            CORSMiddleware,
            allow_origins=[
                "http://192.168.2.200:8095",
                "http://127.0.0.1:8095",
                "https://fn7t.tailf314d3.ts.net",
            ],
            allow_methods=["*"],
            allow_headers=["*"],
        )],
        exception_handlers={Exception: _global_exc},
    )
    return app


async def _cleanup_fp(s) -> None:
    before = time.time() - s.dedup_window_days * 86400 * 2
    await asyncio.to_thread(repo.cleanup_fingerprints, before)


async def _heartbeat_tick(rt) -> None:
    """v0.9 决策层心跳：触发 heartbeat 事件，交给 trigger 规则层（decision_heartbeat）处理。"""
    try:
        te = getattr(rt, "trigger_engine", None)
        if te is None:
            return
        await te.handle_event("heartbeat", {})
    except Exception as e:
        logger.warning("heartbeat tick failed: %s", e)


async def _scheduled_device_inspection(rt) -> None:
    """v1.5 P1-1 定时设备巡检：执行 HA 设备巡检，结果通过 Bark 推送。"""
    try:
        from butler.skills.engines.ha_inspection.engine import HAInspectionEngine
        from butler.skills.runner_types import SkillContext, SkillResult

        engine = HAInspectionEngine()
        # 构造最小 SkillContext
        ctx = SkillContext(
            skill={"brain": {"checks": "all", "battery_threshold": 20}},
            source="scheduler",
            rt=rt,
        )
        result = await engine.run(ctx)
        if not result.ok:
            logger.warning("scheduled device inspection failed: %s", result.error)
            return

        text = result.text or "设备巡检完成。"
        # Bark 推送（不播 TTS，避免打扰）
        if rt.bark:
            await rt.bark.push(
                body=text,
                title="【设备巡检】定时报告",
                level="active",
                group="device_inspection",
            )
            logger.info("scheduled device inspection done, bark pushed (%d chars)", len(text))
        else:
            logger.info("scheduled device inspection done (bark not configured): %s", text[:100])
    except Exception as e:
        logger.warning("scheduled device inspection error: %s", e)


async def _scheduled_anomaly_check(rt) -> None:
    """v1.7 P0-3 实时异常检测：每 15 分钟执行，发现异常推送 Bark。"""
    try:
        from butler.timeseries.anomaly import AnomalyDetector
        detector = AnomalyDetector(rt=rt)
        events = await detector.check_all()
        if events:
            logger.info("anomaly check found %d new anomalies", len(events))
    except Exception as e:
        logger.warning("scheduled anomaly check error: %s", e)


async def _scheduled_morning_routine(rt) -> None:
    """v1.7 P0-1 晨起场景：每 5 分钟检查，满足条件则播报。"""
    try:
        from butler.morning import MorningRoutine
        routine = MorningRoutine(rt=rt)
        broadcasted = await routine.check_and_broadcast()
        if broadcasted:
            logger.info("morning routine broadcasted successfully")
    except Exception as e:
        logger.warning("scheduled morning routine error: %s", e)


async def _scheduled_mode_auto_switch(rt) -> None:
    """v1.6 P0-2 定位驱动模式自动切换：每 5 分钟检查。"""
    try:
        from butler.modes.auto_switch import ModeAutoSwitcher
        switcher = ModeAutoSwitcher(rt=rt)
        result = await switcher.check_and_switch()
        if result.switched:
            logger.info("mode auto switch: %s -> %s (%s)",
                        result.from_mode, result.to_mode, result.reason)
    except Exception as e:
        logger.warning("scheduled mode auto switch error: %s", e)


async def _scheduled_proactive_scenes(rt) -> None:
    """v1.6 P1-2 主动问询场景库：每 15 分钟检查场景触发条件。"""
    try:
        engine = getattr(rt, "proactive_engine", None)
        if engine is None:
            return
        triggered = await engine.check_scenes()
        if triggered:
            logger.info("proactive scenes triggered: %d", len(triggered))
    except Exception as e:
        logger.warning("scheduled proactive scenes error: %s", e)


async def _scheduled_memory_extract(rt) -> None:
    """v1.1 记忆提取：每天 03:15 分析最近7天对话，提取偏好/家庭/事件/习惯到 memory_facts。"""
    try:
        from butler.memory.extractor import MemoryExtractor
        extractor = MemoryExtractor(rt)
        result = await extractor.run(days=7)
        logger.info("scheduled memory extract done: %s", result)
    except Exception as e:
        logger.warning("scheduled memory extract error: %s", e)


def create_app() -> Starlette:
    return _build_app()


app = create_app()


def _start_evolution_scheduler(rt) -> None:
    """启动定时自进化分析（每天凌晨3点运行）。"""
    import asyncio
    import threading

    def _scheduler_loop():
        """后台线程：每天凌晨3点运行自进化分析。"""
        import time as _time
        while True:
            now = _time.localtime()
            # 计算到下一个凌晨3点的秒数
            seconds_until_3am = (
                (3 - now.tm_hour) % 24 * 3600
                - now.tm_min * 60
                - now.tm_sec
            )
            if seconds_until_3am <= 0:
                seconds_until_3am += 86400
            _time.sleep(seconds_until_3am)
            try:
                evo = getattr(rt, "self_evolution", None)
                if evo:
                    suggestions = evo.analyze()
                    evo.save_state()
                    logger.info(
                        "scheduled evolution analysis done: %d suggestions",
                        len(suggestions),
                    )
            except Exception as e:
                logger.error("scheduled evolution analysis failed: %s", e)

    t = threading.Thread(target=_scheduler_loop, daemon=True, name="evolution-scheduler")
    t.start()
    logger.info("evolution scheduler started (daily 03:00)")


async def _presence_poll_loop(rt) -> None:
    """定位引擎轮询循环：定时从 HA 拉取数据，融合推理，更新用户位置。"""
    import asyncio as _asyncio
    import time as _time
    from butler.presence import UserPresence, RoomState

    engine = rt.presence_engine
    ha_source = rt._presence_ha_source
    fusion = rt._presence_fusion
    inference = rt._presence_inference
    store = rt.presence_store

    interval = engine.config.get("poll_interval_seconds", 10)
    # P1-5：闩由 lifespan 装配；缺则当场补一枚并挂回 rt（⛔ 读不到就一路裸跑到停机窗口之后）
    stop = getattr(rt, "_stop_event", None)
    if stop is None:
        stop = _asyncio.Event()
        rt._stop_event = stop
    logger.info("presence poll loop started (interval=%ds)", interval)

    while not stop.is_set():
        try:
            # 1. 从 HA 拉取数据
            ha_data = await ha_source.fetch()
            if not ha_data:
                logger.debug("presence poll: ha_data empty")

            # 2. 融合推理
            fused = fusion.fuse(ha_data)
            locations = fusion.determine_locations(fused)

            # 3. 更新用户位置
            for user_id, (room, conf, sources) in locations.items():
                if user_id not in engine._users:
                    engine._users[user_id] = UserPresence(user_id)
                u = engine._users[user_id]
                if room and conf >= engine.config.get("confidence_threshold", 0.6):
                    if u.room != room or u.confidence < conf:
                        u.update(room, conf, sources)
                        store.record_location(user_id, room, conf, sources)
                elif room is None and u.room is not None:
                    # 位置变未知，保留旧位置但降低置信度
                    u.confidence = max(0.0, u.confidence - 0.1)

            # 4. 更新房间状态
            occupancy = ha_data.get("occupancy", {})
            for room_id in set(list(occupancy.keys()) + list(engine._rooms.keys())):
                if room_id not in engine._rooms:
                    engine._rooms[room_id] = RoomState(room_id)
                r = engine._rooms[room_id]
                r.occupancy = room_id in occupancy
                r.active_sensors = occupancy.get(room_id, [])
                if r.occupancy:
                    r.last_motion = _time.time()
                # 身份排除推理
                inferred = inference.infer_room_occupants(room_id, ha_data, locations)
                r.users = inferred["users"]
                r.unknown_person = inferred["unknown_person"]

            engine._last_poll = _time.time()

        except Exception as e:
            logger.warning("presence poll failed: %s", e)

        # 等的是退出闩（带超时），⛔ 等满周期：置位之后哪怕间隔 30 秒也要当场醒
        try:
            await _asyncio.wait_for(stop.wait(), timeout=interval)
        except _asyncio.TimeoutError:
            pass
