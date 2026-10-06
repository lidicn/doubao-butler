"""SSE 实时推送：把 butler/dialog/event 等总线事件转发给 WebUI。"""
from __future__ import annotations

import asyncio
import json
import time

from starlette.responses import StreamingResponse
from starlette.routing import Route

from butler.api.deps import err, guard
from butler.logging_setup import get_logger
from butler.runtime import get_runtime

logger = get_logger("butler.api.stream")

# 表行 52（P1-18 R4）的「无上限」腿：`runtime.py:52` 的 sse_subscribers 是裸 set，没上界。
# best-effort 由这一格说死：检查在 HTTP 层、注册在生成器里，中间没锁，并发下可短暂多进 1~2 枚
# ⇒ 上界要的是「⛔ 无限涨」，⛔ 精确计数（要精确就得把注册也搬出生成器＝① 那个坑第二次复发）。
MAX_SSE_SUBSCRIBERS = 64


async def stream(request):
    denied = guard(request)   # 表行 171（P2-21）：改前 `if guard(request): return guard(request)`
    if denied is not None:    # ＝被拒的请求鉴权两回，`note_bearer_failure` 跟着记两回（刹车阈值减半）
        return denied
    rt = get_runtime()
    if len(rt.sse_subscribers) >= MAX_SSE_SUBSCRIBERS:
        logger.warning("SSE_SUBSCRIBER_LIMIT 订阅者已达上限 %d，拒绝新的 SSE 流", MAX_SSE_SUBSCRIBERS)
        return err("sse_subscriber_limit_reached", 503)
    q: asyncio.Queue = asyncio.Queue(maxsize=200)

    async def gen():
        # ① 根因：注册与注销必须在**同一条执行路径**上。放进生成器 ⇒ 「没起跑」＝「没注册」，
        #    僵尸订阅者无从产生（改前漏的那两枚形状＝响应被丢弃不消费 / send 在 response.start 抛）。
        rt.sse_subscribers.add(q)
        last = time.time()
        try:
            yield ": connected\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"event: dialog\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    last = time.time()
                except asyncio.TimeoutError:
                    if time.time() - last > 60:
                        yield ": ping\n\n"
                        last = time.time()
        finally:
            rt.sse_subscribers.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def routes():
    return [Route("/api/stream", stream, methods=["GET"])]
