"""批48（P1-13 超时腿）：一次性的 HTTP 请求必须带超时；WS 握手读必须有界。

审计原文（最终审计报告 P1-13／_缺陷汇总 表行 51、54）说「8 处 ClientSession 漏 timeout
（默认 300s）」＋「WS 握手后 `receive_json()` 无超时 ⇒ HA 不回帧即永久挂起、永不重试」。
本批开工前先用 AST 尺在权威树现读重量（读数 `readings/1007_b48/b48_sess_ast_1007.txt`）：
10 枚会话构造点里，真缺口只有 1 处一次性请求（`core/tts_baidu.py` 取 token 的 GET），
另外两枚是 HA 长连接（`event_stream`／`xiaomi_ear` 的 `ws_connect`）——它们**有** heartbeat
保活，缺的是**握手那几帧的读取上界**，而会话级 total 超时放在长连接上是**错的**
（到点就把活着的订阅掐断），所以单独有第五条腿把这条约束钉住。

判据全部走同一把 AST helper（`_scan`）：对真文件跑、对合成夹具跑，⛔ 两套切法。
夹具腿（`test_ruler_*`）先证明这把尺会咬——否则前面几条的「0 缺口」可以只是尺写窄了。

只读生产码，⛔ 起网络、⛔ 连 HA。
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "request"}
RECV_METHODS = {"receive", "receive_json", "receive_str", "receive_bytes"}

FIXTURE_UNWRAPPED = (
    "import aiohttp\n"
    "async def f():\n"
    "    async with aiohttp.ClientSession() as s:\n"
    "        async with s.ws_connect('ws://h/api/websocket', heartbeat=30) as ws:\n"
    "            m = await ws.receive_json()\n"
    "            return m\n"
)
FIXTURE_WRAPPED = (
    "import aiohttp\n"
    "import asyncio\n"
    "async def f():\n"
    "    async with aiohttp.ClientSession() as s:\n"
    "        async with s.ws_connect('ws://h/api/websocket', heartbeat=30) as ws:\n"
    "            m = await asyncio.wait_for(ws.receive_json(), timeout=15)\n"
    "            return m\n"
)
FIXTURE_HTTP_MISSING = (
    "import aiohttp\n"
    "async def f():\n"
    "    async with aiohttp.ClientSession() as s:\n"
    "        async with s.get('http://h/x') as r:\n"
    "            return await r.text()\n"
)
FIXTURE_HTTP_COVERED = (
    "import aiohttp\n"
    "async def f():\n"
    "    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=9)) as s:\n"
    "        async with s.get('http://h/x') as r:\n"
    "            return await r.json()\n"
)


def _is_session_ctor(node) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != "ClientSession":
        return False
    v = node.func.value
    return isinstance(v, ast.Name) and v.id in ("aiohttp", "aio")


def _has_kw(call, name: str) -> bool:
    return any(kw.arg == name for kw in call.keywords)


def _parent_map(tree):
    parents = {}
    for parent in ast.walk(tree):
        for _field, value in ast.iter_fields(parent):
            if isinstance(value, ast.AST):
                parents[value] = parent
            elif isinstance(value, list):
                for ch in value:
                    if isinstance(ch, ast.AST):
                        parents[ch] = parent
    return parents


def _session_scope(tree, ctor):
    """返回 (会话变量名, AsyncWith 节点)；不是 `async with ClientSession() as n` 形态 ⇒ (None, None)。"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncWith):
            continue
        for item in node.items:
            if item.context_expr is ctor:
                if isinstance(item.optional_vars, ast.Name):
                    return item.optional_vars.id, node
    return None, None


def _bounded_by_wait_for(node, parents) -> bool:
    cur = parents.get(node)
    for _ in range(12):
        if cur is None:
            return False
        if (isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute)
                and cur.func.attr == "wait_for"):
            return True
        cur = parents.get(cur)
    return False


def _receiver_bound_to_wsconnect(node, parents) -> bool:
    """`await ws.receive*()` 里的 ws 是不是 `... ws_connect(...) as ws` 绑出来的那个。"""
    f = node.func
    if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)):
        return False
    name = f.value.id
    cur = parents.get(node)
    for _ in range(20):
        if cur is None:
            return False
        if isinstance(cur, (ast.With, ast.AsyncWith)):
            for item in cur.items:
                if (isinstance(item.optional_vars, ast.Name) and item.optional_vars.id == name
                        and isinstance(item.context_expr, ast.Call)
                        and isinstance(item.context_expr.func, ast.Attribute)
                        and item.context_expr.func.attr == "ws_connect"):
                    return True
        cur = parents.get(cur)
    return False


def _scan(tree):
    parents = _parent_map(tree)
    rows = {"ctors": [], "missing_http": [], "recv_unwrapped": [], "recv_total": [], "ws_sessions": []}
    for ctor in (n for n in ast.walk(tree) if _is_session_ctor(n)):
        var, awith = _session_scope(tree, ctor)
        ctor_timeout = _has_kw(ctor, "timeout")
        scope = awith if awith is not None else ctor
        http_n = ws_n = 0
        for n in ast.walk(scope):
            if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute):
                continue
            attr = n.func.attr
            if var is not None and isinstance(n.func.value, ast.Name) and n.func.value.id == var:
                if attr in HTTP_METHODS:
                    http_n += 1
                    if not (ctor_timeout or _has_kw(n, "timeout")):
                        rows["missing_http"].append((n.lineno, attr))
                elif attr == "ws_connect":
                    ws_n += 1
                    rows["ws_sessions"].append({"line": n.lineno, "heartbeat": _has_kw(n, "heartbeat")})
            if attr in RECV_METHODS and _receiver_bound_to_wsconnect(n, parents):
                rows["recv_total"].append(n.lineno)
                if not _bounded_by_wait_for(n, parents):
                    rows["recv_unwrapped"].append(n.lineno)
        rows["ctors"].append({"line": ctor.lineno, "ctor_timeout": ctor_timeout,
                              "http": http_n, "ws": ws_n, "shape": "named" if var else "other"})
    return rows


def _scan_file(rel: str):
    p = REPO / rel
    return _scan(ast.parse(p.read_text(encoding="utf-8"))), p


def _scan_source(src: str):
    return _scan(ast.parse(src))


class TestAuditBatch48SessionTimeout(unittest.TestCase):
    """一次性 HTTP 请求的超时覆盖（表行 51 那一类）。"""

    def test_tree_has_no_uncovered_http_request(self):
        missing, ctors, files = [], 0, 0
        for p in sorted((REPO / "butler").rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            files += 1
            rows = _scan(ast.parse(p.read_text(encoding="utf-8", errors="replace")))
            ctors += len(rows["ctors"])
            for line, attr in rows["missing_http"]:
                missing.append("%s:%d %s()" % (p.relative_to(REPO).as_posix(), line, attr))
        self.assertGreater(ctors, 0, "名册尺本身没找到任何会话构造点＝尺失效")
        self.assertEqual([], missing,
                         "一次性 aiohttp 请求无 timeout（默认 300s 挂死）：%s" % missing)

    def test_baidu_token_request_holds_timeout(self):
        rows, path = _scan_file("butler/core/tts_baidu.py")
        self.assertEqual(2, len(rows["ctors"]),
                         "%s 会话构造点数与批48 现读名册不符，先重量再谈修法" % path.name)
        self.assertEqual([], rows["missing_http"],
                         "取 token 的 GET 无超时：HA/百度不回即把整条播报链挂住 %s" % path.name)

    def test_ruler_bites_on_synthetic_uncovered_request(self):
        self.assertEqual([(4, "get")], _scan_source(FIXTURE_HTTP_MISSING)["missing_http"])
        self.assertEqual([], _scan_source(FIXTURE_HTTP_COVERED)["missing_http"])


class TestAuditBatch48HandshakeTimeout(unittest.TestCase):
    """WS 握手帧的读取上界（表行 54 那一类：只回 ping 不回 auth_ok ⇒ 永久挂起且永不重连）。"""

    def test_event_stream_handshake_reads_are_bounded(self):
        rows, path = _scan_file("butler/core/event_stream.py")
        self.assertEqual(3, len(rows["recv_total"]),
                         "%s 握手/快照读帧点数与批48 现读名册（3 枚）不符，先重量再谈修法" % path.name)
        self.assertEqual([], rows["recv_unwrapped"],
                         "%s 握手/快照帧的 receive 没有 wait_for 上界：HA 只回 ping 不回帧即永久挂起且永不重连" % path.name)

    def test_xiaomi_ear_handshake_reads_are_bounded(self):
        rows, path = _scan_file("butler/core/xiaomi_ear.py")
        self.assertEqual(2, len(rows["recv_total"]),
                         "%s 握手读帧点数与批48 现读名册（2 枚）不符，先重量再谈修法" % path.name)
        self.assertEqual([], rows["recv_unwrapped"],
                         "%s 握手帧的 receive_json 没有 wait_for 上界" % path.name)

    def test_ruler_bites_on_synthetic_unwrapped_receive(self):
        self.assertEqual([5], _scan_source(FIXTURE_UNWRAPPED)["recv_unwrapped"])
        self.assertEqual([], _scan_source(FIXTURE_WRAPPED)["recv_unwrapped"])

    def test_ws_sessions_keep_heartbeat_and_no_session_total_timeout(self):
        """长连接那两枚：保活靠 heartbeat，⛔ 拿会话级 total 超时去「修 P1-13」。

        到点的 total 会把还活着的订阅掐断＝把「无声停摆」换成「每 N 秒抖一次」，
        这条腿把修复形状钉死在「握手读取有界」，⛔ 会话级超时。
        """
        for rel in ("butler/core/event_stream.py", "butler/core/xiaomi_ear.py"):
            rows, path = _scan_file(rel)
            ws_ctors = [c for c in rows["ctors"] if c["ws"] > 0]
            self.assertEqual(1, len(ws_ctors), "%s 应有且只有一枚长连接会话" % path.name)
            self.assertFalse(ws_ctors[0]["ctor_timeout"],
                             "%s 给长连接会话加了 total 超时＝订阅会被掐断" % path.name)
            self.assertTrue(rows["ws_sessions"][0]["heartbeat"],
                            "%s 的 ws_connect 丢了 heartbeat＝活体探测没了" % path.name)


if __name__ == "__main__":
    unittest.main()
