r"""批180 判据门 · 密钥改走 headers 那条路要真通 ＋ 发送前复核（rebinding 腿）。

对应文书：`E:\\NAS\\关键决策部\\decisions\\20261011-DB-SSRF护栏接线与密钥外插-合并裁定.md`
  - §4.2：`_resolve_url` 现读只作用于 url 与 body，**不作用于 headers** ⇒ 「今天把 `{secret:t}`
    写进 header 会被字面发出去」，所以 §4.3「密钥优先放 headers」的引导目前是空话。
    本门钉的是**替换真的发生**（§五 表 15 行），不是"文档里说了可以放"；
  - §二-3：注册时点的校验挡不住 DNS 事后改指 ⇒ 执行腿（`_test_api_async`／`execute_task`）
    **发送前复核一次**，失败即不发送、落 `failed` 并记原因（§五 表 16 行）；
  - §4.4 残余风险：URL 里带 `{secret:}` 会进对方日志——本批⛔ 再放宽（那是 owner 那一格）。

三条写在脸上免得后人误读：
1. 本门用**桩**跑的：`url_guard` 桩成确定性判据、`httpx.AsyncClient` 桩成假客户端。
   ⇒ 全绿只证"代码里这条腿在"，⛔ 等于"现网已生效"（`.py` 要装机窗）。
2. 复核拿的是**注册时的 URL 模板**（带 `{secret:}` 那一串），⛔ 替换后的成品：
   把密钥喂进 DNS 查询／错误文本＝换一种方式泄露。本门有一格专门咬这件事。
3. `_resolve_headers` 返回**新** dict，⛔ 就地改注册表——就地改会把明文密钥写进 `apis.json`。
   这一格同样是本门的内容（表 15 行的另一半：换一条通道⛔ 顺手造一条新的泄露面）。
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import pathlib
import tempfile
import types
import unittest
from unittest import mock

from butler.core import cron_task as ct

SECRET_NAME = "ha"
SENTINEL = "SENTINEL-REPLACED"          # 假密钥值：只在桩里流转，⛔ 不是任何真凭据的形态
PUBLIC_URL = "http://93.184.216.34:8123/api"
HARD_URL = "http://169.254.169.254/latest/meta-data/"


class _Guard:
    """`url_guard` 的确定性替身：记下每一次被复核的 URL，返回钉死的判定。"""

    def __init__(self, valid=True, reason="", raises=False):
        self.valid = valid
        self.reason = reason
        self.raises = raises
        self.seen: list[str] = []

    def validate_api_url(self, url):
        self.seen.append(url)
        if self.raises:
            raise RuntimeError("复核器自己炸了")
        return self.valid, self.reason


class _Response:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _ClientSpy:
    """假客户端：只记录"发出去了什么"，⛔ 真联网。"""

    def __init__(self, log, payload=None):
        self.log = log
        self.payload = payload or {"temperature": 3}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        self.log.append(("GET", url, dict(headers or {})))
        return _Response(self.payload)

    async def post(self, url, headers=None, content=None):
        self.log.append(("POST", url, dict(headers or {}), content))
        return _Response(self.payload)


def _executor(mod, tmp: str, url=PUBLIC_URL, headers=None, method="GET", body="",
              secrets=None):
    ex = mod.CronTaskExecutor(types.SimpleNamespace(data_dir=pathlib.Path(tmp)))
    ex.register_api("api1", url, headers if headers is not None else {},
                    5, "", "token", method, body)
    ex._secrets[SECRET_NAME] = SENTINEL
    if secrets:
        ex._secrets.update(secrets)
    return ex


def _skill():
    return {"id": "s1", "brain": {"api_id": "api1", "message_template": "x",
                                  "condition": {"jsonpath": "$.missing",
                                                "comparator": "exists"}},
            "output": []}


# ===================== 探针（负控用同一批，⛔ 另开一份算术复制品）=====================

def probe_header_substitution(mod, tmp, method):
    """表 15 行：`{secret:}` 写在 header 值里 ⇒ 发出去的那一帧必须已是替换后的值。"""
    ex = _executor(mod, tmp, headers={"X-Key": "{secret:%s}" % SECRET_NAME},
                   method=method, body='{"a": 1}' if method == "POST" else "")
    log = []
    with mock.patch.object(mod, "url_guard", _Guard()), \
            mock.patch.object(mod.httpx, "AsyncClient", lambda *a, **kw: _ClientSpy(log)):
        asyncio.run(ex._test_api_async("api1"))
    if not log:
        raise AssertionError("假客户端一次都没被调用：这条探针没测到发送腿")
    sent_headers = log[0][2]
    if sent_headers.get("X-Key") != SENTINEL:
        raise AssertionError(f"header 里的占位符没被替换：{sent_headers.get('X-Key')!r}")
    if "{secret:" in json.dumps(sent_headers):
        raise AssertionError("header 仍有未替换的占位符")


def probe_registry_untouched(mod, tmp):
    """替换只发生在**发出的那一帧**：注册表与 apis.json 里必须还是占位符。"""
    ex = _executor(mod, tmp, headers={"X-Key": "{secret:%s}" % SECRET_NAME})
    with mock.patch.object(mod, "url_guard", _Guard()), \
            mock.patch.object(mod.httpx, "AsyncClient", lambda *a, **kw: _ClientSpy([])):
        asyncio.run(ex._test_api_async("api1"))
    if ex._api_registry["api1"]["headers"]["X-Key"] != "{secret:%s}" % SECRET_NAME:
        raise AssertionError("注册表被就地改写：明文密钥进了内存态")
    on_disk = (pathlib.Path(tmp) / "cron_task" / "apis.json").read_text(encoding="utf-8")
    if SENTINEL in on_disk:
        raise AssertionError("明文密钥被写进了 apis.json")


def probe_execute_denied(mod, tmp):
    """表 16 行：复核判拒 ⇒ 一次都不发送，且落 failed 带原因。"""
    ex = _executor(mod, tmp, url=HARD_URL)
    log = []
    guard = _Guard(valid=False, reason="不允许访问该地址: x -> y（169.254.0.0/16）")
    with mock.patch.object(mod, "url_guard", guard), \
            mock.patch.object(mod.httpx, "AsyncClient", lambda *a, **kw: _ClientSpy(log)):
        result = asyncio.run(ex.execute_task(_skill()))
    if log:
        raise AssertionError("复核已判拒，请求仍然发了出去")
    if result["status"] != "failed" or "复核" not in result["reason"]:
        raise AssertionError(f"被拒的执行没落 failed＋原因：{result}")


def probe_test_api_denied(mod, tmp):
    ex = _executor(mod, tmp, url=HARD_URL)
    log = []
    with mock.patch.object(mod, "url_guard", _Guard(valid=False, reason="域名解析失败")), \
            mock.patch.object(mod.httpx, "AsyncClient", lambda *a, **kw: _ClientSpy(log)):
        result = asyncio.run(ex._test_api_async("api1"))
    if log:
        raise AssertionError("复核已判拒，测试请求仍然发了出去")
    if result.get("ok") or "复核" not in result.get("error", ""):
        raise AssertionError(f"被拒的测试没带原因：{result}")


def probe_recheck_judges_template(mod, tmp):
    """复核吃的是**注册模板**：⛔ 把替换后的成品喂进 DNS／错误文本。"""
    ex = _executor(mod, tmp, url="http://93.184.216.34/x?k={secret:%s}" % SECRET_NAME)
    guard = _Guard()
    with mock.patch.object(mod, "url_guard", guard), \
            mock.patch.object(mod.httpx, "AsyncClient", lambda *a, **kw: _ClientSpy([])):
        asyncio.run(ex.execute_task(_skill()))
    if not guard.seen:
        raise AssertionError("发送前没有任何复核")
    if SENTINEL in guard.seen[0]:
        raise AssertionError("复核用的是替换后的 URL：明文密钥进了判据与日志")
    if "{secret:" not in guard.seen[0]:
        raise AssertionError(f"复核用的不是注册模板：{guard.seen[0]}")


def probe_recheck_exception_fails_closed(mod, tmp):
    """复核器自身炸掉＝"没判出来"，⛔ 当成"判了放行"。"""
    ex = _executor(mod, tmp, url=PUBLIC_URL)
    log = []
    with mock.patch.object(mod, "url_guard", _Guard(raises=True)), \
            mock.patch.object(mod.httpx, "AsyncClient", lambda *a, **kw: _ClientSpy(log)):
        result = asyncio.run(ex.execute_task(_skill()))
    if log:
        raise AssertionError("复核异常被当成放行，请求发了出去（fail-open）")
    if result["status"] != "failed":
        raise AssertionError(f"复核异常没落 failed：{result}")


def probe_execute_allowed(mod, tmp):
    """正控：放行侧照旧发送——复核⛔ 变成"一律不发"。"""
    ex = _executor(mod, tmp, url="http://192.168.2.200:8123/api")
    log = []
    with mock.patch.object(mod, "url_guard", _Guard(valid=True)), \
            mock.patch.object(mod.httpx, "AsyncClient", lambda *a, **kw: _ClientSpy(log)):
        asyncio.run(ex.execute_task(_skill()))
    if not log:
        raise AssertionError("放行侧被拦住了：复核写成了全拒＝没有闸")


PROBES = {
    "表15 header GET 替换": lambda m, t: probe_header_substitution(m, t, "GET"),
    "表15 header POST 替换": lambda m, t: probe_header_substitution(m, t, "POST"),
    "注册表⛔ 被就地改写": lambda m, t: probe_registry_untouched(m, t),
    "表16 执行腿被拒不发送": lambda m, t: probe_execute_denied(m, t),
    "表16 测试腿被拒不发送": lambda m, t: probe_test_api_denied(m, t),
    "复核吃模板": lambda m, t: probe_recheck_judges_template(m, t),
    "复核异常 fail-closed": lambda m, t: probe_recheck_exception_fails_closed(m, t),
    "复核放行侧仍发送": lambda m, t: probe_execute_allowed(m, t),
}


class HeaderSecretTests(unittest.TestCase):
    """裁定 §4.2／§五 表 15 行：密钥放 header 这条路今天必须真的通。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name

    def test_secret_placeholder_in_header_value_replaced_on_get(self):
        probe_header_substitution(ct, self.tmp, "GET")

    def test_secret_placeholder_in_header_value_replaced_on_post(self):
        probe_header_substitution(ct, self.tmp, "POST")

    def test_registry_and_disk_stay_unresolved(self):
        probe_registry_untouched(ct, self.tmp)

    def test_unknown_secret_name_stays_literal(self):
        """控制腿：没入库的名字⛔ 被"顺手删掉占位符"——原样发出去才是现行为。"""
        ex = _executor(ct, self.tmp, headers={"X-Key": "{secret: absent }"})
        log = []
        with mock.patch.object(ct, "url_guard", _Guard()), \
                mock.patch.object(ct.httpx, "AsyncClient", lambda *a, **kw: _ClientSpy(log)):
            asyncio.run(ex._test_api_async("api1"))
        self.assertEqual(log[0][2]["X-Key"], "{secret: absent }")

    def test_non_string_header_value_passes_through(self):
        ex = _executor(ct, self.tmp, headers={"X-Count": 3})
        log = []
        with mock.patch.object(ct, "url_guard", _Guard()), \
                mock.patch.object(ct.httpx, "AsyncClient", lambda *a, **kw: _ClientSpy(log)):
            asyncio.run(ex._test_api_async("api1"))
        self.assertEqual(log[0][2]["X-Count"], 3)


class PreSendRecheckTests(unittest.TestCase):
    """裁定 §二-3／§五 表 16 行：发送前复核一次解析结果（挡 rebinding）。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name

    def test_execute_task_denied_never_sends_and_lands_failed(self):
        probe_execute_denied(ct, self.tmp)

    def test_test_api_denied_never_sends(self):
        probe_test_api_denied(ct, self.tmp)

    def test_recheck_judges_the_registered_template(self):
        probe_recheck_judges_template(ct, self.tmp)

    def test_recheck_exception_fails_closed(self):
        probe_recheck_exception_fails_closed(ct, self.tmp)

    def test_allowed_registration_still_sends(self):
        probe_execute_allowed(ct, self.tmp)


class MutationControlTests(unittest.TestCase):
    """§五 负控：把这两条腿各自摘掉，对应探针必须判红。"""

    @classmethod
    def setUpClass(cls):
        cls.src = pathlib.Path(ct.__file__).read_text(encoding="utf-8")

    def _load(self, name, source):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = pathlib.Path(tmp.name) / f"{name}.py"
        path.write_text(source, encoding="utf-8")
        spec = importlib.util.spec_from_file_location(f"b180_mut_{name}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _cut(self, old, new, count=None):
        # ⛔ 用 assertIn：失败时会把整份源码（这里 40KB）打进读数里。锚点没找到只报锚点。
        if old not in self.src:
            raise self.failureException(
                f"变异锚点在源码里找不到——源码已变形或该腿尚未落地，需 re-anchor: {old[:70]!r}")
        mutated = self.src.replace(old, new) if count is None else self.src.replace(old, new, count)
        self.assertNotEqual(mutated, self.src, "变异没改动源码")
        if count is not None:
            # 数的是**锚点在源码里的命中数**（⛔ 数替换后的文本：`return ""` 这类值
            # 在别处本来就有，数新文本会把"锚点写窄了"读成"命中数不符"）。
            self.assertEqual(self.src.count(old), count, "变异锚点命中数与预期不符")
        return mutated

    def _assert_matrix(self, name, source, kills, survivors):
        mod = self._load(name, source)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for label in kills:
            with self.subTest(变异=name, 应判红=label):
                with self.assertRaises(AssertionError):
                    PROBES[label](mod, tmp.name)
        for label in survivors:
            with self.subTest(变异=name, 应照旧绿=label):
                PROBES[label](mod, tmp.name)

    def test_baseline_probes_all_pass(self):
        """校准：未变异的源码副本上所有探针必须全绿（否则"判红"是假的）。"""
        mod = self._load("baseline", self.src)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for label, probe in PROBES.items():
            with self.subTest(探针=label):
                probe(mod, tmp.name)

    def test_killing_header_secret_resolution(self):
        mutated = self._cut("self._resolve_headers(api_config[\"headers\"])",
                            "dict(api_config[\"headers\"])")
        self._assert_matrix("no_header_resolve", mutated,
                            kills=["表15 header GET 替换", "表15 header POST 替换"],
                            survivors=["表16 执行腿被拒不发送", "复核放行侧仍发送",
                                       "注册表⛔ 被就地改写"])

    def test_killing_pre_send_recheck(self):
        # 锚点带上下文：两条腿都叫 `_recheck_url`，只摘 execute_task 那一腿，
        # 才能证明"测试腿"和"执行腿"是两道各自在咬的门（⛔ 一次变异蒙对两条）。
        mutated = self._cut('denied = await self._recheck_url(api_config)\n'
                            '        if denied:\n'
                            '            result["ok"] = False',
                            'denied = ""\n'
                            '        if denied:\n'
                            '            result["ok"] = False', count=1)
        self._assert_matrix("no_pre_send_recheck", mutated,
                            kills=["表16 执行腿被拒不发送", "复核吃模板"],
                            survivors=["表15 header GET 替换", "表16 测试腿被拒不发送",
                                       "复核放行侧仍发送"])

    def test_killing_fail_closed_on_recheck_exception(self):
        mutated = self._cut('return f"发送前复核异常，按 fail-closed 拒绝: {e}"',
                            'return ""', count=1)
        self._assert_matrix("recheck_fail_open", mutated,
                            kills=["复核异常 fail-closed"],
                            survivors=["表16 执行腿被拒不发送", "表15 header GET 替换"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
