"""DeskPilot（Windows 工具层）HTTP 客户端。

与 TVPilot 完全同构：统一响应包络 {ok, tool, result, cost_ms, error}，
Bearer Token 鉴权，结构化观察优先。DeskPilot = 豆包管家的「Windows 手」。

API 文档：http://192.168.2.201:8765/docs
交接单：E:/NAS/doubao-butler/doc/交接单_DeskPilot工具层对接.md
"""
from __future__ import annotations

import httpx

from butler.logging_setup import get_logger

logger = get_logger("butler.integrations.deskpilot")


class DeskPilotError(Exception):
    """DeskPilot 调用异常。"""


class DeskPilotClient:
    """DeskPilot HTTP 工具客户端。所有方法返回 dict，失败时 ok=False + error。"""

    def __init__(self, base_url: str, api_token: str, timeout: float = 15.0):
        self.base = base_url.rstrip("/")
        self.token = api_token
        self.timeout = timeout
        self._headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    # ── 底层 HTTP ───────────────────────────────────────────

    async def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.get(url, params=params, headers=self._headers)
            return r.json()
        except httpx.TimeoutException:
            return {"ok": False, "tool": path.lstrip("/"), "error": "operation_timeout",
                    "message": f"GET {path} 超时"}
        except Exception as e:
            return {"ok": False, "tool": path.lstrip("/"), "error": "unavailable",
                    "message": f"GET {path} 失败: {e}"}

    async def _post(self, path: str, body: dict, timeout: float | None = None) -> dict:
        url = f"{self.base}{path}"
        t = timeout if timeout is not None else self.timeout
        try:
            async with httpx.AsyncClient(timeout=t) as c:
                r = await c.post(url, json=body, headers=self._headers)
            return r.json()
        except httpx.TimeoutException:
            return {"ok": False, "tool": path.lstrip("/"), "error": "operation_timeout",
                    "message": f"POST {path} 超时"}
        except Exception as e:
            return {"ok": False, "tool": path.lstrip("/"), "error": "unavailable",
                    "message": f"POST {path} 失败: {e}"}

    # ── 健康检查（免鉴权） ──────────────────────────────────

    async def health(self) -> dict:
        """DeskPilot 服务健康检查（免鉴权）。"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get(f"{self.base}/health")
            return {"ok": True, "result": r.json()}
        except Exception as e:
            return {"ok": False, "error": "unavailable", "message": str(e)}

    # ── 系统 ────────────────────────────────────────────────

    async def system_status(self) -> dict:
        """系统状态：CPU/内存/磁盘/平台/主机名。"""
        return await self._get("/api/v1/system/status")

    async def system_notify(self, title: str, message: str) -> dict:
        """Windows 桌面通知。"""
        return await self._post("/api/v1/system/notify", {"title": title, "message": message})

    async def system_run(self, path: str, args: str = "") -> dict:
        """运行程序/打开文件。path 为可执行文件路径或文件路径。"""
        body = {"path": path}
        if args:
            body["args"] = args
        return await self._post("/api/v1/system/run", body)

    # ── 音量 ────────────────────────────────────────────────

    async def volume_get(self) -> dict:
        """查询当前音量和静音状态。"""
        return await self._get("/api/v1/volume")

    async def volume_set(self, level: int) -> dict:
        """设置音量（0-100）。"""
        return await self._post("/api/v1/volume", {"level": max(0, min(100, int(level)))})

    async def volume_toggle_mute(self) -> dict:
        """切换静音。"""
        return await self._post("/api/v1/volume/mute", {})

    # ── 窗口管理 ────────────────────────────────────────────

    async def windows_list(self) -> dict:
        """列出所有可见窗口（标题/句柄/进程）。"""
        return await self._get("/api/v1/windows/list")

    async def windows_activate(self, title: str) -> dict:
        """按标题激活窗口（切换前台）。"""
        return await self._post("/api/v1/windows/activate", {"title": title})

    async def windows_close(self, title: str) -> dict:
        """按标题关闭窗口。"""
        return await self._post("/api/v1/windows/close", {"title": title})

    async def windows_maximize(self, title: str) -> dict:
        """按标题最大化窗口。"""
        return await self._post("/api/v1/windows/maximize", {"title": title})

    async def windows_minimize(self, title: str) -> dict:
        """按标题最小化窗口。"""
        return await self._post("/api/v1/windows/minimize", {"title": title})

    # ── SSH ─────────────────────────────────────────────────

    async def ssh_connect(self, host: str, username: str, password: str = "",
                           port: int = 22) -> dict:
        """建立 SSH 会话。返回 session_id。"""
        body = {"host": host, "username": username, "port": port}
        if password:
            body["password"] = password
        return await self._post("/api/v1/ssh/connect", body, timeout=30.0)

    async def ssh_exec(self, command: str, session_id: str = "") -> dict:
        """在 SSH 会话中执行命令。未指定 session_id 时使用默认会话。"""
        body = {"command": command}
        if session_id:
            body["session_id"] = session_id
        return await self._post("/api/v1/ssh/exec", body, timeout=30.0)

    async def ssh_sessions(self) -> dict:
        """列出所有 SSH 会话。"""
        return await self._get("/api/v1/ssh/sessions")

    # ── LX Music（落雪音乐） ────────────────────────────────

    async def lxmusic_status(self) -> dict:
        """LX Music 播放状态。"""
        return await self._get("/api/v1/lxmusic/status")

    async def lxmusic_play(self) -> dict:
        """播放。"""
        return await self._post("/api/v1/lxmusic/play", {})

    async def lxmusic_pause(self) -> dict:
        """暂停。"""
        return await self._post("/api/v1/lxmusic/pause", {})

    async def lxmusic_toggle(self) -> dict:
        """播放/暂停切换。"""
        return await self._post("/api/v1/lxmusic/toggle", {})

    async def lxmusic_play_by_keyword(self, keyword: str) -> dict:
        """按关键词搜索并播放。"""
        return await self._post("/api/v1/lxmusic/play-by-keyword", {"keyword": keyword})

    # ── 轨迹/对账 ────────────────────────────────────────────

    async def traces_query(self, limit: int = 50, tool: str = "",
                            ok: bool | None = None) -> dict:
        """查询 DeskPilot 操作轨迹（ops.jsonl），供管家联调对账。"""
        params = {"limit": limit}
        if tool:
            params["tool"] = tool
        if ok is not None:
            params["ok"] = str(ok).lower()
        return await self._get("/api/v1/traces", params)

    async def traces_stats(self, limit: int = 100) -> dict:
        """操作轨迹统计：每个工具的调用次数/成功率/平均耗时。"""
        return await self._get("/api/v1/traces/stats", {"limit": limit})

    # ── 便捷验证方法 ─────────────────────────────────────────

    async def ensure_volume(self, expected: int) -> tuple[bool, int]:
        """验证音量是否为期望值。返回 (匹配, 实际音量)。"""
        r = await self.volume_get()
        if not r.get("ok"):
            return False, -1
        actual = int((r.get("result") or {}).get("level", -1))
        return actual == expected, actual

    async def ensure_window_active(self, title_keyword: str) -> tuple[bool, str]:
        """验证包含 title_keyword 的窗口是否在窗口列表中（前台近似判断）。"""
        r = await self.windows_list()
        if not r.get("ok"):
            return False, f"查询失败: {r.get('error')}"
        windows = r.get("result") or []
        for w in windows:
            t = str(w.get("title", ""))
            if title_keyword.lower() in t.lower():
                return True, t
        return False, f"未找到包含 '{title_keyword}' 的窗口"
