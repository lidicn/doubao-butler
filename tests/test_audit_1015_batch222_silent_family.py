# -*- coding: utf-8 -*-
"""批222：第五期报告「静默失败族」逐格行为尺（13 格中除 DB109/DB126/DB139 之外的一档）。

每格只钉一件事：**坏输入／失败态⛔ 被读成成功**。改前的形制都是「接住了、什么也没说」，
所以这些格子的 RED 全部落在同一点上——要么返回了成功形状，要么一条日志也没留。

DB139（`bus/inbox.py` 的 `bool(payload.get(...))`）由批221 收，行为格在
`test_audit_1015_batch221_sweep_sites.py::test_false_word_for_override_quiet_reads_false`；
DB109／DB126 两枚铸形盲区在 `test_audit_1015_batch222_gate_blind_sites.py`。本档⛔ 重复。
"""
import asyncio
import json
import types
import unittest
from urllib.parse import urlencode

from butler.param import ParamError


def _request(query=None, path=None, body_bytes=b""):
    from starlette.requests import Request

    headers = [(b"content-type", b"application/json")]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/__probe",
        "headers": headers,
        "query_string": urlencode(query or {}).encode("ascii"),
        "client": ("127.0.0.1", 1),
        "scheme": "http",
        "path_params": dict(path or {}),
    }

    async def receive():
        return {"type": "http.request", "body": body_bytes, "more_body": False}

    return Request(scope, receive)


class PatchMixin(unittest.TestCase):
    """把站点周围的守卫／外置换成桩，只留被审的那一腿在跑；tearDown 逐件还原。"""

    def setUp(self):
        self._saved = []

    def tearDown(self):
        for mod, name, value in self._saved:
            setattr(mod, name, value)
        self._saved = []

    def _patch(self, mod, **attrs):
        for name, value in attrs.items():
            self._saved.append((mod, name, getattr(mod, name)))
            setattr(mod, name, value)


class _SleepSpy:
    """代理真 asyncio，只把 `sleep` 记下来（⛔ 真等）。"""

    def __init__(self, real):
        self._real = real
        self.slept = []

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def sleep(self, seconds):
        self.slept.append(seconds)


# ── DB100：integrations/tv.py 非 JSON 的 200 报「已提交」 ──

class _Response:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("Expecting value")
        return self._payload


class _ClientCtx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *a, **k):
        return self._resp


class TvNonJsonTest(PatchMixin):
    def test_non_json_200_is_not_reported_as_submitted(self):
        import butler.integrations.tv as mod

        tv = mod.TVClient.__new__(mod.TVClient)
        tv.s = types.SimpleNamespace(tv_http_url="http://127.0.0.1:9")
        self._patch(mod, httpx=types.SimpleNamespace(
            AsyncClient=lambda **k: _ClientCtx(_Response(status_code=200, payload=None,
                                                         text="<html>boom</html>"))))
        res = asyncio.run(tv.play_fongmi("流浪地球"))
        self.assertFalse(res.get("ok"),
                         "200 但 body 不是 JSON＝无从判断 TV 收没收到，⛔ 回 ok=True")


# ── DB134：ilink get_updates 超时伪装 ok:True ──

class IlinkTimeoutTest(PatchMixin):
    def test_poll_timeout_is_not_reported_as_ok(self):
        import httpx as real_httpx

        from butler.integrations.ilink import client as mod

        class _Http:
            async def post(self, *a, **k):
                raise real_httpx.TimeoutException("read timeout")

        class _Stub(mod.ILinkClient):
            is_logged_in = True
            # 让 `self.base_url` 抛 AttributeError 会被通用 except 接住、照样回 ok:False
            # ⇒ 这一格会「因为测试自己的破绽」而假绿，所以把属性钉成可读的。
            base_url = "http://127.0.0.1:9"

            def _auth_headers(self):
                return {}

            def _base_info(self):
                return {}

        c = _Stub.__new__(_Stub)
        c._http = _Http()
        c._context_tokens = {}
        res = asyncio.run(c.get_updates(get_updates_buf="buf-1", timeout=35))
        self.assertFalse(res.get("ok"),
                         "客户端读超时＝服务没答；报 ok:True 会让桥接把故障当「本轮无消息」")
        self.assertEqual(res.get("error"), "timeout",
                         "错误名要能被桥接侧的连续失败阶梯认出来")
        self.assertEqual(res.get("next_buf"), "buf-1", "超时⛔ 推进 buf，否则那批消息永久丢失")


# ── DB94：TTS 引擎返回 None 绕过整条降级链 ──

class _Engine:
    def __init__(self, result, available=True):
        self.result = result
        self.available = available
        self.calls = 0

    async def synthesize(self, text, voice, speed):
        self.calls += 1
        return self.result

    async def is_available(self):
        return self.available


class TtsNullResultTest(PatchMixin):
    NOWVOICE_RESULT = object()

    def _manager(self, primary_result):
        from butler.tts.manager import TTSManager, _CircuitBreaker

        mgr = TTSManager.__new__(TTSManager)
        mgr.s = types.SimpleNamespace(
            tts_primary="edge-tts", tts_speed=1.0, tts_edge_voice="xiaoxiao",
            tts_kokoro_voice="zm_a", nowvoice_voice="00000000",
            tts_cache_enabled=False, base_url="http://127.0.0.1:9", tts_dir=".")
        mgr.ha = None
        mgr.bark = None
        mgr.edge = _Engine(primary_result)
        mgr.nowvoice = _Engine(self.NOWVOICE_RESULT)
        mgr.kokoro = _Engine(None, available=False)
        mgr._breakers = {n: _CircuitBreaker(n) for n in ("edge-tts", "nowvoice", "kokoro")}
        return mgr

    def test_engine_returning_none_counts_as_failure(self):
        mgr = self._manager(None)
        with self.assertRaises(RuntimeError):
            asyncio.run(mgr._try_engine("edge-tts", "你好", "xiaoxiao", 1.0))

    def test_null_primary_still_walks_the_fallback_chain(self):
        mgr = self._manager(None)
        got = asyncio.run(mgr.synthesize("你好", nowvoice_voice="abcd1234"))
        self.assertIs(got, self.NOWVOICE_RESULT,
                      "主引擎没给出结果就 return None⇒备引擎与 Bark 整条降级链被绕过")
        self.assertEqual(mgr.nowvoice.calls, 1)


# ── DB97：trigger_creator 重试零退避 ──

class TriggerRetryBackoffTest(PatchMixin):
    def test_backoff_grows_and_caps(self):
        from butler.skills.engines.trigger_creator import engine as mod

        gaps = [mod._retry_backoff(a) for a in range(4)]
        self.assertTrue(all(b > a for a, b in zip(gaps, gaps[1:])), gaps)
        self.assertLessEqual(gaps[-1], 8.0, gaps)
        self.assertLessEqual(mod._retry_backoff(12), 8.0, "⛔ 指数无上限：一次坏规则能睡死整条技能链")

    def test_failing_attempts_are_spaced(self):
        from butler.skills import runner_types
        from butler.skills.engines.trigger_creator import engine as mod

        class _Llm:
            def __init__(self):
                self.calls = 0

            async def chat(self, *a, **k):
                self.calls += 1
                raise RuntimeError("upstream down")

        spy = _SleepSpy(mod.asyncio)
        rt = types.SimpleNamespace(runner=None, llm=_Llm(), skill_store=None)
        ctx = runner_types.SkillContext(rt=rt, payload={"text": "下午看到我就打招呼"},
                                        skill={"brain": {"max_retry": 2}})
        self._patch(mod, asyncio=spy)
        result = asyncio.run(mod.TriggerCreatorEngine().run(ctx))
        self.assertFalse(result.ok)
        self.assertEqual(len(spy.slept), 2,
                         "三次尝试之间只有两枚间隔：⛔ 末次失败还睡一觉")
        self.assertTrue(spy.slept[1] > spy.slept[0], spy.slept)


# ── DB106：extractor 置信度静默回落 0.5 ──

class ExtractorConfidenceTest(PatchMixin):
    def test_garbage_confidence_falls_back_with_a_log(self):
        import butler.param as param_mod

        from butler.memory import extractor as mod

        notes = []
        self._patch(param_mod, logger=types.SimpleNamespace(
            warning=lambda *a: notes.append(a[0] % a[1:] if len(a) > 1 else a[0])))
        self.assertEqual(mod._fact_confidence("很高"), 0.5)
        self.assertEqual(len(notes), 1, "解析失败⛔ 无声：0.5 与 LLM 真给的 0.5 同形")
        self.assertEqual(mod._fact_confidence("0.9"), 0.9)
        self.assertEqual(len(notes), 1, "可解的值⛔ 跟着记一笔")

    def test_out_of_range_confidence_is_clamped(self):
        from butler.memory.extractor import _fact_confidence

        self.assertEqual(_fact_confidence(1.8), 1.0)
        self.assertEqual(_fact_confidence(-2), 0.0)


# ── DB113：role_routes 兜底文案实际推送 ──

class _Notifier:
    def __init__(self):
        self.pushed = []

    async def push(self, rid, scene, direct_text=None):
        self.pushed.append((rid, scene, direct_text))
        return "pushed"


class RolePushFallbackTest(PatchMixin):
    def _handler(self, body_bytes):
        import butler.api.role_routes as mod

        notifier = _Notifier()
        rt = types.SimpleNamespace(notifier=notifier, role_state={})
        self._patch(mod, guard=lambda request: None, get_runtime=lambda: rt)
        resp = asyncio.run(mod.push_role(_request(path={"id": "butler"}, body_bytes=body_bytes)))
        return resp, notifier

    def test_missing_scene_is_rejected_without_pushing(self):
        with self.assertRaises(ParamError):
            self._handler(json.dumps({"direct_text": ""}).encode())

    def test_invalid_json_is_rejected_without_pushing(self):
        with self.assertRaises(ParamError):
            self._handler(b"{not json")

    def test_given_scene_still_pushes(self):
        resp, notifier = self._handler(json.dumps({"scene": "提醒喝水"}).encode())
        self.assertEqual(notifier.pushed, [("butler", "提醒喝水", None)])
        self.assertEqual(resp.status_code, 200)


# ── DB114：audiobook 异常路径不计 unknown_count ──

class AudiobookUnknownCountTest(PatchMixin):
    def test_raising_status_counts_toward_the_early_stop(self):
        from butler.audiobook import manager as mod

        calls = []

        class _Ha:
            async def _xiaomi_play_status(self, xiaoai_id):
                calls.append(xiaoai_id)
                raise RuntimeError("cloud unreachable")

        spy = _SleepSpy(mod.asyncio)
        mgr = mod.AudiobookManager.__new__(mod.AudiobookManager)
        mgr.rt = types.SimpleNamespace(ha=_Ha())
        mgr.state = types.SimpleNamespace(playing=True, paused=False)
        self._patch(mod, asyncio=spy)
        # timeout=1 只为封顶：改前这里是「空转到超时」，别让它真烧满 300 秒
        asyncio.run(mgr._wait_playback_end("speaker-1", timeout=1))
        self.assertEqual(len(calls), 3,
                         "每次抛都只 sleep+continue⇒unknown_count 永远归不了账，一路空转到超时")


# ── DB121：command_routes 非法 JSON 当空 body ──

class _Store:
    def __init__(self):
        self.completed = []

    def complete_command(self, cmd_id, result="", reporter=""):
        self.completed.append((cmd_id, result, reporter))
        return {"id": cmd_id, "status": "done"}

    def fail_command(self, cmd_id, error="", reporter=""):
        self.completed.append(("fail", cmd_id, error))
        return {"id": cmd_id, "status": "failed"}


class CommandInvalidJsonTest(PatchMixin):
    def _call(self, handler, body_bytes):
        import butler.api.command_routes as mod
        import butler.api.deps as deps

        store = _Store()
        self._patch(mod, cmd=store)
        # command_routes 的 _guard 在调用时才 from butler.api.deps import guard ⇒ 桩要打在源头
        self._patch(deps, guard=lambda request: None)
        with self.assertRaises(ParamError):
            asyncio.run(getattr(mod, handler)(_request(path={"cmd_id": "c1"},
                                                       body_bytes=body_bytes)))
        return store

    def test_complete_rejects_invalid_json(self):
        store = self._call("command_complete", b"result=ok")
        self.assertEqual(store.completed, [],
                         "非法 JSON 被当空 body⇒指令被标成「已完成、结果为空」")

    def test_fail_rejects_invalid_json(self):
        store = self._call("command_fail", b"[1,2,3]")
        self.assertEqual(store.completed, [])

    def test_absent_body_is_absent_not_garbage(self):
        """「什么都没发」与「发错了」是两件事：TP/DP 里 `accept`／`cancel` 这类只点一下的调用不带 body。

        报告 DB121 告的是**后者**被读成前者；把前者一并判 400 是我给跨仓调用方加的约束，⛔ 本批未与之对齐。
        """
        import butler.api.command_routes as mod
        import butler.api.deps as deps

        store = _Store()
        self._patch(mod, cmd=store)
        self._patch(deps, guard=lambda request: None)
        resp = asyncio.run(mod.command_complete(_request(path={"cmd_id": "c1"}, body_bytes=b"")))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(store.completed, [("c1", "", "")])

    def test_valid_body_still_completes(self):
        import butler.api.command_routes as mod
        import butler.api.deps as deps

        store = _Store()
        self._patch(mod, cmd=store)
        self._patch(deps, guard=lambda request: None)
        resp = asyncio.run(mod.command_complete(_request(
            path={"cmd_id": "c1"},
            body_bytes=json.dumps({"result": "done"}).encode())))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(store.completed, [("c1", "done", "")])


# ── DB127：topics 不可解载荷静默返回 False ──

class TopicsUnreadableTest(PatchMixin):
    def test_unreadable_payload_logs_instead_of_silence(self):
        import butler.bus.topics as mod

        notes = []
        self._patch(mod, logger=types.SimpleNamespace(
            warning=lambda *a: notes.append(a[0] % a[1:] if len(a) > 1 else a[0])))
        self.assertFalse(mod.adm_status_is_online("<<>>"))
        self.assertEqual(len(notes), 1,
                         "「载荷读不懂」与「对端确实离线」同形，消费侧据此报 PEER_OFFLINE")
        self.assertNotIn("<<>>", "".join(notes), "日志⛔ 回显原值")

    def test_readable_states_are_untouched(self):
        from butler.bus.topics import adm_status_is_online

        self.assertTrue(adm_status_is_online('{"state": "online"}'))
        self.assertTrue(adm_status_is_online({"state": "degraded"}))
        self.assertFalse(adm_status_is_online({"state": "offline"}))


# ── DB129：bark_routes volume/badge/ttl 置 None 照常推送 ──

class _Router:
    def __init__(self):
        self.sent = []

    async def notify(self, channel, text, title="", trace_id="", bark_kwargs=None):
        self.sent.append((text, title, bark_kwargs))
        return types.SimpleNamespace(results={"bark": types.SimpleNamespace(ok=True)})


class BarkParamTest(PatchMixin):
    def _push(self, payload):
        import butler.api.bark_routes as mod
        import butler.notify.singleton as single

        router = _Router()
        rt = types.SimpleNamespace(bark=object(), settings=types.SimpleNamespace(base_url=""))
        self._patch(mod, guard=lambda request: None, get_runtime=lambda: rt)
        self._patch(single, get_router=lambda: router)
        resp = asyncio.run(mod.bark_push(_request(body_bytes=json.dumps(payload).encode())))
        return resp, router

    def test_invalid_volume_is_rejected_without_pushing(self):
        with self.assertRaises(ParamError):
            self._push({"body": "着火了", "volume": "loud"})

    def test_out_of_range_volume_is_rejected(self):
        with self.assertRaises(ParamError):
            self._push({"body": "着火了", "volume": 99})

    def test_valid_volume_reaches_the_push(self):
        _, router = self._push({"body": "着火了", "volume": "7", "ttl": "60", "badge": "3"})
        self.assertEqual(len(router.sent), 1)
        kwargs = router.sent[0][2]
        self.assertEqual((kwargs["volume"], kwargs["ttl"], kwargs["badge"]), (7, 60, 3))

    def test_false_word_for_is_archive_reads_false(self):
        _, router = self._push({"body": "着火了", "is_archive": "false"})
        self.assertIs(router.sent[0][2]["is_archive"], False)


if __name__ == "__main__":
    unittest.main()
