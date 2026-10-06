"""简单命令规则匹配层 — 最小 Token 原则。

简单命令（开灯、关灯、现在几点等）直接执行，不走 LLM，
零 token、更快、更稳定。LLM 只在需要判断、排查、创建、验证时才激活。

P0-G（DCD 2026-10-06 裁 A′）：设备那一族⛔ 认不出实体也发令——不点名设备的
`call_service(domain, service)` 在 HA 里就是"该领域全部设备"，说一句"开灯"全屋灯真动。
现规则＝认出实体才发令（`data` 带 `entity_id`），认不出只回一句追问；疑问/否定句不当命令。
非设备话（时间、技能创建）口径不变。
"""
from __future__ import annotations

import re
from datetime import datetime

from butler.logging_setup import get_logger

logger = get_logger("butler.simple_rules")


# ---- 规则定义 ----
# 每条规则：匹配模式 + 执行函数
# 执行函数返回 (reply_text, action_dict) 或 None（不匹配）


# 1. 时间查询
_TIME_PATTERNS = [
    re.compile(r"现在几点"),
    re.compile(r"几点了"),
    re.compile(r"现在时间"),
    re.compile(r"今天几号"),
    re.compile(r"今天星期几"),
]


def _check_time(text: str) -> tuple[str, dict] | None:
    """时间查询：直接回答，不走 LLM。"""
    for pat in _TIME_PATTERNS:
        if pat.search(text):
            now = datetime.now()
            if "几点" in text or "时间" in text:
                reply = f"现在是 {now.hour}:{now.minute:02d}"
            elif "几号" in text:
                reply = f"今天是 {now.month}月{now.day}号"
            elif "星期" in text:
                weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
                reply = f"今天是{weekdays[now.weekday()]}"
            else:
                reply = f"现在是 {now.strftime('%Y年%m月%d日 %H:%M')}"
            return reply, {"action": "none"}
    return None


# 2. 设备控制简单命令
# 元组＝(匹配式, 线索抽取式, domain, service)。抽取式取动词与设备名之间那段当设备线索。
_DEVICE_ON_PATTERNS = [
    (re.compile(r"开.*灯"), re.compile(r"开(.*?)灯"), "light", "turn_on"),
    (re.compile(r"关.*灯"), re.compile(r"关(.*?)灯"), "light", "turn_off"),
    (re.compile(r"开.*空调"), re.compile(r"开(.*?)空调"), "climate", "turn_on"),
    (re.compile(r"关.*空调"), re.compile(r"关(.*?)空调"), "climate", "turn_off"),
    (re.compile(r"打开.*电视"), re.compile(r"打开(.*?)电视"), "media_player", "turn_on"),
    (re.compile(r"关闭.*电视"), re.compile(r"关闭(.*?)电视"), "media_player", "turn_off"),
]
_DEVICE_NOUNS = {"light": "灯", "climate": "空调", "media_player": "电视"}

# 语气闸：出现这些字样＝问话或否定，⛔ 当命令执行（"我是不是忘关客厅的灯了"是在问家里有没有灯开着）
_TONE_EXCLUDE = re.compile(
    r"是不是|有没有|要不要|用不用|会不会|能不能|难道|不是|没有|不要|不用|不必|别|忘"
    r"|吗|呢|怎么|为什么|为何|？|\?"
)

_HINT_LEAD = re.compile(r"^(把|给|帮|我|你|他|它|她|们|请|再|还|就|这|那|一下)+")
_HINT_TAIL = re.compile(r"(的|了|着|吧|啊|呢|嘛|哦|一下|这|那|个|些|和|与|们)+$")
_ENTITY_ID_SHAPE = re.compile(r"^[a-z_]+\.[a-z0-9_.\-]+$")


def _device_hint(text: str, extract: re.Pattern) -> str:
    """取出动词与设备名之间的设备线索（"打开客厅的灯"→"客厅"）；只说"开灯"＝空。"""
    match = extract.search(text)
    if not match:
        return ""
    raw = match.group(1).strip()
    raw = _HINT_LEAD.sub("", raw)
    raw = _HINT_TAIL.sub("", raw)
    return raw.strip()


def _ask_device_reply(domain: str, service: str) -> str:
    verb = "开" if service == "turn_on" else "关"
    return f"要{verb}哪个房间的{_DEVICE_NOUNS.get(domain, '设备')}？"


def _ask_device_action(domain: str, service: str, text: str, hint: str = "") -> dict:
    return {"action": "ask_device", "domain": domain, "service": service,
            "device_hint": hint, "text": text}


def _check_device_control(text: str) -> tuple[str, dict] | None:
    """设备控制的**匹配**腿：认语气、抽线索。判实体要 HA 句柄，在 `_resolve_device_entity` 那一腿。"""
    if _TONE_EXCLUDE.search(text):
        return None
    for pat, extract, domain, service in _DEVICE_ON_PATTERNS:
        if not pat.search(text):
            continue
        hint = _device_hint(text, extract)
        if not hint:
            return _ask_device_reply(domain, service), _ask_device_action(domain, service, text)
        action_word = "打开" if service == "turn_on" else "关闭"
        reply = f"好的，{action_word}了。"
        return reply, {
            "action": "call_service",
            "domain": domain,
            "service": service,
            "data": {},
            "device_hint": hint,
            "text": text,
        }
    return None


async def _resolve_device_entity(agent, hint: str, domain: str) -> str:
    """复用正路的模糊匹配器（⛔ 在本模块另写一套打分＝"同名两套"）。

    它认不出时**原样退回 hint**（`butler/tools/devices.py:72`），所以"认没认出"在这儿另判，
    判据与 `butler/tools/registry.py:1017` 同形：变了＝认出；没变但 hint 本身就是合法
    entity_id 形状（匹配器第一步的精确命中）＝也算认出。
    """
    if not hint or agent is None:
        return ""
    if getattr(agent, "ha", None) is None:
        return ""
    from butler.tools.devices import _resolve_entity
    try:
        resolved = await _resolve_entity(agent, hint, domain)
    except Exception as exc:  # noqa: BLE001
        logger.warning("simple device entity resolve failed: %s", type(exc).__name__)
        return ""
    if resolved and resolved != hint:
        return resolved
    if _ENTITY_ID_SHAPE.match(hint):
        return hint
    return ""


# 3. 技能创建意图（WO-BUT-026：拦截"创建技能"，返回简短反问，不走 LLM 长篇介绍）
_SKILL_CREATE_PATTERNS = [
    re.compile(r"创建技能"),
    re.compile(r"新建技能"),
    re.compile(r"做个技能"),
    re.compile(r"写个技能"),
    re.compile(r"弄个技能"),
    re.compile(r"加个技能"),
]


def _check_skill_create(text: str) -> tuple[str, dict] | None:
    """技能创建意图：返回简短反问，设置待创建状态，不走 LLM。"""
    for pat in _SKILL_CREATE_PATTERNS:
        if pat.search(text):
            reply = "你想创建什么技能？"  # WO-DB-101：固定短反问，不举例
            return reply, {"action": "skill_create_intent"}
    return None


# ---- 主入口 ----

async def check_simple_command(text: str, agent=None) -> tuple[str, dict] | None:
    """检查是否是简单命令。

    返回：
        (reply_text, action_dict) 如果匹配
        None 如果不匹配（继续走 LLM）

    `agent`＝带 `.ha` 的运行时句柄（`dialog` 传 `get_runtime()`）：设备那一族靠它认实体。
    缺句柄＝认不出＝只回追问，⛔ 退回"发一条不点名设备的全域命令"。
    """
    text = text.strip()
    if not text:
        return None

    # 1. 时间查询
    result = _check_time(text)
    if result:
        logger.info("simple rule matched: time query")
        return result

    # 2. 设备控制
    result = _check_device_control(text)
    if result:
        reply, action = result
        if action.get("action") == "call_service":
            entity_id = await _resolve_device_entity(
                agent, action.get("device_hint", ""), action["domain"])
            if entity_id:
                action["data"] = {"entity_id": entity_id}
                logger.info("simple rule matched: device control %s.%s entity=%s",
                            action["domain"], action["service"], entity_id)
            else:
                reply = _ask_device_reply(action["domain"], action["service"])
                action = _ask_device_action(action["domain"], action["service"],
                                            action.get("text", ""),
                                            action.get("device_hint", ""))
                logger.info("simple rule matched: device control needs a room (hint=%s)",
                            action.get("device_hint", ""))
        else:
            logger.info("simple rule matched: device control")
        return reply, action

    # 3. 技能创建意图
    result = _check_skill_create(text)
    if result:
        logger.info("simple rule matched: skill create intent")
        return result

    # 不匹配，走 LLM
    return None
