r"""批15 验收（TDD，先红后绿）：`_env_bool` 恒 False 三元 + TTS 缓存 key 缺 speed。

来源（`doc/审计报告/_缺陷汇总_供审阅.md`，:N＝该文件正文行号）：
  - 表行 6（RO P0-2，:26）「`_env_bool` 三元两分支均 False → 布尔配置永不为 True」。
    权威树现读落点 `butler/config.py:36-42`，死三元在 `:41`（报告写的 :31-37 已漂移，按符号定位）。
  - 表行 14（R1 P1-7，:34）「TTS 缓存 key 不含 speed → 语速设置静默失效」。
    现读：同一句 `sha1(f"{{text}}|{{voice}}|{{engine}}")` 抄在 4 个文件——
    `manager.py:101`（读侧）、`edge_tts.py:58`、`kokoro.py:33`、`nowvoice_tts.py:83`（写侧）。

为什么两腿同批（不是搭车）：`_env_bool` 全仓只有 3 个调用点（`config.py:215/216/278` 现读），
其中唯一 default=True 的是 `tts_cache_enabled`，而 `.env` 里没有 `TTS_CACHE_ENABLED` 这个键（grep 0 命中）
⇒ 补完 `_env_bool` 等价于把 TTS 磁盘缓存**打开**；缓存一开，「改语速仍播旧音频」立刻从潜在变现役。
所以 speed 进 key 必须与开闸同一批落码，且 4 个落点收敛成一条公式（否则读写两侧命名一错开，缓存只在纸面上存在）。
"""
from __future__ import annotations

import asyncio
import hashlib
import httpx
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from butler.config import Settings, _env_bool
from butler.tts.base import TTSResult
from butler.tts.edge_tts import EdgeTTS
from butler.tts.kokoro import KokoroTTS
from butler.tts.manager import TTSManager

REQUIRED_KEYS = ("DOUBAO_API_KEY", "DESKPILOT_API_TOKEN", "TASK_REPORT_TOKEN", "BUTLER_WEB_PASSWORD")
PROBE_KEY = "B15_PROBE_FLAG"
# 要钉住的命名公式：text|voice|engine|speed（第 4 段就是这次要补进去的）
OLD3 = lambda t, v, e: hashlib.sha1(f"{t}|{v}|{e}".encode("utf-8")).hexdigest()
NEW4 = lambda t, v, e, s: hashlib.sha1(f"{t}|{v}|{e}|{s}".encode("utf-8")).hexdigest()
CACHE_FILES = ["butler/tts/manager.py", "butler/tts/edge_tts.py",
               "butler/tts/kokoro.py", "butler/tts/nowvoice_tts.py"]


class EnvMixin:
    def _pin_env(self, **kv):
        old = {}
        for k, v in kv.items():
            old[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

        def restore():
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        self.addCleanup(restore)


class EnvBoolTest(EnvMixin, unittest.TestCase):
    def test_unset_with_true_default_returns_true(self):
        self._pin_env(**{PROBE_KEY: None})
        self.assertIs(_env_bool(PROBE_KEY, True), True)

    def test_empty_value_with_true_default_returns_default(self):
        self._pin_env(**{PROBE_KEY: ""})
        self.assertIs(_env_bool(PROBE_KEY, True), True)

    def test_unset_with_false_default_stays_false(self):
        """安全开关方向不许被这次修复翻成 fail-open。"""
        self._pin_env(**{PROBE_KEY: None})
        self.assertIs(_env_bool(PROBE_KEY, False), False)

    def test_true_spellings(self):
        for v in ("1", "true", "TRUE", " yes ", "On"):
            self._pin_env(**{PROBE_KEY: v})
            with self.subTest(v=v):
                self.assertIs(_env_bool(PROBE_KEY, False), True)

    def test_false_spellings(self):
        for v in ("0", "false", "no", "off"):
            self._pin_env(**{PROBE_KEY: v})
            with self.subTest(v=v):
                self.assertIs(_env_bool(PROBE_KEY, True), False)

    def test_garbage_falls_back_to_default(self):
        self._pin_env(**{PROBE_KEY: "maybe"})
        self.assertIs(_env_bool(PROBE_KEY, True), True)
        self._pin_env(**{PROBE_KEY: "maybe"})
        self.assertIs(_env_bool(PROBE_KEY, False), False)


class SettingsLoadTest(EnvMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="b15_cfg_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        pin = {k: "probe-not-a-secret" for k in REQUIRED_KEYS}
        pin["DATA_DIR"] = self.tmp
        pin["TTS_CACHE_ENABLED"] = None
        self._pin_env(**pin)

    def test_cache_flag_on_by_default(self):
        self.assertIs(Settings.load().tts_cache_enabled, True)

    def test_explicit_false_still_disables(self):
        self._pin_env(**{"TTS_CACHE_ENABLED": "false"})
        self.assertIs(Settings.load().tts_cache_enabled, False)


class CacheReaderTest(EnvMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="b15_tts_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.text = "客厅的灯已经关了"
        self.voice = "zm_yunxi"
        self.engine = "kokoro"
        self.s = Settings(data_dir=self.tmp, tts_dir=self.tmp,
                          base_url="http://192.168.2.200:8095",
                          tts_cache_enabled=True, tts_speed=1.0, tts_primary=self.engine)
        self.mgr = TTSManager(self.s)

    def _install(self, filename_for):
        """替身引擎：按 filename_for(speed) 落一个 >5000B 的假 mp3（读侧的门槛在 manager.py:104）。"""
        async def fake(engine, text, voice, speed):
            fn = filename_for(speed)
            (Path(self.s.tts_dir) / fn).write_bytes(b"\xff" * 6000)
            return TTSResult(filename=fn, public_url=f"{self.s.base_url}/tts/{fn}",
                             provider=engine, voice=voice, duration_ms=1, cached=False)

        self.mgr._try_engine = fake

    def _synth(self, speed):
        return asyncio.run(self.mgr.synthesize(self.text, voice=self.voice,
                                               backend=self.engine, speed=speed))

    def test_same_text_different_speed_must_not_reuse_cache(self):
        self._install(lambda sp: f"{OLD3(self.text, self.voice, self.engine)}.mp3")
        first, second = self._synth(0.8), self._synth(1.2)
        self.assertIsNot(first, None)
        self.assertIsNot(second, None)
        self.assertIsNot(second.cached, True, "语速不同却命中了旧语速的缓存文件")

    def test_same_speed_does_reuse_cache(self):
        """反向腿：加了 speed ≠ 把缓存关掉——同语速必须仍命中自己的文件。"""
        self._install(lambda sp: f"{NEW4(self.text, self.voice, self.engine, sp)}.mp3")
        first, second = self._synth(0.8), self._synth(0.8)
        self.assertIsNot(first, None)
        self.assertIsNot(second, None)
        self.assertIs(second.cached, True)
        self.assertEqual(second.filename, first.filename)

    def test_disabled_cache_never_hits(self):
        self.s.tts_cache_enabled = False
        self._install(lambda sp: f"{NEW4(self.text, self.voice, self.engine, sp)}.mp3")
        self._synth(0.8)
        second = self._synth(0.8)
        self.assertIsNot(second, None)
        self.assertIsNot(second.cached, True)


class CacheWriterTest(EnvMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="b15_w_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.text = "主卧的空调开了"
        self.voice = "zm_yunxia"
        self.s = Settings(data_dir=self.tmp, tts_dir=self.tmp,
                          base_url="http://192.168.2.200:8095",
                          tts_cache_enabled=True, tts_speed=1.0)

    def test_edge_writer_name_includes_speed(self):
        eng = EdgeTTS(self.s)

        async def fake_once(text, voice, speed, path):
            Path(path).write_bytes(b"\xff" * 4000)

        eng._synth_once = fake_once
        a = asyncio.run(eng.synthesize(self.text, self.voice, 0.8))
        b = asyncio.run(eng.synthesize(self.text, self.voice, 1.2))
        self.assertNotEqual(a.filename, b.filename)
        self.assertEqual(a.filename, f"{NEW4(self.text, self.voice, 'edge-tts', 0.8)}.mp3")

    def test_kokoro_writer_name_includes_speed(self):
        eng = KokoroTTS(self.s)
        real = httpx.AsyncClient

        class FakeResp:
            content = b"\xff" * 4000

            def raise_for_status(self):
                return None

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **k):
                return FakeResp()

        httpx.AsyncClient = FakeClient
        try:
            a = asyncio.run(eng.synthesize(self.text, self.voice, 0.8))
            b = asyncio.run(eng.synthesize(self.text, self.voice, 1.2))
        finally:
            httpx.AsyncClient = real
        self.assertNotEqual(a.filename, b.filename)
        self.assertEqual(a.filename, f"{NEW4(self.text, self.voice, 'kokoro', 0.8)}.mp3")


class SingleFormulaTest(unittest.TestCase):
    """一条公式四个落点：sha1 只许住在 base.py，四个落点各自引用 cache_filename。"""

    def _tree(self, rel):
        import ast
        root = Path(__file__).resolve().parents[1]
        return ast.parse((root / rel).read_text(encoding="utf-8-sig")), (root / rel)

    def test_no_inline_sha1_left_in_tts_files(self):
        import ast
        left = []
        for rel in CACHE_FILES:
            tree, path = self._tree(rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "sha1":
                    left.append("%s:%d" % (rel, node.lineno))
        self.assertEqual(left, [], "仍自己算 sha1 的落点: %s" % left)

    def test_every_site_references_the_helper(self):
        import ast
        missing = []
        for rel in CACHE_FILES:
            tree, _ = self._tree(rel)
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Attribute, ast.Name)):
                    names.add(node.attr if isinstance(node, ast.Attribute) else node.id)
            if "cache_filename" not in names:
                missing.append(rel)
        self.assertEqual(missing, [], "未引用单一真源 cache_filename 的落点: %s" % missing)

    def test_every_site_calls_the_helper_with_speed(self):
        """四个落点各自必须把本地 `speed` 传给唯一公式（写成常量/漏传＝该腿红）。"""
        import ast
        bad = []
        for rel in CACHE_FILES:
            tree, _ = self._tree(rel)
            calls = [n for n in ast.walk(tree)
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                     and n.func.id == "cache_filename"]
            if len(calls) != 1:
                bad.append("%s calls=%d" % (rel, len(calls)))
                continue
            args = calls[0].args
            if len(args) != 4 or not (isinstance(args[3], ast.Name) and args[3].id == "speed"):
                bad.append("%s:%d args=%s" % (rel, calls[0].lineno,
                                              [ast.unparse(a) for a in args]))
        self.assertEqual(bad, [], "cache_filename 调用形状不符: %s" % bad)

    def test_helper_keys_differ_when_any_input_differs(self):
        from butler.tts.base import cache_filename
        base = cache_filename("同一句", "zm_a", "kokoro", 1.0)
        self.assertEqual(base, cache_filename("同一句", "zm_a", "kokoro", 1.0))
        for other in (cache_filename("另一句", "zm_a", "kokoro", 1.0),
                      cache_filename("同一句", "zm_b", "kokoro", 1.0),
                      cache_filename("同一句", "zm_a", "edge-tts", 1.0),
                      cache_filename("同一句", "zm_a", "kokoro", 1.2)):
            self.assertNotEqual(base, other)


if __name__ == "__main__":
    unittest.main()
