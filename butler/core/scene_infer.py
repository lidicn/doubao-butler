"""场景自动推断：从 agent_traces 里分析时间+操作模式，自动生成场景候选。"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from datetime import datetime

from butler.store.db import get_conn
from butler.logging_setup import get_logger

logger = get_logger("butler.core.scene_infer")


def _ensure_table():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scene_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            days INTEGER,
            total_traces INTEGER,
            scenes_json TEXT
        )
    """)
    conn.commit()


def infer_scenes(days: int = 7, min_count: int = 3, save: bool = True) -> dict:
    """分析最近 N 天的对话轨迹，推断场景模式。"""
    _ensure_table()
    conn = get_conn()
    c = conn.cursor()

    # 查最近 N 天的 trace
    since = (datetime.now().timestamp()) - days * 86400
    c.execute("""
        SELECT trace_id, user_text, ts, status
        FROM agent_traces
        WHERE ts > ? AND status = 'ok'
        ORDER BY ts DESC
    """, (since,))

    rows = c.fetchall()
    columns = [desc[0] for desc in c.description]
    traces = [dict(zip(columns, row)) for row in rows]

    # 按小时分组
    by_hour = defaultdict(list)
    for t in traces:
        hour = datetime.fromtimestamp(t["ts"]).hour
        by_hour[hour].append(t["user_text"])

    # 找每小时最常做的操作
    scenes = []
    for hour, texts in sorted(by_hour.items()):
        if len(texts) < min_count:
            continue

        # 提取关键词（设备名、动作）
        keywords = defaultdict(int)
        for text in texts:
            # 简单提取：2-6个汉字的短语
            import re
            for m in re.finditer(r'[\u4e00-\u9fa5]{2,6}', text):
                kw = m.group()
                if len(kw) >= 2:
                    keywords[kw] += 1

        # 取 top 3 关键词
        top_keywords = sorted(keywords.items(), key=lambda x: -x[1])[:3]
        if not top_keywords:
            continue

        # 生成场景建议
        # 判断是什么场景
        if 6 <= hour <= 9:
            scene_name = "晨起场景"
            icon = "🌅"
        elif 18 <= hour <= 22:
            scene_name = "晚间休闲"
            icon = "🌆"
        elif 22 <= hour or hour <= 1:
            scene_name = "睡眠准备"
            icon = "😴"
        elif 12 <= hour <= 14:
            scene_name = "午休时段"
            icon = "🍜"
        else:
            scene_name = f"{hour}点时段"
            icon = "🕐"

        scenes.append({
            "name": scene_name,
            "icon": icon,
            "hour": hour,
            "frequency": len(texts),
            "top_keywords": [k[0] for k in top_keywords],
            "sample_texts": texts[:3],
            "confidence": min(len(texts) / 10, 1.0),  # 出现次数越多置信度越高
        })

    # 按频率排序
    scenes.sort(key=lambda x: -x["frequency"])

    result = {
        "total_traces": len(traces),
        "analyzed_days": days,
        "by_hour_count": {str(k): len(v) for k, v in by_hour.items()},
        "scenes": scenes,
    }

    # 持久化
    if save:
        conn.execute(
            "INSERT INTO scene_reports (ts, days, total_traces, scenes_json) VALUES (?, ?, ?, ?)",
            (time.time(), days, len(traces), json.dumps(scenes, ensure_ascii=False))
        )
        conn.commit()

    return result


def list_scene_reports(limit: int = 10) -> list[dict]:
    """列出历史场景推断报告。"""
    _ensure_table()
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM scene_reports ORDER BY ts DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    columns = [desc[0] for desc in c.description]
    return [dict(zip(columns, row)) for row in rows]
