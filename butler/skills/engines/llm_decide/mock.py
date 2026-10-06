"""llm_decide mock 测试：不调真实工具，用预设假数据验证 LLM 决策。

用法：
  scenarios = [
    {"name": "电视关着", "mock": {"tv_on": False, "weather": "晴 33度"}},
    {"name": "电视开着", "mock": {"tv_on": True, "tv_package": "com.mitv.tvhome", "weather": "多云 26度"}},
  ]
  results = await mock_test(skill, scenarios, user_replies=["好", "不用", None])
"""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from butler.core.tools import TOOL_SCHEMAS
from butler.logging_setup import get_logger

logger = get_logger("butler.skills.engines.llm_decide.mock")


def _mock_tool_result(name: str, args: dict, mock: dict) -> str:
    """根据场景假数据返回工具结果。"""
    if name == "tv_foreground":
        if mock.get("tv_on", False):
            return f'{{"ok": true, "package": "{mock.get("tv_package", "com.mitv.tvhome")}"}}'
        return '{"ok": true, "package": ""}'
    if name == "tv_turn_on":
        return '{"ok": true, "message": "电视已打开"}'
    if name == "tv_go_home":
        return '{"ok": true}'
    if name == "switch_channel":
        return f'{{"ok": true, "channel": "{args.get("channel", "CCTV1")}"}}'
    if name == "get_weather":
        return mock.get("weather", "晴 28度 湿度50%")
    if name == "get_current_time":
        return mock.get("time", "07:30")
    return f'{{"ok": true, "mock": "{name}"}}'


async def mock_test(skill: dict, rt, scenarios: list[dict],
                    user_replies: list[str | None] | None = None) -> list[dict]:
    """跑多个场景，返回每个场景的 LLM 行为记录。

    scenarios: [{"name": "...", "mock": {"tv_on": false, "weather": "..."}}]
    user_replies: 每个场景模拟的用户回复（None=不模拟第二轮）
    """
    brain = skill.get("brain") or {}
    llm = getattr(rt, "llm", None)
    if llm is None:
        return [{"error": "llm not ready"}]

    whitelist = set(brain.get("tools_whitelist") or [])
    filtered_schemas = [
        s for s in TOOL_SCHEMAS
        if s.get("function", {}).get("name") in whitelist
    ]
    system_tpl = brain.get("system") or ""
    prompt_tpl = brain.get("prompt") or ""
    temperature = float(brain.get("temperature") or 0.7)
    max_chars = int(brain.get("max_chars") or 80)
    ask_wait = float(brain.get("ask_wait_seconds") or 0)

    results = []
    for i, sc in enumerate(scenarios):
        mock = sc.get("mock") or {}
        sc_name = sc.get("name", f"场景{i+1}")
        tools_called = []
        blocked = []

        # 上下文
        now_str = mock.get("time", "07:30")
        fmt = {"member": "lidicn", "room": "客厅", "time": now_str,
               "date": "09月19日", "weekday": "周一"}

        def _render(s):
            for k, v in fmt.items():
                s = s.replace("{{" + k + "}}", v)
            return s

        system = _render(system_tpl)
        prompt = _render(prompt_tpl)
        system += f"\n\n【当前时间】2026年09月19日 周一 {now_str}（Asia/Shanghai）。"

        async def mock_executor(name, args, _wl=whitelist, _tc=tools_called, _bl=blocked, _mock=mock):
            if name not in _wl:
                _bl.append(name)
                return f"工具 {name} 不在白名单中。"
            _tc.append({"tool": name, "args": args})
            return _mock_tool_result(name, args, _mock)

        # 第一轮
        try:
            text1 = await llm.chat_with_tools(
                system, [{"role": "user", "content": prompt}],
                filtered_schemas, mock_executor,
                max_iter=5, time_budget=30.0, temperature=temperature, max_tokens=500,
            )
        except Exception as e:
            results.append({"scenario": sc_name, "error": str(e)})
            continue

        text1 = (text1 or "").strip()[:max_chars]
        entry = {
            "scenario": sc_name,
            "mock": mock,
            "round1_text": text1,
            "tools_called": tools_called,
            "blocked_attempts": blocked,
        }

        # 第二轮：模拟用户回复
        reply = (user_replies or [None] * len(scenarios))[i] if user_replies else None
        if reply and ask_wait > 0:
            tools_called2 = []
            async def mock_exec2(name, args, _wl=whitelist, _tc=tools_called2, _mock=mock):
                if name not in _wl:
                    return f"工具 {name} 不在白名单中。"
                _tc.append({"tool": name, "args": args})
                return _mock_tool_result(name, args, _mock)

            followup_prompt = (
                f"【用户回答】{reply}\n"
                f"【你刚才问的】{text1}\n\n"
                f"你必须根据用户的回答执行操作：\n"
                f"- 如果用户说的是肯定（好/行/可以/要/打开/嗯/是的），立刻调用 tv_launch_app 工具，参数 package=com.tvcam.mytv，然后说'好的，打开了'\n"
                f"- 如果用户说的是否定（不用/不要/算了/暂时不），不要调用任何工具，只说'好的'"
            )
            system2 = "你是家庭管家。根据用户的回答调用对应工具。肯定回答必须调用 tv_launch_app。"
            try:
                text2 = await llm.chat_with_tools(
                    system2, [{"role": "user", "content": followup_prompt}],
                    filtered_schemas, mock_exec2,
                    max_iter=3, time_budget=20.0, temperature=0.5, max_tokens=300,
                )
                entry["user_reply"] = reply
                entry["round2_text"] = (text2 or "").strip()[:60]
                entry["round2_tools"] = tools_called2
            except Exception as e:
                entry["round2_error"] = str(e)

        results.append(entry)
        logger.info("mock scenario '%s' done: round1=%.30s tools=%d blocked=%d",
                     sc_name, text1, len(tools_called), len(blocked))

    return results
