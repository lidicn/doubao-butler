"""薄 MCP Server 核心（v1.8 P0-7）。

实现 MCP 协议的 JSON-RPC 处理：
- initialize：协议握手，返回 Server 能力
- tools/list：返回可用工具列表（从 schema_gen 自动生成）
- tools/call：调用工具（适配到管家内部工具）
- resources/list：返回可用资源列表
- resources/read：读取资源内容

不实现：prompts、sampling、roots 等扩展（薄 MCP，后续版本按需添加）。
"""
from __future__ import annotations

import json
import time
from typing import Any

from butler.logging_setup import get_logger
from butler.mcp.schema_gen import get_tools, get_resources, get_tool_names
from butler.triggers.schema import validate_trigger
from butler.runtime import get_runtime

logger = get_logger("butler.mcp.server")

# MCP 协议版本
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "doubao-butler"
SERVER_VERSION = "1.8.0"


class MCPServer:
    """薄 MCP Server。"""

    def __init__(self):
        self._initialized = False
        self._tool_names = get_tool_names()

    async def handle_request(self, request: dict) -> dict:
        """处理 JSON-RPC 请求，返回 JSON-RPC 响应。"""
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        try:
            if method == "initialize":
                return self._response(req_id, self._handle_initialize(params))
            elif method == "notifications/initialized":
                self._initialized = True
                return None  # notification 不需要响应
            elif method == "tools/list":
                return self._response(req_id, {"tools": get_tools()})
            elif method == "tools/call":
                return self._response(req_id, await self._handle_tool_call(params))
            elif method == "resources/list":
                return self._response(req_id, {"resources": get_resources()})
            elif method == "resources/read":
                return self._response(req_id, self._handle_resource_read(params))
            elif method == "ping":
                return self._response(req_id, {})
            else:
                return self._error(req_id, -32601, f"Method not found: {method}")
        except Exception as e:
            logger.error("MCP request handler error: %s", e, exc_info=True)
            return self._error(req_id, -32603, f"Internal error: {str(e)[:200]}")

    def _handle_initialize(self, params: dict) -> dict:
        """处理 initialize 握手。"""
        client_version = params.get("protocolVersion", "")
        if client_version and client_version != MCP_PROTOCOL_VERSION:
            logger.warning("MCP client protocol version mismatch: client=%s server=%s",
                           client_version, MCP_PROTOCOL_VERSION)

        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
                # 薄 MCP 不实现 prompts/sampling/roots
            },
            "serverInfo": {
                "name": SERVER_NAME,
                "version": SERVER_VERSION,
            },
        }

    async def _handle_tool_call(self, params: dict) -> dict:
        """处理 tools/call，适配到管家内部工具。"""
        name = params.get("name", "")
        arguments = params.get("arguments", {})

        if name not in self._tool_names:
            return {
                "content": [{"type": "text", "text": f"Error: unknown tool '{name}'"}],
                "isError": True,
            }

        # 记录 MCP 调用审计
        rt = get_runtime()
        self._audit_mcp_call(rt, name, arguments)

        # 分发到具体工具适配器
        try:
            if name == "create_trigger":
                result = self._tool_create_trigger(rt, arguments)
            elif name == "list_triggers":
                result = self._tool_list_triggers(rt, arguments)
            elif name == "update_trigger":
                result = self._tool_update_trigger(rt, arguments)
            elif name == "delete_trigger":
                result = self._tool_delete_trigger(rt, arguments)
            elif name == "run_skill":
                result = await self._tool_run_skill(rt, arguments)
            elif name == "send_bark":
                result = await self._tool_send_bark(rt, arguments)
            elif name == "list_skills":
                result = self._tool_list_skills(rt, arguments)
            elif name == "create_skill":
                result = self._tool_create_skill(rt, arguments)
            elif name == "delete_skill":
                result = self._tool_delete_skill(rt, arguments)
            elif name == "get_skill_health":
                result = self._tool_get_skill_health(rt, arguments)
            elif name == "list_cron_apis":
                result = self._tool_list_cron_apis(rt, arguments)
            else:
                result = {"content": [{"type": "text", "text": f"Tool '{name}' not implemented"}], "isError": True}

            return result
        except Exception as e:
            logger.error("MCP tool call error: %s - %s", name, e, exc_info=True)
            return {
                "content": [{"type": "text", "text": f"Tool execution error: {str(e)[:200]}"}],
                "isError": True,
            }

    def _handle_resource_read(self, params: dict) -> dict:
        """处理 resources/read。"""
        uri = params.get("uri", "")
        rt = get_runtime()

        try:
            if uri == "butler://triggers":
                trigger_engine = getattr(rt, "trigger_engine", None)
                if trigger_engine and hasattr(trigger_engine, "store"):
                    store = trigger_engine.store
                    if hasattr(store, "list_all"):
                        triggers = store.list_all()
                    elif hasattr(store, "list"):
                        triggers = store.list()
                    else:
                        triggers = []
                    content = json.dumps([self._serialize_trigger(t, engine=trigger_engine) for t in triggers], ensure_ascii=False)
                else:
                    content = "[]"
                return {"contents": [{"uri": uri, "mimeType": "application/json", "text": content}]}

            elif uri == "butler://skills":
                runner = getattr(rt, "runner", None)
                if runner and hasattr(runner, "store"):
                    store = runner.store
                    if hasattr(store, "list_all"):
                        skills = store.list_all()
                    elif hasattr(store, "list"):
                        skills = store.list()
                    else:
                        skills = []
                    content = json.dumps([self._serialize_skill(s) for s in skills], ensure_ascii=False)
                else:
                    content = "[]"
                return {"contents": [{"uri": uri, "mimeType": "application/json", "text": content}]}

            elif uri == "butler://guard/status":
                pg = getattr(rt, "push_guard", None)
                if pg and hasattr(pg, "get_status"):
                    status = pg.get_status()
                    content = json.dumps(status, ensure_ascii=False)
                else:
                    content = "{}"
                return {"contents": [{"uri": uri, "mimeType": "application/json", "text": content}]}

            else:
                return {"contents": [], "isError": True, "error": {"code": -32602, "message": f"Unknown resource: {uri}"}}

        except Exception as e:
            logger.error("MCP resource read error: %s - %s", uri, e, exc_info=True)
            return {"contents": [], "isError": True, "error": {"code": -32603, "message": str(e)[:200]}}

    # ============================================================
    # 工具适配器（把 MCP 调用适配到管家内部工具）
    # ============================================================

    def _tool_create_trigger(self, rt, args: dict) -> dict:
        """create_trigger 适配器。"""
        trigger_json = args.get("trigger_json", {})
        confirm = args.get("confirm", False)

        trigger_engine = getattr(rt, "trigger_engine", None)
        if not trigger_engine:
            return self._text_result("Error: trigger_engine not available", is_error=True)

        # 风险判定：动作只含只读/通知类→低风险；含设备控制/模式切换→高风险
        risk_level = self._assess_trigger_risk(trigger_json)
        if risk_level == "high" and not confirm:
            return self._text_result(
                f"高风险触发规则（含设备控制/模式切换），需 confirm=true 才会生效。"
                f"规则定义：{json.dumps(trigger_json, ensure_ascii=False)[:300]}",
                is_error=False,
                extra={"created": False, "need_confirm": True, "risk_level": "high"},
            )

        # 创建触发规则
        try:
            store = getattr(trigger_engine, "store", None)
            if store and hasattr(store, "save"):
                # 确保 trigger 有 id
                if "id" not in trigger_json:
                    import uuid
                    trigger_json["id"] = "trig_" + uuid.uuid4().hex[:8]
                trigger_json.setdefault("priority", 50)
                trigger_json.setdefault("enabled", True)
                normalized, verr = validate_trigger(trigger_json)
                if verr:
                    return self._text_result(
                        f"触发规则未创建，定义不符合 schema：{verr}",
                        is_error=True,
                        extra={"created": False, "validation_error": verr},
                    )
                store.save(normalized)
                trigger_id = normalized["id"]
            elif hasattr(trigger_engine, "create_rule"):
                rule = trigger_engine.create_rule(trigger_json)
                trigger_id = rule.get("id", "") if isinstance(rule, dict) else str(rule)
            else:
                return self._text_result("Error: trigger creation not supported", is_error=True)

            return self._text_result(
                f"触发规则已创建（ID: {trigger_id}，风险等级: {risk_level}）",
                extra={"created": True, "trigger_id": trigger_id, "risk_level": risk_level},
            )
        except Exception as e:
            return self._text_result(f"创建触发规则失败: {str(e)[:200]}", is_error=True)

    def _tool_list_triggers(self, rt, args: dict) -> dict:
        """list_triggers 适配器。"""
        trigger_engine = getattr(rt, "trigger_engine", None)
        if not trigger_engine or not hasattr(trigger_engine, "store"):
            return self._text_result("[]", extra={"triggers": []})

        store = trigger_engine.store
        if hasattr(store, "list_all"):
            triggers = store.list_all()
        elif hasattr(store, "list"):
            triggers = store.list()
        else:
            triggers = []

        # 过滤
        event = args.get("event")
        enabled = args.get("enabled")
        if event:
            triggers = [t for t in triggers if t.get("event") == event]
        if enabled is not None:
            triggers = [t for t in triggers if t.get("enabled", True) == enabled]

        serialized = [self._serialize_trigger(t, engine=trigger_engine) for t in triggers]
        # risk_level 此前只在 schema 里向客户端声明、这里从不读取：传 high 也返回全量。
        # 风险等级是 server 侧算出来的（触发规则本身不带该字段），所以过滤挂在序列化之后。
        risk_level = args.get("risk_level")
        if risk_level in ("low", "high"):
            serialized = [t for t in serialized if t.get("risk_level") == risk_level]
        return self._text_result(
            f"共 {len(serialized)} 条触发规则",
            extra={"triggers": serialized, "count": len(serialized)},
        )

    def _tool_update_trigger(self, rt, args: dict) -> dict:
        """update_trigger 适配器。"""
        trigger_id = args.get("trigger_id", "")
        trigger_json = args.get("trigger_json", {})
        confirm = args.get("confirm", False)

        risk_level = self._assess_trigger_risk(trigger_json)
        if risk_level == "high" and not confirm:
            return self._text_result(
                f"高风险更新（修改动作/条件），需 confirm=true。",
                extra={"updated": False, "need_confirm": True, "risk_level": "high"},
            )

        trigger_engine = getattr(rt, "trigger_engine", None)
        if not trigger_engine or not hasattr(trigger_engine, "store"):
            return self._text_result("Error: trigger_engine not available", is_error=True)

        try:
            store = trigger_engine.store
            if hasattr(store, "update"):
                store.update(trigger_id, trigger_json)
            elif hasattr(store, "set"):
                store.set(trigger_id, trigger_json)
            else:
                return self._text_result("Error: trigger update not supported", is_error=True)

            return self._text_result(
                f"触发规则 {trigger_id} 已更新（风险等级: {risk_level}）",
                extra={"updated": True, "trigger_id": trigger_id, "risk_level": risk_level},
            )
        except Exception as e:
            return self._text_result(f"更新触发规则失败: {str(e)[:200]}", is_error=True)

    def _tool_delete_trigger(self, rt, args: dict) -> dict:
        """delete_trigger 适配器（必须 confirm=true，Server 侧硬编码）。"""
        trigger_id = args.get("trigger_id", "")
        confirm = args.get("confirm", False)

        # Server 侧硬编码：删除操作必须 confirm=true
        if not confirm:
            return self._text_result(
                f"删除触发规则是高风险操作，必须 confirm=true。规则 ID: {trigger_id}",
                extra={"deleted": False, "need_confirm": True},
            )

        trigger_engine = getattr(rt, "trigger_engine", None)
        if not trigger_engine or not hasattr(trigger_engine, "store"):
            return self._text_result("Error: trigger_engine not available", is_error=True)

        try:
            store = trigger_engine.store
            if hasattr(store, "delete"):
                store.delete(trigger_id)
            elif hasattr(store, "remove"):
                store.remove(trigger_id)
            else:
                return self._text_result("Error: trigger delete not supported", is_error=True)

            return self._text_result(
                f"触发规则 {trigger_id} 已删除",
                extra={"deleted": True, "trigger_id": trigger_id},
            )
        except Exception as e:
            return self._text_result(f"删除触发规则失败: {str(e)[:200]}", is_error=True)

    async def _tool_run_skill(self, rt, args: dict) -> dict:
        """run_skill 适配器（白名单内技能）。"""
        skill_id = args.get("skill_id", "")
        params = args.get("params", {})
        dry_run = bool(args.get("dry_run", False))

        runner = getattr(rt, "runner", None)
        if not runner:
            return self._text_result("Error: skill runner not available", is_error=True)

        # 检查技能是否在白名单内（薄 MCP 只允许查询/分析/通知类）
        MCP_SKILL_WHITELIST = {
            # 查询类
            "query_device", "ha_query", "get_weather", "check_schedule",
            # 分析类
            "analyze_usage", "device_inspection", "anomaly_report",
            # 通知类
            "send_notification", "send_bark_skill",
        }

        # N4: 硬白名单——不在白名单一律拒绝（原软关键词检查可被绕过）
        if skill_id not in MCP_SKILL_WHITELIST:
            return self._text_result(
                f"技能 '{skill_id}' 不在 MCP 白名单中，不允许直接调用。"
                f"白名单仅包含查询/分析/通知类技能。设备控制类请通过管家对话或 create_trigger 间接触发。",
                is_error=True,
            )

        # 执行技能
        try:
            if hasattr(runner, "run"):
                # 原写法 runner.run(skill_id, params) 把 params 塞进了第二个形参 source，
                # 技能侧 payload 恒空——一并纠正为具名传参（DCD 裁定 C 只加了 dry_run，这条是顺带修）
                result = await self._run_coro(runner.run(
                    skill_id, source="mcp", payload=params or {}, dry_run=dry_run))
            elif hasattr(runner, "execute"):
                result = await self._run_coro(runner.execute(skill_id, params or {}))
            else:
                return self._text_result("Error: skill execution not supported", is_error=True)

            result_str = json.dumps(result, ensure_ascii=False, default=str)[:500] if result else "执行完成"
            return self._text_result(
                f"技能 '{skill_id}' {'模拟完成' if dry_run else '执行完成'}: {result_str}",
                extra={"skill_id": skill_id, "result": result},
            )
        except Exception as e:
            return self._text_result(f"技能执行失败: {str(e)[:200]}", is_error=True)

    async def _run_coro(self, coro):
        """在调用方事件循环上直接 await。另起线程跑会让 butler 侧的锁与 Future 跨循环，
        唤醒送不到；且线程池退出时会 join 卡死的 worker，把调用方一起拖停。"""
        return await coro

    async def _tool_send_bark(self, rt, args: dict) -> dict:
        """send_bark 适配器（自动走 PushGuard 风控）。"""
        title = args.get("title", "")
        body = args.get("body", "")
        priority = args.get("priority", "info")
        group = args.get("group", "")

        bark = getattr(rt, "bark", None)
        if not bark:
            return self._text_result("Error: bark not available", is_error=True)

        try:
            # Bark.push() 已集成 PushGuard 风控（v1.8 P0-1）
            if hasattr(bark, "push"):
                result = await self._run_coro(bark.push(title=title, body=body, priority=priority, group=group or None))
            else:
                return self._text_result("Error: bark.push not available", is_error=True)

            # Bark.push() 返回 bool（True=HTTP 200），不是 dict。
            # 此前"非 dict 即 sent"，推送失败也会被 MCP 报成"已发送"。
            if isinstance(result, dict):
                decision = result.get("decision", "unknown")
                reason = result.get("reason", "")
            elif result is False:
                decision = "failed"
                reason = "bark.push() returned False (http error / not configured / guard)"
            else:
                decision = "sent"
                reason = ""

            if decision == "drop":
                return self._text_result(
                    f"Bark 推送被风控熔断丢弃（{reason}）。请降低推送频率。",
                    extra={"sent": False, "decision": decision, "reason": reason},
                )
            elif decision == "merge":
                return self._text_result(
                    f"Bark 推送已合并到缓存（{reason}），将批量推送。",
                    extra={"sent": False, "decision": decision, "reason": reason},
                )
            elif decision == "failed":
                return self._text_result(
                    f"Bark 推送未送达（{reason}）。请检查 BARK_URL/BARK_KEY 与网络。",
                    extra={"sent": False, "decision": decision, "reason": reason},
                )
            else:
                return self._text_result(
                    f"Bark 推送已发送（优先级: {priority}）",
                    extra={"sent": True, "decision": decision},
                )
        except Exception as e:
            return self._text_result(f"Bark 推送失败: {str(e)[:200]}", is_error=True)

    # ============================================================
    # 辅助方法
    # ============================================================

    def _assess_trigger_risk(self, trigger_json: dict) -> str:
        """评估触发规则风险等级（Server 侧硬编码，不由 LLM 判断）。"""
        actions = trigger_json.get("actions", [])
        if isinstance(actions, dict):
            actions = [actions]

        high_risk_keywords = [
            "device_action", "control_device", "switch_mode", "set_mode",
            "turn_on", "turn_off", "set_volume", "play_music", "open_app",
            "设备控制", "模式切换", "开关",
        ]

        for action in actions:
            action_str = json.dumps(action, ensure_ascii=False).lower()
            if any(kw.lower() in action_str for kw in high_risk_keywords):
                return "high"

        return "low"

    def _serialize_trigger(self, trigger: dict, engine=None) -> dict:
        """序列化触发规则（只返回安全字段）。"""
        if not isinstance(trigger, dict):
            return {"id": str(trigger)}
        # N3: last_run_at 从引擎 _last_fired 查（key 格式: id 或 id:member）
        last_run = 0
        if engine is not None and hasattr(engine, "_last_fired"):
            tid = trigger.get("id", "")
            # 找所有以 tid: 开头的 key，取最新
            for k, v in engine._last_fired.items():
                if k == tid or k.startswith(tid + ":"):
                    if v > last_run:
                        last_run = v
        return {
            "id": trigger.get("id", ""),
            "name": trigger.get("name", ""),
            "event": trigger.get("event", ""),
            "conditions": trigger.get("conditions", {}),
            "actions": trigger.get("actions", []),
            "enabled": trigger.get("enabled", True),
            "last_run_at": last_run,
            # 消费端（MA/豆包）按这几个字段做展示与判断，此前全部缺失：
            # risk_level 由 server 侧硬编码评估，不是存储字段。
            "risk_level": self._assess_trigger_risk(trigger),
            "priority": trigger.get("priority", 0),
            "cooldown_sec": trigger.get("cooldown_sec", 0),
            "updated_at": trigger.get("updated_at", 0),
            # 存储里 13/13 没有 created_at（真名 updated_at），保留该键只为向后兼容，
            # 值恒为 0；请读 updated_at。
            "created_at": trigger.get("created_at", 0),
        }

    def _serialize_skill(self, skill: dict) -> dict:
        """序列化技能（只返回安全字段，不返回敏感配置）。"""
        if not isinstance(skill, dict):
            return {"id": str(skill)}
        return {
            "id": skill.get("id", ""),
            "name": skill.get("name", ""),
            "description": skill.get("description", "")[:200],
            "version": skill.get("version", "1.0"),
            "enabled": skill.get("enabled", True),
            "category": skill.get("category", ""),
        }

    def _audit_mcp_call(self, rt, tool_name: str, arguments: dict) -> None:
        """记录 MCP 调用审计（复用管家现有审计体系）。"""
        try:
            # 记录到触发审计（source_type=mcp）
            registry = getattr(rt, "trigger_registry", None)
            if registry and registry.auditor:
                registry.auditor.record(
                    source_id=f"mcp:{tool_name}",
                    source_type="mcp",
                    name=f"MCP Tool: {tool_name}",
                    result="success",
                    payload={"tool": tool_name, "arguments_keys": list(arguments.keys())},
                )
        except Exception as e:
            logger.debug("MCP audit record failed: %s", e)

    # ===== v2.0 技能工厂工具 =====

    def _tool_list_skills(self, rt, args: dict) -> dict:
        """list_skills 适配器。"""
        runner = getattr(rt, "runner", None)
        if not runner or not hasattr(runner, "store"):
            return self._text_result("技能存储未就绪", is_error=True)
        skills = runner.store.list_all() if hasattr(runner.store, "list_all") else runner.store.list()
        engine_filter = args.get("engine")
        enabled_filter = args.get("enabled")
        result = []
        for s in skills:
            brain = s.get("brain", {})
            engine = brain.get("engine", "")
            if engine_filter and engine != engine_filter:
                continue
            if enabled_filter is not None and s.get("enabled", True) != enabled_filter:
                continue
            result.append({
                "id": s.get("id"),
                "name": s.get("name"),
                "engine": engine,
                "enabled": s.get("enabled", True),
                "trigger": s.get("trigger", {}),
            })
        return self._text_result(json.dumps(result, ensure_ascii=False), extra={"skills": result})

    def _tool_create_skill(self, rt, args: dict) -> dict:
        """create_skill 适配器。"""
        skill_json = args.get("skill_json", {})
        confirm = args.get("confirm", False)
        creator = getattr(rt, "skill_creator", None)
        if not creator:
            return self._text_result("技能生成器未就绪", is_error=True)
        result = creator.create_draft(skill_json, role_id="mcp")
        if not result.get("ok"):
            return self._text_result(f"创建失败: {result.get('error')}", is_error=True)
        if confirm:
            confirm_result = creator.confirm(role_id="mcp")
            if confirm_result.get("ok"):
                return self._text_result(f"技能已创建并启用: {skill_json.get('id')}",
                                         extra={"skill": confirm_result.get("skill")})
            return self._text_result(f"草稿已创建但启用失败: {confirm_result.get('error')}", is_error=True)
        return self._text_result(f"技能草稿已创建，待确认: {result.get('preview', '')}",
                                 extra={"draft": result.get("draft")})

    def _tool_delete_skill(self, rt, args: dict) -> dict:
        """delete_skill 适配器。"""
        skill_id = args.get("skill_id", "")
        confirm = args.get("confirm", False)
        if not confirm:
            return self._text_result("删除技能需要 confirm=true", is_error=True)
        runner = getattr(rt, "runner", None)
        if not runner or not hasattr(runner, "store"):
            return self._text_result("技能存储未就绪", is_error=True)
        try:
            runner.store.delete(skill_id)
            return self._text_result(f"技能已删除: {skill_id}")
        except Exception as e:
            return self._text_result(f"删除失败: {e}", is_error=True)

    def _tool_get_skill_health(self, rt, args: dict) -> dict:
        """get_skill_health 适配器。"""
        executor = getattr(rt, "cron_task_executor", None)
        if not executor:
            return self._text_result("cron_task 执行器未就绪", is_error=True)
        health = executor.get_task_health()
        skill_id = args.get("skill_id")
        if skill_id:
            health = [h for h in health if h.get("skill_id") == skill_id]
        return self._text_result(json.dumps(health, ensure_ascii=False), extra={"health": health})

    def _tool_list_cron_apis(self, rt, args: dict) -> dict:
        """list_cron_apis 适配器。"""
        executor = getattr(rt, "cron_task_executor", None)
        if not executor:
            return self._text_result("cron_task 执行器未就绪", is_error=True)
        apis = executor.list_apis()
        return self._text_result(json.dumps(apis, ensure_ascii=False), extra={"apis": apis})

    def _text_result(self, text: str, is_error: bool = False, extra: dict | None = None) -> dict:
        """构造 MCP 工具调用结果。"""
        result = {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        }
        if extra:
            # MCP 协议不支持额外字段，但可以放在 structuredContent 中
            result["structuredContent"] = extra
        return result

    def _response(self, req_id, result: dict) -> dict:
        """构造 JSON-RPC 成功响应。"""
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def _error(self, req_id, code: int, message: str) -> dict:
        """构造 JSON-RPC 错误响应。"""
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message},
        }
