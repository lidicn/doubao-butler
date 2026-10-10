r"""批184（先红后绿）：`_env` 把「键在但值为空」读成空串，绕过了它自己的默认值。

起因是 `.env.example` 的记载缺口（41 枚键代码在读、样例文件里没有），可一旦把这些键
写进样例，`KEY=` 这种形制就会满天飞 ⇒ 先要回答：空值到底是「没配」还是「配成空」？

同一文件里三种读法已经替本仓答过这道题（现读，⛔ 抄别处的说法）：
  - `_env_int` / `_env_float`（`butler/config.py:21`／`:32`）：`int("")` 抛 ValueError ⇒ **回默认**；
  - `_env_bool`（`:39-45`）：空串不在真假词表里 ⇒ **回默认**；
  - `_env_file_defaults` 的文档串（`:56`）自己写着「只收非空值：空值＝没配」。
只有 `_env`（`:17-18`）例外：`os.environ.get(key, default).strip()` ⇒ 键在而值为空时
`default` 形同虚设。今天被咬到的是 `PRIMARY_MEMBER=`（`:326`）：主成员名变空串后，
批178 那三条判定（`morning/routine.py`、`modes/auto_switch.py`、`timeseries/anomaly.py`）
都在拿空串比在场表 ⇒ 「谁都不在主卧」这种结论会静默成立，而样例文件里根本没这个人可配。

闸门形制＝**行为两档 + 一根不能松的绊线**：
  - 空值腿必须回默认（改 `_env` 前红）；
  - 默认本来就是空串的键⛔ 被我改成非空（`_env("X")` 空 ⇒ 仍空）；
  - 启动硬门槛四把键：空值照样拒启（`or default` 类改法最容易在这里把护栏喂假）。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest import mock

from butler import config as config_mod
from butler.config import Settings, _env

REQUIRED_KEYS = ("DOUBAO_API_KEY", "DESKPILOT_API_TOKEN", "TASK_REPORT_TOKEN", "BUTLER_WEB_PASSWORD")
SENTINEL = "B184_PROBE_KEY"
DEFAULT_VALUE = "not-empty-default"


class EnvPinMixin(unittest.TestCase):
    """只动这一枚键的环境，跑完原样还回去（⛔ 把环境污染留给下一个模块）。"""

    def setUp(self):
        self._saved = {}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _pin(self, **kv):
        for k, v in kv.items():
            self._saved.setdefault(k, os.environ.get(k))
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class EnvEmptyFallbackTests(EnvPinMixin):
    """`_env` 本体：空值＝没配，与 `_env_int`／`_env_bool` 同口径。"""

    def test_empty_string_falls_back_to_default(self):
        self._pin(**{SENTINEL: ""})
        self.assertEqual(_env(SENTINEL, DEFAULT_VALUE), DEFAULT_VALUE,
                         "键在而值为空时默认值被跳过＝`KEY=` 会把配置读成空串")

    def test_whitespace_only_falls_back_to_default(self):
        self._pin(**{SENTINEL: "   "})
        self.assertEqual(_env(SENTINEL, DEFAULT_VALUE), DEFAULT_VALUE)

    def test_unset_falls_back_to_default(self):
        self._pin(**{SENTINEL: None})
        self.assertEqual(_env(SENTINEL, DEFAULT_VALUE), DEFAULT_VALUE)

    def test_real_value_is_honored_and_stripped(self):
        self._pin(**{SENTINEL: "  real  "})
        self.assertEqual(_env(SENTINEL, DEFAULT_VALUE), "real")

    def test_blank_does_not_invent_a_value(self):
        """负控：默认本来就是空串的键，空值仍得是空串（⛔ 把「没配」读出一个值来）。"""
        self._pin(**{SENTINEL: ""})
        self.assertEqual(_env(SENTINEL), "")
        self._pin(**{SENTINEL: None})
        self.assertEqual(_env(SENTINEL), "")


class SettingsEmptyFallbackTests(EnvPinMixin):
    """`Settings.load()` 面：两枚不同的键都吃到同一个修复 ⇒ 修的是 `_env`，不是某一行。"""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="b184_cfg_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._pin(DATA_DIR=self.tmp, TTS_DIR=self.tmp)
        for k in REQUIRED_KEYS:
            self._pin(**{k: "probe-not-a-secret"})

    def test_empty_primary_member_uses_documented_default(self):
        self._pin(PRIMARY_MEMBER="")
        self.assertEqual(Settings.load().primary_member, "lidicn")

    def test_empty_wake_phrase_uses_documented_default(self):
        self._pin(WAKE_PHRASE="")
        self.assertEqual(Settings.load().wake_phrase, "豆包管家")

    def test_configured_values_still_win(self):
        self._pin(PRIMARY_MEMBER="Kevin", WAKE_PHRASE="小管家")
        s = Settings.load()
        self.assertEqual(s.primary_member, "Kevin")
        self.assertEqual(s.wake_phrase, "小管家")

    def test_empty_required_credential_still_refuses_to_start(self):
        """硬门槛不许被这次改动喂假：盘上兜底也拿不到值时，空串照样拒启。"""
        for key in REQUIRED_KEYS:
            self._pin(**{key: ""})
        with mock.patch.object(config_mod, "_env_file_defaults", return_value={}):
            with self.assertRaises(RuntimeError) as ctx:
                Settings.load()
        self.assertIn("is required but not set", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
