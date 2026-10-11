"""批201 DB47 门：半死 HA（TCP 通、永不开成 WebSocket）把 `ws_connect` 自己吊死。

现读（`workorders/readings/1011/b201_db47_ws_timeout_1011.txt`，本机 aiohttp 3.14.1，⛔ 抄成现网 3.9.5 读数）：
    [A] 裸 `ws_connect`（现网形状）      elapsed=15.0s ⇒ 撞我的闸＝无界
    [D] `timeout=ClientWSTimeout(...)`  elapsed=15.0s ⇒ 无界（印证报告第十五轮自己的疑点：只管收发，管不到握手）
    [E] `asyncio.wait_for(ws_connect)`  elapsed=2.0s  ⇒ 有界
选 [E] 而不是 `ClientTimeout`：`total=` 是整次操作的上界，健康长连接会被它掐死；`sock_read=` 对 ws 帧
在 3.9.5 上的语义我在这台机上验不了 ⇒ 只挑不依赖 aiohttp 版本语义的那一种（asyncio 自己的上界），
且上界只套在「连接＋握手」那一段，稳态收发一点没被管。

两条腿（`event_stream._connect` / `xiaomi_ear._connect`）共用一个单点，⛔ 各写各的 try（根因家族①）。
"""
import ast
import asyncio
import pathlib
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUTLER = ROOT / "butler"
SITES = (BUTLER / "core" / "event_stream.py", BUTLER / "core" / "xiaomi_ear.py")


def bare_ws_connect_files(root: pathlib.Path) -> list[str]:
    """返回「直接调 `session.ws_connect(...)` 但⛔ 挂在单点上」的文件（相对路径）。

    按 AST 归属：定义单点的那个模块本身不算病灶，其余任何 `.ws_connect(` 调用都算。
    """
    bad = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "ws_connect.py" or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "ws_connect"):
                bad.append(str(path.relative_to(root)))
                break
    return bad


async def _half_dead_server():
    """accept 之后一个字节都不发的靶子（HA 半死最真实形态）。"""
    async def handler(reader, writer):
        handler.held.append(writer)
        await asyncio.sleep(3600)

    handler.held = []
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], handler


class TestBatch201Db47BoundedConnect(unittest.TestCase):
    """真连接行为：上界套得住半死靶子，且⛔ 误伤正常握手。"""

    def test_01_half_dead_handshake_is_bounded(self):
        import aiohttp
        from butler.core import ws_connect as wsc

        async def go():
            server, port, handler = await _half_dead_server()
            await server.start_serving()
            t0 = time.monotonic()
            try:
                async with aiohttp.ClientSession() as s:
                    async with wsc.bounded_ws_connect(s, f"http://127.0.0.1:{port}/api/websocket",
                                                      connect_timeout=1.0):
                        self.fail("半死靶子竟然开成了连接")
            except (asyncio.TimeoutError, aiohttp.ClientError):
                pass
            finally:
                elapsed = time.monotonic() - t0
                server.close()
                for w in handler.held:
                    w.close()
                await asyncio.sleep(0)
            return elapsed

        elapsed = asyncio.run(go())
        self.assertLess(elapsed, 4.0, f"上界没咬住：{elapsed:.1f}s 才回来")

    def test_02_healthy_ws_still_connects(self):
        """反向用例（⛔ 只测拦截会漏掉拦太宽）：正常应答的握手必须照放行、拿到可用连接。"""
        from aiohttp import web
        from butler.core import ws_connect as wsc

        async def handler(request):
            ws = web.WebSocketResponse(heartbeat=None)
            await ws.prepare(request)
            await ws.send_json({"type": "auth_required"})
            async for _msg in ws:
                break
            return ws

        async def go():
            app = web.Application()
            app.router.add_get("/api/websocket", handler)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = runner.addresses[0][1]
            try:
                import aiohttp
                async with aiohttp.ClientSession() as s:
                    async with wsc.bounded_ws_connect(s, f"http://127.0.0.1:{port}/api/websocket",
                                                      connect_timeout=5.0) as ws:
                        msg = await asyncio.wait_for(ws.receive_json(), timeout=5.0)
                return msg.get("type")
            finally:
                await runner.cleanup()

        self.assertEqual(asyncio.run(go()), "auth_required")

    def test_03_body_exception_still_closes_the_socket(self):
        import aiohttp
        from aiohttp import web
        from butler.core import ws_connect as wsc

        async def handler(request):
            ws = web.WebSocketResponse(heartbeat=None)
            await ws.prepare(request)
            return ws

        async def go():
            app = web.Application()
            app.router.add_get("/api/websocket", handler)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = runner.addresses[0][1]
            try:
                async with aiohttp.ClientSession() as s:
                    try:
                        async with wsc.bounded_ws_connect(
                                s, f"http://127.0.0.1:{port}/api/websocket",
                                connect_timeout=5.0) as ws:
                            raise RuntimeError("体内出错")
                    except RuntimeError:
                        pass
                    await asyncio.sleep(0.05)
                    return ws.closed
            finally:
                await runner.cleanup()

        self.assertTrue(asyncio.run(go()), "体内异常后 socket 没关＝连接泄漏")


class TestBatch201Db47SinglePoint(unittest.TestCase):
    """两条腿必须挂在这一个点上，⛔ 再各写各的。"""

    def test_04_no_bare_ws_connect_outside_the_single_point(self):
        self.assertEqual(bare_ws_connect_files(BUTLER), [],
                         "有站点裸调 ws_connect＝半死 HA 能把它吊死")

    def test_05_shape_ruler_bites_synthetic(self):
        src = ('import aiohttp\n'
               'async def f(session):\n'
               '    async with session.ws_connect("ws://x", heartbeat=30) as ws:\n'
               '        pass\n')
        found = [n for n in ast.walk(ast.parse(src))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "ws_connect"]
        self.assertEqual(len(found), 1, "尺子咬不住裸调用⇒test_04 是摆设")

    def test_06_shape_ruler_passes_the_single_point(self):
        """单点自己的 `session.ws_connect(` ⛔ 不能被判成病灶——这条是放行侧校准。"""
        src = (BUTLER / "core" / "ws_connect.py").read_text(encoding="utf-8")
        self.assertIn("ws_connect(", src)
        self.assertNotIn("butler/core/ws_connect.py", bare_ws_connect_files(BUTLER))


if __name__ == "__main__":
    unittest.main()
