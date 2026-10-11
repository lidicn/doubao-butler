"""半死 HA 的 WebSocket connect 上界（批201 DB47 单点）。

病：`session.ws_connect(...)` 在「TCP 连得上、对方永不开成 WebSocket」的服务器上会**自己**吊死——
`asyncio.wait_for(ws.receive_json(), 15)` 那种上界只套得住握手**之后**的读帧，套不到握手本身。
本机实测（`workorders/readings/1011/b201_db47_ws_timeout_1011.txt`，靶子＝accept 后零字节）：
裸 `ws_connect` 与 `timeout=ClientWSTimeout(...)` 两形都在 15s 撞闸（无界），`asyncio.wait_for` 那形 2.0s 收回。

⛔ 改用 aiohttp 自己的 timeout 参数：
  - `ClientTimeout(total=)` 是**整次操作**的上界，WebSocket 的"这次操作"就是整条连接⇒健康的长连接会被掐死；
  - `sock_read` 对 ws 帧在现网 aiohttp==3.9.5 上的语义我在这台机验不了（本机是 3.14.1）⇒ 不押它。
所以这里只用 asyncio 的上界，且只套「连接＋握手」那一段，稳态收发一点没被管。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

# HA 正常握手在亚秒级；20s 留给慢机器，撞不上健康连接，又不会让重试循环排队等到人睡着。
DEFAULT_CONNECT_TIMEOUT_SECONDS = 20.0
HEARTBEAT_SECONDS = 30


@asynccontextmanager
async def bounded_ws_connect(session, url: str, *,
                             connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
                             heartbeat: float = HEARTBEAT_SECONDS):
    """带开连接上界的 `session.ws_connect`：上界只管握手，退出时把 socket 关掉。"""
    ws = await asyncio.wait_for(session.ws_connect(url, heartbeat=heartbeat),
                                timeout=connect_timeout)
    try:
        yield ws
    finally:
        await ws.close()
