r"""批16 验收（TDD，先红后绿）：三处「顺序错 / 无条件收尾」导致的静默丢数据。

来源（`doc/审计报告/_缺陷汇总_供审阅.md`，:N＝该文件正文行号；该目录**只存在于 E 盘工作副本，权威树没有**）：
  - 表行 119 B-10 (RB)（:139）「加密模式 flush_merged 打到缺 `/推送加密` 的错误 URL，且无论成败无条件清空
    合并缓存 → 消息丢失」。报告坐标 `bark.py:174,191` 已漂移；权威树现读＝`butler/integrations/bark.py:175-178`
    （POST 打 `self._push_url`，而同文件 `push()` 在 `:126` 打的是 `self._push_url + "/推送加密"`）
    与 `:192`（`self._merge_cache.clear()` 在分组循环之外、无条件执行）。
  - 表行 143 M-19 (RB)（:163）「recall 先切片 `[-limit:]` 后过滤 revoked → 取回条数不足」。现读＝
    `butler/integrations/memory_agent.py:309`（切片）与 `:311`（过滤）。
    方向要说死：revoked 内容⛔ 漏进上下文（切片内那条仍被 `:311` 挡掉）；缺的是**条数**——
    最新 limit 条里每有一条已撤销就少召回一条，且⛔ 往前补。
  - 表行 46 P1-15 (R3)（:66）「_save 非原子全量覆盖，损坏即别名归零、无备份无告警」。现读＝
    `butler/core/aliases.py:31-33`：`ALIAS_FILE.write_text(json.dumps(...))` 就地截断写。
    本仓已有四处同款（`triggers/store.py:90-91`、`api/config_routes.py:36-38`、`api/deps.py:56-59`、
    `skills/store.py:158-159`：写 tmp 再 `os.replace`）⇒ 补法是**归队**，不是新发明。
    同族另有 13 处裸 `write_text(json...)`（`devices.py:175`、`roles/state.py:46`、`self_evolution.py:418` …）
    本批⛔ 一并做＝逐点验符号可达是另一批的量，见台账 §17 ⛔清单。

为什么三枚同批（不是搭车）：报告给的等级不同（S2/S2/S3），机制是同一条——**先把结果用掉，再判它成没成**：
切片先于过滤、`clear()` 先于状态判定、截断写先于替换。三枚失败时都**静默**（⛔ 报错、⛔ 重试、⛔ 留痕），
只能从代码侧验。

覆盖口径：本文件 14 条腿＝8 条先红（标 ①）＋6 条笼头对照（标 ②，绿码上也该绿，⛔ 计入先红）：
  ① test_encrypt_leg_posts_to_encrypted_endpoint / test_http_error_keeps_cache_for_retry /
    test_post_exception_keeps_cache_for_retry / test_success_clears_only_that_group /
    test_push_and_flush_share_one_encrypt_url / test_recall_backfills_when_newest_slice_is_revoked /
    test_save_does_not_write_in_place_on_target / test_replace_crash_keeps_previous_content
  ② test_plain_leg_posts_to_push_endpoint / test_successful_flush_clears_and_counts /
    test_recall_never_returns_revoked_content / test_recall_output_capped_at_limit /
    test_save_leaves_no_temp_residue / test_saved_file_roundtrips_unchanged_format

它测不到什么：真·掉电／SIGKILL 落在 write 与 replace 之间那一瞬（leg 12 用 patch 让 replace 抛异常来等效，
证的是「目标文件不会被就地截断」这条不变量，⛔ 等于证了 fsync 级持久性——本批补法⛔ 做 fsync）；
Bark 服务端收到错误 URL 时回什么状态码（⛔ 现网，只断 URL 字符串）；MA 侧 `list_agent_memories` 的真实
排序（只断客户端的取用顺序）；合并缓存滞留后重投的时间语义（leg 只断「留着」，⛔ 断「何时再投」）。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import butler.core.aliases as aliases_mod
import butler.integrations.bark as bark_mod
from butler.core.aliases import AliasStore
from butler.integrations.bark import Bark
from butler.integrations.memory_agent import MemoryAgentClient

ENCRYPT_PATH = "/推送加密"


class _Resp:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.text = "stub"


class _Recorder:
    """httpx.AsyncClient 替身：记录每次 post 的 (url, json, data)，按需返回状态码或抛异常。

    `fail_if_group` 只作用在明文腿的 JSON payload（`group` 字段）上；加密腿的密文被 SpyBark 打成
    固定串，⛔ 靠 payload 分辨组，所以跨组部分保留那条腿走明文模式。
    """

    def __init__(self, status_code: int = 200, raise_exc: Exception | None = None,
                 fail_if_group: str = ""):
        self.status_code = status_code
        self.raise_exc = raise_exc
        self.fail_if_group = fail_if_group
        self.calls: list[dict] = []

    def install(self):
        fake = types.SimpleNamespace(AsyncClient=lambda **kw: _Client(self))
        return mock.patch.object(bark_mod, "httpx", fake)


class _Client:
    def __init__(self, rec: _Recorder):
        self.rec = rec

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, *, json=None, data=None):
        self.rec.calls.append({"url": url, "json": json, "data": data})
        if self.rec.raise_exc is not None:
            raise self.rec.raise_exc
        if self.rec.fail_if_group and isinstance(json, dict) \
                and json.get("group") == self.rec.fail_if_group:
            return _Resp(500)
        return _Resp(self.rec.status_code)


class _BarkSettings:
    def __init__(self, encrypt: bool = False, key: str = "DEVICEKEY"):
        self.bark_url = "http://bark.test:18273"
        self.bark_key = key
        self.bark_encrypt_key = "0123456789abcdef" if encrypt else ""
        self.bark_encrypt_iv = "fedcba9876543210" if encrypt else ""


class SpyBark(Bark):
    """覆盖 `_encrypt`：绕开 pycryptodome，只验 URL 与缓存收尾这两件事。"""

    def __init__(self, settings):
        super().__init__(settings)
        self.plaintexts: list[str] = []

    def _encrypt(self, plaintext: str) -> str:
        self.plaintexts.append(plaintext)
        return "CT"

    def seed(self, *flat):
        """扁平参数 (title, group, title, group, …)：与 push() 在 :87 落缓存的字段形状一致。"""
        self._merge_cache.clear()
        for i in range(0, len(flat), 2):
            self._merge_cache.append({"title": flat[i], "body": "b",
                                      "group": flat[i + 1], "ts": 1.0})


def _no_runtime_ctx():
    """push() 在 :71 `from butler.runtime import get_runtime`：测试里让它当场失败，
    走 `:89` 那条 fail-open 分支把推送放下去——⛔ 让真 runtime 被 import 出副作用。"""
    mod = types.ModuleType("butler.runtime")

    def get_runtime():
        raise RuntimeError("runtime not initialised in test")

    mod.get_runtime = get_runtime
    return mock.patch.dict(sys.modules, {"butler.runtime": mod})


class BarkFlushMerged16(unittest.TestCase):
    def _flush(self, bark, rec):
        with rec.install():
            return asyncio.run(bark.flush_merged())

    def test_encrypt_leg_posts_to_encrypted_endpoint(self):
        """①：加密模式的合并摘要必须打到 `/推送加密`，与 push() 同一条公式。"""
        bark = SpyBark(_BarkSettings(encrypt=True))
        bark.seed("夜里洗衣完了", "洗衣")
        rec = _Recorder(status_code=200)
        sent = self._flush(bark, rec)
        self.assertEqual(sent, 1, sent)
        self.assertEqual(len(rec.calls), 1, rec.calls)
        self.assertTrue(rec.calls[0]["url"].endswith(ENCRYPT_PATH), rec.calls[0]["url"])
        self.assertIn("ciphertext", rec.calls[0]["data"] or {}, rec.calls[0])

    def test_plain_leg_posts_to_push_endpoint(self):
        """②笼头：明文腿本来就补了 `/push`（:183），这条绿码上也绿。"""
        bark = SpyBark(_BarkSettings(encrypt=False))
        bark.seed("客厅灯关了", "客厅")
        rec = _Recorder(status_code=200)
        sent = self._flush(bark, rec)
        self.assertEqual(sent, 1, sent)
        self.assertTrue(rec.calls[0]["url"].endswith("/push"), rec.calls[0]["url"])
        self.assertIsInstance(rec.calls[0]["json"], dict, rec.calls[0])

    def test_http_error_keeps_cache_for_retry(self):
        """①：HTTP 非 200 ⇒ sent=0，合并缓存⛔ 被清空（每 5 分钟那轮 job 要能重试）。"""
        bark = SpyBark(_BarkSettings(encrypt=False))
        bark.seed("阳台收到快递", "阳台", "阳台又一条", "阳台")
        rec = _Recorder(status_code=500)
        sent = self._flush(bark, rec)
        self.assertEqual(sent, 0, sent)
        self.assertEqual(bark.get_merge_cache_size(), 2, bark._merge_cache)

    def test_post_exception_keeps_cache_for_retry(self):
        """①：网络异常（内网断＝过载合并最需要的那刻）⇒ 缓存整条留着。"""
        bark = SpyBark(_BarkSettings(encrypt=False))
        bark.seed("热水器故障", "设备")
        rec = _Recorder(raise_exc=OSError("network down"))
        sent = self._flush(bark, rec)
        self.assertEqual(sent, 0, sent)
        self.assertEqual(bark.get_merge_cache_size(), 1, bark._merge_cache)

    def test_success_clears_only_that_group(self):
        """①：一组成功一组失败 ⇒ 只清成功那组，失败那组留在缓存里。"""
        bark = SpyBark(_BarkSettings(encrypt=False))
        bark.seed("厨房水开了", "厨房", "卧室有人", "卧室")
        rec = _Recorder(status_code=200, fail_if_group="merged_卧室")
        sent = self._flush(bark, rec)
        self.assertEqual(sent, 1, sent)
        left = [i["group"] for i in bark._merge_cache]
        self.assertEqual(left, ["卧室"], left)

    def test_successful_flush_clears_and_counts(self):
        """②笼头：全成功时 sent=1、缓存归零（现在就是对的，改动⛔ 把它弄坏）。"""
        bark = SpyBark(_BarkSettings(encrypt=False))
        bark.seed("门口有包裹", "门禁")
        rec = _Recorder(status_code=200)
        sent = self._flush(bark, rec)
        self.assertEqual(sent, 1, sent)
        self.assertEqual(bark.get_merge_cache_size(), 0)

    def test_push_and_flush_share_one_encrypt_url(self):
        """①：一份定义一份判定——同设置下 push() 与 flush_merged() 必须打同一个 URL。"""
        bark = SpyBark(_BarkSettings(encrypt=True))
        bark.seed("书房窗没关", "书房")
        rec = _Recorder(status_code=200)
        with _no_runtime_ctx(), rec.install():
            pushed = asyncio.run(bark.push("单条测试", group="书房"))
        sent = self._flush(bark, rec)
        self.assertTrue(pushed, pushed)
        self.assertEqual(sent, 1, sent)
        urls = [c["url"] for c in rec.calls]
        self.assertEqual(len(urls), 2, urls)
        self.assertEqual(urls[0], urls[1], urls)


def _ma_res(payload):
    return {"result": {"isError": False,
                       "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}}


class SpyMA(MemoryAgentClient):
    """⛔ 调 super().__init__：recall/list_memories 这条链只用到 _text_of/_refusal/_extract_list
    三个不碰 settings 的静态方法，绕开 Settings 的启动硬门。"""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def call_tool(self, name, arguments, timeout=8):
        self.calls.append((name, arguments))
        return _ma_res(self.payload)


class RecallQuota16(unittest.TestCase):
    def test_recall_backfills_when_newest_slice_is_revoked(self):
        """①：limit=2、最新两条里有一条 revoked ⇒ 往前补，召回 2 条。"""
        payload = {"ok": True, "memories": [
            {"content": "留一", "state": "staging"},
            {"content": "撤", "state": "revoked"},
            {"content": "留二", "state": "live"},
        ]}
        out = asyncio.run(SpyMA(payload).recall("fb62", limit=2))
        self.assertEqual(out, ["留一", "留二"], out)

    def test_recall_never_returns_revoked_content(self):
        """②笼头：revoked 内容⛔ 进上下文（换顺序前后都该成立）。"""
        payload = {"ok": True, "memories": [
            {"content": "撤一", "state": "revoked"},
            {"content": "留一", "state": "live"},
            {"content": "撤二", "state": "revoked"},
        ]}
        out = asyncio.run(SpyMA(payload).recall("fb62", limit=8))
        self.assertEqual(out, ["留一"], out)

    def test_recall_output_capped_at_limit(self):
        """②笼头：补位⛔ 越过 limit，且仍取「最新的那批」。"""
        payload = {"ok": True, "memories": [
            {"content": "c%d" % i, "state": "live"} for i in range(6)
        ]}
        out = asyncio.run(SpyMA(payload).recall("fb62", limit=3))
        self.assertEqual(len(out), 3, out)
        self.assertEqual(out, ["c3", "c4", "c5"], out)


class AliasSaveAtomic16(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="b16_alias_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.target = Path(self.tmp) / "device_aliases.json"
        starter = mock.patch.object(aliases_mod, "ALIAS_FILE", self.target)
        starter.start()
        self.addCleanup(starter.stop)
        self.store = AliasStore()
        self.store.aliases = {"客厅灯": {"entity_id": "light.ke", "domain": "light",
                                         "service": "turn_on", "count": 3}}

    def test_save_does_not_write_in_place_on_target(self):
        """①：目标文件本身⛔ 被就地截断写（写 tmp 再 replace）。"""
        real_write_text = Path.write_text
        calls = []

        def guarded(self_path, *a, **k):
            calls.append(str(self_path))
            if Path(self_path) == self.target:
                raise AssertionError("就地截断写了目标文件: %s" % self_path)
            return real_write_text(self_path, *a, **k)

        with mock.patch.object(Path, "write_text", guarded):
            self.store._save()
        self.assertNotIn(str(self.target), calls, calls)
        self.assertEqual(json.loads(self.target.read_text(encoding="utf-8")), self.store.aliases)

    def test_replace_crash_keeps_previous_content(self):
        """①：os.replace 那一刻炸（掉电/杀进程的等效腿）⇒ 旧文件内容原样还在。"""
        self.store._save()
        before = self.target.read_text(encoding="utf-8")
        self.store.aliases = {"新alias": {"entity_id": "light.zz", "domain": "light",
                                          "service": "turn_on", "count": 1}}
        with mock.patch.object(os, "replace", side_effect=OSError("killed in flight")):
            with self.assertRaises(OSError):
                self.store._save()
        self.assertEqual(self.target.read_text(encoding="utf-8"), before)

    def test_save_leaves_no_temp_residue(self):
        """②笼头：成功路径⛔ 留 .tmp／.bak 残留（NAS 总闸禁堆积）。"""
        self.store._save()
        self.assertEqual(sorted(p.name for p in Path(self.tmp).iterdir()),
                         ["device_aliases.json"])

    def test_saved_file_roundtrips_unchanged_format(self):
        """②笼头：改成原子写⛔ 动文件格式（仍 ensure_ascii=False, indent=2），_load 读得回。"""
        self.store._save()
        text = self.target.read_text(encoding="utf-8")
        self.assertEqual(text, json.dumps(self.store.aliases, ensure_ascii=False, indent=2))
        fresh = AliasStore()
        self.assertEqual(fresh.aliases, self.store.aliases)


if __name__ == "__main__":
    unittest.main()
