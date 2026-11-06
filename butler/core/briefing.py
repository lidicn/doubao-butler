"""LLM 智能简报 v2：人格驱动型管家播报。

不是给 LLM 数据让它写摘要——而是给它一个完整的角色（SOUL+USER+MEMORY），
让它以管家身份自然地说话。
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import aiohttp

from butler.config import get_settings
from butler.store.db import get_conn
from butler.logging_setup import get_logger

logger = get_logger("butler.core.briefing")


# 简报内容闸门。LLM 偶发只回 markdown 分隔符或纯表情（实测 2026-09-30 07:10
# 早间简报 Bark 正文只有 3 个字符 "---"），这类文本当简报推出去就是"乱推送"。
_MIN_BRIEFING_CHARS = 8


def is_degenerate_content(text: str) -> bool:
    """简报正文是否没有信息量：空白 / 过短 / 去掉标点后没有任何字母数字或汉字。"""
    if not text:
        return True
    t = text.strip()
    if len(t) < _MIN_BRIEFING_CHARS:
        return True
    return re.search(r"[0-9A-Za-z\u4e00-\u9fff]", t) is None


# 播报闸门（2026-11-06 P0）：
# 实测两类残次品会原样推给用户——① 模型把 prompt 脚手架吐回来
# （"新闻候选：暂无" / "规则：1. …"）；② 拿不到数据时用占位词交差
# （"undefined" / "null" / 未渲染的 `{}`）。这两类一律判坏，整条丢弃。
# 判「语义胡言」不能靠词表——那一半靠 prompt 的「无数据不提示」硬规则，见 `_build_prompt`。
_SCAFFOLD_TOKENS = ("新闻候选", "生活提醒：", "天气：", "规则：", "家里今天的情况")
_PLACEHOLDER_TOKENS = ("undefined", "null", "None", "{", "}")


def _briefing_is_bad(text: str) -> bool:
    t = text or ""
    if is_degenerate_content(t):
        return True
    if "失败" in t:
        return True
    return any(tok in t for tok in _SCAFFOLD_TOKENS + _PLACEHOLDER_TOKENS)

# 人格定义加载顺序：先读随代码走的 `butler/core/` 副本，再退到 `doc/`。
# 生产容器只挂 `butler/ data/ tts/`，`doc/` 不在容器里 ⇒ 只读 doc 那份会退化成一句兜底
# （生产日志实证：`persona file not found: /app/doc/briefing_persona.md`）。
# 两份内容必须一致，漂移由 `tests/test_briefing_persona_and_gate.py` 守。
_PERSONA_PATHS = (
    Path(__file__).resolve().parent / "briefing_persona.md",
    Path(__file__).resolve().parent.parent.parent / "doc" / "06-场景设计" / "briefing_persona.md",
)
_PERSONA_CACHE: str = ""


def _load_persona() -> str:
    global _PERSONA_CACHE
    if _PERSONA_CACHE:
        return _PERSONA_CACHE
    for p in _PERSONA_PATHS:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("persona file not found: %s (%s)", p, e)
            continue
        if text.strip():
            _PERSONA_CACHE = text
            logger.info("persona loaded: %s (%d chars)", p, len(text))
            return _PERSONA_CACHE
    logger.error("persona unavailable, one-liner fallback: %s", _PERSONA_PATHS)
    _PERSONA_CACHE = "你是这个家的管家。"
    return _PERSONA_CACHE


def _build_report() -> dict:
    """从 home_events 汇总当天活动报告。"""
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0).timestamp()

    c.execute("""
        SELECT ts, entity_id, state, room, event_type
        FROM home_events WHERE ts > ? ORDER BY ts ASC
    """, (today_start,))
    rows = c.fetchall()

    if not rows:
        return {"total_events": 0, "note": "今天还没有设备活动记录"}

    room_events = defaultdict(int)
    late_night_count = 0  # 23:00-02:00 的活动
    key_changes = []

    for row in rows:
        ts, eid, state, room, etype = row
        dt = datetime.fromtimestamp(ts)
        room_events[room or "未知"] += 1
        hour = dt.hour
        if hour >= 23 or hour < 2:
            late_night_count += 1
        if etype in ("light", "climate", "media") and len(key_changes) < 10:
            key_changes.append({"time": dt.strftime("%H:%M"), "device": eid.split(".")[-1][:20], "room": room or ""})

    return {
        "total_events": len(rows),
        "top_rooms": [r for r, _ in sorted(room_events.items(), key=lambda x: -x[1])[:3]],
        "late_night_events": late_night_count,
        "key_changes": key_changes,
    }


async def _fetch_weather() -> str:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(get_settings().weather_api_url,
                                   timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
                if data.get("status") == "ok":
                    rt = data.get("result", {}).get("realtime", {})
                    temp = rt.get("temperature", "?")
                    sky = rt.get("skycon", "")
                    sky_map = {"CLEAR_DAY": "晴", "PARTLY_CLOUDY_DAY": "多云", "CLOUDY": "阴",
                               "LIGHT_RAIN": "小雨", "MODERATE_RAIN": "中雨", "HEAVY_RAIN": "大雨"}
                    return f"{sky_map.get(sky, sky)} {temp}°C"
    except Exception as e:
        logger.warning("weather: %s", e)
    return ""


async def _fetch_news() -> list[str]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://60s.viki.moe/v2/60s", timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
                items = data.get("data", [])
                if isinstance(items, dict):
                    items = list(items.values())
                if not isinstance(items, list):
                    return []
                return [n.get("title", "") for n in items[:8] if isinstance(n, dict) and n.get("title")]
    except Exception as e:
        logger.warning("news: %s", e)
        return []


def _get_topic(briefing_type: str) -> str:
    w = datetime.now().weekday()
    if briefing_type == "morning":
        # 早报：提醒今天
        tips = {
            0: "今天周一，提醒凯文和爱美丽检查书包、戴红领巾",
            4: "今天周五，提醒孩子周末作业要做完",
        }
    else:
        # 晚报：提醒明天
        tips = {
            0: "",
            4: "明天周六，周末了",
            5: "明天周日",
            6: "明天周一，提醒孩子早点休息、检查书包红领巾",
        }
    return tips.get(w, "")


def _build_prompt(persona: str, report: dict, weather: str, news: list[str],
                  topic: str, briefing_type: str) -> str:
    """拼简报 prompt：**有数据才提**，没数据那格改写「不许编」的硬规则（2026-11-06 P0）。

    为什么不是「先给 LLM 全量数据再让它自己判断」：实测新闻源失效时 `news=0`，
    prompt 仍写「新闻里挑一条」⇒ 没数据也要挑，只能编。规则必须按有无数据**分支生成**，
    不能让模型自己权衡。
    """
    has_weather = bool((weather or "").strip())
    has_news = bool(news)
    has_topic = bool((topic or "").strip())
    has_events = int((report or {}).get("total_events", 0) or 0) > 0

    lines = [
        persona, "",
        "---", "",
        f"现在是{'早间' if briefing_type == 'morning' else '晚间'}。",
        "下面是今天家里的情况和一些外部信息。请你以管家的身份，自然地说一段话——像跟主人聊天一样。",
        "",
        "规则：",
        "1. 自己判断什么值得说，什么不值得；【最重要】不确定的事不要乱说，没数据就不提",
    ]
    n = 2
    if has_news:
        lines.append(f"{n}. 新闻里挑一条大佬可能感兴趣的（科技、游戏、AI相关优先），简单提一下；"
                     f"只许用下面给出的新闻候选，不许自己编")
    else:
        lines.append(f"{n}. 今天没有新闻候选：不要提新闻，也不许自己编新闻")
    n += 1
    if has_topic:
        lines.append(f"{n}. 生活提醒自然带出来")
    else:
        lines.append(f"{n}. 今天没有生活提醒：不许编造提醒")
    n += 1
    if not has_weather:
        lines.append(f"{n}. 天气数据没拿到：不要报天气、不要猜温度")
        n += 1
    if not has_events:
        lines.append(f"{n}. 家里今天还没有设备活动记录：不要编造谁在家做了什么")
        n += 1
    lines.append(f"{n}. 长度100-200字，一段连贯的话，不要列表")
    n += 1
    lines.append(f"{n}. 不要用emoji堆砌")

    lines += ["", "---"]
    if has_weather:
        lines.append(f"天气：{weather}")
    if has_news:
        lines.append(f"新闻候选：{'；'.join(news[:5])}")
    if has_topic:
        lines.append(f"生活提醒：{topic}")
    if has_events:
        lines.append(f"家里今天的情况：{json.dumps(report, ensure_ascii=False)}")
    else:
        lines.append("家里今天的情况：今天还没有设备活动记录")
    return "\n".join(lines)


async def generate_briefing(rt, briefing_type: str = "morning") -> str:
    persona = _load_persona()
    report = _build_report()
    weather = await _fetch_weather()
    news = await _fetch_news()
    topic = _get_topic(briefing_type)

    logger.info("briefing inputs: type=%s events=%s weather=%s news=%d topic=%s",
                briefing_type, report.get("total_events", 0), bool(weather), len(news), bool(topic))
    system = _build_prompt(persona, report, weather, news, topic, briefing_type)

    llm = getattr(rt, "llm", None)
    if not llm:
        return "LLM 未就绪。"
    try:
        # 温度 0.8 → 0.6（2026-11-06 P0）：播报是固定场景的短文本，降温度压「自由发挥」，
        # 语义闸门挡不住编造，只能从采样面少给机会。
        text, _ = await llm.chat(system, [], max_tokens=500, temperature=0.6)
        return (text or "").strip()
    except Exception as e:
        logger.error("briefing LLM: %s", e)
        return f"简报失败：{e}"


async def push_briefing(rt, briefing_type: str = "morning"):
    text = await generate_briefing(rt, briefing_type)
    # 不合格先重试一次（LLM 偶发抖动），仍不合格整条丢弃：宁可不播，也不推空内容。
    if _briefing_is_bad(text):
        logger.warning("briefing degenerate, retry once: %r", (text or "")[:80])
        text = await generate_briefing(rt, briefing_type)
    if _briefing_is_bad(text):
        logger.warning("briefing skipped (still degenerate): %r", (text or "")[:80])
        return

    title = "☀️ 早间简报" if briefing_type == "morning" else "🌙 晚间简报"

    # 1. Bark 推送（始终）
    bark = getattr(rt, "bark", None)
    if bark:
        try:
            await bark.push(text, title=title, group="briefing")
        except Exception as e:
            logger.warning("briefing bark push failed: %s", e)

    # 2. TTS 播报：v2.5 优先走 TTS 队列（P2 高优），fallback 到旧路径
    # 函数级导入（与 dialog.py:664 / app.py:727 同一写法，避开模块级循环导入）。
    # 这行此前缺失：enqueue_tts 的 NameError 落在下面的 except 里被吞掉，
    # 结果简报既没进队列也没走 fallback，只有 Bark 一条腿。
    from butler.tts.helper import enqueue_tts

    try:
        queued = enqueue_tts(text, priority=2, room="客厅", member="系统", override_quiet=True)
        if queued:
            logger.info("briefing TTS enqueued (P2): %d chars", len(text))
        else:
            # fallback: 旧路径
            dialog = getattr(rt, "dialog", None)
            if dialog:
                butler_role = rt.roles.get("butler") if getattr(rt, "roles", None) else None
                if butler_role:
                    await dialog.speak_as_role(butler_role, text, "客厅", member="系统")
                    logger.info("briefing TTS dispatched to living room (fallback)")
    except Exception as e:
        logger.warning("briefing TTS failed: %s", e)

    logger.info("briefing pushed: %s (%d chars)", briefing_type, len(text))
