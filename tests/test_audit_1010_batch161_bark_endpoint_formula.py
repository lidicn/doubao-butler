"""批161 验收（先红）：Bark 明文腿的 endpoint 公式——⛔ 把 "push" 当路径段送出去。

现证（2026-10-10 08:0xZ，权威树容器现读，非推断）：
- `bark.py` 明文腿过去写的是 `push_url = _push_url if _push_url.endswith("/push") else _push_url + "/push"`；
  设备钥匙非空时 `_push_url = {base}/{key}` ⇒ 拼成 `{base}/{key}/push`。
- Bark 服务端（finab/bark-server）日志实证：单段路径命中路由 `/:device_key`，
  两段路径命中 `/:device_key/:body` ⇒ `{key}/push` 被服务端读成「正文＝push」。
  这正是 v2.4 路线图挂着的那条「Bark 乱推送根因（内容只有 "push"）」的机制。
- 同一公式在两处各拼一遍（`push()` 与 `flush_merged()`）⇒ 一处修好另一处仍坏。

本批只改明文腿的 URL 公式（一个属性、两处共用），⛔ 碰加密腿：
加密腿（`{key}/推送加密`）在同一服务端同样被当成正文段（服务端二进制里 `加密` 0 命中），
但换不换加密方案＝跨端契约＋发声路径红线，已单开 DCD 决策件，待裁后再落。

判据：
- 行为腿：用假 httpx 记录真正发出的 URL 与载荷——有钥匙时 POST 到 `{base}/{key}`（不带 `/push` 尾巴）、
  标题与正文按 JSON 字段送达；无钥匙时 **不发 HTTP** 直接 False（过去会 POST `/push` 收 400「device key is empty」）。
- 两腿同式：`push()` 与 `flush_merged()` 记到的 URL 必须逐字节相同。
- 形状腿＋校准：`endswith("/push")` 在 `bark.py` 归零（同一条判断先用合成样本证明尺会咬）。
"""
import os
import tempfile
import types
import unittest
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="butler_test_batch161_")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("TTS_DIR", _TMP)

from butler.integrations import bark as bark_mod

BASE = "http://bark.test:18273"
DEVICE_KEY = "unitkey"


def settings(key=DEVICE_KEY, encrypt_key="", encrypt_iv=""):
    return types.SimpleNamespace(
        bark_url=BASE,
        bark_key=key,
        bark_encrypt_key=encrypt_key,
        bark_encrypt_iv=encrypt_iv,
    )


class _Resp:
    status_code = 200
    text = "ok"


class _RecordingClient:
    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kw):
        _RecordingClient.calls.append((url, kw))
        return _Resp()


class EndpointBehavior(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _RecordingClient.calls = []
        self.patcher = mock.patch.object(bark_mod.httpx, "AsyncClient", _RecordingClient)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    async def test_push_with_key_posts_to_device_endpoint_not_push_suffix(self):
        b = bark_mod.Bark(settings())
        ok = await b.push("客厅温度 30 度", title="设备巡检")
        self.assertTrue(ok)
        self.assertEqual(len(_RecordingClient.calls), 1)
        url, kw = _RecordingClient.calls[0]
        self.assertEqual(url, f"{BASE}/{DEVICE_KEY}")
        self.assertFalse(url.endswith("/push"))
        self.assertEqual(kw["json"]["title"], "设备巡检")
        self.assertEqual(kw["json"]["body"], "客厅温度 30 度")

    async def test_push_without_key_fails_before_http(self):
        b = bark_mod.Bark(settings(key=""))
        ok = await b.push("内容", title="标题")
        self.assertFalse(ok)
        self.assertEqual(_RecordingClient.calls, [], "无钥匙仍发出了 HTTP＝把 /push 当设备钥匙用")

    async def test_flush_merged_uses_same_endpoint_as_push(self):
        b = bark_mod.Bark(settings())
        b._merge_cache = [
            {"title": "甲", "body": "内容甲", "group": "default", "ts": 1.0},
            {"title": "乙", "body": "内容乙", "group": "default", "ts": 2.0},
        ]
        sent = await b.flush_merged()
        self.assertEqual(sent, 1)
        flush_url = _RecordingClient.calls[-1][0]
        push_ok = await b.push("对照内容", title="对照")
        self.assertTrue(push_ok)
        push_url = _RecordingClient.calls[-1][0]
        self.assertEqual(flush_url, push_url, "两腿各拼一遍＝其中一腿会漂回 /push")

    async def test_encrypted_leg_untouched_this_batch(self):
        # 加密腿待 DCD 裁：本批⛔ 改它的 URL，只钉住"它仍走加密公式"这一现状
        b = bark_mod.Bark(settings(key=DEVICE_KEY, encrypt_key="k" * 16, encrypt_iv="v" * 16))
        self.assertTrue(b._encrypt_enabled)
        self.assertEqual(len(_RecordingClient.calls), 0)


def push_suffix_hits(text: str) -> list[int]:
    """明文腿拼 /push 尾巴的判据行号（`_push_url` 自己的 def 行不算）。"""
    return [i for i, line in enumerate(text.splitlines(), 1)
            if 'endswith("/push")' in line and "def _push_url" not in line]


class ShapeRuler(unittest.TestCase):
    def test_ruler_bites_synthetic_text(self):
        # 负控：尺子必须咬得住含该公式的源码，否则下面的 0 命中毫无意义
        synthetic = 'def f(self):\n    u = self._push_url if self._push_url.endswith("/push") else "x"\n'
        self.assertEqual(push_suffix_hits(synthetic), [2])

    def test_no_push_suffix_formula_in_source(self):
        with open(bark_mod.__file__, encoding="utf-8") as f:
            hits = push_suffix_hits(f.read())
        self.assertEqual(hits, [], f"明文腿仍在拼 /push 尾巴：行 {hits}")

    def test_both_legs_share_one_property(self):
        with open(bark_mod.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertEqual(src.count("self._plain_endpoint"), 2,
                         "push() 与 flush_merged() 必须各引用一次同一个公式属性")


if __name__ == "__main__":
    unittest.main()
