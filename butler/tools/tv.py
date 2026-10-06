"""TVPilot 域（从 butler/core/tools.py 拆分）。"""
from __future__ import annotations

import asyncio
import time

from butler.logging_setup import get_logger

logger = get_logger("butler.tools")

# ── TVPilot 细粒度工具分发 ──────────────────────────────

async def _dispatch_tvpilot(agent, name: str, args: dict) -> str:
    """TVPilot 工具统一分发。返回结构化 JSON 字符串（含 ok/error/result），
    供 ReAct 循环解析错误码并做重试/换路径决策。"""
    tvp = getattr(agent, "tvpilot", None)
    if tvp is None:
        import json as _json
        return _json.dumps({"ok": False, "error": "not_implemented",
                            "message": "TVPilot 工具层未初始化"}, ensure_ascii=False)
    try:
        if name == "tv_foreground":
            r = await tvp.foreground()
        elif name == "tv_keyevent":
            r = await tvp.keyevent(args.get("key", ""))
        elif name == "tv_launch_app":
            r = await tvp.launch_app(args.get("package", ""), args.get("activity"))
        elif name == "tv_input_text":
            r = await tvp.input_text(args.get("text", ""))
        elif name == "tv_tap":
            r = await tvp.tap(int(args.get("x", 0)), int(args.get("y", 0)))
        elif name == "tv_swipe":
            r = await tvp.swipe(
                int(args.get("x1", 0)), int(args.get("y1", 0)),
                int(args.get("x2", 0)), int(args.get("y2", 0)),
                int(args.get("duration_ms", 300)),
            )
        elif name == "tv_screenshot":
            r = await tvp.screenshot(width=480)
        elif name == "tv_go_home":
            r = await tvp.combo_go_home()
        elif name == "tv_search_play":
            r = await tvp.combo_search_play(args.get("keyword", ""))
        else:
            r = {"ok": False, "error": "invalid_parameter", "message": f"未知 TVPilot 工具: {name}"}
        import json as _json
        return _json.dumps(r, ensure_ascii=False)
    except Exception as e:
        logger.warning("tvpilot %s failed: %s", name, e)
        import json as _json
        return _json.dumps({"ok": False, "error": "tv_unreachable",
                            "message": str(e)}, ensure_ascii=False)
