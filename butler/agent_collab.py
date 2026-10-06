"""多Agent协作管理器（v1.9）。

实现角色Agent之间的：
1. 能力注册与发现：每个Agent注册自己的能力（技能、工具、房间范围）
2. 消息传递：Agent之间可以发送消息（请求/响应/通知）
3. 任务分发：主Agent可以把任务分发给专业Agent
4. 协作上下文：共享对话上下文，避免重复描述
5. 角色路由：根据房间/成员/技能自动路由到对应Agent
"""
from __future__ import annotations

import json
import time
import asyncio
from pathlib import Path
from collections import defaultdict, deque
from typing import Any, Callable

from butler.logging_setup import get_logger

logger = get_logger("butler.agent_collab")


class AgentMessage:
    """Agent间消息。"""
    def __init__(self, sender: str, receiver: str, msg_type: str,
                 content: str, context: dict | None = None,
                 requires_response: bool = False, timeout_sec: int = 30):
        self.id = f"msg_{int(time.time()*1000)}_{sender}"
        self.sender = sender
        self.receiver = receiver
        self.msg_type = msg_type  # request / response / notify / task
        self.content = content
        self.context = context or {}
        self.requires_response = requires_response
        self.timeout_sec = timeout_sec
        self.created_at = time.time()
        self.response = None
        self.responded_at = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "sender": self.sender,
            "receiver": self.receiver,
            "msg_type": self.msg_type,
            "content": self.content,
            "context": self.context,
            "requires_response": self.requires_response,
            "created_at": self.created_at,
            "response": self.response,
            "responded_at": self.responded_at,
        }


class AgentCollaborationManager:
    """多Agent协作管理器。"""

    def __init__(self, data_dir: str = "/app/data"):
        self.data_dir = Path(data_dir) / "agent_collab"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Agent能力注册表
        self._capabilities: dict[str, dict] = {}
        # 消息队列（每个Agent一个收件箱）
        self._inboxes: dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
        # 待响应的请求
        self._pending_requests: dict[str, AgentMessage] = {}
        # 请求响应事件（delegate_task 用 asyncio.Event 真等待，而非 sleep 轮询）
        self._response_events: dict[str, asyncio.Event] = {}
        # 消息历史
        self._history: deque = deque(maxlen=500)
        # Agent状态
        self._agent_status: dict[str, dict] = {}
        # 消息处理器
        self._handlers: dict[str, Callable] = {}

    def register_agent(self, agent_id: str, name: str, role: str,
                        capabilities: list[str], rooms: list[str] | None = None,
                        members: list[str] | None = None,
                        description: str = "") -> dict:
        """注册Agent能力。"""
        self._capabilities[agent_id] = {
            "agent_id": agent_id,
            "name": name,
            "role": role,
            "capabilities": capabilities,
            "rooms": rooms or [],
            "members": members or [],
            "description": description,
            "registered_at": time.time(),
            "last_heartbeat": time.time(),
        }
        self._agent_status[agent_id] = {
            "status": "online",
            "last_active": time.time(),
            "tasks_completed": 0,
            "tasks_failed": 0,
        }
        logger.info("agent registered: %s (%s) capabilities=%s", agent_id, name, capabilities)
        return self._capabilities[agent_id]

    def unregister_agent(self, agent_id: str) -> None:
        """注销Agent。"""
        self._capabilities.pop(agent_id, None)
        self._agent_status.pop(agent_id, None)
        logger.info("agent unregistered: %s", agent_id)

    def heartbeat(self, agent_id: str) -> None:
        """Agent心跳。"""
        if agent_id in self._capabilities:
            self._capabilities[agent_id]["last_heartbeat"] = time.time()
        if agent_id in self._agent_status:
            self._agent_status[agent_id]["last_active"] = time.time()

    def list_agents(self) -> list[dict]:
        """列出所有已注册Agent。"""
        return list(self._capabilities.values())

    def get_agent(self, agent_id: str) -> dict | None:
        """获取Agent信息。"""
        return self._capabilities.get(agent_id)

    def find_agent_by_capability(self, capability: str) -> list[dict]:
        """根据能力查找Agent。"""
        return [a for a in self._capabilities.values()
                if capability in a.get("capabilities", [])]

    def find_agent_by_room(self, room: str) -> list[dict]:
        """根据房间查找Agent。"""
        return [a for a in self._capabilities.values()
                if room in a.get("rooms", []) or not a.get("rooms")]

    def find_agent_by_member(self, member: str) -> list[dict]:
        """根据成员查找Agent。"""
        return [a for a in self._capabilities.values()
                if member in a.get("members", []) or not a.get("members")]

    def route_task(self, task: str, room: str | None = None,
                    member: str | None = None,
                    required_capabilities: list[str] | None = None) -> dict | None:
        """智能路由：根据任务描述和上下文选择最合适的Agent。"""
        candidates = list(self._capabilities.values())

        # 按房间过滤
        if room:
            room_matches = [a for a in candidates if room in a.get("rooms", [])]
            if room_matches:
                candidates = room_matches

        # 按成员过滤
        if member:
            member_matches = [a for a in candidates if member in a.get("members", [])]
            if member_matches:
                candidates = member_matches

        # 按能力过滤
        if required_capabilities:
            cap_matches = [a for a in candidates
                           if all(c in a.get("capabilities", []) for c in required_capabilities)]
            if cap_matches:
                candidates = cap_matches

        if not candidates:
            # 回退到 butler（总管）
            return self._capabilities.get("butler")

        # 简单评分：能力匹配度 + 房间匹配 + 成员匹配
        def score(a):
            s = 0
            if required_capabilities:
                s += sum(1 for c in required_capabilities if c in a.get("capabilities", [])) * 10
            if room and room in a.get("rooms", []):
                s += 5
            if member and member in a.get("members", []):
                s += 5
            return s

        candidates.sort(key=score, reverse=True)
        return candidates[0]

    async def send_message(self, sender: str, receiver: str, msg_type: str,
                            content: str, context: dict | None = None,
                            requires_response: bool = False,
                            timeout_sec: int = 30) -> AgentMessage:
        """发送消息给另一个Agent。"""
        msg = AgentMessage(sender, receiver, msg_type, content, context,
                           requires_response, timeout_sec)
        self._inboxes[receiver].append(msg)
        self._history.append(msg.to_dict())

        if requires_response:
            self._pending_requests[msg.id] = msg
            self._response_events[msg.id] = asyncio.Event()

        logger.info("message sent: %s -> %s type=%s", sender, receiver, msg_type)

        # 如果有注册的处理器，立即处理
        handler = self._handlers.get(receiver)
        if handler and msg_type != "response":
            try:
                if asyncio.iscoroutinefunction(handler):
                    response = await handler(msg)
                else:
                    response = handler(msg)
                if response and requires_response:
                    self.respond(msg.id, receiver, response)
            except Exception as e:
                logger.error("message handler error: %s", e)

        return msg

    def respond(self, message_id: str, responder: str, response_content: str) -> bool:
        """响应请求。"""
        msg = self._pending_requests.pop(message_id, None)
        if msg is None:
            return False
        msg.response = response_content
        msg.responded_at = time.time()
        self._history.append({**msg.to_dict(), "response": response_content})
        # 唤醒等待中的 delegate_task（asyncio.Event）
        ev = self._response_events.pop(message_id, None)
        if ev is not None and not ev.is_set():
            ev.set()
        logger.info("message responded: %s by %s", message_id, responder)
        return True

    def get_inbox(self, agent_id: str, unread_only: bool = True) -> list[dict]:
        """获取Agent收件箱。"""
        messages = list(self._inboxes[agent_id])
        if unread_only:
            # 简单实现：返回最近的未处理消息
            return [m.to_dict() for m in messages[-20:]]
        return [m.to_dict() for m in messages]

    def register_handler(self, agent_id: str, handler: Callable) -> None:
        """注册消息处理器。"""
        self._handlers[agent_id] = handler
        logger.info("handler registered for agent: %s", agent_id)

    async def delegate_task(self, delegator: str, task: str,
                             target_agent: str | None = None,
                             context: dict | None = None,
                             room: str | None = None,
                             member: str | None = None,
                             timeout_sec: int = 60) -> dict:
        """委派任务给另一个Agent，并等待真实响应（asyncio.Event，超时兜底）。

        - handler 内联响应（send_message 内调 respond）或异步响应（外部调 respond）
          都会 set 对应事件，delegate_task 通过事件等待而非 sleep 轮询。
        - 超时返回 ok=False / error=response_timeout，计入 tasks_failed。
        """
        # 如果没有指定目标，自动路由
        if target_agent is None:
            target = self.route_task(task, room, member)
            if target is None:
                return {"ok": False, "error": "no suitable agent found"}
            target_agent = target["agent_id"]

        # 发送任务消息
        msg = await self.send_message(
            sender=delegator,
            receiver=target_agent,
            msg_type="task",
            content=task,
            context=context,
            requires_response=True,
            timeout_sec=timeout_sec,
        )

        # 等待真实响应
        ev = self._response_events.get(msg.id)
        if ev is not None:
            try:
                await asyncio.wait_for(ev.wait(), timeout=msg.timeout_sec)
            except asyncio.TimeoutError:
                self._response_events.pop(msg.id, None)
                if target_agent in self._agent_status:
                    self._agent_status[target_agent]["tasks_failed"] += 1
                return {
                    "ok": False,
                    "task_id": msg.id,
                    "delegated_to": target_agent,
                    "error": "response_timeout",
                    "message": f"任务 {msg.id} 响应超时（{msg.timeout_sec}s）",
                }
            self._response_events.pop(msg.id, None)

        if target_agent in self._agent_status:
            self._agent_status[target_agent]["tasks_completed"] += 1

        return {
            "ok": True,
            "task_id": msg.id,
            "delegated_to": target_agent,
            "response": msg.response,
            "message": f"任务已委派给 {self._capabilities.get(target_agent, {}).get('name', target_agent)}",
        }

    def get_collaboration_stats(self) -> dict:
        """获取协作统计。"""
        return {
            "registered_agents": len(self._capabilities),
            "agents": list(self._capabilities.values()),
            "total_messages": len(self._history),
            "pending_requests": len(self._pending_requests),
            "agent_status": self._agent_status,
        }

    def save_state(self) -> None:
        """保存状态到文件。"""
        state = {
            "capabilities": self._capabilities,
            "agent_status": self._agent_status,
            "saved_at": time.time(),
        }
        try:
            path = self.data_dir / "state.json"
            path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("collab state save failed: %s", e)

    def load_state(self) -> None:
        """从文件加载状态。"""
        path = self.data_dir / "state.json"
        if not path.exists():
            return
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            self._capabilities = state.get("capabilities", {})
            self._agent_status = state.get("agent_status", {})
        except Exception as e:
            logger.warning("collab state load failed: %s", e)

    def register_builtin_agents(self) -> None:
        """注册内置角色Agent（v1.9）。"""
        builtin_agents = [
            {"agent_id": "butler", "name": "豆包管家", "role": "family_assistant",
             "capabilities": ["dialog", "decision", "orchestration", "skill_management",
                              "device_control", "schedule", "music", "tv_control"],
             "rooms": ["客厅", "书房", "主卧室", "Kevin房间", "Emily房间"],
             "members": ["lidicn", "Kevin", "Emily"],
             "description": "家庭总管，全屋可见，负责协调所有角色"},
            {"agent_id": "jarvis", "name": "贾维斯", "role": "personal_assistant",
             "capabilities": ["dialog", "schedule", "coding", "research", "device_control"],
             "rooms": ["书房", "主卧室"], "members": ["lidicn"],
             "description": "lidicn的私人助理，懂技术、管日程、写脚本"},
            {"agent_id": "caesar", "name": "凯撒", "role": "kids_assistant",
             "capabilities": ["dialog", "study", "games", "schedule"],
             "rooms": ["Kevin房间", "客厅"], "members": ["Kevin"],
             "description": "Kevin的学习伙伴和游戏搭档"},
            {"agent_id": "luna", "name": "露娜", "role": "kids_assistant",
             "capabilities": ["dialog", "music", "story", "schedule"],
             "rooms": ["Emily房间", "客厅"], "members": ["Emily"],
             "description": "Emily的音乐伙伴和故事姐姐"},
            {"agent_id": "xiaoyue", "name": "晓月", "role": "health_expert",
             "capabilities": ["dialog", "health_advice", "food_analysis", "exercise"],
             "rooms": ["客厅"], "members": ["lidicn", "Kevin", "Emily"],
             "description": "健康专家，负责食物热量分析和健康建议"},
            {"agent_id": "gu_anheng", "name": "顾安恒", "role": "security_expert",
             "capabilities": ["dialog", "security_monitor", "alert", "camera_analysis"],
             "rooms": ["客厅", "门口"], "members": ["lidicn"],
             "description": "安防专员，负责家庭监控和安全告警"},
        ]
        for agent in builtin_agents:
            self.register_agent(**agent)
        self.save_state()
        logger.info("registered %d builtin agents", len(builtin_agents))
