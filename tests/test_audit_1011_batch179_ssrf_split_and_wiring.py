r"""批179 判据门 · SSRF 护栏「先拆再接」。

对应文书：`E:\\NAS\\关键决策部\\decisions\\20261011-DB-SSRF护栏接线与密钥外插-合并裁定.md`
（它取代同题两份前身，§〇 说明了各自对在哪）：
  - §一 甲：判据拆成 **硬拒集**（回环／链路本地／`0.0.0.0/8`／`localhost` 名）与
    **放行集**（RFC1918 三段 + ULA `fc00::/7`）两组，⛔ 把 RFC1918/ULA 留在拒绝侧；
  - §二：域名解析后判（v4+v6 同一份名单）、解析失败 fail-closed；
  - §四.1：URL 里带 `{secret:}` ⇒ scheme 必须 https；
  - §一.2：接在 `cron_task_routes.register_api` 写入之前，被拒即「当场不写」
    （`cron_task.py` 的 `register_api` 是唯一落盘处）；
  - §五 验收表 1-14 行逐条对应本门，§五 负控＝摘掉任一条判据必须有对应用例判红
    （变异探针读数进台账，⛔ 把「拦得住」三个字当结论）。

裁定 §〇-3 的实跑读数钉死了关键一件事：`validate_api_url` 在默认参数下**全拦内网＝乙**，
所以"接线"绝不能写成"把函数挂上去"。本门第 7/8/9/10 行（`192.168`／`10.`／`fd00::`／`fc00::`
必须**放行**）就是防止挂上去那一刻甲变成乙的那道反控。

域名那一类走 `_resolve_ips` 这一道缝：本门把缝桩成确定性名单，⛔ 拿真 DNS 当判据
（离线或公共解析器换答案都会让门自己变红＝没有闸）。
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import pathlib
import tempfile
import unittest
from unittest import mock

from butler.api import cron_task_routes as routes
from butler.core import cron_validator as cv

# 裁定 §五 验收表 1-10 行（形制：(行号, url, 期望放行?)）
TABLE_IP_FORMS = [
    ("1", "http://127.0.0.1:8095/x", False),
    ("2a", "http://localhost:8095/x", False),
    ("2b", "http://a.b.localhost:8080/", False),
    ("3", "http://[::1]:8080/x", False),
    ("4", "http://[fe80::1]/x", False),
    ("5", "http://169.254.169.254/latest/meta-data/", False),
    ("6", "http://0.0.0.0:8095/x", False),
    ("7", "http://192.168.2.200:8123/api", True),   # ← 本次的开关行
    ("8a", "http://10.0.0.5/x", True),
    ("8b", "http://172.16.3.4/x", True),
    ("9", "http://[fd00::1]/x", True),
    ("10", "http://[fc00::1]/x", True),
]
# 放行集成员（裁定 §1.1 那一档），⛔ 出现在硬拒名单里
ALLOWED_INTERNAL_CIDRS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"]
HARD_REJECT_CIDRS = ["127.0.0.0/8", "169.254.0.0/16", "0.0.0.0/8", "::1/128", "fe80::/10"]
# 域名判定用的样例解析结果（v6 同一份名单，裁定 §二-1）
DOMAIN = "home-api.example.test"
PUBLIC_IP = "93.184.216.34"   # 用作"解析到公网"的确定性读数，⛔ 在本门里发真 DNS 查询


class _StubRequest:
    def __init__(self, payload: dict):
        self._payload = payload

    async def json(self):
        return self._payload


class _StubExecutor:
    def __init__(self):
        self.calls: list[tuple] = []

    def register_api(self, *args, **kw):
        self.calls.append((args, kw))


class _StubRuntime:
    def __init__(self, executor):
        self.cron_task_executor = executor


def _call_register(url: str, mod=routes) -> tuple[_StubExecutor, int, str]:
    """走一遍真实的路由函数（含 `guard`／`get_runtime` 两条腿），返回执行器与响应读数。"""
    ex = _StubExecutor()
    req = _StubRequest({"api_id": "b179", "url": url})
    with mock.patch.object(mod, "guard", lambda request: None), \
            mock.patch.object(mod, "get_runtime", lambda: _StubRuntime(ex)):
        resp = asyncio.run(mod.register_api(req))
    return ex, resp.status_code, resp.body.decode("utf-8")


class SplitRosterTests(unittest.TestCase):
    """裁定 §1.1：名单必须先拆成两组——这一格钉的是"拆没拆"，不是"拦不拦"。"""

    def test_hard_reject_roster_covers_the_five_homeless_forms(self):
        nets = {str(net) for net in cv._HARD_REJECT_NETS}
        for cidr in HARD_REJECT_CIDRS:
            self.assertIn(cidr, nets, f"硬拒集缺 {cidr}")

    def test_rfc1918_and_ula_are_not_in_the_hard_reject_roster(self):
        """⛔ 把 RFC1918/ULA 留在硬拒侧＝把裁定的甲执行成被驳回的乙。"""
        hard = {str(net) for net in cv._HARD_REJECT_NETS}
        for cidr in ALLOWED_INTERNAL_CIDRS:
            self.assertNotIn(cidr, hard, f"硬拒集里还留着 {cidr}（没拆干净）")
        self.assertEqual({str(net) for net in cv._ALLOWED_INTERNAL_NETS},
                         set(ALLOWED_INTERNAL_CIDRS))


class AcceptanceTableIpFormsTests(unittest.TestCase):
    """裁定 §五 表 1-10 行逐行实跑。"""

    def test_rows_1_to_6_denied(self):
        v = cv.CronTaskValidator()
        for row, url, expect in TABLE_IP_FORMS:
            if expect:
                continue
            with self.subTest(row=row, url=url):
                valid, error = v.validate_api_url(url)
                self.assertFalse(valid, f"第 {row} 行应拒却放行")
                self.assertTrue(error, f"第 {row} 行拒了却没带原因")

    def test_rows_7_to_10_allowed(self):
        """这一格是本次的开关行：挂上去那一刻甲⛔ 变成乙。"""
        v = cv.CronTaskValidator()
        for row, url, expect in TABLE_IP_FORMS:
            if not expect:
                continue
            with self.subTest(row=row, url=url):
                valid, error = v.validate_api_url(url)
                self.assertTrue(valid, f"第 {row} 行应放却被拒：{error}")


class SecretHttpsTests(unittest.TestCase):
    """裁定 §四.1（表 13/14 行）：密钥写在 URL 里 ⇒ 必须 https。"""

    def test_url_secret_over_http_denied(self):
        valid, error = cv.CronTaskValidator().validate_api_url(
            "http://wttr.example.test/get?k={secret:weather}")
        self.assertFalse(valid, "http 带 {secret:} 被放行")
        self.assertIn("https", error)

    def test_url_secret_over_https_allowed(self):
        # 字面公网 IP：这一格只判 https 那条腿，⛔ 顺带依赖真 DNS
        valid, error = cv.CronTaskValidator().validate_api_url(
            "https://93.184.216.34/get?k={secret:weather}")
        self.assertTrue(valid, "https 带 {secret:} 被误拒：" + error)

    def test_plain_http_without_secret_still_allowed(self):
        """反控：⛔ 顺手把"带密钥要 https"写成"一律要 https"（家里一堆明文 LAN 服务）。"""
        valid, error = cv.CronTaskValidator().validate_api_url("http://192.168.2.200:8123/api")
        self.assertTrue(valid, f"无密钥的 http 被误拒：{error}")


class DomainResolutionTests(unittest.TestCase):
    """裁定 §二（表 11/12 行）：域名解析后判，解析失败 fail-closed。"""

    def _judge(self, ips, host=DOMAIN):
        resolver = mock.Mock(return_value=list(ips))
        with mock.patch.object(cv, "_resolve_ips", resolver):
            valid, error = cv.CronTaskValidator().validate_api_url(f"http://{host}/x")
        return valid, error, resolver

    def test_domain_resolving_to_loopback_denied(self):
        valid, error, _ = self._judge(["127.0.0.1"])
        self.assertFalse(valid, "域名解析到回环却放行（`except ValueError: pass` 那条洞）")
        self.assertTrue(error)

    def test_domain_resolving_to_ipv6_loopback_denied(self):
        valid, error, _ = self._judge(["::1"])
        self.assertFalse(valid, "v6 回环漏判：解析结果只过了 v4 名单")

    def test_domain_resolving_to_metadata_address_denied(self):
        valid, error, _ = self._judge(["169.254.169.254"])
        self.assertFalse(valid, "域名解析到链路本地／元数据地址却放行")

    def test_resolution_failure_denied(self):
        valid, error, _ = self._judge([])
        self.assertFalse(valid, "解析失败被放行＝fail-open")
        self.assertTrue(error)

    def test_domain_resolving_to_public_allowed(self):
        valid, error, _ = self._judge(["93.184.216.34"])
        self.assertTrue(valid, f"公网域名被误拒：{error}")

    def test_domain_resolving_to_rfc1918_allowed(self):
        """甲：家里有内网 DNS 名的正当用途（自家 HA／电视／人脸服务）。"""
        valid, error, _ = self._judge(["192.168.2.200"])
        self.assertTrue(valid, f"解析到 RFC1918 的域名被拒（甲变成了乙）：{error}")

    def test_literal_ip_does_not_hit_the_resolver(self):
        """字面 IP ⛔ 再发一次 DNS 查询（结果会变，判据不会）。"""
        resolver = mock.Mock(return_value=[])
        with mock.patch.object(cv, "_resolve_ips", resolver):
            valid, _ = cv.CronTaskValidator().validate_api_url("http://192.168.2.200:8123/api")
        self.assertTrue(valid)
        resolver.assert_not_called()

    def test_mixed_resolution_any_hard_ip_denies(self):
        """v4 公网 + v6 回环同时返回：任一条落硬拒集即拒。"""
        valid, _, _ = self._judge(["93.184.216.34", "::1"])
        self.assertFalse(valid, "多答案域名只判了第一个结果")


class RegisterRouteWiringTests(unittest.TestCase):
    """裁定 §一.2：护栏接在写入之前，被拒即当场不写。"""

    def test_hard_rejected_ip_never_reaches_the_writer(self):
        ex, status, body = _call_register("http://169.254.169.254/latest/meta-data/")
        self.assertEqual(ex.calls, [], "被拒的 URL 仍然走到了唯一落盘处")
        self.assertEqual(status, 400)
        self.assertNotIn("meta-data", body, "错误响应把 URL 路径整段回显了")

    def test_localhost_name_never_reaches_the_writer(self):
        ex, status, _ = _call_register("http://localhost:8095/api")
        self.assertEqual(ex.calls, [])
        self.assertEqual(status, 400)

    def test_http_url_with_secret_never_reaches_the_writer(self):
        ex, status, _ = _call_register("http://192.168.2.200:8123/api?k={secret:ha}")
        self.assertEqual(ex.calls, [], "密钥走明文 URL 的注册被写进了盘")
        self.assertEqual(status, 400)

    def test_rfc1918_registration_still_goes_through(self):
        """放行侧必须在**接线之后**仍成立：接线把家用任务全拦掉＝乙，那不可接受。"""
        ex, status, _ = _call_register("http://192.168.2.200:8123/api")
        self.assertEqual(len(ex.calls), 1)
        self.assertEqual(status, 200)

    def test_resolver_failure_never_reaches_the_writer(self):
        with mock.patch.object(cv, "_resolve_ips", lambda host: []):
            ex, status, _ = _call_register(f"http://{DOMAIN}/x")
        self.assertEqual(ex.calls, [])
        self.assertEqual(status, 400)


class MissingArgsControlTests(unittest.TestCase):
    """控制腿：原有的入参门⛔ 被这次接线顶掉。"""

    def test_missing_url_still_errors_without_registering(self):
        ex, status, _ = _call_register("")
        self.assertEqual(ex.calls, [])
        self.assertEqual(status, 400)


# ==================== 裁定 §五 负控：每条判据各自在咬什么 ====================
#
# 做法：把被测模块的**源码**读进来做一处定向删改，装成独立模块（`spec_from_file_location`），
# 再拿同一批探针跑变异体——探针就是那一格的断言本体，调的是变异模块自己的函数。
# 两道校准，缺一条这套负控就零信息：
#   ① `test_baseline_probes_all_pass`：探针在**未变异的源码副本**上必须全绿
#      （一条本来就红的探针，摘什么都会"红"＝假负控）；
#   ② 每格还带 `survivors`：变异体上其余探针必须照旧绿——证明摘的是**这一条**判据，
#      不是把整扇门一起拆了。

def _denies(mod, url, resolver=None):
    with contextlib.ExitStack() as stack:
        if resolver is not None:
            stack.enter_context(mock.patch.object(mod, "_resolve_ips", resolver))
        valid, error = mod.CronTaskValidator().validate_api_url(url)
    if valid or not error:
        raise AssertionError(f"应拒却放行: {url}")


def _allows(mod, url, resolver=None):
    with contextlib.ExitStack() as stack:
        if resolver is not None:
            stack.enter_context(mock.patch.object(mod, "_resolve_ips", resolver))
        valid, error = mod.CronTaskValidator().validate_api_url(url)
    if not valid:
        raise AssertionError(f"应放却被拒: {url} -> {error}")


def _rosters_split(mod):
    hard = {str(net) for net in mod._HARD_REJECT_NETS}
    for cidr in ALLOWED_INTERNAL_CIDRS:
        if cidr in hard:
            raise AssertionError(f"硬拒集里还留着 {cidr}")
    if {str(net) for net in mod._ALLOWED_INTERNAL_NETS} != set(ALLOWED_INTERNAL_CIDRS):
        raise AssertionError("放行集名单变了")


def _writer_blocked(mod, url):
    ex, status, _ = _call_register(url, mod)
    if ex.calls or status != 400:
        raise AssertionError(f"被拒 URL 仍然走到了唯一落盘处: {url}")


PROBES = {
    "表1 回环 IP 拒": lambda m: _denies(m, "http://127.0.0.1:8095/x"),
    # 名形式两格把解析器桩成**公网**：不桩的话 `localhost` 会被真 hosts 解析成 127.0.0.1，
    # 于是"摘掉名判据"这一格照样拒＝两条腿混成一格，负控测不出名判据自己在咬什么。
    "表2a localhost 名拒": lambda m: _denies(m, "http://localhost:8095/x", lambda h: [PUBLIC_IP]),
    "表2b 子域 localhost 拒": lambda m: _denies(m, "http://a.b.localhost:8080/", lambda h: [PUBLIC_IP]),
    "表3 IPv6 回环拒": lambda m: _denies(m, "http://[::1]:8080/x"),
    "表4 链路本地拒": lambda m: _denies(m, "http://[fe80::1]/x"),
    "表5 元数据段拒": lambda m: _denies(m, "http://169.254.169.254/latest/meta-data/"),
    "表6 本机 0.0.0.0 拒": lambda m: _denies(m, "http://0.0.0.0:8095/x"),
    "表7 RFC1918 放": lambda m: _allows(m, "http://192.168.2.200:8123/api"),
    "表8 10/8 放": lambda m: _allows(m, "http://10.0.0.5/x"),
    "表10 ULA 放": lambda m: _allows(m, "http://[fc00::1]:8123/api"),
    "表11 域名解析到回环拒": lambda m: _denies(m, f"http://{DOMAIN}/x", lambda h: ["127.0.0.1"]),
    "表12 解析失败 fail-closed 拒": lambda m: _denies(m, f"http://{DOMAIN}/x", lambda h: []),
    "表13 secret+http 拒": lambda m: _denies(m, "http://93.184.216.34/get?k={secret:weather}"),
    "表14 secret+https 放": lambda m: _allows(m, "https://93.184.216.34/get?k={secret:weather}"),
    "两组名单拆开了": _rosters_split,
}

# 变异锚点（逐字取自现读源码）：找不到就当场红，⛔ 静默跳过＝这格负控从没跑过。
LOC_HOST = 'if hostname == "localhost" or hostname.endswith(".localhost"):'
LOC_SECRET = 'if _SECRET_PLACEHOLDER.search(url) and parsed.scheme != "https":'
LOC_RESOLVE = ('            ips = _resolve_ips(hostname)\n'
               '            if not ips:\n'
               '                return False, f"域名解析失败，按 fail-closed 拒绝: {hostname}"\n')
LOC_NET = '    ipaddress.ip_network("127.0.0.0/8"),\n'
LOC_FLAG = 'def __init__(self, allow_internal_ips: bool = False):'
LOC_GUARD = ('    valid, reason = await asyncio.to_thread(cron_validator.validate_api_url, url)\n'
             '    if not valid:\n'
             '        logger.warning("cron_task api %s rejected by URL guard: %s", api_id, reason)\n'
             '        return err(f"URL 校验未通过：{reason}")\n\n')


class MutationControlTests(unittest.TestCase):
    """裁定 §五「把任一条判据摘掉，对应用例必须判红」。"""

    @classmethod
    def setUpClass(cls):
        cls.validator_src = pathlib.Path(cv.__file__).read_text(encoding="utf-8")
        cls.routes_src = pathlib.Path(routes.__file__).read_text(encoding="utf-8")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmpdir = pathlib.Path(tmp.name)

    def _load(self, name, source):
        path = self.tmpdir / f"{name}.py"
        path.write_text(source, encoding="utf-8")
        spec = importlib.util.spec_from_file_location(f"b179_mut_{name}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _cut(self, source, old, new=""):
        # ⛔ 用 assertIn：失败时会把整份源码打进读数里。锚点没找到只报锚点。
        if old not in source:
            raise self.failureException(
                f"变异锚点在源码里找不到——源码已变形，本门需要 re-anchor: {old[:70]!r}")
        return source.replace(old, new, 1)

    def _assert_matrix(self, name, source, kills, survivors):
        mod = self._load(name, source)
        for label in kills:
            with self.subTest(变异=name, 应判红=label):
                with self.assertRaises(AssertionError):
                    PROBES[label](mod)
        for label in survivors:
            with self.subTest(变异=name, 应照旧绿=label):
                PROBES[label](mod)

    def test_baseline_probes_all_pass(self):
        """校准①：未变异的源码副本上，所有探针必须全绿（否则下面的"判红"是假的）。"""
        mod = self._load("baseline_validator", self.validator_src)
        for label, probe in PROBES.items():
            with self.subTest(探针=label):
                probe(mod)

    def test_killing_localhost_name_check(self):
        source = self._cut(self.validator_src, LOC_HOST, "if False:")
        self._assert_matrix("no_localhost_name", source,
                            kills=["表2a localhost 名拒", "表2b 子域 localhost 拒"],
                            survivors=["表1 回环 IP 拒", "表7 RFC1918 放",
                                       "表11 域名解析到回环拒"])

    def test_killing_secret_https_rule(self):
        source = self._cut(self.validator_src, LOC_SECRET, "if False:")
        self._assert_matrix("no_secret_https", source,
                            kills=["表13 secret+http 拒"],
                            survivors=["表14 secret+https 放", "表1 回环 IP 拒",
                                       "表7 RFC1918 放"])

    def test_killing_domain_resolution(self):
        # 复现历史洞（§二）：域名解析这一步整个不做事 ⇒ 域名绕过全部 IP 判据。
        source = self._cut(self.validator_src, LOC_RESOLVE, "            ips = []\n")
        self._assert_matrix("no_domain_resolution", source,
                            kills=["表11 域名解析到回环拒", "表12 解析失败 fail-closed 拒"],
                            survivors=["表1 回环 IP 拒", "表5 元数据段拒", "表7 RFC1918 放"])

    def test_killing_fail_closed_only(self):
        # 只摘 fail-closed 那一半：解析成功仍照判 ⇒ 证明表 11 与表 12 是两条腿，不是一条。
        source = self._cut(self.validator_src, LOC_RESOLVE,
                           '            ips = _resolve_ips(hostname)\n'
                           '            if not ips:\n'
                           '                ips = ["8.8.8.8"]\n')
        self._assert_matrix("no_fail_closed", source,
                            kills=["表12 解析失败 fail-closed 拒"],
                            survivors=["表11 域名解析到回环拒", "表1 回环 IP 拒"])

    def test_killing_roster_split(self):
        # 裁定 §1.1 的⛔ 禁止项一：把 RFC1918 塞回硬拒集＝甲当场变乙。
        source = self._cut(self.validator_src, LOC_NET,
                           LOC_NET + '    ipaddress.ip_network("192.168.0.0/16"),\n')
        self._assert_matrix("rfc1918_back_in_hard", source,
                            kills=["表7 RFC1918 放", "两组名单拆开了"],
                            survivors=["表1 回环 IP 拒", "表8 10/8 放", "表10 ULA 放"])

    def test_killing_internal_flag_default(self):
        # ⛔ 禁止项二「靠改 allow_internal_ips 默认值实现甲」——这一格把它跑给人看：
        # 那枚开关是"整段地址判据全跳"，改默认值会连回环／元数据段一起放掉。
        source = self._cut(self.validator_src, LOC_FLAG,
                           'def __init__(self, allow_internal_ips: bool = True):')
        self._assert_matrix("flag_default_flipped", source,
                            kills=["表1 回环 IP 拒", "表5 元数据段拒", "表2a localhost 名拒",
                                   "表11 域名解析到回环拒"],
                            survivors=["表13 secret+http 拒", "表14 secret+https 放"])

    def test_killing_route_wiring(self):
        # 接线腿拆掉：护栏函数本身仍然会拒，但**没人叫它** ⇒ 被拒 URL 直接落盘。
        # 这一格钉的就是本件开头那句「本门全绿 ≠ 现网有闸」的反面。
        source = self._cut(self.routes_src, LOC_GUARD)
        mod = self._load("no_wiring_routes", source)
        with self.assertRaises(AssertionError):
            _writer_blocked(mod, "http://169.254.169.254/latest/meta-data/")
        # 正控：真门（未变异的那份）在同一支 URL 上确实拦住了写入。
        _writer_blocked(routes, "http://169.254.169.254/latest/meta-data/")


if __name__ == "__main__":
    unittest.main(verbosity=2)
