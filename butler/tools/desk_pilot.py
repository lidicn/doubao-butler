"""DeskPilot 远程控制域（从 butler/core/tools.py 拆分）。"""
from __future__ import annotations

import asyncio
import time

from butler.logging_setup import get_logger

logger = get_logger("butler.tools")

DESKPILOT_PM_SEND_URL = "http://192.168.2.201:8765/api/v1/pm/send"
# WO-BUT-018: token 从 config 读取，不再硬编码

async def _deskpilot_pm_send(target: str, message: str, press_enter: bool = True) -> tuple[bool, str]:
    """通过 DeskPilot PM 消息管道发送消息到豆包对话。
    target: PM / TP / DP
    返回 (ok, error_msg)
    """
    import httpx
    from butler.config import get_settings
    try:
        token = get_settings().deskpilot_api_token
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.post(
                DESKPILOT_PM_SEND_URL,
                json={"target": target, "message": message, "press_enter": press_enter},
                headers={"Authorization": f"Bearer {token}"},
            )
            result = r.json()
            if result.get("ok"):
                return True, ""
            return False, result.get("error") or result.get("message", "unknown")
    except Exception as e:
        logger.warning("deskpilot_pm_send(%s) failed: %s", target, e)
        return False, str(e)


async def _dispatch_deskpilot(agent, name: str, args: dict) -> str:
    """DeskPilot 工具统一分发。返回结构化 JSON 字符串（含 ok/error/result），
    供 ReAct 循环解析错误码并做重试/换路径决策。"""
    import json as _json
    dpk = getattr(agent, "deskpilot", None)
    if dpk is None:
        return _json.dumps({"ok": False, "error": "not_implemented",
                            "message": "DeskPilot 工具层未初始化"}, ensure_ascii=False)
    try:
        # 最近一次截图的缩放比例（截图坐标 → 原始屏幕坐标）
        # screenshot 默认返回 480px 宽，但 click/swipe 需要原始屏幕坐标（如 2560px）
        # 每次 screenshot 成功后更新此比例，click/swipe 转发前自动换算
        if not hasattr(_dispatch_deskpilot, "_scale_x"):
            _dispatch_deskpilot._scale_x = 1.0
            _dispatch_deskpilot._scale_y = 1.0
        if name == "desk_system_status":
            r = await dpk.system_status()
        elif name == "desk_system_notify":
            r = await dpk.system_notify(args.get("title", ""), args.get("message", ""))
        elif name == "desk_system_run":
            r = await dpk.system_run(args.get("path", ""), args.get("args", ""))
        elif name == "desk_volume_get":
            r = await dpk.volume_get()
        elif name == "desk_volume_set":
            r = await dpk.volume_set(int(args.get("level", 50)))
        elif name == "desk_volume_mute":
            r = await dpk.volume_toggle_mute()
        elif name == "desk_windows_list":
            r = await dpk.windows_list()
        elif name == "desk_windows_activate":
            r = await dpk.windows_activate(args.get("title", ""))
        elif name == "desk_windows_maximize":
            r = await dpk.windows_maximize(args.get("title", ""))
        elif name == "desk_windows_close":
            r = await dpk.windows_close(args.get("title", ""))
        elif name == "desk_music_play":
            r = await dpk.lxmusic_play_by_keyword(args.get("keyword", ""))
        elif name == "desk_desktop_screenshot":
            r = await dpk.desktop_screenshot(args.get("region", ""))
            # 保存缩放比例，供 click/swipe 坐标换算
            if r.get("ok"):
                sr = r.get("result") or {}
                ow = sr.get("orig_width")
                oh = sr.get("orig_height")
                w = sr.get("width")
                h = sr.get("height")
                if ow and w and w > 0:
                    _dispatch_deskpilot._scale_x = ow / w
                if oh and h and h > 0:
                    _dispatch_deskpilot._scale_y = oh / h
        elif name == "desk_desktop_click":
            # 坐标自动换算：截图坐标 → 原始屏幕坐标
            sx = _dispatch_deskpilot._scale_x
            sy = _dispatch_deskpilot._scale_y
            raw_x = int(args.get("x", 0))
            raw_y = int(args.get("y", 0))
            # 如果坐标明显在原始屏幕范围内（> 1000），不换算（LLM 可能直接用了原始坐标）
            if sx > 1.1 and raw_x < 1000:
                conv_x = int(round(raw_x * sx))
                conv_y = int(round(raw_y * sy))
            else:
                conv_x, conv_y = raw_x, raw_y
            r = await dpk.desktop_click(
                conv_x, conv_y,
                button=args.get("button", "left"),
                double=bool(args.get("double", False)),
                confirm=bool(args.get("confirm", False)),
            )
            # 观察验证器：click 成功后自动截图，取元信息（不放 base64 省 token）
            if r.get("ok"):
                try:
                    shot = await dpk.desktop_screenshot()
                    if shot.get("ok"):
                        sr = shot.get("result") or {}
                        r["post_click_screenshot"] = {
                            "width": sr.get("width"),
                            "height": sr.get("height"),
                            "format": sr.get("format", "jpeg"),
                            "note": "点击后画面已截图，如需查看请调用 desk_desktop_screenshot 获取 base64",
                        }
                except Exception:
                    pass  # 验证器失败不影响 click 结果
        elif name == "desk_desktop_type":
            r = await dpk.desktop_type(args.get("text", ""))
        elif name == "desk_desktop_swipe":
            # 坐标自动换算：截图坐标 → 原始屏幕坐标
            sx = _dispatch_deskpilot._scale_x
            sy = _dispatch_deskpilot._scale_y
            def _conv(v, scale):
                if scale > 1.1 and v < 1000:
                    return int(round(v * scale))
                return v
            r = await dpk.desktop_swipe(
                _conv(int(args.get("x1", 0)), sx),
                _conv(int(args.get("y1", 0)), sy),
                _conv(int(args.get("x2", 0)), sx),
                _conv(int(args.get("y2", 0)), sy),
                duration_ms=int(args.get("duration_ms", 300)),
            )
        elif name == "desk_desktop_key":
            # 解析组合键："win+right" → key="right", modifiers=["win"]
            raw_key = str(args.get("key", "")).strip().lower()
            if "+" in raw_key:
                parts = [p.strip() for p in raw_key.split("+") if p.strip()]
                modifiers = parts[:-1]
                key_name = parts[-1]
            else:
                modifiers = []
                key_name = raw_key
            r = await dpk.desktop_key(key_name, modifiers)
        elif name == "desk_uia_snapshot":
            r = await dpk.uia_snapshot(
                window=args.get("window", ""),
                pid=int(args.get("pid") or 0),
                depth=int(args.get("depth", 8)),
                max_nodes=int(args.get("max_nodes", 200)),
            )
        elif name == "desk_uia_click":
            r = await dpk.uia_click(
                window=args.get("window", ""),
                pid=int(args.get("pid") or 0),
                path=args.get("path"),
                name=args.get("name", ""),
                automation_id=args.get("automation_id", ""),
                control_type=args.get("control_type", ""),
                class_name=args.get("class_name", ""),
            )
        elif name == "desk_uia_type":
            r = await dpk.uia_type(
                text=args.get("text", ""),
                window=args.get("window", ""),
                pid=int(args.get("pid") or 0),
                path=args.get("path"),
                name=args.get("name", ""),
                automation_id=args.get("automation_id", ""),
                control_type=args.get("control_type", ""),
                class_name=args.get("class_name", ""),
                clear_existing=bool(args.get("clear_existing", False)),
            )
        elif name == "desk_uia_wait":
            r = await dpk.uia_wait(
                window=args.get("window", ""),
                pid=int(args.get("pid") or 0),
                path=args.get("path"),
                name=args.get("name", ""),
                automation_id=args.get("automation_id", ""),
                control_type=args.get("control_type", ""),
                class_name=args.get("class_name", ""),
                timeout=float(args.get("timeout", 5.0)),
                condition=args.get("condition", "exists"),
            )
        else:
            r = {"ok": False, "error": "invalid_parameter", "message": f"未知 DeskPilot 工具: {name}"}
        return _json.dumps(r, ensure_ascii=False)
    except Exception as e:
        logger.warning("deskpilot %s failed: %s", name, e)
        return _json.dumps({"ok": False, "error": "unavailable",
                            "message": str(e)}, ensure_ascii=False)


def _dispatch_newapi(agent, name: str, args: dict) -> str:
    """new-api 运维工具分发（同步，SQLite 直连）。"""
    import json as _json
    nap = getattr(agent, "newapi", None)
    if nap is None:
        return _json.dumps({"ok": False, "error": "not_implemented",
                            "message": "new-api 运维客户端未初始化"}, ensure_ascii=False)
    try:
        if name == "newapi_list_channels":
            r = nap.list_channels()
        elif name == "newapi_list_models":
            r = nap.list_virtual_models()
        elif name == "newapi_get_model_allocation":
            r = nap.get_model_allocation(args.get("model", ""))
        elif name == "newapi_set_model_allocation":
            r = nap.set_model_allocation(args.get("model", ""), args.get("allocations", []))
        else:
            r = {"ok": False, "error": "invalid_parameter", "message": f"未知 new-api 工具: {name}"}
        return _json.dumps(r, ensure_ascii=False)
    except Exception as e:
        logger.warning("newapi %s failed: %s", name, e)
        return _json.dumps({"ok": False, "error": "internal_error",
                            "message": str(e)}, ensure_ascii=False)


async def _dispatch_docker(agent, name: str, args: dict) -> str:
    """Docker 运维工具分发（同步，docker.sock）。"""
    import json as _json
    dk = getattr(agent, "docker", None)
    if dk is None:
        return _json.dumps({"ok": False, "error": "not_implemented",
                            "message": "Docker 运维客户端未初始化"}, ensure_ascii=False)
    try:
        if name == "docker_ps":
            r = await dk.ps(all_containers=args.get("all", False))
        elif name == "docker_restart":
            r = await dk.restart(args.get("container", ""))
        elif name == "docker_logs":
            r = await dk.logs(args.get("container", ""), int(args.get("tail", 50)))
        elif name == "docker_compose_up":
            r = await dk.compose_up(
                args.get("project_dir", ""),
                args.get("services"),
                build=args.get("build", True),
            )
        else:
            r = {"ok": False, "error": "invalid_parameter", "message": f"未知 Docker 工具: {name}"}
        return _json.dumps(r, ensure_ascii=False)
    except Exception as e:
        logger.warning("docker %s failed: %s", name, e)
        return _json.dumps({"ok": False, "error": "internal_error",
                            "message": str(e)}, ensure_ascii=False)


# ── PM 工单分发工具实现 ──────────────────────────────────────────

async def _assign_task_to_tp(agent, args: dict) -> str:
    """给 TP 发工单：创建任务看板记录 + 发消息到 TP 对话（含上报命令）"""
    import base64
    from butler.store import task_store

    title = args.get("title", "").strip()
    desc = args.get("desc", "").strip()
    priority = args.get("priority", "P2")
    task_type = args.get("task_type", "需求")
    if not title or not desc:
        return "工单标题和描述不能为空"

    # 1. 创建任务看板记录
    try:
        task = task_store.create_task(
            title=title, description=desc, priority=priority,
            task_type=task_type, assignee="TP",
        )
        task_id = task["id"]
    except Exception as e:
        logger.warning("create task failed: %s", e)
        return f"❌ 创建任务记录失败：{e}"

    # 2. 构造消息（含任务 ID 和上报命令）
    report_cmd = f'python C:\\tools\\task_report.py {task_id} 进行中'
    complete_cmd = f'python C:\\tools\\task_report.py {task_id} 已完成 "完成说明"'

    message = (
        f"【PM 工单】{title}\n"
        f"任务ID：{task_id}\n"
        f"优先级：{priority} | 类型：{task_type}\n"
        f"发起方：豆包管家 PM\n"
        f"时间：{time.strftime('%Y-%m-%d %H:%M')}\n"
        f"────────────────\n"
        f"{desc}\n"
        f"────────────────\n"
        f"【状态上报】接手/完成时执行以下命令（中文无乱码）：\n"
        f"接手：{report_cmd}\n"
        f"完成：{complete_cmd}\n"
        f"也可以直接回复本对话告知进度。"
    )
    b64 = base64.b64encode(message.encode("utf-8")).decode("ascii")

    dpk = getattr(agent, "deskpilot", None)
    if dpk is None:
        return f"DeskPilot 客户端未初始化，但任务 {task_id} 已创建。"

    try:
        r = await dpk.system_run(
            r"C:\Users\lidicn\AppData\Local\Programs\Python\Python313\python.exe",
            [r"C:\tools\pm_send_to_tp.py", b64],
            timeout=30.0,
        )
        ok = r.get("ok", False) if isinstance(r, dict) else False
        if ok:
            return (f"✅ TP 工单已创建并发送：{task_id}「{title}」（{priority}）。"
                    f"TP 接手/完成时会通过 API 上报状态，PM 可在看板实时跟踪。"
                    f"注意：需电脑未锁屏，否则消息发送失败。")
        err = r.get("message") or r.get("error") or str(r)
        return f"⚠️ 任务 {task_id} 已创建，但消息发送失败：{err}。TP 可在看板查看任务。"
    except Exception as e:
        logger.warning("assign_task_to_tp failed: %s", e)
        return f"⚠️ 任务 {task_id} 已创建，但消息发送异常：{e}"


async def _assign_task_to_dp(agent, args: dict) -> str:
    """给 DP 发工单：创建任务看板记录 + 生成交接单文件 + 发消息到 DP 对话（含上报命令）"""
    import base64
    import json as _json
    from butler.store import task_store

    title = args.get("title", "").strip()
    desc = args.get("desc", "").strip()
    priority = args.get("priority", "P2")
    task_type = args.get("task_type", "需求")
    if not title or not desc:
        return "工单标题和描述不能为空"

    # 1. 创建任务看板记录
    try:
        task = task_store.create_task(
            title=title, description=desc, priority=priority,
            task_type=task_type, assignee="DP",
        )
        task_id = task["id"]
    except Exception as e:
        logger.warning("create task failed: %s", e)
        return f"❌ 创建任务记录失败：{e}"

    # 2. 生成交接单文件（文件名含任务 ID）
    report_cmd = f'python C:\\tools\\task_report.py {task_id} 进行中'
    complete_cmd = f'python C:\\tools\\task_report.py {task_id} 已完成 "完成说明"'

    payload = _json.dumps({
        "title": title,
        "desc": desc,
        "priority": priority,
        "type": task_type,
        "task_id": task_id,
        "report_cmd": report_cmd,
        "complete_cmd": complete_cmd,
    }, ensure_ascii=False)
    b64 = base64.b64encode(payload.encode("utf-8")).decode("ascii")

    dpk = getattr(agent, "deskpilot", None)
    if dpk is None:
        return f"DeskPilot 客户端未初始化，但任务 {task_id} 已创建。"

    try:
        # 生成交接单文件
        r = await dpk.system_run(
            r"C:\Users\lidicn\AppData\Local\Programs\Python\Python313\python.exe",
            [r"C:\tools\pm_send_to_dp.py", b64],
            timeout=15.0,
        )
        ok = r.get("ok", False) if isinstance(r, dict) else False

        # 3. 发消息到 DP 对话通知
        msg = (
            f"【PM 工单】{title}\n"
            f"任务ID：{task_id} | 优先级：{priority} | 类型：{task_type}\n"
            f"交接单已生成到 DeskPilot/docs/，请查看。\n"
            f"【状态上报】接手：{report_cmd}\n"
            f"完成：{complete_cmd}"
        )
        msg_b64 = base64.b64encode(msg.encode("utf-8")).decode("ascii")
        await dpk.system_run(
            r"C:\Users\lidicn\AppData\Local\Programs\Python\Python313\python.exe",
            [r"C:\tools\pm_send_to_dp_msg.py", msg_b64],
            timeout=30.0,
        )

        if ok:
            return (f"✅ DP 工单已创建：{task_id}「{title}」（{priority}/{task_type}）。"
                    f"交接单已生成 + DP 对话已通知。DP 接手/完成时通过 API 上报状态，PM 可在看板实时跟踪。")
        err = r.get("message") or r.get("error") or str(r)
        return f"⚠️ 任务 {task_id} 已创建，DP 已通知，但交接单生成可能失败：{err}"
    except Exception as e:
        logger.warning("assign_task_to_dp failed: %s", e)
        return f"⚠️ 任务 {task_id} 已创建，但执行异常：{e}"


async def _send_to_tp(agent, args: dict) -> str:
    """给 TP 发即时消息：通过 DeskPilot PM 管道（target=TP，Ctrl+2）"""
    message = args.get("message", "").strip()
    if not message:
        return "消息内容不能为空"
    ok, err = await _deskpilot_pm_send("TP", message)
    if ok:
        return "✅ 已向 TP 发送即时消息（DeskPilot PM 管道）。注意：需要电脑未锁屏。"
    return f"❌ TP 消息发送失败：{err}"

async def _send_to_dp(agent, args: dict) -> str:
    """给 DP 发即时消息：通过 DeskPilot PM 管道（target=DP，Ctrl+3）"""
    message = args.get("message", "").strip()
    if not message:
        return "消息内容不能为空"
    ok, err = await _deskpilot_pm_send("DP", message)
    if ok:
        return "✅ 已向 DP 发送即时消息（DeskPilot PM 管道）。注意：需要电脑未锁屏。"
