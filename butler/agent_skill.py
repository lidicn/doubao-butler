"""ReAct 轨迹 → 操作模板技能（M3 核心）。

把 agent_traces 里成功的多步工具调用序列提取为可参数化的「工具序列技能」，
存到 data/skills/agent/ 目录（source=agent, approval=pending_review），
人工审核后发布。Agent.run 对话时优先匹配已发布的操作模板，命中直接执行，失败回退 ReAct。

技能格式（brain.engine = "tool_sequence"）：
{
  "id": "fongmi_search_play",
  "name": "飞牛TV搜索播放",
  "brain": {
    "engine": "tool_sequence",
    "steps": [
      {"tool": "tv_search_play", "args": {"keyword": "{{keyword}}"}}
    ],
    "intent_keywords": ["飞牛TV", "搜索播放", "在电视上放", "看电视"],
    "success_text": "已在飞牛TV上播放《{{keyword}}》"
  },
  "trigger": {"entry": "webhook"},
  "source": "agent",
  "approval": "pending_review"
}
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from butler.agent_trace import AgentTracer
from butler.logging_setup import get_logger

logger = get_logger("butler.agent_skill")

# 不沉淀为步骤的工具（纯观察/查询类，不影响操作序列）
_OBSERVE_ONLY = {"tv_foreground", "get_current_time", "query_memory"}


def trace_to_skill_draft(trace_id: str, skill_id: str = "", name: str = "") -> dict | None:
    """从 agent_traces 提取成功的工具调用序列，生成技能草稿。

    返回技能定义 dict（未持久化），失败返回 None。
    只提取 action 步骤（工具调用），跳过 thought/observation/final/escape。
    """
    trace = AgentTracer.get_trace(trace_id)
    if trace is None:
        logger.warning("trace_to_skill_draft: trace %s not found", trace_id)
        return None
    if trace.get("status") != "ok":
        logger.warning("trace_to_skill_draft: trace %s status=%s, 只沉淀成功轨迹", trace_id, trace.get("status"))
        return None

    steps = []
    for s in trace.get("steps", []):
        if s.get("role") != "action":
            continue
        tool = s.get("tool", "")
        if not tool or tool in _OBSERVE_ONLY:
            continue
        try:
            args = json.loads(s.get("args") or "{}")
        except Exception:
            args = {}
        steps.append({"tool": tool, "args": args})

    if not steps:
        logger.warning("trace_to_skill_draft: trace %s 没有可沉淀的工具步骤", trace_id)
        return None

    # 参数化：把高频字符串值提取为 {{param}} 占位符
    steps, params = _parameterize(steps)

    sid = skill_id or f"agent_{int(time.time())}"
    skill_name = name or f"操作模板 {sid}"
    intent_kw = _infer_keywords(trace.get("user_text", ""), steps)

    draft = {
        "id": sid,
        "name": skill_name,
        "version": 1,
        "status": "draft",
        "source": "agent",
        "priority": 60,
        "approval": "pending_review",
        "trigger": {"entry": "webhook"},
        "brain": {
            "type": "tool_sequence",
            "engine": "tool_sequence",
            "steps": steps,
            "intent_keywords": intent_kw,
            "params": params,
            "success_text": f"已执行{len(steps)}步操作。",
        },
        "output": [{"type": "tv_notify", "tts": True}],
        "limits": {"per_day": 50},
        "meta": {
            "from_trace": trace_id,
            "user_text": trace.get("user_text", ""),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
    }
    return draft


def save_skill_draft(draft: dict, data_dir: str) -> str:
    """把技能草稿存到 data/skills/agent/ 目录。返回文件路径。"""
    d = Path(data_dir) / "skills" / "agent"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{draft['id']}.json"
    path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("skill draft saved: %s", path)
    return str(path)


def match_dialog_skill(user_text: str, skills: list[dict]) -> dict | None:
    """从技能列表里匹配对话意图。返回匹配的技能 dict，无匹配返回 None。

    匹配规则：brain.intent_keywords 里的任一关键词出现在 user_text 中。
    只匹配 source=agent、status=enabled、approval=approved、brain.engine=tool_sequence 的技能。
    """
    text = (user_text or "").lower()
    best = None
    best_score = 0
    for skill in skills:
        if skill.get("source") != "agent":
            continue
        if skill.get("status") != "enabled":
            continue
        if skill.get("approval") not in ("approved", "auto_approved"):
            continue
        brain = skill.get("brain") or {}
        if brain.get("engine") != "tool_sequence":
            continue
        keywords = brain.get("intent_keywords") or []
        score = sum(1 for kw in keywords if kw and kw.lower() in text)
        if score > best_score:
            best_score = score
            best = skill
    return best


def extract_params_from_text(user_text: str, skill: dict) -> dict:
    """从用户文本里提取技能参数（简单规则：关键词后的内容）。

    目前支持：
    - keyword/影片名/节目名：「播放XXX」「看XXX」「放XXX」后的内容
    - channel/频道：「换到XXX」「XX台」
    """
    params = {}
    text = (user_text or "").strip()
    brain = skill.get("brain") or {}
    skill_params = brain.get("params") or {}

    for pname in skill_params:
        if pname in ("keyword", "movie", "movie_name", "film", "show", "video"):
            # 匹配「播放/放/看/搜索/播/想看」后的内容，跳过「播放/放/看/搜索」本身
            m = re.search(r"(?:想看|我想看|播放|放一下|放个|放|搜索|播)[《\"']?\s*(?:播放|放|看|搜索|播)?\s*([^》\"',，。！？\s]+(?:[\s·][^》\"',，。！？\s]+)*)", text)
            if m:
                val = m.group(1).strip()
                # 去掉可能残留的前缀词
                for prefix in ("播放", "放", "看", "搜索", "播"):
                    if val.startswith(prefix) and len(val) > len(prefix):
                        val = val[len(prefix):]
                if val:
                    params[pname] = val
        elif pname in ("channel", "channel_name"):
            m = re.search(r"(?:换到|切换到|换台到|调至|调到|看|播)\s*([^，。！？\s]+(?:台|卫视|CCTV\d+|中央.+台|频道)?)", text)
            if m:
                params[pname] = m.group(1).strip()
    return params


# ── 内部辅助 ──────────────────────────────────────────────

def _parameterize(steps: list[dict]) -> tuple[list[dict], list[str]]:
    """把步骤里的高频字符串值参数化为 {{param}}。

    简单策略：长度 > 2 的字符串值，如果不是常见固定值（如包名、按键名），
    且在所有步骤里只出现一次，提取为参数。
    """
    params = []
    param_values = {}  # value -> param_name
    fixed_values = {
        "com.tvcam.mytv", "com.trim.tv", "com.mitv.tvhome",
        "KEYCODE_HOME", "KEYCODE_ENTER", "KEYCODE_BACK",
        "true", "false",
    }
    for step in steps:
        for k, v in step.get("args", {}).items():
            if isinstance(v, str) and len(v) > 2 and v not in fixed_values:
                if v not in param_values:
                    pname = k if k not in ("text", "name", "title") else "keyword"
                    if pname in params:
                        pname = f"{pname}_{len(params)}"
                    param_values[v] = pname
                    params.append(pname)
    # 替换
    for step in steps:
        for k, v in step.get("args", {}).items():
            if isinstance(v, str) and v in param_values:
                step["args"][k] = "{{" + param_values[v] + "}}"
    return steps, params


def _infer_keywords(user_text: str, steps: list[dict]) -> list[str]:
    """从用户文本和工具序列推断意图关键词。"""
    kws = []
    tools_used = {s["tool"] for s in steps}
    if "tv_search_play" in tools_used or "tv_launch_app" in tools_used:
        kws.extend(["飞牛TV", "搜索播放", "在电视上放", "看电视", "播放"])
    if "switch_channel" in tools_used or "tv_zap" in tools_used:
        kws.extend(["换台", "换到", "频道", "CCTV", "卫视"])
    if "tv_go_home" in tools_used or "tv_keyevent" in tools_used:
        kws.extend(["回桌面", "回主页", "返回"])
    # 从用户文本提取核心词
    if user_text:
        clean = re.sub(r"[的了吗呢啊吧呀，。！？\s]", "", user_text)
        if 2 <= len(clean) <= 10:
            kws.append(clean)
    # 去重
    seen = set()
    result = []
    for kw in kws:
        if kw and kw not in seen:
            seen.add(kw)
            result.append(kw)
    return result[:8]
