"""感知自进化：分析最近7天事件流，LLM 发现用户行为模式，生成候选规则。"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timedelta

from butler.store.db import get_conn
from butler.logging_setup import get_logger

logger = get_logger("butler.core.perception_learn")


def _ensure_table():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS perception_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            days INTEGER,
            total_events INTEGER,
            patterns_json TEXT,
            llm_analysis_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS candidate_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            name TEXT,
            description TEXT,
            conditions_json TEXT,
            infer TEXT,
            status TEXT DEFAULT 'pending',
            report_id INTEGER
        )
    """)
    conn.commit()


def analyze_patterns(days: int = 7, save: bool = True) -> dict:
    """分析最近 N 天的事件流，发现行为模式（统计版）。"""
    _ensure_table()
    conn = get_conn()
    c = conn.cursor()

    since = (datetime.now() - timedelta(days=days)).timestamp()
    c.execute("""
        SELECT ts, entity_id, state, room, event_type
        FROM home_events
        WHERE ts > ?
        ORDER BY ts ASC
    """, (since,))

    rows = c.fetchall()
    columns = [desc[0] for desc in c.description]
    events = [dict(zip(columns, row)) for row in rows]

    if not events:
        logger.info("perception_learn: no events in last %d days", days)
        return {"total_events": 0, "patterns": []}

    # 统计：每个实体在什么时间段最活跃
    entity_hours = defaultdict(lambda: defaultdict(int))
    room_activity = defaultdict(lambda: defaultdict(int))
    entity_state_changes = defaultdict(int)

    for e in events:
        ts = e["ts"]
        hour = datetime.fromtimestamp(ts).hour
        entity_id = e["entity_id"]
        room = e["room"] or "unknown"

        entity_hours[entity_id][hour] += 1
        room_activity[room][hour] += 1
        entity_state_changes[entity_id] += 1

    # 找出高频事件
    top_entities = sorted(entity_state_changes.items(), key=lambda x: -x[1])[:10]

    patterns = []
    for entity_id, count in top_entities:
        hours = entity_hours[entity_id]
        peak_hours = sorted(hours.items(), key=lambda x: -x[1])[:3]
        peak_text = ", ".join(f"{h}时({c}次)" for h, c in peak_hours if c > 0)
        patterns.append({
            "entity": entity_id,
            "total_changes": count,
            "peak_hours": peak_text,
            "pattern_type": "regular_activity",
        })

    # 找出房间活动规律
    room_patterns = []
    for room, hours in room_activity.items():
        if room == "unknown":
            continue
        active_hours = sum(1 for h, c in hours.items() if c > 0)
        if active_hours > 0:
            total = sum(hours.values())
            room_patterns.append({
                "room": room,
                "total_events": total,
                "active_hours": active_hours,
            })

    result = {
        "total_events": len(events),
        "days": days,
        "top_entities": patterns[:5],
        "room_activity": sorted(room_patterns, key=lambda x: -x["total_events"])[:5],
    }

    if save:
        c.execute("""
            INSERT INTO perception_reports (ts, days, total_events, patterns_json)
            VALUES (?, ?, ?, ?)
        """, (time.time(), days, len(events), json.dumps(result, ensure_ascii=False)))
        conn.commit()
        logger.info("perception_learn: saved report, %d events analyzed", len(events))

    return result


async def llm_analyze_patterns(days: int = 7) -> dict:
    """LLM 深度分析：把事件流摘要喂给 LLM，发现复杂行为模式。"""
    from butler.runtime import get_runtime
    rt = get_runtime()

    # 先做统计分析
    stats = analyze_patterns(days=days, save=False)

    # 构造 LLM prompt
    prompt = f"""你是一个家庭行为分析专家。以下是最近{days}天的家庭事件流统计数据：

总事件数：{stats.get('total_events', 0)}

高频设备活动：
{json.dumps(stats.get('top_entities', []), ensure_ascii=False, indent=2)}

房间活动规律：
{json.dumps(stats.get('room_activity', []), ensure_ascii=False, indent=2)}

请你分析这些数据，发现用户的行为模式，生成 3-5 条候选的场景规则。

每条规则格式：
- 名称：简短描述场景
- 触发条件：什么事件序列触发
- 推断结果：推断用户在做什么
- 置信度：高/中/低

只返回 JSON 格式，不要其他文字：
{{
  "candidates": [
    {{
      "name": "规则名称",
      "description": "规则描述",
      "conditions": "触发条件",
      "infer": "推断结果",
      "confidence": "高/中/低"
    }}
  ],
  "summary": "整体行为模式总结"
}}"""

    try:
        llm = rt.llm
        resp = await llm.chat(
            system="你是一个家庭行为分析专家，擅长从物联网设备事件流中发现用户行为模式。",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
        )
        content = resp.get("content", "") if isinstance(resp, dict) else str(resp)

        # 解析 JSON
        # 找 JSON 开始和结束
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            json_str = content[start:end+1]
            analysis = json.loads(json_str)
        else:
            analysis = {"candidates": [], "summary": content[:200]}

        # 保存候选规则
        _ensure_table()
        conn = get_conn()
        c = conn.cursor()

        # 找最新的 report_id
        c.execute("SELECT id FROM perception_reports ORDER BY ts DESC LIMIT 1")
        row = c.fetchone()
        report_id = row[0] if row else None

        candidates = analysis.get("candidates", [])
        for cand in candidates:
            c.execute("""
                INSERT INTO candidate_rules (ts, name, description, conditions_json, infer, status, report_id)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """, (
                time.time(),
                cand.get("name", "未命名规则"),
                cand.get("description", ""),
                json.dumps({"conditions": cand.get("conditions", "")}, ensure_ascii=False),
                cand.get("infer", ""),
                report_id,
            ))
        conn.commit()

        # 更新 report
        if report_id:
            c.execute("""
                UPDATE perception_reports SET llm_analysis_json = ? WHERE id = ?
            """, (json.dumps(analysis, ensure_ascii=False), report_id))
            conn.commit()

        logger.info("perception_learn LLM: generated %d candidates", len(candidates))
        return analysis

    except Exception as e:
        logger.warning("perception_learn LLM failed: %s", e)
        return {"candidates": [], "summary": f"LLM 分析失败: {e}"}


def list_candidate_rules(status: str = "pending") -> list[dict]:
    """列出候选规则。"""
    _ensure_table()
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT id, ts, name, description, conditions_json, infer, status
        FROM candidate_rules
        WHERE status = ?
        ORDER BY ts DESC
    """, (status,))
    rows = c.fetchall()
    rules = []
    for row in rows:
        try:
            conditions = json.loads(row[4] or "{}")
        except Exception:
            conditions = {}
        rules.append({
            "id": row[0],
            "ts": row[1],
            "name": row[2],
            "description": row[3],
            "conditions": conditions,
            "infer": row[5],
            "status": row[6],
        })
    return rules


def update_candidate_rule(rule_id: int, status: str) -> bool:
    """更新候选规则状态（approved/rejected）。"""
    _ensure_table()
    conn = get_conn()
    conn.execute("""
        UPDATE candidate_rules SET status = ? WHERE id = ?
    """, (status, rule_id))
    conn.commit()
    return True


def list_perception_reports(limit: int = 10) -> list[dict]:
    """列出历史感知分析报告。"""
    _ensure_table()
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT id, ts, days, total_events, patterns_json, llm_analysis_json
        FROM perception_reports
        ORDER BY ts DESC LIMIT ?
    """, (limit,))
    rows = c.fetchall()
    reports = []
    for row in rows:
        try:
            patterns = json.loads(row[4] or "{}")
        except Exception:
            patterns = {}
        try:
            llm_analysis = json.loads(row[5] or "{}")
        except Exception:
            llm_analysis = {}
        reports.append({
            "id": row[0],
            "ts": row[1],
            "days": row[2],
            "total_events": row[3],
            "patterns": patterns,
            "llm_analysis": llm_analysis,
        })
    return reports
