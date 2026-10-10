"""批173 判据门 · `CronTaskValidator.validate_api_url` 的确定性拒绝名单补全。

**本节口径覆盖本文件 2026-10-10 版的第 2、3 条纪律**（批179 落裁定 §一/§二 之后）：
- 原第 2 条里钉"现状"的那格 `test_rfc1918_currently_denied_this_batch_does_not_change_it`
  已被裁定翻面（RFC1918 是放行集）⇒ 那一格改成指向新门，历史口径不再当判据；
- 原第 3 条"域名一律不在本门判定"作废：域名现在**先解析再按同一份硬拒名单判**，
  所以本文件凡出现域名的格子都把 `_resolve_ips` 桩成确定性结果，⛔ 依赖真 DNS。

对应文书：`E:\\NAS\\关键决策部\\inbox\\20261010-DB-SSRF护栏从未接线与密钥外插-决策申请.md` §六
（「不等裁定我已经做的」那一格）＋ `decisions\\20261011-DB-SSRF护栏接线与密钥外插-合并裁定.md`。

三条纪律，写在脸上免得后人误读这扇门：
1. 这里测的是**判据本身**。接线腿（`cron_task_routes.register_api` 写入前那一拦）在
   批179 的门里另有格子 ⇒ 本文件全绿 ≠ 现网有闸，现网生效要装机窗。
2. 拒绝侧与**放行侧同批断言**（ULA／公网／普通域名必须放行）。只断言"被拒"的话，
   一个"全拒"的实现也能骗过这扇门——那就是没有闸。
3. `localhost` 名形式配**近邻反控**（`notlocalhost.example.com`、`localhost.evil.test`），
   钉住"后缀匹配"⛔"子串/前缀匹配"——这两条近邻名靠名字放行已不成立，
   现在靠的是解析结果不在硬拒集里。

硬拒名单只含「在家里没有正经任务用途」的形态：IPv4 回环、链路本地／元数据、`0.0.0.0/8`、
IPv6 回环 `::1/128`、`fe80::/10`，加 `localhost`／`*.localhost` 名形式。
RFC1918 三段与 `fc00::/7`（ULA）在放行集——判据是「这个地址在家里有没有任务用途」，
⛔「名单缺了就补」。
"""
from __future__ import annotations

import ipaddress
import unittest
from unittest import mock

from butler.core import cron_validator as cv
from butler.core.cron_validator import CronTaskValidator, _HARD_REJECT_NETS

PUBLIC_IP = "93.184.216.34"


def _network_covering(cidr: str) -> list[str]:
    """返回硬拒名单里覆盖给定地址的那几段（用于把"哪一段咬的"写进失败信息）。"""
    addr = ipaddress.ip_address(cidr)
    return [str(net) for net in _HARD_REJECT_NETS if addr in net]


def _resolves_to(*ips):
    """把域名判定那道缝桩成确定性结果（⛔ 让门依赖真 DNS）。"""
    return mock.patch.object(cv, "_resolve_ips", lambda host: list(ips))


class Batch173DenyFormsTests(unittest.TestCase):
    def setUp(self):
        self.v = CronTaskValidator()

    # ---- 本批要补的三段子网 ----

    def test_ipv6_loopback_denied(self):
        valid, error = self.v.validate_api_url("http://[::1]:8080/api/states")
        self.assertFalse(valid, f"::1 被放行，命中段={_network_covering('::1')}")
        self.assertTrue(error)

    def test_ipv6_link_local_denied(self):
        valid, error = self.v.validate_api_url("http://[fe80::1]/x")
        self.assertFalse(valid, f"fe80::1 被放行，命中段={_network_covering('fe80::1')}")
        self.assertTrue(error)

    def test_this_host_v4_denied(self):
        valid, error = self.v.validate_api_url("http://0.0.0.0:8095/api")
        self.assertFalse(valid, f"0.0.0.0 被放行，命中段={_network_covering('0.0.0.0')}")
        self.assertTrue(error)

    # ---- 名形式（不依赖 DNS：只看主机名本身）----

    def test_localhost_name_denied(self):
        valid, error = self.v.validate_api_url("http://localhost:8095/api")
        self.assertFalse(valid, "localhost 名形式被放行")
        self.assertTrue(error)

    def test_localhost_name_case_insensitive_denied(self):
        # urlparse 已把主机名转小写；这一格钉的是"别用原始串比"。
        valid, error = self.v.validate_api_url("http://LOCALHOST:8095/api")
        self.assertFalse(valid, "大写 LOCALHOST 被放行")
        self.assertTrue(error)

    def test_subdomain_of_localhost_denied(self):
        valid, error = self.v.validate_api_url("http://a.b.localhost:8080/")
        self.assertFalse(valid, "*.localhost 被放行")
        self.assertTrue(error)

    # ---- 近邻反控：名字里带 localhost 但不在 localhost 区里 ----

    def test_near_miss_localhost_names_allowed(self):
        # 名字像 localhost 但不是那个区：靠的是解析结果不在硬拒集，⛔ 靠名字放行
        with _resolves_to(PUBLIC_IP):
            for url in ("https://notlocalhost.example.com/api",
                        "https://localhost.evil.test/api"):
                with self.subTest(url=url):
                    valid, error = self.v.validate_api_url(url)
                    self.assertTrue(valid, f"{url} 被误拒（后缀匹配写成了子串匹配）：{error}")

    # ---- 放行侧：这些必须照旧放行，否则"全拒"也能骗过本门 ----

    def test_ula_ipv6_allowed(self):
        # fc00::/7 是 RFC1918 的 IPv6 对应物 ⇒ 按 §四-2 的判据不补进拒绝名单。
        for url in ("http://[fd12::1]:8123/api", "http://[fc00::1]:8123/api"):
            valid, error = self.v.validate_api_url(url)
            self.assertTrue(valid, f"{url} 被误拒：{error}")

    def test_rfc1918_now_allowed_by_ruling(self):
        """口径覆盖本文件历史那格「现状应拒」：裁定 §一.1 把 RFC1918 判成放行集。

        这一格与批179 的开关行同向——挂上线那一刻甲⛔ 变成乙。
        """
        for url in ("http://192.168.2.200:8123/api",
                    "http://10.0.0.5/x",
                    "http://172.16.3.4/x"):
            valid, error = self.v.validate_api_url(url)
            self.assertTrue(valid, f"{url} 应放行却被拒：{error}")

    def test_public_ip_allowed(self):
        with _resolves_to(PUBLIC_IP):
            valid, error = self.v.validate_api_url("https://api.example.com/v1/x")
        self.assertTrue(valid, f"公网 https 被误拒：{error}")

    def test_plain_hostname_allowed(self):
        # 域名先解析再判（批179 起的口径）：解析到公网 ⇒ 放行。
        with _resolves_to(PUBLIC_IP):
            valid, error = self.v.validate_api_url("https://wttr.in/Hefei?format=3")
        self.assertTrue(valid, f"解析到公网的域名被拒：{error}")

    # ---- 既有行为控制腿：证明我没把原来的门写坏 ----

    def test_existing_v4_forms_still_denied(self):
        for url in ("http://127.0.0.1:8095/api", "http://169.254.169.254/latest/meta-data/"):
            valid, error = self.v.validate_api_url(url)
            self.assertFalse(valid, f"{url} 原本就该拒，现在放行了")
            self.assertTrue(error)

    def test_scheme_still_enforced(self):
        valid, error = self.v.validate_api_url("ftp://8.8.8.8/x")
        self.assertFalse(valid, "scheme 门被写坏了")
        self.assertIn("scheme", error)

    def test_allow_internal_ips_flag_still_permits_rfc1918(self):
        # 旗标语义不动：整段判据（含本批新增的名形式）仍在旗标之后。
        v = CronTaskValidator(allow_internal_ips=True)
        valid, error = v.validate_api_url("http://192.168.1.10:8123/api")
        self.assertTrue(valid, f"旗标失效：{error}")


if __name__ == "__main__":
    unittest.main()
