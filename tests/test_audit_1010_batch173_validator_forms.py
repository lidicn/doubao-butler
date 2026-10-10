"""批173 判据门 · `CronTaskValidator.validate_api_url` 的确定性拒绝名单补全。

对应文书：`E:\\NAS\\关键决策部\\inbox\\20261010-DB-SSRF护栏从未接线与密钥外插-决策申请.md` §六
（「不等裁定我已经做的」那一格）。

三条纪律，写在脸上免得后人误读这扇门：
1. 这里测的是**判据本身**。该函数眼下**没接在注册写入路径上**（同件 §四-4/5：`register_api`
   从收到 URL 到落盘不叫它，唯一叫它的 `/api/cron_task/validate_api` 全仓 0 调用者）
   ⇒ 本门全绿 ≠ 现网有闸。接线归裁定，裁下来另开一格门。
2. 拒绝侧与**放行侧同批断言**（ULA／公网 IP／普通域名／近邻名必须放行）。只断言"被拒"的话，
   一个"全拒"的实现也能骗过这扇门——那就是没有闸。
   另有一格 `test_rfc1918_currently_denied_this_batch_does_not_change_it` 钉的是**现状**
   （三段私有网照旧拒）：把它们改成放行是 §二 Q1 待裁的那一笔，本批⛔ 预裁。
3. `localhost` 名形式配**近邻反控**（`notlocalhost.example.com`、`localhost.evil.test` 放行），
   钉住"后缀匹配"⛔"子串/前缀匹配"。

拒绝名单只补三类「在家里没有正经任务用途」的形态：IPv6 回环 `::1/128`、IPv6 链路本地
`fe80::/10`、`0.0.0.0/8`，加 `localhost`／`*.localhost` 名形式。
`fc00::/7`（ULA）是 RFC1918 的 IPv6 对应物，按同一判据**留在放行侧**——§四-2 明写这一格。
"""
from __future__ import annotations

import ipaddress
import unittest

from butler.core.cron_validator import CronTaskValidator, _PRIVATE_NETS


def _network_covering(cidr: str) -> list[str]:
    """返回名单里覆盖给定地址的那几段（用于把"哪一段咬的"写进失败信息）。"""
    addr = ipaddress.ip_address(cidr)
    return [str(net) for net in _PRIVATE_NETS if addr in net]


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
        for url in ("https://notlocalhost.example.com/api",
                    "https://localhost.evil.test/api"):
            valid, error = self.v.validate_api_url(url)
            self.assertTrue(valid, f"{url} 被误拒（后缀匹配写成了子串匹配）：{error}")

    # ---- 放行侧：这些必须照旧放行，否则"全拒"也能骗过本门 ----

    def test_ula_ipv6_allowed(self):
        # fc00::/7 是 RFC1918 的 IPv6 对应物 ⇒ 按 §四-2 的判据不补进拒绝名单。
        for url in ("http://[fd12::1]:8123/api", "http://[fc00::1]:8123/api"):
            valid, error = self.v.validate_api_url(url)
            self.assertTrue(valid, f"{url} 被误拒：{error}")

    def test_rfc1918_currently_denied_this_batch_does_not_change_it(self):
        # 这一格钉的是**现状**，不是方向：甲方案会把 RFC1918 改成放行，那是 §二 Q1 待裁的那一笔，
        # 本批⛔ 预裁 ⇒ 三段照旧拒绝。裁定落地后要开**新的**一格把它翻成放行，别改这一格的历史口径。
        for url in ("http://192.168.2.200:8123/api",
                    "http://10.0.0.5/x",
                    "http://172.16.3.4/x"):
            valid, error = self.v.validate_api_url(url)
            self.assertFalse(valid, f"{url} 现状应拒（本批不动这一段判据）")
            self.assertIn("内网", error)

    def test_public_ip_allowed(self):
        valid, error = self.v.validate_api_url("https://api.example.com/v1/x")
        self.assertTrue(valid, f"公网 https 被误拒：{error}")

    def test_plain_hostname_allowed(self):
        # 域名一律不在本门判定（解析与否是 20261009 那件的方向，本批⛔ 碰）。
        valid, error = self.v.validate_api_url("https://wttr.in/Hefei?format=3")
        self.assertTrue(valid, f"外部域名被拒（本批不该改这一格）：{error}")

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
