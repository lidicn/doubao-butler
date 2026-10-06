"""日程提醒域（从 butler/core/tools.py 拆分）。"""
from __future__ import annotations

import asyncio
import concurrent.futures
import time

from butler.logging_setup import get_logger

logger = get_logger("butler.tools")

def _parse_reminder_at(s: str) -> str | None:
    """解析绝对时间，返回 'YYYY-MM-DD HH:MM:SS' 或 None。

    支持格式（按序匹配，区分是否含日期）：
      YYYY-MM-DD HH:MM[:SS] / YYYY/MM/DD HH:MM   完整日期时间
      MM-DD HH:MM / MM月DD日 HH:MM               当年日期
      HH:MM                                       今天该时刻（已过则明天）
    """
    s = (s or "").strip()
    if not s:
        return None
    from datetime import datetime, timedelta

    now = datetime.now()
    dt = None
    # 含完整年份
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    # 含月日（当年）
    for fmt in ("%m-%d %H:%M", "%m月%d日 %H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            dt = dt.replace(year=now.year)
            if dt <= now:
                dt = dt + timedelta(days=1)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    # 仅时分 → 今天，已过则明天
    try:
        hm = datetime.strptime(s, "%H:%M")
    except ValueError:
        return None
    dt = now.replace(hour=hm.hour, minute=hm.minute, second=0, microsecond=0)
    if dt <= now:
        dt = dt + timedelta(days=1)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


async def _set_reminder(agent, text: str, minutes: int, at: str = "", member: str = "") -> str:
    if not agent.scheduler:
        return "提醒服务未启用"
    if at:
        run_date = _parse_reminder_at(at)
        if not run_date:
            return "无法解析时间，请用格式 YYYY-MM-DD HH:MM 或 HH:MM"
        desc = f"{at} 的提醒"
    else:
        when = time.time() + max(1, minutes) * 60
        run_date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(when))
        desc = f"{minutes} 分钟后的提醒"
    try:
        loop = asyncio.get_running_loop()
        def _safe_fire():
            fut = asyncio.run_coroutine_threadsafe(_fire_reminder(agent, text, member), loop)
            try:
                fut.result(timeout=30)
            except concurrent.futures.TimeoutError:
                import logging
                logging.getLogger("butler.schedule").error("reminder fire timed out after 30s, cancelling")
                fut.cancel()
            except Exception:
                import logging
                logging.getLogger("butler.schedule").error("reminder fire failed", exc_info=True)
        agent.scheduler.add_job(
            _safe_fire,
            "date",
            run_date=run_date,
        )
        m = f"，到点全屋找{member}" if member else ""
        return f"已设定 {desc}：{text}{m}"
    except Exception as e:
        return f"设定提醒失败：{e}"


async def _fire_reminder(agent, text: str, member: str = "") -> None:
    """到点触发：优先走 trigger 引擎 scheduled 事件（全屋找人+所在房间播报/Bark兜底）；
    无 trigger 引擎时降级为原逻辑（TV 弹窗 + 默认小爱播报）。"""
    try:
        from butler.runtime import get_runtime

        rt = get_runtime()
        te = getattr(rt, "trigger_engine", None)
        if te is not None:
            await te.handle_event("scheduled", {"member": member or "", "text": text, "room": ""})
            return
    except Exception as e:
        logger.warning("scheduled trigger dispatch failed, fallback: %s", e, exc_info=True)
    try:
        agent.tv.notify({"title": "提醒", "message": text})
        await agent.ha.tts_speak(text)
    except Exception as e:
        logger.error("reminder fire failed", exc_info=True)


# ── 日程管理工具 ──────────────────────────────────────────────

def _query_schedule(agent, args: dict) -> str:
    from datetime import datetime, timedelta
    from butler.store.db import get_conn
    member = args.get("member") or ""
    days = int(args.get("days") or 1)
    date_str = args.get("date") or ""
    now = datetime.now()
    if date_str:
        try:
            start = datetime.strptime(date_str, "%Y-%m-%d")
            end = start + timedelta(days=1)
        except ValueError:
            return "日期格式不对，请用 YYYY-MM-DD"
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=days)
    db = get_conn()
    q = "SELECT id, title, description, start_time, end_time, location, recurrence, done FROM schedules WHERE start_time >= ? AND start_time < ?"
    params = [start.strftime("%Y-%m-%d %H:%M"), end.strftime("%Y-%m-%d %H:%M")]
    if member:
        q += " AND member = ?"
        params.append(member)
    q += " ORDER BY start_time ASC"
    rows = db.execute(q, params).fetchall()
    if not rows:
        scope = date_str or f"未来{days}天"
        return f"{member or '全家'}{scope}没有日程安排。"
    lines = []
    for r in rows:
        status = "✅" if r["done"] else "📋"
        loc = f"（{r['location']}）" if r["location"] else ""
        recur_map = {"daily": "每天", "weekly": "每周", "monthly": "每月"}
        recur = f" [{recur_map.get(r['recurrence'], '')}]" if r["recurrence"] != "none" else ""
        desc = f"：{r['description']}" if r["description"] else ""
        lines.append(f"{status} #{r['id']} {r['start_time']} {r['title']}{loc}{recur}{desc}")
    return f"{member or '全家'}的日程：\n" + "\n".join(lines)


def _create_schedule(agent, args: dict) -> str:
    import time
    from datetime import datetime
    from butler.store.db import get_conn
    member = args.get("member") or ""
    title = args.get("title", "").strip()
    if not title:
        return "日程标题不能为空"
    start_time = args.get("start_time", "").strip()
    if not start_time:
        return "需要开始时间，格式 YYYY-MM-DD HH:MM"
    if len(start_time) == 11:
        start_time = f"{datetime.now().year}-{start_time}"
    db = get_conn()
    now = time.time()
    db.execute(
        "INSERT INTO schedules (member, title, description, start_time, end_time, location, recurrence, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (member, title, args.get("description", ""), start_time,
         args.get("end_time", ""), args.get("location", ""),
         args.get("recurrence", "none"), now, now)
    )
    db.commit()
    return f"已创建日程：{start_time} {title}"


def _update_schedule(agent, args: dict) -> str:
    import time
    from butler.store.db import get_conn
    sid = args.get("schedule_id")
    if not sid:
        return "需要日程ID（先 query_schedule 查看）"
    db = get_conn()
    row = db.execute("SELECT id, title FROM schedules WHERE id=?", (sid,)).fetchone()
    if not row:
        return f"找不到日程 #{sid}"
    sets, params = [], []
    if args.get("title"):
        sets.append("title=?"); params.append(args["title"])
    if args.get("start_time"):
        sets.append("start_time=?"); params.append(args["start_time"])
    if args.get("end_time") is not None:
        sets.append("end_time=?"); params.append(args["end_time"])
    if args.get("done") is not None:
        sets.append("done=?"); params.append(1 if args["done"] else 0)
    if not sets:
        return "没有要修改的内容"
    sets.append("updated_at=?"); params.append(time.time())
    params.append(sid)
    db.execute(f"UPDATE schedules SET {', '.join(sets)} WHERE id=?", params)
    db.commit()
    return f"已修改日程 #{sid}（{row['title']}）"
