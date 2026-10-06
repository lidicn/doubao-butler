"""通知路由层：notify(channel, text, **kwargs) 统一入口，按 channel 分发到 4 个通道。

通道（§0）：
  app  → 豆包App对话   但ler/notifier.py 的 AppNotifier.push(role_id, scene, direct_text)
  tts  → 小爱TTS       butler/integrations/ha.py 的 HAClient.tts_speak(message, entity_id)
  bark → Bark推送      butler/tts/manager.py 兜底用的 bark.push(text, title=...)
  tv   → TV弹窗        §1 未给既有契约，本模块定义 TVPopupPort.popup(...)，实现在调用方

每条通知携带：优先级、目标房间、是否需要TTS、是否需要推送（见 Notification）。
本模块**只做分发**：不实现 HA / 弹窗 / TTS 合成细节，各通道以协议注入。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

try:  # 复用现网日志封装；缺失时退回 stdlib logging
    from butler.logging_setup import get_logger
except Exception:  # pragma: no cover
    import logging

    def get_logger(name: str):
        return logging.getLogger(name)

logger = get_logger("butler.notify.router")

from butler.modes.engine import gate_bark  # DCD 裁定②：推送入口的模式门（⛔ 循环依赖：modes/⛔ import notify）

# 通知类型白名单的**单一真源**：api/notify_routes.py 与 core/cron_task.py 都从这里取。
# 原先定义只在 api 层，cron 出口要用就得 core→api 反向依赖（且被 api 的 PIL 导入拖崩）。
ALLOWED_TYPES = ("info", "warning", "error")

# ── 通道常量 ────────────────────────────────────────────────────────
CHANNEL_APP = "app"
CHANNEL_TTS = "tts"
CHANNEL_BARK = "bark"
CHANNEL_TV = "tv"
CHANNEL_AUTO = "auto"
CHANNEL_ALL = "all"

CHANNEL_ALIASES = {
    "app": CHANNEL_APP, "doubao": CHANNEL_APP, "doubao_app": CHANNEL_APP,
    "豆包": CHANNEL_APP, "豆包app": CHANNEL_APP, "豆包app对话": CHANNEL_APP, "chat": CHANNEL_APP,
    "tts": CHANNEL_TTS, "xiaoai": CHANNEL_TTS, "xiaoi": CHANNEL_TTS, "ha": CHANNEL_TTS,
    "speaker": CHANNEL_TTS, "小爱": CHANNEL_TTS, "小爱tts": CHANNEL_TTS,
    "bark": CHANNEL_BARK, "push": CHANNEL_BARK, "手机": CHANNEL_BARK, "推送": CHANNEL_BARK,
    "tv": CHANNEL_TV, "弹窗": CHANNEL_TV, "tv弹窗": CHANNEL_TV,
    "auto": CHANNEL_AUTO, "自动": CHANNEL_AUTO,
    "all": CHANNEL_ALL, "全部": CHANNEL_ALL,
}


# ── 通道端口（只定义签名；实现在调用方，且与 §1 现有契约逐一对齐） ──
@runtime_checkable
class AppPort(Protocol):
    """对齐 butler/notifier.py AppNotifier.push。"""

    async def push(self, role_id: str, scene: str, direct_text: str | None = None) -> str: ...


@runtime_checkable
class TTSChannelPort(Protocol):
    """对齐 butler/integrations/ha.py HAClient.tts_speak。"""

    async def tts_speak(self, message: str, entity_id: str = "tts.doubao_tts") -> bool: ...


@runtime_checkable
class BarkPort(Protocol):
    """对齐 butler/tts/manager.py 兜底 bark.push(text, title=...)。"""

    async def push(self, text: str, title: str = "") -> Any: ...


@runtime_checkable
class TVPopupPort(Protocol):
    """TV 弹窗（§1 无既有契约，此为本模块定义的最小接口）。

    生产侧绑的是 butler.integrations.tv.TVClient——它给的是 notify(payload)->bool；
    popup 至今只有本模块单测的假件实现过，所以无 tv_payload 那条分支在真身对象上
    拿不到 popup，路由据实记失败（⛔ 把它伪装成已送达）。
    """

    def notify(self, payload: dict) -> bool: ...

    async def popup(
        self, text: str, *, title: str = "", priority: int = 3,
        room: str = "", duration_s: float = 8.0,
    ) -> bool: ...


@runtime_checkable
class TTSQueuePort(Protocol):
    """TTS 队列入队口（butler.tts.queue.TTSQueue.enqueue 天然满足）。"""

    def enqueue(self, text: str, **kwargs) -> Any: ...


@dataclass
class Notification:
    """一条通知的完整元数据（优先级 / 目标房间 / 是否需要TTS / 是否需要推送）。"""

    text: str
    channel: str = CHANNEL_AUTO
    priority: int = 3
    room: str = ""
    need_tts: bool = False
    need_push: bool = False
    title: str = ""
    role_id: str = ""
    scene: str = ""
    direct_text: str | None = None
    device_id: str | None = None
    entity_id: str | None = None
    member: str = ""
    voice: str | None = None
    backend: str | None = None
    speed: float | None = None
    whole_house: bool = False
    ttl_s: float | None = None
    override_quiet: bool = False
    duration_s: float | None = None
    tv_payload: dict | None = None
    bark_kwargs: dict = field(default_factory=dict)
    trace_id: str = ""


@dataclass
class ChannelResult:
    channel: str
    ok: bool
    detail: Any = None
    error: str | None = None


@dataclass
class NotifyResult:
    channel: str
    text: str
    targets: list[str] = field(default_factory=list)
    results: dict[str, ChannelResult] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.targets) and all(r.ok for r in self.results.values())

    @property
    def delivered(self) -> list[str]:
        return [c for c, r in self.results.items() if r.ok]


def _current_trace_id() -> str:
    """从对话链 contextvar 取 trace_id；不在链上时返回空串。"""
    try:
        from butler.core.agent import _current_trace_id as _cv
        return _cv.get() or ""
    except Exception:
        return ""


_BARK_EXTRA_KEYS = frozenset({
    "subtitle", "level", "sound", "volume", "icon", "image", "url", "group",
    "badge", "call", "is_archive", "ttl", "markdown",
})


class NotifyRouter:
    """统一通知入口：route / notify 皆可，按 channel 分发。

    失败语义：
      - 未知 channel、空 text、非法 priority → 抛 ValueError（fail-closed，编程错误）
      - 通道投递失败 / 通道未绑定 → 记入结果不抛异常（fail-open，通知失败不许打断业务）
    """

    def __init__(
        self,
        *,
        app: AppPort | None = None,
        ha: TTSChannelPort | None = None,
        bark: BarkPort | None = None,
        tv: TVPopupPort | None = None,
        tts_queue: TTSQueuePort | None = None,
        room_entities: dict[str, str] | None = None,
        default_entity_id: str = "tts.doubao_tts",
        default_role_id: str = "butler",
        default_tv_duration_s: float = 8.0,
        use_queue_for_tts: bool = True,
    ):
        self.app = app
        self.ha = ha
        self.bark = bark
        self.tv = tv
        self.tts_queue = tts_queue
        self.room_entities = dict(room_entities or {})
        self.default_entity_id = default_entity_id
        self.default_role_id = default_role_id
        self.default_tv_duration_s = default_tv_duration_s
        self.use_queue_for_tts = use_queue_for_tts

    # ── 统一入口 ──────────────────────────────────────────────────
    async def notify(self, channel: str, text: str, **kwargs) -> NotifyResult:
        return await self.route(channel, text, **kwargs)

    async def route(self, channel: str, text: str, **kwargs) -> NotifyResult:
        ch = self._normalize(channel)
        if not (text or "").strip():
            raise ValueError("text 不能为空")
        note = Notification(text=text, channel=ch, **kwargs)
        if not (1 <= int(note.priority) <= 5):
            raise ValueError(f"priority 必须是 1-5: {note.priority!r}")
        targets = self.resolve_targets(note)
        result = NotifyResult(channel=ch, text=text, targets=targets)
        trace = note.trace_id or _current_trace_id()
        note.trace_id = trace
        for target in targets:
            result.results[target] = await self._deliver(target, note)
            logger.info("notify channel=%s trace=%s ok=%s",
                        target, trace or "-", result.results[target].ok)
        return result

    # ── 目标解析 ──────────────────────────────────────────────────
    def resolve_targets(self, note: Notification) -> list[str]:
        if note.channel == CHANNEL_ALL:
            return [CHANNEL_APP, CHANNEL_TTS, CHANNEL_BARK, CHANNEL_TV]
        if note.channel == CHANNEL_AUTO:
            targets: list[str] = []
            if note.need_tts:
                targets.append(CHANNEL_TTS)
            if note.need_push:
                targets.append(CHANNEL_BARK)
            return targets or [CHANNEL_APP]      # 默认落到豆包App对话
        return [note.channel]

    def available_channels(self) -> list[str]:
        bound = []
        if self.app is not None:
            bound.append(CHANNEL_APP)
        if self.ha is not None or (self.tts_queue is not None and self.use_queue_for_tts):
            bound.append(CHANNEL_TTS)
        if self.bark is not None:
            bound.append(CHANNEL_BARK)
        if self.tv is not None:
            bound.append(CHANNEL_TV)
        return bound

    # ── 分发 ──────────────────────────────────────────────────────
    async def _deliver(self, target: str, note: Notification) -> ChannelResult:
        try:
            if target == CHANNEL_APP:
                return await self._to_app(note)
            if target == CHANNEL_TTS:
                return await self._to_tts(note)
            if target == CHANNEL_BARK:
                return await self._to_bark(note)
            if target == CHANNEL_TV:
                return await self._to_tv(note)
            return ChannelResult(target, False, error=f"unknown target: {target}")
        except Exception as e:
            logger.warning("notify %s failed: %s", target, e)
            return ChannelResult(target, False, error=f"{type(e).__name__}: {e}")

    async def _to_app(self, note: Notification) -> ChannelResult:
        if self.app is None:
            return ChannelResult(CHANNEL_APP, False, error="not_bound: app")
        role_id = note.role_id or self.default_role_id
        # scene/direct_text 语义（§1 契约）：
        #   direct_text 显式给出 → 精确提醒模式（豆包原样输出）
        #   只给 scene          → 场景描述模式（豆包自由发挥）
        #   都没给              → 以 text 作为精确提醒
        if note.direct_text is not None:
            scene, direct = note.scene or "提醒", note.direct_text
        elif note.scene:
            scene, direct = note.scene, None
        else:
            scene, direct = "提醒", note.text
        reply = await self.app.push(role_id, scene, direct_text=direct)
        return ChannelResult(CHANNEL_APP, True, detail=reply)

    async def _to_tts(self, note: Notification) -> ChannelResult:
        if self.tts_queue is not None and self.use_queue_for_tts:
            res = self.tts_queue.enqueue(
                note.text, priority=note.priority, device_id=note.device_id,
                room=note.room, voice=note.voice, member=note.member,
                backend=note.backend, speed=note.speed, whole_house=note.whole_house,
                ttl_s=note.ttl_s, override_quiet=note.override_quiet,
                trace_id=note.trace_id,
            )
            ok = bool(getattr(res, "accepted", False))
            reason = getattr(res, "reason", "rejected")
            return ChannelResult(CHANNEL_TTS, ok, detail=res, error=None if ok else reason)
        if self.ha is None:
            return ChannelResult(CHANNEL_TTS, False, error="not_bound: ha")
        entity_id = note.entity_id or self.room_entities.get(note.room, "") or self.default_entity_id
        ok = bool(await self.ha.tts_speak(note.text, entity_id=entity_id))
        return ChannelResult(CHANNEL_TTS, ok, detail=entity_id)

    async def _to_bark(self, note: Notification) -> ChannelResult:
        if self.bark is None:
            return ChannelResult(CHANNEL_BARK, False, error="not_bound: bark")
        # DCD 裁定②：push_guard.py:223 那句「由调用方在调用前检查模式」的调用方，此前就是零调用的
        # can_bark。silent 的口径＝Bark 的 level=passive（只落通知栏、不响）；critical 要不要一并放行
        # 属规则表语义（bark_silent 没有紧急例外），已投待裁。
        silent = str((note.bark_kwargs or {}).get("level") or "").strip().lower() == "passive"
        allowed, mode = gate_bark(silent=silent)
        if not allowed:
            logger.info("NOTIFY_BARK_BLOCKED mode=%s trace=%s: %.30s",
                        mode, note.trace_id or "-", note.text)
            return ChannelResult(CHANNEL_BARK, False, error=f"mode_blocked:{mode}")
        title = note.title or (f"豆包管家 · {note.member}" if note.member else "豆包管家")
        extra = {k: v for k, v in (note.bark_kwargs or {}).items() if k in _BARK_EXTRA_KEYS}
        detail = await self.bark.push(note.text, title=title, **extra)
        return ChannelResult(CHANNEL_BARK, bool(detail), detail=detail)

    async def _to_tv(self, note: Notification) -> ChannelResult:
        if self.tv is None:
            return ChannelResult(CHANNEL_TV, False, error="not_bound: tv")
        if note.tv_payload is not None:
            if not hasattr(self.tv, "notify"):
                return ChannelResult(CHANNEL_TV, False, error="tv client has no notify()")
            sent = self.tv.notify(note.tv_payload)
            return ChannelResult(CHANNEL_TV, bool(sent),
                                 error=None if sent else "tv client dropped the payload")
        ok = bool(await self.tv.popup(
            note.text, title=note.title, priority=note.priority, room=note.room,
            duration_s=note.duration_s or self.default_tv_duration_s,
        ))
        return ChannelResult(CHANNEL_TV, ok)

    @staticmethod
    def _normalize(channel: str) -> str:
        key = str(channel or "").strip().lower()
        ch = CHANNEL_ALIASES.get(key)
        if ch is None:
            raise ValueError(
                f"未知通知通道: {channel!r}（可用: {sorted(set(CHANNEL_ALIASES.values()))}）"
            )
        return ch