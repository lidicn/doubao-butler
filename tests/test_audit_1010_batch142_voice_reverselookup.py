"""批142 验收（先红）：⛔ 静默换嗓子——角色档嗓子名先反查对照表再落全局默认。

依据：DCD 20261008-DB五件 件四 甲（现读 45 次合成 10 次丢角色嗓、`fc7f98b9`(云扬) 48h 0 次开口）。
甲口径：
  - L2（`_voice_for` nowvoice 分支）：落到全局默认 **之前**，先拿角色档嗓子名
    （如 `zh-CN-YunyangNeural`）去 `nowvoice_client.SAMPLE_VOICES` 反查 8 位号；
  - L1（备引擎兜底）：兜底同样带上角色档的嗓子名/反查号，⛔ 直接吃全局晓晓/小晨；
  - 显式 8 位号优先级照旧（原语义不动）；
  - 反查不到而被迫落全局默认 ⇒ **必须 error 记账**（「不静默」的另一半）；
  - L2b/L3/L4/L5 只登记⛔ 动码（kokoro 兜底照旧全局，本文件配 guard 腿钉住「⛔ 顺手扩面」）。

改 1 枚文件 butler/tts/manager.py（甲的边界）。
"""
import unittest
from types import SimpleNamespace

from butler.tts import manager as mgr_mod
from butler.tts.base import TTSResult
from butler.tts.manager import TTSManager

_XIAOYI_HEX = "5bc21988"   # nowvoice_client.SAMPLE_VOICES["zh-CN-Xiaoyi"]
_YUNYANG_HEX = "fc7f98b9"  # SAMPLE_VOICES["zh-CN-Yunyang"]


def _mgr():
    m = TTSManager.__new__(TTSManager)
    m.s = SimpleNamespace(
        tts_primary="nowvoice",
        nowvoice_voice="afeb4759",        # 全局默认＝小晨（女）——现网 compose 现值
        tts_edge_voice="zh-CN-XiaoxiaoNeural",  # 全局默认＝晓晓（女）
        tts_kokoro_voice="zm_yunjian",
        tts_cache_enabled=False,
        tts_speed=1.0,
    )
    m.bark = None
    m.ha = None
    m._breakers = {}

    class _FakeKokoro:
        async def is_available(self):
            return True

    m.kokoro = _FakeKokoro()
    return m


class ShapeRuler(unittest.TestCase):
    def test_manager_imports_the_voice_table(self):
        with open(mgr_mod.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("SAMPLE_VOICES", src)


class VoiceForReversalTests(unittest.TestCase):
    def test_explicit_hex_still_wins(self):
        # 原语义 guard：8 位号优先，反查只救「写了名字没写号」的角色档
        m = _mgr()
        self.assertEqual(
            m._voice_for("", voice="zh-CN-XiaoyiNeural", backend="nowvoice",
                         nowvoice_voice="deadbeef"), "deadbeef")

    def test_voice_field_hex_wins(self):
        m = _mgr()
        self.assertEqual(
            m._voice_for("", voice=_XIAOYI_HEX, backend="nowvoice",
                         nowvoice_voice=None), _XIAOYI_HEX)

    def test_role_name_yunyang_resolves_to_men_hex(self):
        # 甲的心脏格①：档里写 zh-CN-YunyangNeural ⇒ 反查 fc7f98b9，⛔ 落小晨
        m = _mgr()
        got = m._voice_for("", voice="zh-CN-YunyangNeural", backend="nowvoice",
                           nowvoice_voice=None)
        self.assertEqual(got, _YUNYANG_HEX)

    def test_role_name_xiaoyi_resolves(self):
        # 申请里的活体形状（07:08:09 那条：声称晓伊、实际小晨）
        m = _mgr()
        got = m._voice_for("", voice="zh-CN-XiaoyiNeural", backend="nowvoice",
                           nowvoice_voice=None)
        self.assertEqual(got, _XIAOYI_HEX)

    def test_role_field_hex_reverse_lookup_when_in_nowvoice_slot(self):
        # nowvoice_voice 槽位里写的是名字而非号 ⇒ 同样反查
        m = _mgr()
        got = m._voice_for("", voice=None, backend="nowvoice",
                           nowvoice_voice="zh-CN-YunyangNeural")
        self.assertEqual(got, _YUNYANG_HEX)

    def test_unresolvable_name_falls_back_with_error_log(self):
        m = _mgr()
        with self.assertLogs("butler.tts.manager", level="ERROR") as logs:
            got = m._voice_for("", voice="zh-CN-NobodyNeural", backend="nowvoice",
                               nowvoice_voice=None)
        self.assertEqual(got, "afeb4759")
        self.assertTrue(len(logs.output) >= 1)

    def test_no_voice_input_stays_silent_global(self):
        # 路径没带角色嗓（briefing 那类）＝合法全局，⛔ error 记账——防把尺子写宽
        m = _mgr()
        with self.assertNoLogs("butler.tts.manager", level="ERROR"):
            got = m._voice_for("", voice=None, backend="nowvoice", nowvoice_voice=None)
        self.assertEqual(got, "afeb4759")


class FallbackVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, backend, *, voice=None, nowvoice_voice=None, fail_engines=()):
        m = _mgr()
        calls = []

        async def fake_try(engine, text, v, speed):
            calls.append((engine, v))
            if engine in fail_engines:
                raise RuntimeError(f"{engine} down")
            return TTSResult(filename="f", public_url="u", provider=engine,
                             voice=v, duration_ms=0)

        m._try_engine = fake_try
        result = await m.synthesize(
            "测试", voice=voice, backend=backend, nowvoice_voice=nowvoice_voice
        )
        return calls, result

    async def test_edge_fallback_carries_role_name_not_global_xiaoxiao(self):
        # L1（甲）：nowvoice 主挂 ⇒ 落备 edge 时带角色档嗓子名，⛔ 全局晓晓
        calls, _ = await self._run(
            "nowvoice", voice="zh-CN-YunyangNeural", fail_engines=("nowvoice",))
        self.assertEqual(calls[0], ("nowvoice", _YUNYANG_HEX))
        self.assertEqual(calls[1], ("edge-tts", "zh-CN-YunyangNeural"))

    async def test_nowvoice_fallback_gets_reversed_hex(self):
        # L1（甲）：edge 主挂 ⇒ 落备 nowvoice 时反查角色名成 8 位号，⛔ 全局小晨
        calls, _ = await self._run(
            "edge-tts", voice="zh-CN-YunyangNeural", fail_engines=("edge-tts",))
        self.assertEqual(calls[0], ("edge-tts", "zh-CN-YunyangNeural"))
        self.assertEqual(calls[1], ("nowvoice", _YUNYANG_HEX))

    async def test_kokoro_fallback_stays_global_registered_only(self):
        # 甲的边界：L2b/L3/L4/L5⛔ 动——kokoro 兜底维持全局，本腿钉住「⛔ 顺手扩面」
        calls, _ = await self._run(
            "edge-tts", voice="zh-CN-YunyangNeural",
            fail_engines=("edge-tts", "nowvoice"))
        self.assertEqual(calls[-1], ("kokoro", "zm_yunjian"))


if __name__ == "__main__":
    unittest.main()
