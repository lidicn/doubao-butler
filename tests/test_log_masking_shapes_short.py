"""Q-t4（WO-ME-224 R9 补尺）：日志脱敏对 8 / 12 / 17 字符短哨兵必须形状无关地成立。

DEV1 那份 test_log_masking_shapes.py 只造了 17 字符一档（"SENTINEL-"+hex[:8]），
8/12 两个更短的档位没有被量过——本文件补上，且第一格先自证测的是盘上真身。

⛔ 本文件只 import 盘上现座已有的 `MaskingFilter` / `setup_logging`；
WO-ME-224 候选码里的 `MaskingFormatter` 尚未装机，出现任何对它的引用都是测错树。
同步用例（已知 harness 收不到 async def test）。
"""
from __future__ import annotations

import io
import logging
import os
import unittest
import uuid

import butler.logging_setup as logging_setup
from butler.logging_setup import MaskingFilter

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_MODULE = os.path.realpath(os.path.join(REPO_ROOT, "butler", "logging_setup.py"))

# 掩码约定取自 butler/logging_setup.py 的 `_SENSITIVE_RE.sub(r"\1=***", ...)`：
# 恰好 3 个星号；`k: v` 与 `k=v` 都被归一为 `k=***`（盘上现座行为，DEV1 候选码改成
# 保留 `:`——那是未装机的差异，本文件按盘上自身的约定断言）。
STAR_COUNT = 3
STARS = "*" * STAR_COUNT


def _sentinel(length: int) -> str:
    v = ("SENT" + uuid.uuid4().hex)[:length]
    assert len(v) == length
    return v


def _capture(msg: str, *args) -> str:
    """复刻 setup_logging 的管道（同一 Formatter fmt + 盘上的 MaskingFilter），单 logger 隔离。"""
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    h.addFilter(MaskingFilter())
    lg = logging.getLogger("qt4_mask_" + uuid.uuid4().hex[:8])
    lg.handlers = [h]
    lg.propagate = False
    lg.setLevel(logging.INFO)
    lg.info(msg, *args)
    return buf.getvalue()


class TestMaskingShortShapes(unittest.TestCase):
    def test_00_source_provenance(self):
        """先证明测的是盘上真身，不是 tmp/副本/我自己造的影子。"""
        mod_file = os.path.realpath(getattr(logging_setup, "__file__"))
        self.assertEqual(
            os.path.normcase(mod_file), os.path.normcase(REAL_MODULE),
            f"import 到了非现座文件：{mod_file}",
        )
        tmp_dir = os.path.normcase(os.path.realpath(os.path.join(REPO_ROOT, "tmp"))) + os.sep
        self.assertNotIn(tmp_dir, os.path.normcase(mod_file) + os.sep, "脱敏实现不得来自 tmp 树")
        self.assertTrue(hasattr(logging_setup, "MaskingFilter"), "盘上入口 MaskingFilter 缺失")
        self.assertFalse(
            hasattr(logging_setup, "MaskingFormatter"),
            "MaskingFormatter 属 WO-ME-224 候选码；若它出现在盘上说明测试树已换，需重新对账",
        )

    def _assert_tier(self, length: int) -> None:
        v = _sentinel(length)

        out = _capture(f"api_key={v}")
        self.assertNotIn(v, out, f"{length} 字符裸 kv 泄漏：{out}")
        self.assertIn(f"api_key={STARS}", out, f"{length} 字符 kv 掩码形状不符：{out}")
        self.assertEqual(out.count("*"), STAR_COUNT, f"{length} 字符星号个数≠{STAR_COUNT}：{out}")

        out = _capture(f"token: {v}")
        self.assertNotIn(v, out, f"{length} 字符冒号形状泄漏：{out}")
        self.assertIn(f"token={STARS}", out, f"{length} 字符冒号形状未按盘上约定归一为 `=`：{out}")
        self.assertEqual(out.count("*"), STAR_COUNT, f"{length} 字符冒号形状星号个数不符：{out}")

        out = _capture("cfg %s", {"api_key": v})
        self.assertNotIn(v, out, f"{length} 字符 dict 位置参数泄漏：{out}")
        self.assertIn(STARS, out, f"{length} 字符 dict 位置参数未遮：{out}")
        self.assertEqual(out.count("*"), STAR_COUNT, f"{length} 字符 dict 形状星号个数不符：{out}")

    def test_01_len08_short_value(self):
        self._assert_tier(8)

    def test_02_len12_short_value(self):
        self._assert_tier(12)

    def test_03_len17_control(self):
        # DEV1 己有档位作对照：构造式与其一致（"SENTINEL-" + hex[:8] = 17）。
        v = "SENTINEL-" + uuid.uuid4().hex[:8]
        self.assertEqual(len(v), 17)
        out = _capture(f"api_key={v}")
        self.assertNotIn(v, out, f"17 字符对照档泄漏：{out}")
        self.assertIn(f"api_key={STARS}", out)
        self.assertEqual(out.count("*"), STAR_COUNT)

    def test_04_bare_value_is_known_gap(self):
        """cell 6：裸值（无键名可锚）按现座设计不遮——这格【应当红】。

        现状用显式 skip 记账；若哪天有人加了高熵/上下文启发式把裸值遮了，
        这里会 fail，逼他删掉本 skip、把判据升成断言，而不是默默"全过"。
        一句"8/8 全过"在本园子里本身就是 FAIL 证据，所以 skip 计数必须出现在报告里。
        """
        v = _sentinel(12)
        out = _capture("bearer %s", v)
        if v not in out:
            self.fail(
                "裸值已被遮蔽：现座行为已变，本用例的 skip 台账过期——"
                "请改为断言并更新 WO-ME-224 R9 回执。"
            )
        self.skipTest(
            "按设计漏：MaskingFilter 只锚定键名（api_key=/token:/…），裸值无锚点。"
            "WO-ME-224 回执 cell 6 常红项，禁止计入『通过』。"
        )


if __name__ == "__main__":
    unittest.main()
