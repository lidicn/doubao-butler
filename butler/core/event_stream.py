"""事件流监听器：订阅 HA state_changed 事件，维护滑动窗口，持久化到 SQLite。

职责：
1. 连接 HA websocket，订阅所有设备状态变化
2. 过滤感兴趣的实体类型（门窗/人体/灯光/空调/电视）
3. 维护内存滑动窗口（最近10分钟事件）
4. 事件写入 SQLite（home_events 表，供 MA 凌晨拉取）
5. 提供 API 查询最近事件
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque

import aiohttp

from butler.logging_setup import get_logger, warn_throttled
from butler.store.db import get_conn
from butler.store import write_failures

logger = get_logger("butler.core.event_stream")

# 感兴趣的实体前缀
WATCH_PREFIXES = (
    "binary_sensor.",   # 门窗、人体感应
    "light.",           # 灯光
    "climate.",         # 空调
    "media_player.",    # 电视/音箱
    "cover.",           # 窗帘
    "switch.",          # 开关
    "sensor.xiaomi_",   # 小爱音箱 conversation（已有 xiaomi_ear 处理，这里只记录）
)

# 滑动窗口大小（秒）
WINDOW_SECONDS = 600  # 10 分钟

# 房间映射（从 entity_id 推断）
ROOM_MAP = {
    "living": "客厅", "kitchen": "厨房", "study": "书房",
    "bedroom": "主卧室", "kevin": "Kevin房间", "emily": "Emily房间",
    "bath": "卫生间", "toilet": "卫生间", "master_bath": "主卧室浴室",
}


def _ensure_table():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS home_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            entity_id TEXT,
            state TEXT,
            room TEXT,
            event_type TEXT,
            attributes_json TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_home_events_ts ON home_events(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_home_events_entity ON home_events(entity_id)")
    conn.commit()


def _infer_room(entity_id: str) -> str:
    """从 entity_id 推断房间。"""
    e = entity_id.lower()
    for key, room in ROOM_MAP.items():
        if key in e:
            return room
    return ""


def _infer_type(entity_id: str) -> str:
    """从 entity_id 前缀推断事件类型。"""
    prefix = entity_id.split(".")[0] if "." in entity_id else ""
    type_map = {
        "binary_sensor": "sensor", "light": "light", "climate": "climate",
        "media_player": "media", "cover": "cover", "switch": "switch",
    }
    return type_map.get(prefix, "unknown")


class EventStream:
    """实时事件流监听器。"""

    def __init__(self, runtime):
        self.rt = runtime
        self._task = None
        self._running = False
        # 内存滑动窗口：deque of dict
        self._window: deque = deque(maxlen=500)
        # 当前设备状态快照
        self._states: dict[str, dict] = {}

    def start(self):
        if self._task and not self._task.done():
            return
        _ensure_table()
        self._running = True
        self._task = asyncio.create_task(self._run(), name="event_stream")
        logger.info("event_stream started")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _run(self):
        while self._running:
            try:
                await self._connect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("event_stream disconnected: %s, retry in 5s", e)
                await asyncio.sleep(5)

    async def _connect(self):
        s = self.rt.settings
        ws_url = s.ha_url.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"

        logger.info("event_stream connecting to %s", ws_url)

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, heartbeat=30) as ws:
                # 认证
                msg = await ws.receive_json()
                if msg.get("type") == "auth_required":
                    await ws.send_json({"type": "auth", "access_token": s.ha_token})
                    msg = await ws.receive_json()
                    if msg.get("type") != "auth_ok":
                        raise RuntimeError(f"HA websocket auth failed: {msg}")
                    logger.info("event_stream HA auth ok")

                # 订阅 state_changed
                await ws.send_json({"id": 1, "type": "subscribe_events", "event_type": "state_changed"})
                logger.info("event_stream subscribed state_changed, listening...")

                # 主动拉取当前所有状态（填充快照）——用 id 匹配响应
                await ws.send_json({"id": 99, "type": "get_states"})
                snapshot_done = False
                while not snapshot_done:
                    raw = await ws.receive()
                    if raw.type != aiohttp.WSMsgType.TEXT:
                        break
                    try:
                        data = raw.json()
                    except Exception:
                        warn_throttled(logger, "es.snapshot_frame", "event_stream 快照帧解析失败（快照继续往后拉）")
                        continue
                    if not isinstance(data, dict):
                        continue
                    if data.get("id") == 99 and data.get("type") == "result":
                        states = data.get("result") or []
                        count = 0
                        for st in states:
                            eid = st.get("entity_id", "")
                            if not eid.startswith(WATCH_PREFIXES):
                                continue
                            state = (st.get("state") or "").strip()
                            if not state or state in ("unknown", "unavailable"):
                                continue
                            attrs = st.get("attributes") or {}
                            evt = {
                                "ts": time.time(),
                                "entity_id": eid,
                                "state": state,
                                "room": _infer_room(eid),
                                "event_type": _infer_type(eid),
                                "attributes": {k: v for k, v in list(attrs.items())[:5] if isinstance(v, (str, int, float, bool))},
                            }
                            self._states[eid] = evt
                            count += 1
                        logger.info("event_stream initial snapshot: %d entities", count)
                        snapshot_done = True

                async for msg in ws:
                    try:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = msg.json()
                            except Exception:
                                warn_throttled(logger, "es.frame_json", "event_stream 事件帧解析失败（连接不断，继续收下一帧）")
                                continue
                            if not isinstance(data, dict):
                                continue
                            if data.get("type") == "event":
                                ev = data.get("event", {})
                                if isinstance(ev, dict):
                                    await self._handle_event(ev)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                    except Exception:
                        warn_throttled(logger, "es.handle_event", "event_stream handle_event 抛错（该事件丢弃，订阅不断）")
                        continue

    async def _handle_event(self, event: dict):
        data = event.get("data", {})
        entity_id = data.get("entity_id", "")
        if not entity_id:
            return

        # 过滤：只关注感兴趣的实体
        if not entity_id.startswith(WATCH_PREFIXES):
            return

        new_state = data.get("new_state")
        if not new_state:
            return

        state = (new_state.get("state") or "").strip()
        if not state or state in ("unknown", "unavailable"):
            return

        # 跳过未变化的
        old_state = data.get("old_state") or {}
        old_s = (old_state.get("state") or "").strip()
        if state == old_s:
            return

        attrs = new_state.get("attributes") or {}
        ts = event.get("origin", {}).get("time_fired", "")
        try:
            ts_val = time.time()
        except Exception:
            ts_val = time.time()

        evt = {
            "ts": ts_val,
            "entity_id": entity_id,
            "state": state,
            "room": _infer_room(entity_id),
            "event_type": _infer_type(entity_id),
            "attributes": {k: v for k, v in list(attrs.items())[:5] if isinstance(v, (str, int, float, bool))},
        }

        # 更新内存窗口
        self._window.append(evt)
        # 更新状态快照
        self._states[entity_id] = evt

        # 持久化到 SQLite
        try:
            conn = get_conn()
            conn.execute(
                "INSERT INTO home_events (ts, entity_id, state, room, event_type, attributes_json) VALUES (?, ?, ?, ?, ?, ?)",
                (ts_val, entity_id, state, evt["room"], evt["event_type"],
                 json.dumps(evt["attributes"], ensure_ascii=False))
            )
            conn.commit()
        except Exception as e:
            logger.warning("event_stream persist failed: %s", e)
            write_failures.record("core/event_stream.home_events",
                                  "%s: %s" % (type(e).__name__, str(e)[:200]),
                                  table="home_events")

        logger.debug("event_stream: %s=%s room=%s", entity_id, state, evt["room"])

    def get_recent(self, seconds: int = 600) -> list[dict]:
        """获取最近 N 秒的事件。"""
        cutoff = time.time() - seconds
        return [e for e in self._window if e["ts"] >= cutoff]

    def get_current_state(self) -> dict:
        """获取当前设备状态快照。"""
        return dict(self._states)
