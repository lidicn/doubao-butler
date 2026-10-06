"""失败模式学习：分析 agent_traces 中的失败记录，找出高频失败原因，生成修正建议。"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta

from butler.store.db import get_conn
from butler.logging_setup import get_logger, warn_throttled

logger = get_logger("butler.core.failure_learn")


def _ensure_table():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS failure_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            days INTEGER,
            total_failures INTEGER,
            suggestions_json TEXT
        )
    """)
    conn.commit()


def analyze_failures(days: int = 7, min_count: int = 2, save: bool = True) -> dict:
    """分析最近 N 天的失败模式。"""
    _ensure_table()
    conn = get_conn()
    c = conn.cursor()

    since = (datetime.now() - timedelta(days=days)).timestamp()

    # 查所有失败的 trace
    c.execute("""
        SELECT trace_id, user_text, status, error, steps_json, llm_calls, tool_calls
        FROM agent_traces
        WHERE status != 'ok' AND ts > ?
        ORDER BY trace_id DESC
    """, (since,))

    rows = c.fetchall()
    columns = [desc[0] for desc in c.description]
    failures = [dict(zip(columns, row)) for row in rows]

    # 统计工具失败
    tool_failures = defaultdict(list)
    error_patterns = defaultdict(int)

    for f in failures:
        # 从 steps_json 里提取工具失败
        try:
            steps = json.loads(f.get("steps_json") or "[]")
            for step in steps:
                if step.get("role") == "observation" and step.get("error_code"):
                    tool = step.get("tool", "unknown")
                    result = step.get("result", "")
                    error_code = step.get("error_code", "")
                    tool_failures[tool].append({
                        "user_text": f.get("user_text", ""),
                        "error_code": error_code,
                        "result": result[:200],
                    })
                    # 提取错误模式
                    error_patterns[f"{tool}:{error_code}"] += 1
        except Exception:
            warn_throttled(logger, "learn.trace_steps", "failure_learn 有一条 trace 解析不了（该条跳过，学习继续）")
            continue

    # 生成建议
    suggestions = []
    for tool, fails in tool_failures.items():
        if len(fails) < min_count:
            continue

        # 分析失败原因
        reasons = defaultdict(int)
        for fail in fails:
            result = fail["result"]
            if "not found" in result.lower() or "不存在" in result:
                reasons["entity_not_found"] += 1
            elif "timeout" in result.lower() or "超时" in result:
                reasons["timeout"] += 1
            elif "invalid" in result.lower() or "无效" in result:
                reasons["invalid_param"] += 1
            elif "失败" in result:
                reasons["exec_failed"] += 1

        # 取最常见原因
        top_reason = max(reasons.items(), key=lambda x: x[1]) if reasons else ("unknown", 0)

        # 生成建议
        suggestion = {
            "tool": tool,
            "failure_count": len(fails),
            "top_reason": top_reason[0],
            "reason_count": top_reason[1],
            "samples": [f["user_text"] for f in fails[:3]],
        }

        # 根据原因生成修正建议
        if top_reason[0] == "entity_not_found":
            suggestion["action"] = "entity_resolution"
            suggestion["fix"] = "建议在 _resolve_entity 函数中增加这些关键词的别名映射"
            suggestion["example_keywords"] = list(set(
                re.sub(r'[，。！？\s]', '', f["user_text"])[:10]
                for f in fails
            ))[:5]
        elif top_reason[0] == "invalid_param":
            suggestion["action"] = "param_format"
            suggestion["fix"] = "LLM 传参格式不对，建议在工具说明中明确参数格式示例"
        elif top_reason[0] == "timeout":
            suggestion["action"] = "timeout"
            suggestion["fix"] = "设备响应慢，建议增加重试或降级策略"
        else:
            suggestion["action"] = "manual_review"
            suggestion["fix"] = "需要人工查看具体错误"

        suggestions.append(suggestion)

    # 按失败次数排序
    suggestions.sort(key=lambda x: -x["failure_count"])

    result = {
        "total_failures": len(failures),
        "analyzed_days": days,
        "by_tool": {k: len(v) for k, v in tool_failures.items()},
        "error_patterns": dict(error_patterns),
        "suggestions": suggestions,
    }

    # 持久化到数据库
    if save:
        conn.execute(
            "INSERT INTO failure_reports (ts, days, total_failures, suggestions_json) VALUES (?, ?, ?, ?)",
            (time.time(), days, len(failures), json.dumps(suggestions, ensure_ascii=False))
        )
        conn.commit()

    return result


def list_failure_reports(limit: int = 10) -> list[dict]:
    """列出历史失败分析报告。"""
    _ensure_table()
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM failure_reports ORDER BY ts DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    columns = [desc[0] for desc in c.description]
    return [dict(zip(columns, row)) for row in rows]
