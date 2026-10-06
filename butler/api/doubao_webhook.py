"""豆包app对话同步 webhook（P0：手机豆包app ↔ butler 双向通信）。

doubao2api 轮询豆包网页端对话，发现新用户消息后 POST 到此端点。
管家收到消息后，只做一件事：检测是不是设备控制指令，如果是就执行。
不需要再调用 doubao2api（因为豆包自己会回复用户）。

简化链路：
用户发消息 → 豆包自己回复（在豆包app里）
    ↓
doubao2api轮询到用户消息 → webhook → 管家收到
    ↓
管家解析用户消息 → 如果是设备控制，执行HA调用
    ↓
不需要管家再调用doubao2api
"""

from __future__ import annotations

# 自愈机制：记录每个对话的设备格式错误次数
_format_error_count = {}       # conversation_id -> 连续错误次数
_last_format_error_reply = {}   # conversation_id -> 上次错误回复
_recent_msgs = {}               # 循环闸门：最近处理过的消息 hash

import asyncio
import hmac
import json
import re
import logging as _logging

# WO-DB-109: mask token=value in uvicorn access log (which logs full URL incl query)
class _UvicornAccessTokenMask:
    def filter(self, record):
        if record.args:
            record.args = tuple(
                re.sub(r'(token=)[^&\s"]+', r'\1<r>', a) if isinstance(a, str) else a
                for a in record.args
            )
        if isinstance(record.msg, str):
            record.msg = re.sub(r'(token=)[^&\s"]+', r'\1<r>', record.msg)
        return True
_logging.getLogger("uvicorn.access").addFilter(_UvicornAccessTokenMask())
import time
import httpx
from pathlib import Path

from starlette.requests import Request
from starlette.routing import Route
from starlette.responses import JSONResponse

from butler.logging_setup import get_logger
from butler.runtime import get_runtime
from butler.store import repo
from butler.notifier.app import is_self_push

logger = get_logger("butler.doubao_webhook")


def _conversation_to_role(conversation_id: str) -> str | None:
    """根据 conversation_id 反向查找角色 ID（读 role_state.json）。"""
    state_path = Path("/app/data/role_state.json")
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        for role_id, state in data.items():
            if state.get("conversation_id") == conversation_id:
                return role_id
    except Exception as e:
        logger.warning("read role_state failed: %s", e)
    return None


# 设备名 → HA entity_id 映射
DEVICE_MAP = {
    "显示器挂灯": "light.yeelink_cn_555003624_lamp22_s_2",  # 米家智能显示器挂灯1S
    "书房空调": "climate.lumi_cn_84159632_v2",
    "客厅主灯": "light.philips_cn_248631262_cbulb_s_2_light",  # 起居室灯泡
    "客厅电视": "media_player.xiaomi_rmh1_6103_play_control",
    "客厅风扇": "fan.dmaker_cn_245712731_p9_s_2_fan",
    "主卧室空调": "climate.lumi_cn_124561701_mcn02",
    "房间床头灯": "light.yeelink_cn_434411305_bslamp2_s_2_light",  # 卧室床头灯
}


def parse_device_command_from_reply(reply: str) -> list[dict]:
    """从豆包的回复里提取设备控制命令（支持多设备）
    
    格式："好的！【设备：显示器挂灯】【操作：关闭】【设备：客厅电视】【操作：换台】【频道：深圳卫视】"
    返回：[{"device": "显示器挂灯", ...}, {"device": "客厅电视", ...}]
    """
    reply = reply or ""
    commands = []
    
    # 提取所有【标签：值】的位置
    matches = list(re.finditer(r'【([^：]+)：([^】]+)】', reply))
    if not matches:
        return commands
    
    # 按设备分组：每遇到一个【设备：XXX】就开始一个新命令
    current_cmd = None
    
    for m in matches:
        label = m.group(1)
        value = m.group(2).strip()

        if label == "设备":
            # 新设备，保存上一个
            if current_cmd:
                commands.append(current_cmd)
            entity_id = DEVICE_MAP.get(value)
            if entity_id:
                current_cmd = {"device": value, "entity_id": entity_id}
            else:
                current_cmd = None
        elif current_cmd is not None:
            # 补充参数
            if label == "操作":
                # 兼容豆包自由发挥的操作值，比如"设定温度 26℃"、"设定制冷模式"
                # 1) 从操作值里提取温度
                temp_match = re.search(r'(\d+)\s*[℃度]', value)
                if temp_match and "温度" in value:
                    current_cmd["temperature"] = int(temp_match.group(1))
                    current_cmd["action"] = "设定温度"
                # 2) 从操作值里提取模式
                elif any(k in value for k in ["制冷", "制热", "送风", "自动模式"]):
                    mode_name = "制冷"
                    for k in ["制冷", "制热", "送风", "自动"]:
                        if k in value:
                            mode_name = k
                            break
                    current_cmd["mode"] = mode_name
                    current_cmd["action"] = "设模式"
                # 3) 从操作值里提取风速
                elif "风速" in value or "风量" in value:
                    speed = "自动"
                    for k in ["自动", "低", "中", "高"]:
                        if k in value:
                            speed = k
                            break
                    current_cmd["fan_mode"] = speed
                    current_cmd["action"] = "设风速"
                else:
                    current_cmd["action"] = value
            elif label == "模式":
                current_cmd["mode"] = value
            elif label == "温度":
                current_cmd["temperature"] = int(value)
            elif label == "频道":
                current_cmd["channel"] = value
            elif label == "风速":
                current_cmd["fan_mode"] = value
    
    # 保存最后一个命令
    if current_cmd:
        commands.append(current_cmd)
    
    # 给没有 action 的命令补上默认 action
    for cmd in commands:
        if "action" not in cmd:
            cmd["action"] = "打开"
    
    return commands


# v1.4 场景识别
SCENE_MAP = {
    "观影模式": "movie",
    "睡眠模式": "sleep",
    "晨起模式": "morning",
    "离家模式": "away",
}


def parse_scene_from_reply(reply: str) -> str | None:
    """从豆包回复里提取【场景：XXX】标签，返回场景ID。"""
    m = re.search(r'【场景：([^】]+)】', reply or "")
    if m:
        return SCENE_MAP.get(m.group(1).strip())
    return None


async def execute_scene(scene_id: str) -> None:
    """执行预置场景。"""
    from butler.api.scene_routes import SCENES, _execute_step
    scene = SCENES.get(scene_id)
    if not scene:
        logger.warning("unknown scene: %s", scene_id)
        return
    rt = get_runtime()
    import httpx
    async with httpx.AsyncClient(timeout=15) as client:
        for step in scene["steps"]:
            await _execute_step(client, rt, step)
    logger.info("scene executed: %s", scene_id)


# v1.4 设备状态查询
async def query_device_state(device_name: str) -> str:
    """从 HA 查设备状态，返回可读文本。"""
    entity_id = DEVICE_MAP.get(device_name)
    if not entity_id:
        return f"不认识设备：{device_name}"
    rt = get_runtime()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            headers = {"Authorization": f"Bearer {rt.settings.ha_token}"}
            r = await client.get(f"{rt.settings.ha_url}/api/states/{entity_id}", headers=headers)
            if r.status_code >= 400:
                return f"查询失败：{r.status_code}"
            data = r.json()
            state = data.get("state", "未知")
            attr = data.get("attributes", {})
            # 根据设备类型格式化
            if entity_id.startswith("climate."):
                temp = attr.get("current_temperature", "?")
                target = attr.get("temperature", "?")
                hvac = attr.get("hvac_action", state)
                return f"{device_name}：当前 {temp}°C，设定 {target}°C，状态 {hvac}"
            elif entity_id.startswith("light."):
                return f"{device_name}：{'开' if state == 'on' else '关'}"
            elif entity_id.startswith("media_player."):
                return f"{device_name}：{state}"
            elif entity_id.startswith("fan."):
                return f"{device_name}：{'开' if state == 'on' else '关'}"
            else:
                return f"{device_name}：{state}"
    except Exception as e:
        return f"查询出错：{e}"


def parse_query_from_reply(reply: str) -> str | None:
    """从豆包回复里提取【查询：XXX】标签。"""
    m = re.search(r'【查询：([^】]+)】', reply or "")
    if m:
        return m.group(1).strip()
    return None


# v2.0 页面生成：豆包写HTML，webhook存文件
def parse_page_from_reply(reply: str) -> tuple[str, str] | None:
    """从豆包回复里提取【页面：xxx】和HTML代码块。返回 (page_id, html)。"""
    if not reply:
        return None
    m = re.search(r'【页面：([^】]+)】', reply)
    if not m:
        return None
    page_id = m.group(1).strip()
    # 提取 ```html ... ``` 代码块
    html_match = re.search(r'```(?:html)?\s*\n(.*?)```', reply, re.DOTALL)
    if not html_match:
        return None
    html = html_match.group(1).strip()
    return (page_id, html)


def parse_camera_request(reply: str) -> str | None:
    """从豆包回复里提取【CAMERA_REQUEST:房间名】标签。返回房间名。"""
    m = re.search(r'【CAMERA_REQUEST[：:]\s*([^】]+)】', reply or "")
    if m:
        return m.group(1).strip()
    return None


async def save_user_page(page_id: str, html: str) -> str:
    """把用户页面写入静态目录。返回访问路径。"""
    import os
    safe_id = re.sub(r'[^a-zA-Z0-9_-]', '', page_id)
    if not safe_id:
        return ""
    static_dir = os.path.join(os.path.dirname(__file__), "..", "static", "user_pages")
    os.makedirs(static_dir, exist_ok=True)
    filepath = os.path.join(static_dir, f"{safe_id}.html")

    # S-02 修复：添加 CSP meta 标签，防止存储型 XSS
    # 限制只允许内联样式和图片，禁止脚本执行
    csp_meta = '<meta http-equiv="Content-Security-Policy" content="default-src \'self\'; style-src \'self\' \'unsafe-inline\'; img-src \'self\' data: https:; script-src \'none\'; frame-src \'none\'">'

    # 在 <head> 标签后面插入 CSP meta
    if '<head>' in html:
        html = html.replace('<head>', f'<head>\n  {csp_meta}', 1)
    elif '<html' in html:
        # 如果没有 head 标签，在 html 后面插入
        html = re.sub(r'(<html[^>]*>)', r'\1\n<head>\n  ' + csp_meta + '\n</head>', html, count=1)

    # V49：同目录临时件写完再 os.replace 登顶；就地截断写会让写一半的失败把已服务的页面留成半截
    tmp_path = filepath + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(html)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, filepath)
    except Exception:
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass
        raise
    logger.info("user page saved: %s (%d bytes)", safe_id, len(html))
    return f"/static/user_pages/{safe_id}.html"


class HACommandRejected(RuntimeError):
    """HA 服务调用回了非 2xx：设备没拧动，⛔ 记进成功账（P0-A／V45，批44 组2）。

    既有的 `except` 会把它当一次普通失败：warning ＋ success False ＋ 手机推送。
    """


def _raise_if_ha_failed(resp) -> None:
    if resp.status_code >= 400:
        raise HACommandRejected("http %s: %s" % (resp.status_code, str(resp.text)[:120]))


async def execute_device_command(params: dict) -> None:
    """执行设备控制命令"""
    device_name = params.get("device", "")
    action = params.get("action", "")
    entity_id = params.get("entity_id", "")
    
    rt = get_runtime()
    settings = rt.settings
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            headers = {"Authorization": f"Bearer {settings.ha_token}"}
            
            if action == "打开":
                if entity_id.startswith("light."):
                    service = "light/turn_on"
                    payload = {"entity_id": entity_id}
                elif entity_id.startswith("climate."):
                    service = "climate/set_hvac_mode"
                    # 空调打开需要传 hvac_mode
                    mode = params.get("mode", "制冷")
                    mode_map = {"制冷": "cool", "制热": "heat", "送风": "fan_only"}
                    payload = {"entity_id": entity_id, "hvac_mode": mode_map.get(mode, "cool")}
                elif entity_id.startswith("media_player."):
                    service = "media_player/turn_on"
                    payload = {"entity_id": entity_id}
                elif entity_id.startswith("fan."):
                    service = "fan/turn_on"
                    payload = {"entity_id": entity_id}
                else:
                    service = "homeassistant/turn_on"
                    payload = {"entity_id": entity_id}
                
                ha_resp = await client.post(
                    f"{settings.ha_url}/api/services/{service}",
                    json=payload,
                    headers=headers
                )
                _raise_if_ha_failed(ha_resp)
                
                # 如果是空调，还要设置温度
                if entity_id.startswith("climate."):
                    if "temperature" in params:
                        ha_resp = await client.post(
                            f"{settings.ha_url}/api/services/climate/set_temperature",
                            json={"entity_id": entity_id, "temperature": params["temperature"]},
                            headers=headers
                        )
                        _raise_if_ha_failed(ha_resp)
                
                logger.info("device ON: %s (%s)", device_name, entity_id)
            
            elif action == "关闭":
                if entity_id.startswith("light."):
                    service = "light/turn_off"
                elif entity_id.startswith("climate."):
                    service = "climate/set_hvac_mode"
                elif entity_id.startswith("media_player."):
                    service = "media_player/turn_off"
                elif entity_id.startswith("fan."):
                    service = "fan/turn_off"
                else:
                    service = "homeassistant/turn_off"
                
                ha_resp = await client.post(
                    f"{settings.ha_url}/api/services/{service}",
                    json={"entity_id": entity_id},
                    headers=headers
                )
                _raise_if_ha_failed(ha_resp)
                
                if entity_id.startswith("climate."):
                    ha_resp = await client.post(
                        f"{settings.ha_url}/api/services/climate/set_hvac_mode",
                        json={"entity_id": entity_id, "hvac_mode": "off"},
                        headers=headers
                    )
                    _raise_if_ha_failed(ha_resp)
                
                logger.info("device OFF: %s (%s)", device_name, entity_id)
            
            elif action == "换台":
                channel = params.get("channel", "")
                # 通过 MQTT 调用 zap-tv 换台
                rt = get_runtime()
                ok, result = await rt.tv.zap(channel)
                if ok:
                    logger.info("TV zap success: %s", channel)
                else:
                    logger.warning("TV zap failed: %s", result)

            elif action == "设定温度":
                temp = params.get("temperature")
                if temp and entity_id.startswith("climate."):
                    ha_resp = await client.post(
                        f"{settings.ha_url}/api/services/climate/set_temperature",
                        json={"entity_id": entity_id, "temperature": temp},
                        headers=headers
                    )
                    _raise_if_ha_failed(ha_resp)
                    logger.info("AC set temp: %s -> %s℃", device_name, temp)
                else:
                    logger.warning("set temp skipped: no temp or not climate (%s)", device_name)

            elif action == "设模式":
                mode = params.get("mode", "制冷")
                if entity_id.startswith("climate."):
                    mode_map = {"制冷": "cool", "制热": "heat", "送风": "fan_only", "自动": "auto"}
                    ha_resp = await client.post(
                        f"{settings.ha_url}/api/services/climate/set_hvac_mode",
                        json={"entity_id": entity_id, "hvac_mode": mode_map.get(mode, "cool")},
                        headers=headers
                    )
                    _raise_if_ha_failed(ha_resp)
                    logger.info("AC set mode: %s -> %s", device_name, mode)

            elif action == "设风速":
                fan_mode = params.get("fan_mode", "自动")
                if entity_id.startswith("climate."):
                    fan_map = {"自动": "auto", "低": "low", "中": "medium", "高": "high"}
                    ha_resp = await client.post(
                        f"{settings.ha_url}/api/services/climate/set_fan_mode",
                        json={"entity_id": entity_id, "fan_mode": fan_map.get(fan_mode, "auto")},
                        headers=headers
                    )
                    _raise_if_ha_failed(ha_resp)
                    logger.info("AC set fan: %s -> %s", device_name, fan_mode)

    except Exception as e:
        logger.warning("device command failed: %s", e)
        # 记录学习数据
        try:
            evo = getattr(rt, "self_evolution", None)
            if evo:
                evo.record_learning_data("device_command", {
                    "device": device_name, "action": action,
                    "success": False, "error": str(e)[:100]
                })
        except Exception:
            pass
        # 失败时 Bark 推送（成功不推，保持聊天记录干净）
        try:
            await rt.bark.push(body=f"{device_name} {action} 执行失败：{e}", title="设备控制失败")
        except Exception as push_err:
            # P2-9 ②类（批42 组3）：这里吞掉的正是**失败告警本身**⇒ 设备没动、手机永远不知道，⛔ 再无声
            logger.warning("bark push failed (device command): %s %s: %s", device_name, action, push_err)
    else:
        # 成功也记录
        try:
            evo = getattr(rt, "self_evolution", None)
            if evo:
                evo.record_learning_data("device_command", {
                    "device": device_name, "action": action,
                    "success": True
                })
        except Exception:
            pass


async def handle_webhook(payload: dict) -> dict:
    """处理 doubao2api 推送的用户消息。"""
    global _recent_msgs

    conversation_id = str(payload.get("conversation_id") or "")
    content = str(payload.get("content") or "").strip()
    if not conversation_id or not content:
        return {"ok": False, "error": "missing conversation_id or content"}

    # 过滤 system prompt 伪装的消息
    if content.startswith("[system]:") or content.startswith("你是「"):
        logger.info("webhook: skip system-like message: %s", content[:40])
        return {"ok": True, "skipped": "system_message"}

    # 循环闸门①（无状态）：管家自己的主动推送被 doubao2api 轮询回来时按标记丢弃。
    # _recent_msgs 那道内存 hash 门重启即失效、且只挡 5 分钟，挡不住重推；标记门与它并存。
    if is_self_push(content):
        logger.info("webhook: skip self-push echo: %s", content[:40])
        return {"ok": True, "skipped": "self_push_echo"}

    # 闸门：拦截我们自己发出去又被轮询回来的消息（防循环）
    import hashlib
    msg_hash = hashlib.md5(f"{conversation_id}:{content}".encode()).hexdigest()
    now = time.time()
    # 清理 5 分钟前的记录
    _recent_msgs = {k: v for k, v in _recent_msgs.items() if now - v < 300}
    if msg_hash in _recent_msgs:
        logger.info("webhook: skip duplicate (循环闸门): %s", content[:50])
        return {"ok": True, "skipped": "duplicate"}
    # R2-08 fix: 先标记 processing，成功后才标记 seen；失败则移除允许重试
    _recent_msgs[msg_hash] = now  # 临时占位（防并发），处理完刷新时间
    try:

        # 根据 conversation_id 找角色
        role_id = _conversation_to_role(conversation_id)
        # 兜底：顾安恒专属对话 ID 硬编码（role_state 可能没同步）
        GU_ANHENG_CID = "38440360274498562"
        if conversation_id == GU_ANHENG_CID:
            role_id = "gu_anheng"
        if not role_id:
            logger.info("webhook: conversation_id %s not mapped to any role", conversation_id)
            del _recent_msgs[msg_hash]  # R2-08: 处理失败，允许重试
            return {"ok": False, "error": "unknown_conversation", "conversation_id": conversation_id}

        logger.info("webhook: user msg from %s (role=%s): %s", conversation_id, role_id, content[:60])
        # R2-08: hash 已临时占位，处理成功则保留；下面任意 return 错误前需删 hash

        # 取角色
        rt = get_runtime()
        role = rt.roles.get(role_id)
        member = getattr(role, "member", "") or "手机用户"
        history = list(rt.dialog._role_history.get(role_id, []))

        # 持久化用户消息
        await asyncio.to_thread(
            repo.add_turn, "手机用户", "user", content,
            engine=role_id, source="doubao_app"
        )

        # ---- v1.4 对话内指令：不用开WebUI，直接在豆包APP里说 ----
        # 直接用当前 webhook 的 conversation_id，不用 role_state.json 里的（避免开新对话）
        content_lower = content.strip().lower()

        # 1. 刷新设备说明书
        if any(k in content_lower for k in ["刷新设备", "重新记住设备", "设备说明书", "重新投喂设备", "同步设备"]):
            from butler.api.memory_routes import DEVICE_MANUAL
            logger.info("cmd: refresh device manual (role=%s)", role_id)
            reply, _ = await rt.doubao.chat(
                DEVICE_MANUAL, keep_conversation=True, conversation_id=conversation_id, silent=True,
            )
            await rt.notifier.push(role_id, scene="设备说明书已刷新", direct_text=reply[:200])
            return {"ok": True, "handled": "refresh_device_manual"}

        # 2. 对账（直接发到当前对话）
        if any(k in content_lower for k in ["对账", "你还记得什么", "检查记忆", "记忆同步"]):
            logger.info("cmd: reconcile (role=%s)", role_id)
            ask_prompt = (
                "请列出你目前记住的关于这个家庭/这个人的所有信息，"
                "包括偏好、习惯、家庭成员、设备清单。逐条列出。"
            )
            reply, _ = await rt.doubao.chat(
                ask_prompt, keep_conversation=True, conversation_id=conversation_id, silent=True,
            )
            # 简单报告：把豆包的回复前300字推回去
            await rt.notifier.push(role_id, scene="对账结果", direct_text=reply[:500])
            return {"ok": True, "handled": "reconcile"}

        # 3. 记住XXX（直接发到当前对话）
        if content_lower.startswith("记住") or content_lower.startswith("请记住"):
            fact_text = re.sub(r'^(请)?记住[：:]?\s*', '', content).strip()
            if fact_text:
                logger.info("cmd: remember '%s' (role=%s)", fact_text[:50], role_id)
                reply, _ = await rt.doubao.chat(
                    f"请记住：{fact_text}", keep_conversation=True, conversation_id=conversation_id, silent=True,
                )
                await rt.notifier.push(role_id, scene="已记住", direct_text=reply[:200])
                return {"ok": True, "handled": "remember"}

        # 4. 忘掉XXX（直接发到当前对话）
        if content_lower.startswith("忘掉") or content_lower.startswith("忘记") or content_lower.startswith("不要记"):
            forget_text = re.sub(r'^(忘掉|忘记|不要记[住得]?[：:]?)\s*', '', content).strip()
            if forget_text:
                logger.info("cmd: forget '%s' (role=%s)", forget_text[:50], role_id)
                reply, _ = await rt.doubao.chat(
                    f"请忘记：{forget_text}。以后不要再记住这条信息。",
                    keep_conversation=True, conversation_id=conversation_id, silent=True,
                )
                await rt.notifier.push(role_id, scene="已忘记", direct_text=reply[:200])
                return {"ok": True, "handled": "forget"}

        # 调用 doubao2api，让豆包回复（按角色选择 system prompt）
        doubao_reply = None
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                # 按角色选择 system prompt
                if role_id == "gu_anheng":
                    system_prompt = """# 顾安恒 · 家庭安防专员

你是「顾安恒」，这个家庭的安防专员。你冷静、可靠、观察力敏锐。

## 你的职责
1. 当主人问你关于摄像头、监控、画面、房间情况时，你要先回复一句简短的话（如"好的，正在调取客厅画面"），然后在回复末尾必须加上标签：
   【CAMERA_REQUEST:房间名】
   房间名用：客厅、起居室、书房 等。

2. 示例：
   - 主人说"看看客厅摄像头" → 回复：好的，正在调取客厅画面。【CAMERA_REQUEST:客厅】
   - 主人说"起居室现在什么情况" → 回复：收到，马上看起居室。【CAMERA_REQUEST:起居室】
   - 主人说"书房有人吗" → 回复：好的，看看书房。【CAMERA_REQUEST:书房】

3. 如果主人只是聊天或问其他问题，正常回复即可，不要加标签。

4. 语气专业但不冷漠，像一个值得信赖的保安队长。"""
                else:
                    system_prompt = """# 我的智能家居设备清单 · 请存入长期记忆

下面是你可以控制的所有智能家居设备。请把这份清单作为关于你的「长期记忆」保存下来：以后大佬说"开一下空调"，你要能回忆起书房有空调、客厅有空调，根据上下文判断他指哪一台；不要因为换了对话就忘了有哪些设备。

## 已接入设备清单

### 【书房】
- 显示器挂灯：打开 / 关闭
- 书房空调：打开 / 关闭，设置模式（制冷/制热/送风），设置温度（16-30度）

### 【客厅】
- 客厅主灯：打开 / 关闭
- 客厅电视：打开 / 关闭 / 换台（频道名或频道号）
- 客厅风扇：打开 / 关闭

### 【主卧室】
- 主卧室空调：打开 / 关闭，设置模式，设置温度
- 房间床头灯：打开 / 关闭

## 你应该怎么使用这份长期记忆

1. **控制设备时必须用固定格式回复**，方便自动化系统解析并执行真实的 HA 调用：
   ```
   好的！【设备：{设备名}】【操作：{操作}】
   ```
   有附加参数时继续加标签：
   ```
   【模式：制冷】【温度：26】【频道：CCTV1】
   ```

2. **操作值只能用这些**：打开、关闭、换台、设定温度、设模式、设风速。
   温度必须用单独的【温度：26】标签，不要写在操作里。

3. **示例**：
   - 大佬说"打开显示器挂灯" → 回复：好的！【设备：显示器挂灯】【操作：打开】
   - 大佬说"书房空调制冷26度" → 回复：好的！【设备：书房空调】【操作：打开】【模式：制冷】【温度：26】
   - 大佬说"客厅电视换CCTV1" → 回复：好的！【设备：客厅电视】【操作：换台】【频道：CCTV1】
   - 大佬说"书房空调调到26度" → 回复：好的！【设备：书房空调】【操作：设定温度】【温度：26】

4. **如果大佬只是聊天/问问题**，直接正常回复，不要加【】标签。

5. **不要编造设备**，只控制上面列出的。如果大佬提到不在清单里的设备，直接说"抱歉，我还不认识这个设备"。

## 确认

请确认你已将这份设备清单记入长期记忆，并简要复述你记住了哪些房间、各有什么设备。"""

                # ---- 自愈：如果上一轮格式错误，追加纠正提示 ----
                err_count = _format_error_count.get(conversation_id, 0)
                if err_count > 0:
                    last_bad = _last_format_error_reply.get(conversation_id, "")
                    system_prompt += (
                        f"\n\n【重要纠正】上一次你回复的设备控制格式有误：\n"
                        f"上次回复：{last_bad}\n"
                        f"正确做法：温度用单独的【温度：26】标签，不要写在操作里。"
                        f"操作只能是：打开/关闭/换台/设定温度/设模式/设风速。"
                        f"请严格按格式回复。这是第{err_count}次提醒。"
                    )
                    logger.info("SELF-HEAL: 追加纠正提示 (第%d次)", err_count)

                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content}
                ]

                resp = await client.post(
                    rt.settings.doubao_api_url,
                    json={
                        "model": rt.settings.doubao_model,
                        "messages": messages,
                        "keep_conversation": False,
                    },
                    headers={"Authorization": f"Bearer {rt.settings.doubao_api_key}"}
                )
                resp.raise_for_status()
                data = resp.json()
                doubao_reply = data["choices"][0]["message"]["content"]
                logger.info("doubao2api reply: %s", doubao_reply[:80])
        except Exception as e:
            logger.warning("doubao2api call failed: %s", e)

        # 解析豆包的回复，提取设备控制命令
        device_cmds = parse_device_command_from_reply(doubao_reply or content)

        # ---- 自愈检测：豆包回复里有【设备】标签但格式不对 ----
        known_actions = {"打开", "关闭", "换台", "设定温度", "设模式", "设风速"}
        has_device_tag = "【设备" in (doubao_reply or "")
        format_error = False
        if has_device_tag:
            if not device_cmds:
                # 有【设备】标签但解析不出有效命令
                format_error = True
                logger.warning("SELF-HEAL: 豆包回复有设备标签但解析失败: %s", (doubao_reply or "")[:100])
            else:
                for cmd in device_cmds:
                    if cmd.get("action") not in known_actions:
                        format_error = True
                        logger.warning("SELF-HEAL: 豆包用了未知操作 '%s': %s",
                                       cmd.get("action"), (doubao_reply or "")[:100])
                        break

        # 记录格式错误到内存（下一轮对话时纠正）
        if format_error:
            _format_error_count[conversation_id] = _format_error_count.get(conversation_id, 0) + 1
            _last_format_error_reply[conversation_id] = (doubao_reply or "")[:200]
        else:
            # 成功了就清零
            if has_device_tag:
                _format_error_count[conversation_id] = 0

        if device_cmds:
            logger.info("device commands detected: %s", device_cmds)
            for cmd in device_cmds:
                await execute_device_command(cmd)
        else:
            # v1.4 检测【场景：XXX】标签
            scene_id = parse_scene_from_reply(doubao_reply or content)
            if scene_id:
                logger.info("scene tag detected: %s", scene_id)
                await execute_scene(scene_id)
            else:
                # v1.4 检测【查询：XXX】标签
                query_device = parse_query_from_reply(doubao_reply or content)
                if query_device:
                    logger.info("query tag detected: %s", query_device)
                    result_text = await query_device_state(query_device)
                    await rt.notifier.push(role_id, scene="设备查询结果", direct_text=result_text)
                else:
                    # v2.1 顾安恒安防：检测用户输入里的摄像头关键词，直接触发
                    # （不调 doubao2api 避免双重回复，豆包网页端自己会回复固定话术）
                    if role_id == "gu_anheng" and any(k in content for k in
                        ["摄像头", "监控", "看看", "画面", "起居室", "客厅", "书房", "房间里"]):
                        cam_room = None
                        for room_name in ["起居室", "客厅", "书房", "厨房"]:
                            if room_name in content:
                                cam_room = room_name
                                break
                        if not cam_room:
                            cam_room = "客厅"  # 默认客厅
                        logger.info("camera request from user input: %s", cam_room)
                        try:
                            stream_map = {"客厅": "cam_客厅", "起居室": "cam_小黄人",
                                          "书房": "cam_书房", "厨房": "cam_厨房"}
                            src = stream_map.get(cam_room, f"cam_{cam_room}")
                            from urllib.parse import quote
                            frame_url = f"http://192.168.2.200:1984/api/frame.jpeg?src={quote(src)}"
                            await rt.notifier.push_image(
                                role_id, frame_url,
                                prompt=f"我是安防专员顾安恒。这是{cam_room}的最新监控画面，"
                                       f"请简要汇报：是否有人、是否有异常、安全状况。100字以内。"
                            )
                        except Exception as e:
                            logger.warning("camera request failed: %s", e)
                        return {"ok": True, "handled": "camera_request", "room": cam_room}
                    else:
                        # v2.0 检测【页面：xxx】标签——豆包写HTML
                        page = parse_page_from_reply(doubao_reply or content)
                        if page:
                            page_id, html = page
                            url_path = await save_user_page(page_id, html)
                            logger.info("page saved: %s -> %s", page_id, url_path)
                            try:
                                await rt.bark.push(body=f"页面已生成：{page_id} → {url_path}", title="豆包管家")
                            except Exception as push_err:
                                # P2-9 ②类（批42 组3）：推送没送达＝页面好了而用户不知道，且盘上零痕迹
                                logger.warning("bark push failed (page generated): %s: %s", page_id, push_err)
                        else:
                            # 检测降级标记【调用LLM：XXX】
                            llm_match = re.search(r'【调用LLM[：:]\s*(.+?)】', doubao_reply or content)
                            if llm_match:
                                llm_question = llm_match.group(1)
                                logger.info("fallback to LLM: %s", llm_question[:50])
                                try:
                                    reply = await rt.dialog.agent.run(llm_question, member, history, system_override=role.system)
                                    await rt.notifier.push(role_id, scene="请回复用户", direct_text=reply)
                                except Exception as e:
                                    logger.warning("LLM fallback failed: %s", e)
                            else:
                                logger.info("no device command, just chat")

        # 写入对话记录到 chat_logs
        try:
            import json
            from butler.store.db import get_conn
            conn = get_conn()
            tools_called = [cmd.get("device", "") for cmd in device_cmds]
            conn.execute(
                """
            INSERT INTO chat_logs (ts, user_msg, assistant_reply, room, role, source, tools_called, success, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    time.time(),
                    content,
                    doubao_reply or "",
                    "",  # room
                    role_id,
                    "mobile_app",
                    json.dumps(tools_called, ensure_ascii=False),
                    1 if device_cmds else 0,
                    json.dumps({"conversation_id": conversation_id}, ensure_ascii=False),
                )
            )
            conn.commit()
        except Exception as e:
            logger.warning("chat_logs insert failed: %s", e)

        # 不需要回复用户（豆包自己会回复）
        return {
            "ok": True,
            "role": role_id,
            "conversation_id": conversation_id,
            "device_commands": len(device_cmds),
        }
    except BaseException:
        _recent_msgs.pop(msg_hash, None)
        raise


async def _webhook_handler(request: Request):
    # WO-DB-103：入口打点（早于任何分支/校验/解密），只记元数据不记正文
    client_ip = request.client.host if request.client else "unknown"
    query = request.url.query or ""
    raw_body = await request.body()
    body_bytes = len(raw_body)
    conv_id = ""
    try:
        _parsed = json.loads(raw_body) if raw_body else {}
        if isinstance(_parsed, dict):
            conv_id = str(_parsed.get("conversation_id", ""))[:40]
    except Exception:
        _parsed = {}
    # WO-DB-109: mask token value in query log (key stays, value masked)
    _masked_query = re.sub(r'(token=)[^&]+', r'\1<r>', query) if query else ""
    logger.info("webhook entry: ip=%s method=%s bytes=%d query=%s conv=%s",
                client_ip, request.method, body_bytes, _masked_query, conv_id or "-")

    # WO-ME-203：鉴权默认拒绝。原写法 `if webhook_token:` 是 fail-open ——
    # 未配置即任何人（内网/tailnet）可无凭证直达 handle_webhook() 下发设备指令。
    from butler.config import get_settings
    s = get_settings()
    webhook_token = getattr(s, "doubao_webhook_token", "") or ""
    if webhook_token:
        auth_hdr = request.headers.get("Authorization", "")
        x_token = request.headers.get("X-Webhook-Token", "")
        # WO-DB-109: third channel - ?token= query param (all 3 use compare_digest)
        q_token = request.query_params.get("token", "")
        if not (hmac.compare_digest(auth_hdr, f"Bearer {webhook_token}")
                or hmac.compare_digest(x_token, webhook_token)
                or hmac.compare_digest(q_token, webhook_token)):
            logger.info("webhook exit: branch=unauthorized status=401 conv=%s", conv_id or "-")
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    elif getattr(s, "doubao_webhook_allow_unauthenticated", False):
        logger.warning("WO-ME-203 调试模式：webhook 无鉴权配置但已显式放行，来源=%s", client_ip)
    else:
        logger.warning("WO-ME-203 webhook 无鉴权配置，按默认拒绝，来源=%s", client_ip)
        logger.info("webhook exit: branch=auth_unconfigured status=503 conv=%s", conv_id or "-")
        return JSONResponse({"ok": False, "error": "webhook_auth_not_configured"}, status_code=503)

    if not isinstance(_parsed, dict):
        logger.info("webhook exit: branch=invalid_json status=400 conv=%s", conv_id or "-")
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)

    try:
        result = await handle_webhook(_parsed)
    except Exception as e:
        logger.exception("webhook handler crashed: %s", e)
        result = {"ok": False, "error": str(e)}
    status = 200 if result.get("ok") else 400
    logger.info("webhook exit: branch=handled status=%d ok=%s conv=%s",
                status, result.get("ok"), conv_id or "-")
    return JSONResponse(result, status_code=status)


def routes():
    """返回 webhook 路由列表（供 app.py 装配）。"""
    return [
        Route("/api/doubao/webhook", _webhook_handler, methods=["POST"]),
    ]
