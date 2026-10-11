"""MQTT 主题常量与事件模型（单一真源）。

TV 端契约（已实现）：
  - 发布 tv/livingroom/face      QoS0，1 秒去重，载荷 {"name","confidence","ts",...}
  - 发布 tv/livingroom/status     QoS1，retained + LWT offline
  - 订阅 tv/livingroom/cmd/#      QoS1，其中 cmd/tts 已实现 {"url","volume"}
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

TV_PREFIX = "tv/livingroom"
BUTLER_ROOT = "butler"
MA_PREFIX = "ma"  # memory-agent MQTT 推送前缀

# 订阅
SUB_TV_STATUS = f"{TV_PREFIX}/status"
SUB_TV_FACE = f"{TV_PREFIX}/face"
SUB_TV_FACE_FUSED = f"{TV_PREFIX}/face/fused"
SUB_TV_EXPERIMENT = f"{TV_PREFIX}/experiment"  # TV 端小爱实验数据（xiaoai_listening_end，含 recognized_text）
SUB_TV_RESULT = f"{TV_PREFIX}/result"           # zap-tv 执行回执（含 request_id，换台确认）
SUB_TV_WAKE = f"{TV_PREFIX}/wake"               # Arcface 远场语音命令 {"type":"voice_command","command":"..."}
SUB_EVENT = f"{BUTLER_ROOT}/event/+"
SUB_TRIGGER = f"{BUTLER_ROOT}/trigger/+"        # 技能 MQTT 触发入口（兼容通道）
SUB_MA_PRESENCE = f"{MA_PREFIX}/presence"       # memory-agent 成员在场快照（ArcFace 视觉识别，retain）
SUB_MA_DEVICE_HEALTH = f"{MA_PREFIX}/device-health"  # memory-agent 设备健康变化
SUB_MA_INSIGHTS = f"{MA_PREFIX}/insights"         # memory-agent 视觉异常/安全告警（不 retained，载荷 {kind,summary,evidence[],persons[],source,ts}）

# §13.4 ADM 公共收件箱（任何仓投递、DB 过闸分发）+ 生态在线探测
SUB_INBOX_SPEAK = f"{BUTLER_ROOT}/inbox/speak"
SUB_INBOX_NOTIFY = f"{BUTLER_ROOT}/inbox/notify"
SUB_INBOX_TV = f"{BUTLER_ROOT}/inbox/tv"
ADM_STATUS = "adm/doubao-butler/status"      # retained 在线状态：载荷字面量 online/offline，offline 由 LWT 代发（契约表 §1.1）
ADM_CAPS = "adm/doubao-butler/caps"          # retained 能力摘要（契约表 §1.1：adm/<成员>/caps）
SUB_ADM_MA_STATUS = "adm/memory-agent/status"
SUB_ADM_AF_STATUS = "adm/autoforge/status"


def adm_status_is_online(payload: Any) -> bool:
    """`adm/<成员>/status` 消费侧的判读单点（契约表 §1.1；homesdk 0.3.1 presence.is_online 同式）。

    载荷是字面量 `online`/`offline`。拿到 str 还去 `.get("online")` 会抛 AttributeError——
    表现是「订阅在跑、缓存永远空」，比崩更难查。
    dict 分支是过渡：MA/AF 此刻发什么形状未现采，两种都得能读，⛔ 在每个消费点各写一份。
    """
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", "replace")
    if isinstance(payload, str):
        return payload.strip() == "online"
    if isinstance(payload, dict):
        return bool(payload.get("online"))
    return False

# AF 自动化事件（契约 v2.0 §D）：DB↔AF 事件腿走 HTTP 轮询 /api/asks/pending，
# 不订阅 af/automation/fired|failed（避免与 HTTP 轮询重复消费）。登记于此供后续切换。
# SUB_AF_AUTOMATION_FIRED = "af/automation/fired"
# SUB_AF_AUTOMATION_FAILED = "af/automation/failed"

SUB_TOPICS = (
    SUB_TV_STATUS,
    SUB_TV_FACE,
    SUB_TV_FACE_FUSED,
    SUB_TV_EXPERIMENT,
    SUB_TV_RESULT,
    SUB_TV_WAKE,
    SUB_EVENT,
    SUB_TRIGGER,
    SUB_MA_PRESENCE,
    SUB_MA_DEVICE_HEALTH,
    SUB_MA_INSIGHTS,
    SUB_INBOX_SPEAK,
    SUB_INBOX_NOTIFY,
    SUB_INBOX_TV,
    SUB_ADM_MA_STATUS,
    SUB_ADM_AF_STATUS,
    f"{BUTLER_ROOT}/dialog/event",  # 订阅自身发布的对话事件，用于 SSE 推送
)

# 发布
PUB_TV_TTS = f"{TV_PREFIX}/cmd/tts"          # {"url": str, "volume": int}
PUB_TV_CONTROL = f"{TV_PREFIX}/control"      # {"action":"zap","channel":...} → zap-tv 设备控制层
PUB_TV_NOTIFY = f"{TV_PREFIX}/cmd/notify"    # 预留
PUB_STATUS = f"{BUTLER_ROOT}/status/state"    # retained + LWT
PUB_DIALOG = f"{BUTLER_ROOT}/dialog/event"    # 供 WebUI SSE
PUB_SPEAK_OUT = f"{BUTLER_ROOT}/speak/out"    # 统一发声出口，NR 转小爱/Bark


def skill_done_topic(skill_id: str) -> str:
    """技能执行结果广播主题：butler/skill/{id}/done。"""
    return f"{BUTLER_ROOT}/skill/{skill_id}/done"


@dataclass(frozen=True)
class ButlerEvent:
    kind: str            # face | tv_status | sensor | voice | timer | scene
    room: str = "客厅"
    member: str = ""     # 识别到的成员名；陌生人为 "stranger"
    confidence: float = 0.0
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


def parse_face(topic: str, payload: dict) -> ButlerEvent:
    name = (payload.get("name") or payload.get("member") or "").strip()
    return ButlerEvent(
        kind="face",
        room="客厅",
        member=name or "stranger",
        confidence=float(payload.get("confidence") or 0.0),
        payload=payload,
        ts=float(payload.get("ts") or time.time()),
    )


def parse_tv_status(topic: str, payload: dict) -> ButlerEvent:
    return ButlerEvent(
        kind="tv_status",
        room="客厅",
        member=str(payload.get("state") or payload.get("status") or ""),
        payload=payload,
        ts=float(payload.get("ts") or time.time()),
    )


def parse_event(topic: str, payload: dict) -> ButlerEvent:
    # butler/event/<kind>
    kind = topic.split("/")[-1] if topic else "event"
    return ButlerEvent(
        kind=kind,
        room=str(payload.get("room") or "客厅"),
        member=str(payload.get("member") or payload.get("name") or ""),
        confidence=float(payload.get("confidence") or 0.0),
        payload=payload,
        ts=float(payload.get("ts") or time.time()),
    )


def parse_experiment(topic: str, payload: dict) -> ButlerEvent:
    """TV 端小爱实验数据：tv/livingroom/experiment。

    主要事件 type=xiaoai_listening_end，载荷含：
      - recognized_text：小爱识别到的用户语音文本
      - interrupted：是否因命中关键词白名单而打断了小爱回复（True=用户意图对管家说话）
    我们把它归一化为 kind="tv_voice" 的 ButlerEvent，供 DialogManager 触发管家大脑。
    """
    etype = str(payload.get("type") or "")
    text = str(payload.get("recognized_text") or "")
    interrupted = bool(payload.get("interrupted", False))
    return ButlerEvent(
        kind="tv_voice",
        room="客厅",
        member="",
        confidence=0.0,
        payload={"exp_type": etype, "text": text, "interrupted": interrupted,
                 "skip_reason": payload.get("skip_reason", "")},
        ts=float(payload.get("timestamp") or time.time()),
    )
