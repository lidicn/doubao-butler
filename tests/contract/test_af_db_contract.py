"""DB→AF ask 通道契约闸（ADM 联动执行计划-DB 第 6 步 ② ＋ 契约表 §三/§五）。

契约表 §五 对这条通道的原话：断言 **ask/answer schema ＋ 鉴权 403 ＋ INBOX_KEY 前提**。
一条网络都不许出门：`butler.af_bridge.aiohttp` 在 setUp 里整体换成夹具，
请求 URL／body／headers 全记在内存里；test_00 先证夹具真被吃到（分母不是 0）。

三条口径本档咬住：
  1) answer 的 HMAC 口径与 AF `af_live._expect_sig` 同式（本档独立重算一遍，⛔ 只信实现）；
  2) AF 的 200 只代表「答案文件已写进 answer_inbox」，**不代表已消费**（格13/I4）——
     消费的唯一可见信号是该 ask 从 `/api/asks/pending` 消失；
  3) ≥400（含 403）一律判「没投递」，⛔ 拿它计进投递数；
  4) GET `/api/asks/pending` 也必须带 Bearer（批88：这条腿没鉴权，现网静默断了 2.3 天）——
     ⛔ 只给 POST 装鉴权。test_10 咬 GET 带头，test_11 咬「无令牌＝空 dict」而不是假装成功。
"""
import asyncio
import hashlib
import hmac
import json
import time
import unittest

import butler.af_bridge as af

BASE = "http://af-fake.invalid:9"
SENTINEL_TOKEN = "SENTINEL-AF-TOKEN"       # 测试自造，⛔ 现网值
SENTINEL_KEY = "SENTINEL-AF-INBOX-KEY"


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status, self._payload, self._text = status, payload, text

    async def text(self):
        return self._text

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeAiohttp:
    """只当假 http 客户端：任何请求都要先在 `routes` 里配好，配不到就大声报错（⛔ 静默放行）。"""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    class _Session:
        def __init__(self, outer):
            self.outer = outer

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def _do(self, verb, url, **kw):
            self.outer.requests.append((verb, url, kw))
            key = (verb, url)
            if key not in self.outer.routes:
                raise AssertionError("夹具没配这条路由：%s %s（⛔ 让代码走真网络或走默认）" % (verb, url))
            return self.outer.routes[key]

        def get(self, url, headers=None, timeout=None):
            return self._do("GET", url, headers=headers, timeout=timeout)

        def post(self, url, json=None, headers=None, timeout=None):
            return self._do("POST", url, json=json, headers=headers, timeout=timeout)

    def ClientSession(self):
        return _FakeAiohttp._Session(self)

    def ClientTimeout(self, **kw):
        return ("timeout", kw)

    class ClientError(Exception):
        """生产码 `except (asyncio.TimeoutError, aiohttp.ClientError)` 引用这一枚（真 aiohttp 有）；
        夹具缺它＝AttributeError 假红，⛔ 读成生产码的错。"""


class _AfHarness(unittest.TestCase):
    def setUp(self):
        self._saved = {k: getattr(af, k) for k in
                       ("aiohttp", "_base_url", "_api_token", "_inbox_key", "_answer_window",
                        "_pending", "_delivered_total", "_consumed_total", "_unconsumed_total")}
        af._pending.clear()
        af.init(None, base_url=BASE, poll_interval=5.0,
                api_token=SENTINEL_TOKEN, inbox_key=SENTINEL_KEY, answer_window_s=900.0)
        self.fake = None

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(af, k, v)
        af._pending.clear()

    def wire(self, routes):
        self.fake = _FakeAiohttp(routes)
        af.aiohttp = self.fake

    def run_coro(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def seed(self, ask_id="a1", room="客厅", prompt="要不要开空调？"):
        af._pending[ask_id] = {"room": room, "prompt": prompt, "prompt_ts": time.time()}

    def last_post(self):
        for verb, url, kw in reversed(self.fake.requests):
            if verb == "POST":
                return url, kw
        raise AssertionError("没有 POST 记录＝这条腿根本没发过请求")

    def last_get(self):
        for verb, url, kw in reversed(self.fake.requests):
            if verb == "GET":
                return url, kw
        raise AssertionError("没有 GET 记录＝这条腿根本没发过请求")


class AfDbContractTest(_AfHarness):
    def test_00_fake_http_is_in_effect(self):
        self.seed()
        self.wire({("POST", BASE + "/api/asks/answer"): _Resp(200, {"ok": True, "inbox": "/tmp/x.json"})})
        self.assertTrue(self.run_coro(af.on_user_reply("客厅", "要")))
        url, kw = self.last_post()
        self.assertIn("af-fake.invalid", url)
        self.assertNotIn("192.168.", url + str(kw), "URL 里出现内网地址＝夹具没生效，这条在打真机")

    def test_01_pending_answer_body_schema(self):
        self.seed()
        self.wire({("POST", BASE + "/api/asks/answer"): _Resp(200, {"ok": True, "inbox": "/tmp/x.json"})})
        self.run_coro(af.on_user_reply("客厅", "要"))
        _, kw = self.last_post()
        body = kw["json"]
        self.assertEqual(set(body.keys()), {"ask_id", "text", "room", "answer", "sig"})
        self.assertEqual(body["ask_id"], "a1")
        self.assertEqual(body["text"], "要")
        self.assertEqual(body["room"], "客厅")
        self.assertEqual(body["answer"], {})
        self.assertEqual(kw["headers"]["Authorization"], "Bearer " + SENTINEL_TOKEN)

    def test_02_signature_matches_an_independent_recomputation(self):
        """口径：hmac_sha256(key, "{ask_id}|{text}|{room}|{json(answer,sort_keys=True,ensure_ascii=False)}")。"""
        expected = hmac.new(SENTINEL_KEY.encode("utf-8"),
                            "a1|要|客厅|{}".encode("utf-8"), hashlib.sha256).hexdigest()
        self.assertEqual(af._sign_answer("a1", "要", "客厅", {}), expected)
        # answer 非空时也必须同式（sort_keys 保证 AF 侧重算一致）
        ans = {"b": 2, "a": 1}
        expected2 = hmac.new(SENTINEL_KEY.encode("utf-8"),
                             ("a1|要|客厅|" + json.dumps(ans, sort_keys=True, ensure_ascii=False)).encode("utf-8"),
                             hashlib.sha256).hexdigest()
        self.assertEqual(af._sign_answer("a1", "要", "客厅", ans), expected2)

    def test_03_inbox_key_absent_means_no_sig(self):
        """INBOX_KEY 前提：没这把钥匙时 DB 现在发的是**不带 sig** 的答案
        （AF 写手 af_api.py:706 会覆盖 sig，管家这把 key 眼下是装饰——已登记在 AF 桥那张单里）。"""
        af.init(None, base_url=BASE, poll_interval=5.0, api_token=SENTINEL_TOKEN, inbox_key="")
        self.seed()
        self.wire({("POST", BASE + "/api/asks/answer"): _Resp(200, {"ok": True, "inbox": "/tmp/x.json"})})
        self.assertTrue(self.run_coro(af.on_user_reply("客厅", "要")))
        _, kw = self.last_post()
        self.assertNotIn("sig", kw["json"])

    def test_04_http_403_is_not_delivery(self):
        self.seed()
        self.wire({("POST", BASE + "/api/asks/answer"): _Resp(403, None, "Forbidden")})
        before = af._delivered_total
        self.assertFalse(self.run_coro(af.on_user_reply("客厅", "要")))
        self.assertNotIn("delivered_ts", af._pending["a1"])
        self.assertEqual(af._delivered_total, before)

    def test_05_200_without_ok_is_not_delivery(self):
        self.seed()
        self.wire({("POST", BASE + "/api/asks/answer"): _Resp(200, {"ok": False, "error": "bad sig"})})
        self.assertFalse(self.run_coro(af.on_user_reply("客厅", "要")))
        self.assertNotIn("delivered_ts", af._pending["a1"])

    def test_06_delivery_is_not_consumption(self):
        """200+ok 只记「投递」；消费的唯一可见信号＝这条 ask 从 pending 里消失。"""
        self.seed()
        self.wire({("POST", BASE + "/api/asks/answer"): _Resp(200, {"ok": True, "inbox": "/tmp/x.json"}),
                   ("GET", BASE + "/api/asks/pending"): _Resp(200, {"asks": []})})
        self.assertTrue(self.run_coro(af.on_user_reply("客厅", "要")))
        self.assertIn("delivered_ts", af._pending["a1"])
        delivered, consumed = af._delivered_total, af._consumed_total
        self.run_coro(af._poll_once())
        self.assertEqual(af._delivered_total, delivered, "撤下不该再算一次投递")
        self.assertEqual(af._consumed_total, consumed + 1, "AF 撤下了却没记消费＝假绿的解药失效")
        self.assertNotIn("a1", af._pending)

    def test_07_pending_poll_reads_the_ask_fields(self):
        self.wire({("GET", BASE + "/api/asks/pending"):
                   _Resp(200, {"asks": [{"ask_id": "z9", "room": "书房", "prompt": "几度？"}]})})
        self.run_coro(af._poll_once())
        self.assertIn("z9", af._pending)
        self.assertEqual(af._pending["z9"]["room"], "书房")
        self.assertEqual(af._pending["z9"]["prompt"], "几度？")

    def test_08_get_failure_raises_instead_of_emptying_the_board(self):
        """pending 读失败必须炸出来：把它当成「AF 没有 ask」会静默清空待答表。"""
        self.seed()
        self.wire({("GET", BASE + "/api/asks/pending"): _Resp(500, None, "boom")})
        with self.assertRaises(RuntimeError):
            self.run_coro(af._poll_once())
        self.assertIn("a1", af._pending)

    def test_09_negative_control_same_path_both_ways(self):
        """负控：同一条 POST 腿，403 判假、200+ok 判真——两半各自留痕。"""
        self.seed()
        self.wire({("POST", BASE + "/api/asks/answer"): _Resp(403, None, "Forbidden")})
        self.assertFalse(self.run_coro(af.on_user_reply("客厅", "要")))
        af._pending.clear()
        self.seed()
        self.wire({("POST", BASE + "/api/asks/answer"): _Resp(200, {"ok": True, "inbox": "/tmp/y.json"})})
        self.assertTrue(self.run_coro(af.on_user_reply("客厅", "要")))
        self.assertIn("delivered_ts", af._pending["a1"])

    def test_10_pending_poll_carries_bearer(self):
        """批152：GET 腿的鉴权。生产码 af_bridge.py:124 传 headers=_auth_headers()，
        夹具此前只给 POST 开了 headers 口 ⇒ 这条腿从来没有尺。"""
        self.wire({("GET", BASE + "/api/asks/pending"): _Resp(200, {"asks": []})})
        self.run_coro(af._poll_once())
        url, kw = self.last_get()
        self.assertEqual(url, BASE + "/api/asks/pending")
        self.assertEqual(kw["headers"], {"Authorization": "Bearer " + SENTINEL_TOKEN})

    def test_11_pending_poll_without_token_sends_no_auth(self):
        """无令牌时 GET 传空 dict（⛔ 拼一把空 Bearer 假装带过鉴权）。"""
        af.init(None, base_url=BASE, poll_interval=5.0, api_token="", inbox_key=SENTINEL_KEY)
        self.wire({("GET", BASE + "/api/asks/pending"): _Resp(200, {"asks": []})})
        self.run_coro(af._poll_once())
        _, kw = self.last_get()
        self.assertEqual(kw["headers"], {})


if __name__ == "__main__":
    unittest.main()
