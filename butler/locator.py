"""成员定位模块：全屋摄像头找人（v0.4 大脑触发核心）。

find_member(member) → 返回该成员最近出现在哪个房间。

数据通道（按可用性降级）：
1. presence（MA 窄接口，butler_token Bearer）：实时在场缓存，最快。
2. analyze_camera（MCP，mcp token）：MA 视觉事件历史（含 persons），
   遍历各房间事件找 persons 含目标成员的最新一条。

候选房间：优先 DeviceRegistry 去重（排除空/*），否则用内置默认房间。
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from butler.logging_setup import get_logger

logger = get_logger("butler.locator")

# 无设备表时的默认全屋房间（与 devices.json 房间名对齐）
DEFAULT_ROOMS = ["客厅", "书房", "Kevin房间", "Emily房间", "主卧室", "主卧室浴室", "卫生间"]


class MemberLocator:
    def __init__(self, settings, memory, devices=None):
        self.s = settings
        self.memory = memory          # MemoryAgentClient
        self.devices = devices        # DeviceRegistry | None

    # ---- 候选房间 ----

    def candidate_rooms(self) -> list[str]:
        rooms: list[str] = []
        if self.devices is not None:
            for d in self.devices.all():
                r = getattr(d, "room", "") or ""
                if r and r != "*" and r not in rooms:
                    rooms.append(r)
        return rooms or list(DEFAULT_ROOMS)

    # ---- 成员名归一化 ----

    def _names_of(self, member: str) -> set[str]:
        """成员可能的称呼集合：注册名 + 大小写不敏感 + 昵称。"""
        names = {member.strip()}
        lower = member.strip().lower()
        if lower:
            names.add(lower)
        try:
            nick = self.s.nickname_of(member)
            if nick:
                names.add(nick)
        except Exception:
            pass
        return {n for n in names if n}

    def _match_persons(self, persons, names: set[str]) -> bool:
        for p in persons or []:
            p = str(p or "").strip()
            if p and p in names:
                return True
        return False

    # ---- 主入口 ----

    async def find_member(self, member: str, rooms: list[str] | None = None,
                          minutes: int = 30, timeout_per: float = 20.0) -> dict:
        """定位成员最近出现位置。

        返回 {"found": bool, "room": str, "last_seen": str, "via": str,
              "confidence": float, "scanned": int, "detail": str}
        """
        member = (member or "").strip()
        if not member:
            return {"found": False, "room": "", "last_seen": "", "via": "no_member",
                    "confidence": 0.0, "scanned": 0, "detail": "未指定成员"}
        rooms = rooms or self.candidate_rooms()
        names = self._names_of(member)

        # 通道1：presence 实时在场（快，5s）
        try:
            hit = await asyncio.wait_for(self._probe_presence(rooms, names, minutes), timeout=min(8, timeout_per))
            if hit:
                hit["via"] = "presence"
                return hit
        except Exception as e:
            logger.debug("locator presence probe failed: %s", e)

        # 通道2：analyze_camera 视觉事件（慢，并发受限）
        try:
            hit = await self._probe_vision(rooms, names, timeout_per)
            if hit:
                hit["via"] = "vision"
                return hit
        except Exception as e:
            logger.debug("locator vision probe failed: %s", e)

        return {"found": False, "room": "", "last_seen": "", "via": "none",
                "confidence": 0.0, "scanned": len(rooms), "detail": "全屋未找到"}

    # ---- 通道1：presence ----

    async def _probe_presence(self, rooms: list[str], names: set[str], minutes: int) -> dict | None:
        async def probe(room: str):
            try:
                items = await self.memory.presence(room, minutes=minutes)
            except Exception as e:
                logger.debug("presence %s failed: %s", room, e)
                return None
            best = None
            for it in items or []:
                if not isinstance(it, dict):
                    continue
                nm = str(it.get("name") or "")
                if nm and (nm in names or nm.lower() in names):
                    ts = str(it.get("last_seen") or "")
                    if best is None or ts > best[1]:
                        best = (room, ts, float(it.get("confidence") or 0.5))
            return best

        results = await asyncio.gather(*[probe(r) for r in rooms])
        best = None
        for r in results:
            if r and (best is None or r[1] > best[1]):
                best = r
        if best:
            return {"found": True, "room": best[0], "last_seen": best[1],
                    "via": "presence", "confidence": best[2], "scanned": len(rooms),
                    "detail": f"presence 命中 {best[0]}"}
        return None

    # ---- 通道2：视觉事件 ----

    async def _probe_vision(self, rooms: list[str], names: set[str], timeout_per: float) -> dict | None:
        sem = asyncio.Semaphore(3)  # 并发限制，避免打爆 MA

        async def probe(room: str):
            async with sem:
                try:
                    data = await self.memory.analyze_camera(room, timeout=timeout_per)
                except Exception as e:
                    logger.debug("analyze_camera %s failed: %s", room, e)
                    return None
                if not isinstance(data, dict) or not data.get("ok"):
                    return None
                best = None
                for ev in data.get("events") or []:
                    if not isinstance(ev, dict):
                        continue
                    if self._match_persons(ev.get("persons"), names):
                        ts = str(ev.get("time") or "")
                        if best is None or ts > best[1]:
                            best = (room, ts, ev.get("persons"))
                return best

        results = await asyncio.gather(*[probe(r) for r in rooms])
        best = None
        for r in results:
            if r and (best is None or r[1] > best[1]):
                best = r
        if best:
            return {"found": True, "room": best[0], "last_seen": best[1],
                    "via": "vision", "confidence": 0.8, "scanned": len(rooms),
                    "detail": f"视觉事件命中 {best[0]} @ {best[1]}"}
        return None
