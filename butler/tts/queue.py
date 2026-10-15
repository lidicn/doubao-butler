"""TTS 播放队列：三通道优先级插入、熔断/过载保护、设备级去重、TTL、夜间静默、顺序播放锁。

设计（对齐交接单 §0 / §3.1）：
- P1 告警：强制插队（排到所有非 P1 之前）+ 清除熔断冷却（冷却倒计时与触发滑窗一并清零）
- P2 高优：插到「非 P1 区」队头
- P3+ 标准：入队尾（3/4/5 同属标准带，带内 FIFO）
- 熔断：breaker_window_s 内累计 breaker_threshold 次触发 → 冷却 breaker_cooldown_s；
        冷却期间只放行 P1，非 P1 新消息丢弃
- 过载：入队后（含新消息）条数 >= overload_threshold → 丢最低优先级档（同带丢最旧）、保 P1 告警档、
        当前这条照收；未在暂停中则暂停 overload_pause_s（只挡非 P1 新负载，P1 暂停期间当场能播），
        + 回调 on_overload(文本, 房间) 播报过载通知（DCD 裁定⑤-2）
- 设备级去重：同 dedupe_key（默认 device_id）的新消息替换队列中的旧消息
- TTL：到期丢弃（按档位：告警不过期／高优 1800s／标准 300s，见 PRIORITY_TTL_S；ttl_s<=0 表示不设 TTL）
- 夜间静默：quiet_start-quiet_end 之外静默（P1 与 override_quiet 例外），入队、出队两侧都判
- 音量：P1=80 / P2=60 / P3+=30（可配置，item 可显式覆盖）
- 时长估算：200ms/字 + 2000ms 固定缓冲 + 全屋 +3000ms

本模块只做「排队与决策」，**不实现 HA / TTS 引擎调用细节**：
播放通过 :class:`TTSSpeaker` 协议注入，由调用方用 §1 的 ``TTSManager.speak`` + ``HAClient`` 实现。
"""
from __future__ import annotations

import asyncio
import inspect
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

from butler.param import ParamError, to_int

try:  # 复用现网日志封装；缺失时退回 stdlib logging，保证本模块可独立导入/测试
    from butler.logging_setup import get_logger
except Exception:  # pragma: no cover
    import logging

    def get_logger(name: str):
        return logging.getLogger(name)

logger = get_logger("butler.tts.queue")

# ── 优先级分级（1-5） ────────────────────────────────────────────────
PRIORITY_MIN = 1
PRIORITY_MAX = 5
PRIORITY_ALERT = 1      # 告警：强制插队 + 清冷却
PRIORITY_HIGH = 2       # 高优：插队头（非 P1 区）
PRIORITY_NORMAL = 3     # 标准：入队尾（3/4/5 同带）

# ── 档位 → TTL（DCD 裁定③ 20261002-DB六件影子代码-裁定.md:44-52）──
# 为什么这张表挂在本模块、⛔ 留在 guard/push_guard.py：
#   那里原有 `PRIORITY_TTL`（critical 0／warning 1800／info 300）全树引用只有定义行本身
#   ＝裁定点名的「空号」；而 guard 的 `_tts_queue` 在现网是被 `api/tts_routes.py:280/292`
#   「播完再 tts_pop()」1:1 排空的计数器，条目在里面**不产生年龄** ⇒ 过期分支写在那侧只会
#   多一条没人走的路。真正让一句话排队等待的是本模块这条队列（`notify/router.py:281`）。
# 数值⛔ 改动，仍是裁定表里那三个；TTL 从**入队（＝进风控）那一刻**起算（`created_at`），
# ⛔「播报失败重投」起算——失败风暴下永不到期，能力就没了（裁定原文）。
PRIORITY_TTL_S: dict[int, float] = {
    PRIORITY_ALERT: 0.0,        # 告警不过期
    PRIORITY_HIGH: 1800.0,      # 30 分钟
    PRIORITY_NORMAL: 300.0,     # 5 分钟（4/5 同带）
}
# 档位 → 风控词表：审计行的 priority 要与 `push_audit` 里既有那套词同一份口径。
PRIORITY_LEVEL_NAME: dict[int, str] = {
    PRIORITY_ALERT: "critical",
    PRIORITY_HIGH: "warning",
    PRIORITY_NORMAL: "info",
}


def _band_key(priority: int) -> int:
    """4/5 归标准带：与 `_band()` 同一条分带口径。"""
    if priority == PRIORITY_ALERT:
        return PRIORITY_ALERT
    if priority == PRIORITY_HIGH:
        return PRIORITY_HIGH
    return PRIORITY_NORMAL


def level_name_for(priority: int) -> str:
    """档位 → critical/warning/info（审计行用，⛔ 在别处再造一份词表）。"""
    return PRIORITY_LEVEL_NAME[_band_key(priority)]

# ── 入队结果原因 ────────────────────────────────────────────────────
REASON_QUEUED = "queued"
REASON_REPLACED = "replaced"
REASON_DROPPED_EMPTY = "dropped_empty"
REASON_DROPPED_QUIET = "dropped_quiet"
REASON_DROPPED_COOLDOWN = "dropped_cooldown"
REASON_DROPPED_PAUSED = "dropped_paused"


def _band(priority: int) -> int:
    """优先级 → 插入带：0=告警 1=高优 2=标准（3/4/5 同带，带内 FIFO）。"""
    if priority == PRIORITY_ALERT:
        return 0
    if priority == PRIORITY_HIGH:
        return 1
    return 2


def _parse_hhmm(value: str) -> int:
    """`HH:MM` → 当日分钟数。坏值抛 `ParamError`（点名原因），⛔ 让 unpack／ValueError 裸穿到 `/api/tts`。"""
    parts = str(value).strip().split(":")
    if len(parts) != 2:
        raise ParamError("quiet_start/quiet_end", "必须是 HH:MM 两段")
    hh = to_int(parts[0], "小时", minimum=0, maximum=23)
    mm = to_int(parts[1], "分钟", minimum=0, maximum=59)
    return hh * 60 + mm


@dataclass
class TTSItem:
    """一条待播任务。字段与 §1 ``TTSManager.speak`` 契约逐一对齐（见 speak_kwargs）。"""

    text: str
    priority: int = PRIORITY_NORMAL
    device_id: str | None = None
    room: str = ""
    voice: str | None = None
    member: str = ""
    backend: str | None = None
    speed: float | None = None
    volume: int | None = None          # None → 按优先级自动
    whole_house: bool = False
    ttl_s: float | None = None         # None → config 默认；<=0 → 不过期
    override_quiet: bool = False       # 夜间静默窗口手动覆盖
    dedupe_key: str | None = None      # None → 用 device_id
    trace_id: str = ""
    job_id: str = ""
    created_at: float = 0.0
    expires_at: float = 0.0
    duration_ms: int = 0

    def speak_kwargs(self) -> dict:
        """映射到 §1 契约：``TTSManager.speak(text, *, voice, device_id, member, backend, speed)``。"""
        return {
            "text": self.text,
            "voice": self.voice,
            "device_id": self.device_id,
            "member": self.member,
            "backend": self.backend,
            "speed": self.speed,
        }

    def resolve_key(self) -> str | None:
        return self.dedupe_key or self.device_id


@dataclass(frozen=True)
class EnqueueResult:
    accepted: bool
    reason: str
    item: TTSItem | None
    queue_size: int
    replaced: int = 0


@dataclass
class TTSQueueConfig:
    """全部阈值可配置；默认值即交接单写死的现网语义。"""

    ttl_s: float = 300.0
    # DCD 裁定③：档位默认 TTL。这里给的是 PRIORITY_TTL_S 的一份可改副本——
    # 要裁数值（比如把高优改成 900s）改配置即可，⛔ 动代码。
    ttl_by_priority: dict[int, float] = field(default_factory=lambda: dict(PRIORITY_TTL_S))
    # 过载保护
    overload_threshold: int = 20
    overload_pause_s: float = 600.0
    overload_room: str = "living"
    overload_text: str = "TTS 队列积压，已丢弃若干条低优先级提醒，告警照常播报。"
    # 熔断保护
    breaker_threshold: int = 7
    breaker_window_s: float = 7.0
    breaker_cooldown_s: float = 25.0
    breaker_trigger: str = "playback"    # playback | enqueue | both
    # 夜间静默
    quiet_start: str = "06:50"
    quiet_end: str = "23:00"
    quiet_allow_priority: int = 1        # 优先级 <= 该值不受静默限制
    quiet_override: bool = False         # 全局手动覆盖
    # 时长估算
    per_char_ms: int = 200
    fixed_buffer_ms: int = 2000
    whole_house_extra_ms: int = 3000
    # 音量
    volume_by_priority: dict[int, int] = field(
        default_factory=lambda: {1: 80, 2: 60, 3: 30, 4: 30, 5: 30}
    )
    default_volume: int = 30

    def volume_for(self, priority: int) -> int:
        return int(self.volume_by_priority.get(priority, self.default_volume))

    def ttl_for(self, priority: int) -> float:
        """档位 TTL；表里没登记的档回落 `ttl_s`（部分覆盖配置时不至于把别的档清零）。"""
        key = _band_key(priority)
        if key in self.ttl_by_priority:
            return float(self.ttl_by_priority[key])
        return float(self.ttl_s)

    def estimate_ms(self, text: str, whole_house: bool = False) -> int:
        ms = len(text or "") * self.per_char_ms + self.fixed_buffer_ms
        if whole_house:
            ms += self.whole_house_extra_ms
        return int(ms)

    def is_quiet(self, minutes_of_day: int) -> bool:
        """真 = 静默（在 quiet_start-quiet_end 之外）。"""
        try:
            start, end = _parse_hhmm(self.quiet_start), _parse_hhmm(self.quiet_end)
        except ParamError as exc:
            # 静默窗配坏 ⇒ 按「不静默」走并留痕：全屋静音与整条 TTS 崩，都不是调用方要的。
            logger.error("TTS 静默时段配置不可解，本窗按不静默处理：%s", exc)
            return False
        if start == end:
            return False                      # 起止相同 = 全天可播
        if start < end:
            return not (start <= minutes_of_day < end)
        return not (minutes_of_day >= start or minutes_of_day < end)


@runtime_checkable
class TTSSpeaker(Protocol):
    """播放口（**由调用方实现**，典型实现见使用示例）。

    必须返回 bool：True=已播，False=未播（合成失败/无设备）。抛异常按失败处理（fail-open）。
    """

    async def speak(self, item: TTSItem) -> bool: ...


class TTSQueue:
    """顺序播放队列：一条播完再播下一条（``play_lock`` 保证）。"""

    def __init__(
        self,
        speaker: TTSSpeaker | None = None,
        *,
        config: TTSQueueConfig | None = None,
        on_overload: Callable[[str, str], Any] | None = None,
        on_expired: Callable[[TTSItem, float], Any] | None = None,
        clock: Callable[[], float] = time.time,
        localtime: Callable[[float], Any] = time.localtime,
    ):
        self.config = config or TTSQueueConfig()
        self.speaker = speaker
        self.on_overload = on_overload       # async 或 sync 均可：(text, room)
        self.on_expired = on_expired         # DCD 裁定③：(item, now) 过期审计腿
        self._clock = clock
        self._localtime = localtime

        self._q: deque[TTSItem] = deque()
        self._triggers: deque[float] = deque()
        self._cooldown_until = 0.0
        self._paused_until = 0.0
        self._pause_reason: str | None = None
        self._closed = False
        self._worker: asyncio.Task | None = None
        # 批40（P2-2）：worker 的入睡闸。只在 run() 里现建（⛔ 在 __init__ 建——那会把它
        # 绑到构造时的环上）；同步口只 set 不问环，所以 _wake 为 None 时入队照样能跑。
        self._wake: asyncio.Event | None = None
        # 格14（R1）：原名 _lock。与 butler/tts/playback_queue.py 里那把同名锁守的
        # 根本不是一件事（那把守「给设备起 worker」），两个文件又都叫 _lock、
        # 连 logger 名字都一样 ⇒ 归因时分不清。改名 _play_lock，与公有的 play_lock 同名根。
        self._play_lock: asyncio.Lock | None = None
        self._bg: set[asyncio.Task] = set()
        self._seq = 0

        self._played = 0
        self._failed = 0
        self._overloads = 0
        self._replaced = 0
        self._dropped: dict[str, int] = {
            "empty": 0, "quiet": 0, "cooldown": 0,
            "paused": 0, "expired": 0, "overload": 0,
        }

    # ── 基础设施 ──────────────────────────────────────────────────
    @property
    def play_lock(self) -> asyncio.Lock:
        """懒创建，避免无事件循环时构造 Lock 的绑定问题。"""
        if self._play_lock is None:
            self._play_lock = asyncio.Lock()
        return self._play_lock

    def __len__(self) -> int:
        return len(self._q)

    def items(self) -> list[TTSItem]:
        return list(self._q)

    def set_quiet_override(self, value: bool) -> None:
        """夜间静默全局手动覆盖。"""
        self.config.quiet_override = bool(value)

    # ── 入队 ──────────────────────────────────────────────────────
    def enqueue(
        self,
        text: str,
        *,
        priority: int = PRIORITY_NORMAL,
        device_id: str | None = None,
        room: str = "",
        voice: str | None = None,
        member: str = "",
        backend: str | None = None,
        speed: float | None = None,
        volume: int | None = None,
        whole_house: bool = False,
        ttl_s: float | None = None,
        override_quiet: bool = False,
        dedupe_key: str | None = None,
        job_id: str = "",
        trace_id: str = "",
    ) -> EnqueueResult:
        return self.enqueue_item(
            TTSItem(
                text=text, priority=priority, device_id=device_id, room=room,
                voice=voice, member=member, backend=backend, speed=speed,
                volume=volume, whole_house=whole_house, ttl_s=ttl_s,
                override_quiet=override_quiet, dedupe_key=dedupe_key, job_id=job_id,
                trace_id=trace_id,
            )
        )

    def enqueue_item(self, item: TTSItem) -> EnqueueResult:
        if not isinstance(item.priority, int) or not (PRIORITY_MIN <= item.priority <= PRIORITY_MAX):
            raise ValueError(f"priority 必须是 {PRIORITY_MIN}-{PRIORITY_MAX} 的整数: {item.priority!r}")
        if not (item.text or "").strip():
            self._dropped["empty"] += 1
            return EnqueueResult(False, REASON_DROPPED_EMPTY, None, len(self._q))

        now = self._now()
        self._sweep(now)

        # 完善字段（就地回填，调用方可从结果里拿走）
        item.created_at = now
        # 调用方显式给的 ttl_s 仍然优先：本批是「接上档位默认」，⛔ 改写调用方的选择。
        ttl = self.config.ttl_for(item.priority) if item.ttl_s is None else float(item.ttl_s)
        item.expires_at = now + ttl if ttl > 0 else float("inf")
        if not item.duration_ms:
            item.duration_ms = self.config.estimate_ms(item.text, item.whole_house)
        if item.volume is None:
            item.volume = self.config.volume_for(item.priority)
        if not item.job_id:
            self._seq += 1
            item.job_id = f"tts-{int(now)}-{self._seq}"

        # 夜间静默（入队侧）
        if (self._quiet_active(now) and not self.config.quiet_override
                and item.priority > self.config.quiet_allow_priority
                and not item.override_quiet):
            self._dropped["quiet"] += 1
            return EnqueueResult(False, REASON_DROPPED_QUIET, None, len(self._q))

        # 过载暂停期间：新来的非 P1 负载直接挡掉（P1 照收，暂停期间当场能播——见 dequeue）
        if self._pause_reason == "overload" and self._is_paused(now) and item.priority != PRIORITY_ALERT:
            self._dropped["paused"] += 1
            return EnqueueResult(False, REASON_DROPPED_PAUSED, None, len(self._q))

        # 熔断冷却期间：丢弃非 P1
        if self._cooldown_active(now) and item.priority != PRIORITY_ALERT:
            self._dropped["cooldown"] += 1
            return EnqueueResult(False, REASON_DROPPED_COOLDOWN, None, len(self._q))

        # P1：强制插队 + 清除冷却（冷却倒计时 + 触发滑窗一并清零，避免告警后立刻再熔断）
        if item.priority == PRIORITY_ALERT:
            self._clear_breaker()

        # 设备级去重：同 key 的旧消息被替换
        key = item.resolve_key()
        replaced = 0
        if key:
            keep: deque[TTSItem] = deque()
            for old in self._q:
                if old.resolve_key() == key:
                    replaced += 1
                else:
                    keep.append(old)
            if replaced:
                self._q = keep
                self._replaced += replaced

        # 过载保护（DCD 裁定⑤-2）：入队后（含新消息）>= 阈值 → 丢最低优先级档、保 P1、当前这条照收
        if len(self._q) + 1 >= self.config.overload_threshold:
            shed = self._shed_lowest_for_room()
            self._overloads += 1
            self._dropped["overload"] += shed
            already_paused = self._pause_reason == "overload" and self._is_paused(now)
            if not already_paused:            # 暂停只从第一次触发起算，⛔ 被后续过载续期
                self._paused_until = now + self.config.overload_pause_s
                self._pause_reason = "overload"
            logger.warning(
                "TTS 队列过载：阈值 %d，丢最低优先级 %d 条（P1 告警档不丢），当前这条照收%s",
                self.config.overload_threshold, shed,
                "" if already_paused
                else "，并暂停 %.0fs（只挡非 P1 新负载）" % self.config.overload_pause_s,
            )
            if not already_paused:
                self._fire_overload()

        # 插入：插到第一条「优先级更低」的消息之前（同带保持 FIFO）
        band = _band(item.priority)
        idx = len(self._q)
        for i, other in enumerate(self._q):
            if _band(other.priority) > band:
                idx = i
                break
        self._q.insert(idx, item)
        self._wake_now()

        self._note_trigger(now, "enqueue")
        return EnqueueResult(
            True, REASON_REPLACED if replaced else REASON_QUEUED, item, len(self._q), replaced
        )

    # ── 出队 ──────────────────────────────────────────────────────
    def dequeue(self) -> TTSItem | None:
        """取出下一条该播的任务；顺序播放锁由 play_one/run 持有。"""
        now = self._now()
        self._sweep(now)
        # 过载暂停只挡非 P1（裁定⑤-2「保 ALERT/critical」）；其它原因的暂停（手动）照旧全挡
        overload_pause = self._pause_reason == "overload" and self._is_paused(now)
        if self._is_paused(now) and not overload_pause:
            return None

        cooldown = self._cooldown_active(now)
        quiet = self._quiet_active(now) and not self.config.quiet_override
        while self._q:
            item = self._q[0]
            # 出队侧再判一次静默（避免 22:59 入队、23:01 播放）
            if quiet and item.priority > self.config.quiet_allow_priority and not item.override_quiet:
                self._q.popleft()
                self._dropped["quiet"] += 1
                continue
            if cooldown and item.priority != PRIORITY_ALERT:
                return None                  # 冷却期间非 P1 不播（消息保留）
            if overload_pause and item.priority != PRIORITY_ALERT:
                # 队里有 P1 它必在队头（插入规则保证分带单调）⇒ 队头不是 P1 就是真没有
                return None                  # 过载暂停期间非 P1 继续等（消息保留，⛔ 丢 ⛔ 吞）
            self._q.popleft()
            self._note_trigger(now, "playback")
            return item
        return None

    # ── 播放 ──────────────────────────────────────────────────────
    async def play_one(self) -> bool:
        """顺序播放一条：持锁 → 出队 → speak。speaker 失败 fail-open（只记账）。"""
        if self.speaker is None:
            logger.warning("TTSQueue.play_one: 未绑定 speaker")
            return False
        async with self.play_lock:
            item = self.dequeue()
            if item is None:
                return False
            try:
                ok = bool(await self.speaker.speak(item))
            except Exception as e:
                self._failed += 1
                logger.warning("TTSQueue 播放失败 job=%s: %s", item.job_id, e)
                return False
            if ok:
                self._played += 1
            else:
                self._failed += 1
                logger.warning("TTSQueue 播放返回失败 job=%s", item.job_id)
            return ok

    def _wake_now(self) -> None:
        """叫醒入睡的 worker。worker 还没起（或已退）⇒ 空操作：run() 头一圈必查一次，⛔ 丢唤醒。"""
        if self._wake is not None:
            self._wake.set()

    def _park_seconds(self, *, idle_sleep: float, idle_max_sleep: float, blocked_poll: float) -> float:
        """这一觉最长睡多久＝「本态上限」与「最近一个已知边界」取小，再按 idle_sleep 兜底。

        空仓上限＝idle_max_sleep（新消息靠事件醒，不必回查）；有货却被挡住的上限＝blocked_poll
        ——夜间静默窗、队头非 P1、speaker 未绑定这类没有到期点的状态只能靠回查。
        到期点（暂停/冷却/最早的 TTL）逐个进候选，⛔ 让「等到期」这件事退回轮询。
        """
        now = self._now()
        wait = idle_max_sleep if not self._q else blocked_poll
        if self._is_paused(now):
            wait = min(wait, self._paused_until - now)
        if self._cooldown_active(now):
            wait = min(wait, self._cooldown_until - now)
        if self._q:
            nearest = min(it.expires_at for it in self._q)
            if nearest != float("inf"):
                wait = min(wait, nearest - now)
        return max(idle_sleep, wait)

    async def run(self, *, idle_sleep: float = 0.05, idle_max_sleep: float = 30.0,
                  blocked_poll: float = 1.0) -> None:
        """顺序播放循环。批40 前是无消息每 50ms 醒一次（10-02 稳定性报告 P2-2）⇒ 改为入睡+唤醒。

        ⛔ 照报告说的换成 `await self._q.get()`：dequeue() 里带着分带插队、夜间静默丢弃、
        冷却/过载门和 TTL 清扫，裸 get() 会把这四道判定全绕过。
        """
        wake = asyncio.Event()
        self._wake = wake
        try:
            while not self._closed:
                if await self.play_one():
                    continue                       # 连播：一次唤醒要把队列排空，⛔ 醒一次只播一条
                wake.clear()                       # 先清再算边界：这中间入队的 set() 一定留得住
                sec = self._park_seconds(idle_sleep=idle_sleep, idle_max_sleep=idle_max_sleep,
                                         blocked_poll=blocked_poll)
                try:
                    await asyncio.wait_for(wake.wait(), sec)
                except asyncio.TimeoutError:
                    pass
        finally:
            if self._wake is wake:
                self._wake = None

    def start(self, *, idle_sleep: float = 0.05, idle_max_sleep: float = 30.0,
              blocked_poll: float = 1.0) -> asyncio.Task:
        if self._worker is not None and not self._worker.done():
            return self._worker
        self._closed = False
        self._worker = asyncio.create_task(self.run(idle_sleep=idle_sleep,
                                                    idle_max_sleep=idle_max_sleep,
                                                    blocked_poll=blocked_poll))
        return self._worker

    async def stop(self) -> None:
        self._closed = True
        self._wake_now()
        worker, self._worker = self._worker, None
        if worker is not None and not worker.done():
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
            except Exception as e:  # pragma: no cover
                logger.debug("TTSQueue worker 退出异常: %s", e)
        for task in list(self._bg):
            task.cancel()
        self._bg.clear()

    # ── 状态 / 控制 ───────────────────────────────────────────────
    def pause(self, reason: str = "manual", duration_s: float | None = None) -> None:
        self._pause_reason = reason
        self._paused_until = self._now() + duration_s if duration_s is not None else float("inf")

    def resume(self, *, reset_breaker: bool = True) -> None:
        self._paused_until = 0.0
        self._pause_reason = None
        if reset_breaker:
            self._clear_breaker()
        self._wake_now()

    def status(self) -> dict:
        now = self._now()
        self._sweep(now)
        self._prune_triggers(now)
        by_priority = {p: 0 for p in range(PRIORITY_MIN, PRIORITY_MAX + 1)}
        for it in self._q:
            by_priority[it.priority] = by_priority.get(it.priority, 0) + 1
        nxt = self._q[0] if self._q else None
        paused = self._is_paused(now)
        cooling = self._cooldown_active(now)
        return {
            "size": len(self._q),
            "by_priority": by_priority,
            "paused": paused,
            "pause_reason": self._pause_reason,
            "pause_remaining_s": (max(0.0, self._paused_until - now) if paused else 0.0),
            "breaker_active": cooling,
            "breaker_remaining_s": (max(0.0, self._cooldown_until - now) if cooling else 0.0),
            "breaker_triggers_in_window": len(self._triggers),
            "quiet_active": self._quiet_active(now),
            "quiet_override": self.config.quiet_override,
            "played": self._played,
            "failed": self._failed,
            "overloads": self._overloads,
            "replaced": self._replaced,
            "dropped": dict(self._dropped),
            "closed": self._closed,
            "next": None if nxt is None else {
                "job_id": nxt.job_id,
                "priority": nxt.priority,
                "device_id": nxt.device_id,
                "room": nxt.room,
                "text": nxt.text[:20],
                "duration_ms": nxt.duration_ms,
                "expires_in_s": max(0.0, nxt.expires_at - now),
            },
        }

    # ── 内部 ──────────────────────────────────────────────────────
    def _now(self) -> float:
        return float(self._clock())

    def _quiet_active(self, now: float) -> bool:
        st = self._localtime(now)
        return self.config.is_quiet(int(st.tm_hour) * 60 + int(st.tm_min))

    def _sweep(self, now: float) -> int:
        if not self._q:
            return 0
        keep: deque[TTSItem] = deque()
        expired: list[TTSItem] = []
        for it in self._q:
            if it.expires_at <= now:
                expired.append(it)
            else:
                keep.append(it)
        if expired:
            self._q = keep
            self._dropped["expired"] += len(expired)
            for it in expired:
                self._note_expired(it, now)
        return len(expired)

    def _note_expired(self, item: TTSItem, now: float) -> None:
        """DCD 裁定③「让『为什么没响』可查」：一条过期＝一行审计，⛔ 只留一个计数。

        审计腿自身出错一律不影响播放队列（fail-open，与裁定② `modes/engine.py::_gate` 同一份政策）。
        """
        age = now - (item.created_at or now)
        logger.info("TTS_EXPIRED job=%s trace=%s P%d age=%.0fs text=%.30s",
                    item.job_id or "-", item.trace_id or "-",
                    item.priority, age, item.text)
        if self.on_expired is None:
            return
        try:
            self.on_expired(item, now)
        except Exception as e:
            logger.warning("TTS TTL 审计失败（不阻塞队列）: %s: %s", type(e).__name__, e)

    def _prune_triggers(self, now: float) -> None:
        window = self.config.breaker_window_s
        while self._triggers and self._triggers[0] <= now - window:
            self._triggers.popleft()

    def _note_trigger(self, now: float, event: str) -> None:
        mode = self.config.breaker_trigger
        if mode not in ("playback", "enqueue", "both"):
            mode = "playback"
        hit = (event == "playback" and mode in ("playback", "both")) or (
            event == "enqueue" and mode in ("enqueue", "both")
        )
        if not hit:
            return
        self._triggers.append(now)
        self._prune_triggers(now)
        if len(self._triggers) >= self.config.breaker_threshold:
            self._cooldown_until = now + self.config.breaker_cooldown_s
            self._triggers.clear()
            logger.warning(
                "TTS 熔断触发：%.0fs 内 %d 次 → 冷却 %.0fs（期间丢弃非 P1）",
                self.config.breaker_window_s,
                self.config.breaker_threshold,
                self.config.breaker_cooldown_s,
            )

    def _clear_breaker(self) -> None:
        self._cooldown_until = 0.0
        self._triggers.clear()

    def _cooldown_active(self, now: float) -> bool:
        return now < self._cooldown_until

    def _is_paused(self, now: float) -> bool:
        return now < self._paused_until

    def _shed_lowest_for_room(self) -> int:
        """过载让位：从最低优先级档开始丢（同带丢最旧），P1 告警档⛔ 丢；返回丢掉几条。

        全队列都是 P1 时丢 0 条——当前这条照样进队（裁定⑤-2「不吞」），
        此时队列可短暂越过阈值：那是裁定的代价，⛔ 为了腾位置去丢告警。
        """
        shed = 0
        while len(self._q) + 1 >= self.config.overload_threshold:
            droppable = max({_band(it.priority) for it in self._q} - {0}, default=0)
            if droppable == 0:
                break                        # 只剩告警档，没得丢
            for i, it in enumerate(self._q):
                if _band(it.priority) == droppable:
                    del self._q[i]           # 该带最旧的一条（带内 FIFO）
                    break
            shed += 1
        return shed

    def _fire_overload(self) -> None:
        if self.on_overload is None:
            return
        text, room = self.config.overload_text, self.config.overload_room
        try:
            res = self.on_overload(text, room)
        except Exception as e:                       # fail-open：通知失败不影响清理动作
            logger.warning("on_overload 回调失败（fail-open）: %s", e)
            return
        if not inspect.isawaitable(res):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("on_overload 返回协程但当前无事件循环，已跳过等待")
            return
        task = loop.create_task(self._await_overload(res))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _await_overload(self, coro: Any) -> None:
        try:
            await coro
        except Exception as e:                       # fail-open
            logger.warning("on_overload 异步通知失败（fail-open）: %s", e)


def estimate_duration_ms(
    text: str, whole_house: bool = False, config: TTSQueueConfig | None = None
) -> int:
    """播报时长估算：200ms/字 + 2000ms 固定缓冲 + 全屋 +3000ms。"""
    return (config or TTSQueueConfig()).estimate_ms(text, whole_house)