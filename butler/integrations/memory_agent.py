"""memory-agent 对接：通过 MCP(Streamable HTTP) + Bearer token 调用其 Agent 工具。

已验证：MCP initialize / tools/call 在 Bearer 鉴权下可用；HTTP REST 走 session cookie（不适配）。
语义记忆写入强制要求合法 source_refs（event:/insight:/activity: 前缀），否则 422。
"""
from __future__ import annotations

import json
from typing import Any, Optional

import httpx

from homesdk.http import ma_url, join_url
from butler.config import Settings
from butler.logging_setup import get_logger

logger = get_logger("butler.memory_agent")

# O-3：MA 四个鉴权面的凭据构造只在这里发生。
# 缺凭据时抛 CredentialMissing，⛔ 发空 Bearer / 空 Basic（那会换回 401，
# 再被上层读成「最近30分钟没有识别到家庭成员」＝假绿）。
MA_TOKEN_ENV_KEYS = {
    "mcp": "MEMORY_AGENT_TOKEN",
    "butler": "MEMORY_AGENT_BUTLER_TOKEN",
    "app": "MEMORY_AGENT_APP_TOKEN",
}
MA_SURFACES = ("mcp", "butler", "app", "basic")


class CredentialMissing(RuntimeError):
    # 某面缺凭据：显式失败，不外发空凭据。
    def __init__(self, surface, env_key):
        super().__init__("MA 凭据未配置：面 %s 缺 %s" % (surface, env_key))
        self.surface = surface
        self.env_key = env_key


class MemoryAgentClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self.url = settings.memory_agent_mcp_url
        self.token = settings.memory_agent_token
        self._session_id: Optional[str] = None
        self._initialized = False
        # WO-ADM-001 R-32：熔断 + 健康位
        self._circuit_failures: int = 0
        self._circuit_open_until: float = 0.0
        self._circuit_threshold: int = 5       # 连续失败 5 次开闸
        self._circuit_cooldown: float = 30.0   # 开闸后 30 秒半开探测

    def _token_for(self, surface):
        if surface == "mcp":
            return self.token
        if surface == "butler":
            return self.s.memory_agent_butler_token
        if surface == "app":
            return self.s.memory_agent_app_token
        raise KeyError("unknown MA token surface: " + str(surface))

    def bearer_headers(self, surface):
        # O-3 单点：Bearer 头只在这里构造。
        token = self._token_for(surface)
        if not token:
            raise CredentialMissing(surface, MA_TOKEN_ENV_KEYS[surface])
        return {"Authorization": "Bearer " + token}

    def basic_auth(self):
        user, password = self.s.memory_agent_user, self.s.memory_agent_pass
        if not user or not password:
            raise CredentialMissing("basic", "MEMORY_AGENT_USER/PASS")
        return (user, password)

    def _headers(self) -> dict:
        h = self.bearer_headers("mcp")
        h["Content-Type"] = "application/json"
        h["Accept"] = "application/json, text/event-stream"
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    @staticmethod
    def _parse_sse(text: str) -> dict:
        last = None
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                last = line[5:].strip()
        if last:
            return json.loads(last)
        return json.loads(text)

    async def _post(self, method: str, params: Optional[dict], idn: Optional[int], notification: bool = False, timeout: float = 8) -> Optional[dict]:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notification:
            body["id"] = idn
        if params is not None:
            body["params"] = params
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(self.url, headers=self._headers(), json=body)
            if r.status_code >= 400:
                raise RuntimeError(f"MCP http {r.status_code}: {r.text[:120]}")
            sid = r.headers.get("mcp-session-id") or r.headers.get("Mcp-Session-Id")
            if sid:
                self._session_id = sid
            if notification:
                return None
            return self._parse_sse(r.text)

    async def ensure_initialized(self) -> None:
        if self._initialized:
            return
        try:
            await self._post(
                "initialize",
                {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "butler", "version": "1.0"}},
                idn=1,
            )
            await self._post("notifications/initialized", None, None, notification=True)
            self._initialized = True
        except Exception as e:
            logger.warning("memory-agent init failed: %s", e)

    def _circuit_allow(self) -> bool:
        """R-32：熔断检查。True=允许调用，False=熔断拒绝。"""
        import time as _t
        now = _t.time()
        if self._circuit_failures >= self._circuit_threshold:
            if now < self._circuit_open_until:
                return False
            logger.info("memory-agent circuit half-open probe (failures=%d)", self._circuit_failures)
        return True

    def _circuit_record_success(self) -> None:
        if self._circuit_failures > 0:
            logger.info("memory-agent circuit recovered (was %d failures)", self._circuit_failures)
            self._circuit_failures = 0
            self._circuit_open_until = 0.0

    def _circuit_record_failure(self) -> None:
        import time as _t
        self._circuit_failures += 1
        if self._circuit_failures >= self._circuit_threshold:
            self._circuit_open_until = _t.time() + self._circuit_cooldown
            logger.warning("memory-agent circuit OPEN (failures=%d, cooldown=%.0fs)",
                           self._circuit_failures, self._circuit_cooldown)

    @property
    def is_healthy(self) -> bool:
        """R-32：健康位。熔断开闸时返回 False。"""
        return self._circuit_failures < self._circuit_threshold

    async def call_tool(self, name: str, arguments: dict, timeout: float = 8) -> Optional[dict]:
        # R-32：熔断检查
        if not self._circuit_allow():
            logger.warning("memory-agent tool %s rejected: circuit open (failures=%d)", name, self._circuit_failures)
            return None
        await self.ensure_initialized()
        try:
            res = await self._post("tools/call", {"name": name, "arguments": arguments}, idn=2, timeout=timeout)
            self._circuit_record_success()
            return res
        except Exception as e:
            self._circuit_record_failure()
            logger.warning("memory-agent tool %s failed: %s (failures=%d, healthy=%s)",
                           name, e, self._circuit_failures, self.is_healthy)
            return None

    @staticmethod
    def _text_of(res: Optional[dict]) -> str:
        if not res:
            return ""
        try:
            content = res.get("result", {}).get("content", [])
            return "".join(c.get("text", "") for c in content if c.get("type") == "text")
        except Exception:
            return ""

    @staticmethod
    def _json_of(text: str) -> Any:
        text = (text or "").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            return None

    @classmethod
    def _extract_list(cls, text: str, preferred: str | None = None) -> list:
        """memory-agent 工具多返回 {"ok": true, "<key>": [...]}，这里稳健地取出列表。"""
        data = cls._json_of(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if preferred and isinstance(data.get(preferred), list):
                return data[preferred]
            for k in ("members", "memories", "results", "items", "data"):
                if isinstance(data.get(k), list):
                    return data[k]
            # 仅有一个列表值
            for v in data.values():
                if isinstance(v, list):
                    return v
        return []

    @staticmethod
    def _refusal(text: str) -> str:
        """区分"MA 拒绝调用"与"真的没有记忆"：两者都返回空列表时，空列表会伪装成正常。"""
        data = MemoryAgentClient._json_of(text)
        if isinstance(data, dict) and data.get("ok") is False:
            err = data.get("error")
            if isinstance(err, dict):
                return "%s: %s" % (err.get("code", "?"), str(err.get("message", ""))[:120])
            return str(err)[:120]
        return ""

    async def list_memories(self, member: str = "") -> list[dict]:
        res = await self.call_tool("list_agent_memories", {"member_id": member} if member else {})
        text = self._text_of(res)
        refusal = self._refusal(text)
        if refusal:
            logger.warning("list_agent_memories refused: %s (member=%r)", refusal, member)
            return []
        return self._extract_list(text, preferred="memories")

    async def get_vision_status(self) -> dict:
        res = await self.call_tool("get_vision_status", {})
        data = self._json_of(self._text_of(res))
        return data if isinstance(data, dict) else {"raw": self._text_of(res)}

    async def analyze_camera(self, room: str = "客厅", prompt: str = "", timeout: float = 60) -> dict:
        """取帧 + VLM 识别 + TV ArcFace 生物识别补认。

        返回 {ok, room, description, face_recognition, ...}；失败返回 {ok: False, error}。
        """
        args: dict = {"room": room, "bypass_limits": True}
        if prompt:
            args["prompt"] = prompt
        res = await self.call_tool("analyze_camera", args, timeout=timeout)
        data = self._json_of(self._text_of(res))
        if isinstance(data, dict):
            return data
        text = self._text_of(res)
        if text:
            return {"ok": True, "description": text}
        return {"ok": False, "error": "memory-agent analyze_camera 无响应"}

    async def analyze_room_live(self, room: str = "客厅", timeout: float = 60) -> dict:
        """REST 实时识别：取帧 → VLM（人/场景）→ ArcFace 节点补认。

        走 MA 的 POST /api/vision/analyze（Basic 认证，与 MCP 隔离）。
        返回 {ok, scene, persons:[{name,...}], action, ...}；失败 {ok: False, error}。
        """
        url = join_url(ma_url(), "/api/vision/analyze")
        try:
            auth = self.basic_auth()
        except CredentialMissing as e:
            logger.warning("analyze_room_live skipped: %s", e)
            return {"ok": False, "error": str(e)}
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.post(url, json={"room": room, "force": True, "wait": True}, auth=auth)
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            logger.warning("analyze_room_live failed: %s", e)
            return {"ok": False, "error": str(e)}
        if not isinstance(data, dict):
            return {"ok": False, "error": "invalid response"}
        if not data.get("ok"):
            return {"ok": False, "error": str(data.get("error") or data.get("message") or "识别失败")}
        return data

    async def add_memory(self, content: str, source_refs: list[str], member: str = "", dry_run: bool = False) -> dict:
        """写入语义记忆。source_refs 必须以 event:/insight:/activity: 开头。dry_run=False 才会真正落库。"""
        args = {"text": content, "source_refs": source_refs, "dry_run": dry_run}
        if member:
            args["member_id"] = member
        res = await self.call_tool("add_semantic_memory", args)
        ok = bool(res and res.get("result") and not res.get("error"))
        data = self._json_of(self._text_of(res)) if not ok else None
        if not ok and isinstance(data, dict):
            ok = bool(data.get("ok"))
        return {"ok": ok, "raw": self._text_of(res)}

    async def retrieve(self, query: str, member: str = "", limit: int = 5) -> list[str]:
        # 契约表 §三：retrieve_agent_memories 的 member_id 必填、fail-closed。
        # 没主体就连请求都不发——发出去等于请 MA 把全家的记忆捞进当前这段对话。
        member = (member or "").strip()
        if not member:
            logger.warning("retrieve_agent_memories refused: no member_id (query=%s)", query[:60])
            return []
        args = {"query": query, "top_k": max(1, int(limit)), "member_id": member}
        res = await self.call_tool("retrieve_agent_memories", args)
        text = self._text_of(res)
        refusal = self._refusal(text)
        if refusal:
            logger.warning("retrieve_agent_memories refused: %s (member=%r)", refusal, member)
            return []
        items = self._extract_list(text, preferred="memories")
        return [m.get("content") or m.get("text") or "" if isinstance(m, dict) else str(m) for m in items]

    async def recall(self, member: str = "", limit: int = 8) -> list[str]:
        """召回家庭事实（含 staging）用于对话上下文；跳过 revoked。best-effort。

        顺序＝先滤 revoked 再切 limit（批16/M-19：反过来的话，最新 limit 条里
        每有一条已撤销就少召回一条，且不会往前补）。
        """
        try:
            memories = await self.list_memories(member)
        except Exception:
            return []
        alive = [m for m in memories
                 if not (isinstance(m, dict) and m.get("state") == "revoked")]
        out: list[str] = []
        for m in alive[-limit:]:
            if isinstance(m, dict):
                content = m.get("content") or m.get("text") or ""
                if content:
                    out.append(content)
            elif isinstance(m, str):
                out.append(m)
        return out

    async def ask_memory(self, question: str, days: int = 7) -> str:
        """口语化家庭记忆/设备历史问答（映射到 memory-agent 的 ask_memory 工具）。"""
        res = await self.call_tool("ask_memory", {"question": question, "days": days})
        text = self._text_of(res)
        return text or "没有查到相关记忆"

    # ---- MA 窄接口（butler_token Bearer，交接单交付项） ----

    def _butler_headers(self) -> dict:
        return self.bearer_headers("butler")

    async def _butler_fetch(self, path: str, params: dict | None = None, timeout: float = 10.0) -> dict:
        # O-3 单点请求：{ok, data, error}。缺凭据/非 2xx/解析失败都带 error。
        url = join_url(ma_url(), path)
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            headers = self._butler_headers()
        except CredentialMissing as e:
            logger.warning("MA GET %s skipped: %s", path, e)
            return {"ok": False, "data": None, "error": str(e)}
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(url, headers=headers, params=clean)
                if r.status_code >= 400:
                    logger.warning("MA GET %s http %s", path, r.status_code)
                    return {"ok": False, "data": None, "error": "HTTP %s" % r.status_code}
                return {"ok": True, "data": r.json(), "error": ""}
        except Exception as e:
            logger.warning("MA GET %s failed: %s", path, e)
            return {"ok": False, "data": None, "error": str(e)}

    async def _butler_get(self, path: str, params: dict | None = None) -> Any:
        res = await self._butler_fetch(path, params)
        return res["data"] if res["ok"] else None

    @staticmethod
    def _unwrap(data: Any, *keys: str) -> Any:
        """MA 响应可能是 {ok, data:{...}} 或直接对象；稳健取值。"""
        if not isinstance(data, dict):
            return data
        inner = data.get("data", data)
        for k in keys:
            if isinstance(inner, dict) and k in inner:
                return inner[k]
        return inner

    async def get_members(self) -> list[dict]:
        """成员列表（含 profile_json / profile 视图）。"""
        data = await self._butler_get("/api/members")
        items = self._unwrap(data, "members", "items")
        return items if isinstance(items, list) else []

    async def get_member_profile(self, name: str) -> dict:
        """按名字取成员 profile 视图（routine/courses/interests/...）。"""
        name = (name or "").strip()
        if not name:
            return {}
        for m in await self.get_members():
            if isinstance(m, dict) and m.get("name") == name:
                p = m.get("profile")
                return p if isinstance(p, dict) else {}
        return {}

    async def presence_status(self, room=None, minutes: int = 10, timeout: float = 10.0) -> dict:
        # O-3：room=None＝全屋（不带 room 查询参数）。
        # ok=False 时必须带 error，⛔ 让上层把「查不到」写成「没人在家」。
        res = await self._butler_fetch("/api/vision/presence",
                                       {"room": room, "minutes": minutes}, timeout=timeout)
        if not res["ok"]:
            return {"ok": False, "items": [], "error": res["error"]}
        data = res["data"] if isinstance(res["data"], dict) else {}
        if data.get("ok") is False:
            return {"ok": False, "items": [],
                    "error": str(data.get("error") or data.get("message") or "MA 未返回 ok")}
        items = self._unwrap(res["data"], "items")
        return {"ok": True, "items": items if isinstance(items, list) else [], "error": ""}

    async def presence(self, room: str = "客厅", minutes: int = 10, timeout: float = 10.0) -> list[dict]:
        """最近 N 分钟在场成员：[{name, via, confidence, last_seen, room}]。"""
        res = await self.presence_status(room=room, minutes=minutes, timeout=timeout)
        return res["items"]

    async def member_schedule(self, name: str, days: int = 14) -> dict:
        """作息实测摘要（median_first_seen / median_last_seen 等）。"""
        data = await self._butler_get("/api/insights/member-schedule",
                                      {"name": name, "days": days})
        inner = self._unwrap(data, "summary")
        return inner if isinstance(inner, dict) else {}

    async def _butler_patch(self, path: str, body: dict) -> Any:
        url = join_url(ma_url(), path)
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.patch(url, headers=self._butler_headers(), json=body)
                r.raise_for_status()
                return r.json()
        except Exception as e:
            logger.warning("MA PATCH %s failed: %s", path, e)
            return None

    async def member_id_by_name(self, name: str) -> str:
        """按名字查成员 id（画像写回需要 id）。"""
        name = (name or "").strip()
        if not name:
            return ""
        for m in await self.get_members():
            if isinstance(m, dict) and m.get("name") == name:
                return str(m.get("id") or "")
        return ""

    async def update_member_profile(self, name: str, profile: dict) -> bool:
        """把画像写回 MA 的 profile_json（字符串形态；非法 JSON 会被 MA 拒 400）。"""
        mid = await self.member_id_by_name(name)
        if not mid:
            return False
        res = await self._butler_patch(
            f"/api/members/{mid}",
            {"profile_json": json.dumps(profile, ensure_ascii=False)},
        )
        return res is not None

    async def revoke_observation(self, memory_id: str) -> dict:
        """撤销一条管家观察/记忆（软删墓碑，保留审计轨迹）。

        撤销不在 butler_token 窄接口白名单内，走 MCP 的 revoke_memory 工具。
        """
        res = await self.call_tool("revoke_memory", {"memory_id": memory_id})
        ok = bool(res and not res.get("error"))
        data = self._json_of(self._text_of(res))
        if isinstance(data, dict) and "ok" in data:
            ok = bool(data.get("ok"))
        return {"ok": ok, "raw": self._text_of(res)}


    # ---- MA v0.6 记忆统一入库（app_token，source=butler） ----

    def _app_token_headers(self) -> dict:
        h = self.bearer_headers("app")
        h["Content-Type"] = "application/json"
        return h

    async def add_memory_via_app_token(self, text: str, source_refs: list[str] | None = None,
                                        dry_run: bool = False) -> dict:
        """通过 app_token 写入记忆（source=butler，v0.6 强制派生）。

        这是 v1.5 P0-5 记忆统一入库的核心方法。与 add_memory（走 MCP）不同：
        - 使用专属 app_token，MA v0.6 会强制 source=butler（忽略 body 自报）
        - 走 REST POST /api/agent/memories，不需要 MCP 初始化
        - 写入默认 state=staging，不自动进 live（低置信隔离）
        """
        if not self.s.memory_agent_app_token:
            return {"ok": False, "raw": "", "error": "MEMORY_AGENT_APP_TOKEN 未配置"}

        url = join_url(ma_url(), "/api/agent/memories")
        body: dict[str, Any] = {"text": text, "dry_run": dry_run}
        if source_refs:
            body["source_refs"] = source_refs

        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(url, headers=self._app_token_headers(), json=body)
                if r.status_code == 403:
                    return {"ok": False, "raw": r.text, "error": "app_token 无权限（403）"}
                r.raise_for_status()
                data = r.json()
            ok = bool(data.get("ok")) if isinstance(data, dict) else True
            return {"ok": ok, "raw": json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else r.text, "error": ""}
        except Exception as e:
            logger.warning("add_memory_via_app_token failed: %s", e)
            return {"ok": False, "raw": "", "error": str(e)}

    async def list_memories_via_app_token(self, source: str = "", limit: int = 20) -> list[dict]:
        """通过 app_token 列出记忆（可按 source 过滤）。"""
        if not self.s.memory_agent_app_token:
            return []
        url = join_url(ma_url(), "/api/agent/memories")
        params: dict[str, Any] = {"limit": limit}
        if source:
            params["source"] = source
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(url, headers=self._app_token_headers(), params=params)
                r.raise_for_status()
                data = r.json()
            items = self._extract_list(json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else "", preferred="memories")
            return items if isinstance(items, list) else []
        except Exception as e:
            logger.warning("list_memories_via_app_token failed: %s", e)
            return []
