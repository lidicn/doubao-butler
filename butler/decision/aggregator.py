"""数据聚合器：心跳时汇总多源数据 → 一段状态摘要（喂给 LLM 推理）。

数据源（全部容错，单源失败不阻断）：
- 时间：周几/时段
- 设备状态：config.devices 里的 HA 实体（灯/空调/电脑等）
- 成员在场：locator.presence 各房间（限时并发，失败降级）
- 最近视觉事件：trigger_runs 里 face_detected 最近几条
- 最近对话：dialog_turns 最近几条
- 今日活动：skill_runs / notify_history 最近几条
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime

from homesdk.time import house_now, house_tz

from butler.logging_setup import get_logger

logger = get_logger("butler.decision.aggregator")

# 在场探测超时（秒）
_PRESENCE_TIMEOUT = 6.0
_DEVICE_TIMEOUT = 4.0


class DecisionAggregator:
    def __init__(self, rt, cfg):
        self.rt = rt
        self.cfg = cfg

    # ---- 对外主入口 ----

    async def build_summary(self, mock: dict | None = None) -> str:
        """返回一段多行中文状态摘要。mock 注入（测试用）覆盖真实数据。"""
        parts: list[str] = []

        # 1. 时间（容器时区可能是 UTC，显式用家庭时区；mock.now 可注入）
        if mock and mock.get("now"):
            now = datetime.strptime(str(mock["now"]), "%Y-%m-%d %H:%M").replace(tzinfo=house_tz())
        else:
            now = house_now()
        wd = "一二三四五六日"[now.weekday()]
        tod = self._time_of_day(now.hour)
        parts.append(f"时间：{now.strftime('%Y-%m-%d %H:%M')}（周{wd}，{tod}）")

        # 2. 设备状态（mock 覆盖）
        if mock and isinstance(mock.get("devices"), dict):
            dev_lines = [f"{k}：{v}" for k, v in mock["devices"].items()]
        else:
            dev_lines = await self._probe_devices()
        parts.append("设备状态：" + ("；".join(dev_lines) if dev_lines else "（未获取到）"))

        # 3. 成员在场（mock 覆盖）
        if mock and isinstance(mock.get("presence"), dict):
            pres_lines = [f"{k}在{v}" for k, v in mock["presence"].items()]
        else:
            pres_lines = await self._probe_presence()
        parts.append("成员在场：" + ("；".join(pres_lines) if pres_lines else "（未检测到人在场）"))

        # 4. 最近视觉事件（mock 覆盖）
        if mock and isinstance(mock.get("vision_events"), list):
            vis_lines = [str(v) for v in mock["vision_events"]]
        else:
            vis_lines = self._recent_vision_events()
        parts.append("最近视觉事件：" + ("；".join(vis_lines) if vis_lines else "（无）"))

        # 5. 最近对话（mock 覆盖）
        if mock and isinstance(mock.get("dialog"), list):
            dia_lines = [str(v) for v in mock["dialog"]]
        else:
            dia_lines = self._recent_dialog()
        parts.append("最近对话：" + ("；".join(dia_lines) if dia_lines else "（无）"))

        # 6. 今日活动（mock 覆盖）
        if mock and isinstance(mock.get("activities"), list):
            act_lines = [str(v) for v in mock["activities"]]
        else:
            act_lines = self._recent_activities()
        parts.append("今日活动：" + ("；".join(act_lines) if act_lines else "（无）"))

        # 7. 作息基线（mock 覆盖；MA member_schedule，用于判断"睡过头/作息异常"）
        if mock and isinstance(mock.get("schedules"), dict):
            sched_lines = [f"{k}：{v}" for k, v in mock["schedules"].items()]
        else:
            sched_lines = await self._probe_schedules()
        if sched_lines:
            parts.append("作息基线：" + "；".join(sched_lines))

        return "\n".join(parts)

    # ---- 数据源 ----

    @staticmethod
    def _time_of_day(h: int) -> str:
        if h < 6:
            return "凌晨"
        if h < 11:
            return "早上"
        if h < 14:
            return "中午"
        if h < 18:
            return "下午"
        if h < 23:
            return "晚上"
        return "深夜"

    async def _probe_devices(self) -> list[str]:
        """并发读取配置设备的状态。返回 ['客厅灯：on', ...]。"""
        ha = getattr(self.rt, "ha", None)
        if ha is None or not ha.s.ha_token:
            return []
        devices = self.cfg.devices
        if not devices:
            return []

        async def one(label: str, eid: str):
            try:
                st = await asyncio.wait_for(ha.get_state(eid), timeout=_DEVICE_TIMEOUT)
            except Exception:
                return None
            if not st:
                return f"{label}：不可用"
            state = st.get("state", "")
            if state in ("on", "off"):
                state_cn = "开" if state == "on" else "关"
            elif state in ("cool", "heat", "dry", "fan_only"):
                state_cn = {"cool": "制冷", "heat": "制热", "dry": "除湿", "fan_only": "送风"}.get(state, state)
            elif state in ("playing", "idle", "paused"):
                state_cn = {"playing": "播放中", "idle": "待机", "paused": "暂停"}.get(state, state)
            elif state == "unavailable":
                state_cn = "离线"
            else:
                state_cn = state
            temp = st.get("attributes", {}).get("temperature")
            if temp is not None and state in ("cool", "heat", "dry", "fan_only", "on"):
                return f"{label}：{state_cn}（{temp}°C）"
            return f"{label}：{state_cn}"

        results = await asyncio.gather(*[one(k, v) for k, v in devices.items()])
        return [r for r in results if r]

    async def _probe_presence(self) -> list[str]:
        """探测各房间成员在场。

        优先对每个已知成员调 locator.find_member（自动降级：presence→MCP视觉），
        聚合为 {room: [members]}。比直接调 memory.presence（butler_token 401 时完全失败）更稳。
        """
        locator = getattr(self.rt, "locator", None)
        if locator is None:
            return []
        members = self._known_members()
        if not members:
            return []

        async def one(name: str):
            try:
                res = await asyncio.wait_for(
                    locator.find_member(name, minutes=30, timeout_per=8.0),
                    timeout=_PRESENCE_TIMEOUT,
                )
                if res and res.get("found"):
                    return (res.get("room", ""), name)
            except Exception:
                pass
            return None

        results = await asyncio.gather(*[one(m) for m in members])
        room_map: dict[str, list[str]] = {}
        for r in results:
            if r:
                room_map.setdefault(r[0], []).append(r[1])
        return [f"{room}：{'、'.join(names)}" for room, names in room_map.items()]

    def _known_members(self) -> list[str]:
        """从角色配置取已知成员（去重，排除空）。"""
        roles = getattr(self.rt, "roles", None)
        if roles is None:
            return []
        out = []
        try:
            for r in roles.all():
                m = getattr(r, "member", "") or ""
                if m and m not in out:
                    out.append(m)
        except Exception:
            pass
        return out

    async def _probe_schedules(self) -> list[str]:
        """获取已知成员的作息基线（MA member_schedule，butler_token 窄接口）。

        返回 ['lidicn：通常 07:15 首次出现，23:40 最后出现', ...]。
        MA 修复 butler_token 前返回空（容错）。
        """
        memory = getattr(self.rt, "memory", None)
        if memory is None:
            return []
        members = self._known_members()
        if not members:
            return []

        async def one(name: str):
            try:
                data = await asyncio.wait_for(
                    memory.member_schedule(name, days=14),
                    timeout=5.0,
                )
            except Exception:
                return None
            if not isinstance(data, dict) or not data:
                return None
            first = data.get("median_first_seen") or data.get("first_seen") or ""
            last = data.get("median_last_seen") or data.get("last_seen") or ""
            parts = []
            if first:
                parts.append(f"通常 {first} 首次出现")
            if last:
                parts.append(f"{last} 最后出现")
            if parts:
                return f"{name}：{'，'.join(parts)}"
            return None

        results = await asyncio.gather(*[one(m) for m in members])
        return [r for r in results if r]

    def _recent_vision_events(self) -> list[str]:
        """最近 trigger_runs 里 face_detected 事件。"""
        try:
            from butler.store import repo
            runs = repo.recent_trigger_runs("", limit=10)
        except Exception:
            return []
        out = []
        for r in runs:
            if r.get("event") != "face_detected":
                continue
            ts = time.strftime("%H:%M", time.localtime(r.get("ts", 0)))
            out.append(f"{ts} {r.get('trigger_id', '')}")
            if len(out) >= 3:
                break
        return out

    def _recent_dialog(self) -> list[str]:
        """最近用户消息（不含 system 前缀）。"""
        try:
            from butler.store import repo
            turns = repo.recent_turns(limit=15)
        except Exception:
            return []
        out = []
        for t in turns:
            if t.get("role") != "user":
                continue
            text = str(t.get("text", ""))[:40]
            if text.startswith("[system]") or text.startswith("你是「"):
                continue
            ts = time.strftime("%H:%M", time.localtime(t.get("ts", 0)))
            out.append(f"{ts} {t.get('member', '')}:{text}")
            if len(out) >= 3:
                break
        return out

    def _recent_activities(self) -> list[str]:
        """最近技能运行 + 通知。"""
        out = []
        try:
            from butler.store import repo
            runs = repo.recent_skill_runs("", limit=8)
            for r in runs:
                ts = time.strftime("%H:%M", time.localtime(r.get("ts", 0)))
                status = r.get("status", "")
                if status in ("ok", "dedup"):
                    out.append(f"{ts} 技能{r.get('skill_id', '')}")
                if len(out) >= 3:
                    break
        except Exception:
            pass
        return out
