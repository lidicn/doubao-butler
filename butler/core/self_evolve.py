"""Koin 自进化：快速路由自动沉淀。

从 agent_traces 表分析高频 LLM 路径，生成候选快速路由规则。
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from butler.logging_setup import get_logger, warn_throttled

DB_PATH = Path("/app/data/butler.db")
logger = get_logger("butler.core.self_evolve")


def analyze_llm_traces(min_count: int = 3) -> list[dict]:
    """分析走了 LLM 的成功对话，找出高频模式。

    返回候选规则列表：
    [
      {
        "pattern": "打开书房空调",
        "count": 5,
        "keywords": ["书房", "空调"],
        "suggested_tool": "control_device",
        "suggested_args": {"entity": "书房空调"},
        "samples": ["打开书房空调", "书房太热了", ...]
      }
    ]
    """
    db = sqlite3.connect(str(DB_PATH))
    rows = db.execute(
        "SELECT user_text, tool_calls, steps_json FROM agent_traces "
        "WHERE llm_calls > 0 AND status = 'ok' AND source != 'webui' "
        "ORDER BY ts DESC LIMIT 500"
    ).fetchall()
    db.close()

    # 提取工具调用模式
    patterns = defaultdict(list)
    for text, tool_calls, steps_json in rows:
        if not tool_calls or tool_calls == 0:
            continue
        # 解析步骤，找工具调用
        try:
            steps = json.loads(steps_json) if steps_json else []
        except Exception:
            warn_throttled(logger, "evolve.steps", "self_evolve 有一条 steps_json 解析不了（该条跳过）")
            continue

        tools_used = []
        for step in steps:
            if step.get("role") == "action":
                tool = step.get("tool", "")
                args = step.get("args", {})
                tools_used.append((tool, json.dumps(args, sort_keys=True)))

        if not tools_used:
            continue

        # 用工具名+参数作为模式 key
        for tool, args_key in tools_used:
            pattern_key = f"{tool}|{args_key}"
            patterns[pattern_key].append({
                "text": text,
                "tool": tool,
                "args": json.loads(args_key)
            })

    # 过滤高频模式
    candidates = []
    for pattern_key, samples in patterns.items():
        if len(samples) < min_count:
            continue

        # 提取关键词（从用户文本中）
        texts = [s["text"] for s in samples]
        # 简单提取：去掉停用词，找高频词
        all_words = []
        for t in texts:
            # 提取2-4字的短语
            words = re.findall(r'[\u4e00-\u9fa5]{2,4}', t)
            all_words.extend(words)

        word_counts = Counter(all_words)
        top_keywords = [w for w, c in word_counts.most_common(5) if c >= 2]

        tool = samples[0]["tool"]
        args = samples[0]["args"]

        candidates.append({
            "pattern": pattern_key,
            "count": len(samples),
            "keywords": top_keywords,
            "suggested_tool": tool,
            "suggested_args": args,
            "samples": texts[:5]  # 只放前5个样例
        })

    # 按出现次数排序
    candidates.sort(key=lambda x: x["count"], reverse=True)
    return candidates


def list_current_routes(agent) -> list[dict]:
    """列出当前所有快速路由规则。"""
    return agent.fast_routes.list_routes()


def add_route(agent, route: dict) -> dict:
    """添加新的快速路由规则。"""
    agent.fast_routes.add_route(route)
    return {"ok": True, "added": route}


def toggle_route(agent, route_id: str, enabled: bool) -> dict:
    """启用/禁用规则。"""
    ok = agent.fast_routes.toggle_route(route_id, enabled)
    return {"ok": ok}


def delete_route(agent, route_id: str) -> dict:
    """删除规则。"""
    ok = agent.fast_routes.delete_route(route_id)
    return {"ok": ok}


def run_daily_analysis(min_count: int = 3) -> str:
    """跑一次自进化分析，返回并记一条「算出几条候选」（审计 2026-10-02）。

    analyze_llm_traces 的真返回是 list[dict]，⛔ 当成 dict 取键（定时任务里那样写每天必抛）。
    """
    candidates = analyze_llm_traces(min_count)
    line = "self-evolve auto analysis done: %d candidates" % len(candidates)
    logger.info(line)
    return line
