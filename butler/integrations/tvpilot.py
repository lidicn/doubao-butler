"""TVPilot 工具层客户端（HTTP :8090）—— 豆包管家 ReAct 的"电视手"。

封装 TVPilot v0.2 的 12 个标准化工具接口，统一返回 dict（含 ok/error/result/cost_ms）。
与 tv.py 的 TVClient（MQTT + 8080 ArcFace 服务）互补：TVClient 负责换台/播片高层意图，
本客户端负责细粒度 ADB 操作（前台观察/按键/启动/输入/点击/截图）。

API 文档见 E:/NAS/TVPilot/tvpilot_server.py 头部注释。
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from butler.config import Settings
from butler.logging_setup import get_logger

logger = get_logger("butler.tvpilot")


class TVPilotError(Exception):
    """TVPilot 工具执行错误，携带错误码。"""
    def __init__(self, error_code: str, message: str = ""):
        self.error_code = error_code
        self.message = message
        super().__init__(f"{error_code}: {message}")


class TVPilotClient:
    """TVPilot HTTP 工具客户端。所有方法返回 dict，失败时 ok=False + error。"""

    def __init__(self, settings: Settings):
        self.s = settings
        self.base = settings.tvpilot_http_url.rstrip("/")
        self.timeout = 10.0

    async def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.get(url, params=params)
            if r.status_code >= 400:
                return {"ok": False, "error": f"http_{r.status_code}",
                        "message": f"GET {path} 返回 HTTP {r.status_code}"}
            return r.json()
        except httpx.TimeoutException:
            return {"ok": False, "error": "operation_timeout", "message": f"GET {path} 超时"}
        except Exception as e:
            return {"ok": False, "error": "tv_unreachable", "message": f"GET {path} 失败: {e}"}

    async def _post(self, path: str, body: dict, timeout: float | None = None) -> dict:
        url = f"{self.base}{path}"
        t = timeout if timeout is not None else self.timeout
        try:
            async with httpx.AsyncClient(timeout=t) as c:
                r = await c.post(url, json=body)
            if r.status_code >= 400:
                return {"ok": False, "error": f"http_{r.status_code}",
                        "message": f"POST {path} 返回 HTTP {r.status_code}"}
            return r.json()
        except httpx.TimeoutException:
            return {"ok": False, "error": "operation_timeout", "message": f"POST {path} 超时"}
        except Exception as e:
            return {"ok": False, "error": "tv_unreachable", "message": f"POST {path} 失败: {e}"}

    # ── 观察类（结构化优先） ──────────────────────────────

    async def foreground(self) -> dict:
        """当前前台包名。返回 {ok, result:{package}, error}。"""
        return await self._get("/api/foreground")

    async def current(self) -> dict:
        """mytv 当前频道（代理）。"""
        return await self._get("/api/current")

    async def channels(self) -> dict:
        """mytv 频道列表（代理）。"""
        return await self._get("/api/channels")

    async def health(self) -> dict:
        """全面健康检查。"""
        return await self._get("/api/health")

    # ── 操作类 ─────────────────────────────────────────────

    async def keyevent(self, key: str) -> dict:
        """发送按键，如 KEYCODE_HOME / KEYCODE_ENTER / KEYCODE_BACK。"""
        return await self._post("/api/keyevent", {"key": key})

    async def tap(self, x: int, y: int) -> dict:
        """点击坐标。"""
        return await self._post("/api/tap", {"x": x, "y": y})

    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> dict:
        """滑动。"""
        return await self._post("/api/swipe", {
            "x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration_ms": duration_ms,
        })

    async def input_text(self, text: str) -> dict:
        """输入文字。注意：ADB input text 原生不支持中文，中文输入方案待 TVPilot 侧落地。"""
        return await self._post("/api/input_text", {"text": text})

    async def launch_app(self, package: str, activity: str | None = None) -> dict:
        """启动 App，自动等待进入前台（最多 8s）。返回 foreground 标志。"""
        body = {"package": package}
        if activity:
            body["activity"] = activity
        return await self._post("/api/launch_app", body)

    async def wait(self, seconds: float = 2) -> dict:
        """等待（用于 App 启动后等加载）。"""
        return await self._post("/api/wait", {"seconds": seconds})

    async def zap(self, channel: str) -> dict:
        """mytv 换台（高层封装：确保 mytv 前台 + 频道归一化 + mytv API）。"""
        return await self._post("/api/zap", {"channel": channel})

    # ── 截图兜底 ───────────────────────────────────────────

    async def screenshot(self, width: int = 480) -> dict:
        """截图返回 base64（fmt=json，压缩减 token）。失败时 ok=False。"""
        return await self._get("/api/screenshot", {"fmt": "json", "width": width})

    # ── 便捷方法 ───────────────────────────────────────────

    async def ensure_foreground(self, expected_package: str) -> tuple[bool, str]:
        """验证当前前台是否为 expected_package。返回 (匹配, 实际包名)。"""
        r = await self.foreground()
        if not r.get("ok"):
            return False, f"查询失败: {r.get('error', 'unknown')}"
        pkg = (r.get("result") or {}).get("package", "")
        return pkg == expected_package, pkg

    async def escape_home(self) -> dict:
        """逃生：按 HOME 键回桌面。"""
        return await self.keyevent("KEYCODE_HOME")

    # ── 组合动作（v0.3 高置信 combo，带前台验证） ────────

    async def combo_go_home(self) -> dict:
        """回桌面组合动作：KEYCODE_HOME + 前台验证。返回 is_home 标志。"""
        return await self._post("/api/combo/go_home", {})

    async def combo_search_play(self, keyword: str) -> dict:
        """飞牛TV搜索播放组合动作：launch → tap_search → 中文输入(tvremoteime) → 搜索 → 选首条 → 播放。
        6 步原子操作封装，中文输入可靠，比 ReAct 逐步组合成功率高。耗时约 10-20 秒，需长超时。"""
        return await self._post("/api/combo/trim_search_play", {"keyword": keyword}, timeout=45.0)

    # ── 操作存档（v0.3 ops.jsonl + /api/history） ─────────

    async def history(self, limit: int = 50, request_id: str | None = None) -> dict:
        """查询 TVPilot 操作存档，供管家联调对账。"""
        params = {"limit": limit}
        if request_id:
            params["request_id"] = request_id
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        return await self._get(f"/api/history?{qs}")
