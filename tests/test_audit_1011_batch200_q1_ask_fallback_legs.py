"""批200 Q1 门：小爱链路「跨房抢答」收口——fallback 保留＋同 role＋15s 窗口＋跨房率读数。

裁定（`关键决策部/decisions/20261011-DB-小爱链路语义簇七问-裁定.md` §3 Q1，方案乙 修正版）原文口径：
 「butler/skills/engines/llm_decide/ask.py:133 那行前加两道过滤：a.role_id == role_id 且
   now - a.created_at <= 15.0。保留 :136 日志（读数源）。新增跨房率读数。⛔ 不得删 fallback 本身。」

验收对照（§4）：
 1. 两条 pending＋不同 role ⇒ 跨 role ⛔ 认领；同 role 超 15s ⛔ 认领；同 role 窗口内认领；
 2. 跨房率可聚合（负控：窗口改 0 ⇒ 跨房率归零）。

一处**偏差登记**（回执里请追认，⛔ 按字面硬来）：`on_wakeup` 的 `role_id` 形参可为 `None`
（`dialog.py:131` 电视唤醒、`xiaomi_ear.py:198` 换词路由都不带 role），字面 `a.role_id == role_id`
会把「来话没识别出角色」也判成跨 role ⇒ 主路径的问句回执当场变哑。本门把 `role_id` 为空串/None
判成**通配**（跨房过滤只咬「两边都知道角色且不同」那一档），并配一格把它钉住。

跑法：`python -B -m unittest tests.test_audit_1011_batch200_q1_ask_fallback_legs`（一套一进程）。
"""
from __future__ import annotations

import pathlib
import re
import sys
import time
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import butler.skills.engines.llm_decide.ask as ask_mod  # noqa: E402
from butler.skills.engines.llm_decide.ask import PendingAskManager  # noqa: E402

ASK_PY = REPO_ROOT / "butler" / "skills" / "engines" / "llm_decide" / "ask.py"
ENGINE_PY = REPO_ROOT / "butler" / "skills" / "engines" / "llm_decide" / "engine.py"
DIALOG_PY = REPO_ROOT / "butler" / "core" / "dialog.py"

SAME_ROOM_LINE = "candidates = [a for a in self._pending if a.room == room and not a.resolved]"
LOG_LINE = 'logger.info("ask room mismatch: armed=%s incoming=%s, fallback to any", candidates[0].room, room)'


def _arm(mgr, room, role_id, age=0.0, timeout=30.0):
    """直插一条 pending（⛔ 走 arm()：那枚方法会打 TV 提示音、发 HA 服务）。"""
    ask = ask_mod.PendingAsk(room=room, question="要不要开电视？", brain={}, rt=None,
                             timeout=timeout, role_id=role_id)
    ask.created_at = time.time() - age
    mgr._pending.append(ask)
    return ask


# ── 形状尺：只吃 check() 里 `if not candidates:` 到 `return min(` 之间那段 fallback 区 ──
def fallback_region(src: str) -> str:
    start = src.index("if not candidates:")
    end = src.index("return min(", start)
    return src[start:end]


def hits(text: str) -> list:
    """返回缺失项名单（⛔ 布尔：一格要说清缺哪道）。"""
    try:
        region = fallback_region(text)
    except ValueError:
        return ["fallback-branch-deleted"]
    if "candidates = [" not in region:
        # fallback 区里再也没有第二次 candidates 赋值＝那条腿被删了（⛔ 让「删了」被读成「缺过滤」）
        return ["fallback-branch-deleted"]
    out = []
    if not re.search(r"a\.role_id\s*==\s*role_id", region):
        out.append("role-filter-missing")
    if "ASK_FALLBACK_WINDOW_SECONDS" not in region or not re.search(r"now\s*-\s*a\.created_at", region):
        out.append("window-filter-missing")
    if LOG_LINE not in text:
        out.append("reading-source-log-missing")
    if SAME_ROOM_LINE not in text:
        out.append("same-room-line-changed")
    return out


CLEAN_CHECK = '''
    def check(self, room: str, role_id: str = "") -> PendingAsk | None:
        now = time.time()
        candidates = [a for a in self._pending if a.room == room and not a.resolved]
        if not candidates:
            candidates = [a for a in live
                          if (a.role_id == role_id or not role_id)
                          and (now - a.created_at) <= ASK_FALLBACK_WINDOW_SECONDS]
            if not candidates:
                return None
            logger.info("ask room mismatch: armed=%s incoming=%s, fallback to any", candidates[0].room, room)
        return min(candidates, key=lambda a: a.created_at)
'''
SYNTH_NO_ROLE = CLEAN_CHECK.replace("(a.role_id == role_id or not role_id)\n", "")
SYNTH_NO_WINDOW = CLEAN_CHECK.replace("and (now - a.created_at) <= ASK_FALLBACK_WINDOW_SECONDS", "")
SYNTH_NO_LOG = CLEAN_CHECK.replace(
    '            logger.info("ask room mismatch: armed=%s incoming=%s, fallback to any", candidates[0].room, room)\n', "")
SYNTH_NO_FALLBACK = CLEAN_CHECK.replace(
    '''        if not candidates:
            candidates = [a for a in live
                          if (a.role_id == role_id or not role_id)
                          and (now - a.created_at) <= ASK_FALLBACK_WINDOW_SECONDS]
            if not candidates:
                return None
            logger.info("ask room mismatch: armed=%s incoming=%s, fallback to any", candidates[0].room, room)
''',
    "        if not candidates:\n            return None\n")


class BehaviorTests(unittest.TestCase):
    def setUp(self):
        self.mgr = PendingAskManager()

    def test_01_same_room_claim_not_filtered(self):
        # 裁定只在 fallback 那行前加过滤⇒同房间那一腿⛔ 跟着变（不同 role、超 15s 窗口但在 ask 自身 timeout 内，也要能认领）
        _arm(self.mgr, "客厅", "butler", age=20.0, timeout=30.0)
        got = self.mgr.check("客厅", "kino")
        self.assertIsNotNone(got, "同房间认领被窗口/role 拦了＝裁定外的行为改动")

    def test_02_cross_room_same_role_in_window_claims(self):
        _arm(self.mgr, "客厅", "butler", age=1.0)
        self.assertIsNotNone(self.mgr.check("书房", "butler"))

    def test_03_cross_room_other_role_refuses(self):
        # 验收 1：跨 role ⛔ 认领（抢答那一档）
        _arm(self.mgr, "客厅", "butler", age=1.0)
        self.assertIsNone(self.mgr.check("书房", "kino"))

    def test_04_cross_room_past_window_refuses(self):
        # 验收 1：同 role 超 15s ⛔ 认领
        _arm(self.mgr, "客厅", "butler", age=16.0)
        self.assertIsNone(self.mgr.check("书房", "butler"))

    def test_05_no_pending_returns_none(self):
        self.assertIsNone(self.mgr.check("书房", "butler"))

    def test_06_unknown_incoming_role_is_wildcard(self):
        # 偏差登记格：on_wakeup 的 role_id 可为 None/""（电视唤醒、换词路由），字面相等会当场把回执变哑
        _arm(self.mgr, "客厅", "butler", age=1.0)
        self.assertIsNotNone(self.mgr.check("书房", ""))
        self.assertIsNotNone(self.mgr.check("书房", None))

    def test_07_window_zero_negative_control(self):
        # 验收 2 的负控：窗口改 0 ⇒ 跨房认领必须归零（证明那枚窗口是活的闸、⛔ 装饰）
        _arm(self.mgr, "客厅", "butler", age=1.0)
        with mock.patch.object(ask_mod, "ASK_FALLBACK_WINDOW_SECONDS", 0.0):
            self.assertIsNone(self.mgr.check("书房", "butler"))
        self.assertEqual(self.mgr.cross_room_stats()["cross_room_claims"], 0)

    def test_08_expired_ask_still_cleaned_before_filtering(self):
        _arm(self.mgr, "客厅", "butler", age=45.0, timeout=30.0)
        self.assertIsNone(self.mgr.check("书房", "butler"))
        self.assertEqual(len(self.mgr._pending), 0, "超时件应被清理腿收掉")


class StatsTests(unittest.TestCase):
    def setUp(self):
        self.mgr = PendingAskManager()

    def test_09_rate_aggregates_over_live_checks(self):
        # 分母＝有活 ask 的 check 次数，分子＝经 fallback 认领的次数（check()⛔ 消费 pending，所以三条都数得到）
        _arm(self.mgr, "客厅", "butler", age=1.0)
        self.mgr.check("客厅", "butler")   # 同房间认领＝live check，⛔ 记跨房
        self.mgr.check("书房", "butler")   # 跨房认领
        self.mgr.check("书房", "butler")   # 再问一次，仍是跨房认领
        st = self.mgr.cross_room_stats()
        self.assertEqual(st["live_checks"], 3, st)
        self.assertEqual(st["cross_room_claims"], 2, st)
        self.assertAlmostEqual(st["cross_room_rate"], 2 / 3)

    def test_10_empty_pending_does_not_inflate_denominator(self):
        # ⛔ 让空转 check 稀释跨房率（否则「没人问」会被读成「没抢答」＝一条永远不红的闸）
        for _ in range(5):
            self.mgr.check("书房", "butler")
        self.assertEqual(self.mgr.cross_room_stats()["live_checks"], 0, self.mgr.cross_room_stats())

    def test_11_stats_returns_a_copy(self):
        st = self.mgr.cross_room_stats()
        st["cross_room_claims"] = 999
        self.assertEqual(self.mgr.cross_room_stats()["cross_room_claims"], 0)


class ShapeRulerTests(unittest.TestCase):
    def test_12_ruler_calibrated_both_ways(self):
        # 形状尺⛔ 只跑真文件：先证它能放行、再证它咬得住每一格
        self.assertEqual(hits(CLEAN_CHECK), [], "CLEAN 合成件被咬＝尺子宽窄错了")
        self.assertEqual(hits(SYNTH_NO_ROLE), ["role-filter-missing"], hits(SYNTH_NO_ROLE))
        self.assertEqual(hits(SYNTH_NO_WINDOW), ["window-filter-missing"], hits(SYNTH_NO_WINDOW))
        self.assertEqual(hits(SYNTH_NO_LOG), ["reading-source-log-missing"], hits(SYNTH_NO_LOG))
        self.assertEqual(hits(SYNTH_NO_FALLBACK), ["fallback-branch-deleted"], hits(SYNTH_NO_FALLBACK))

    def test_13_real_source_clean_and_wired(self):
        src = ASK_PY.read_text(encoding="utf-8")
        self.assertEqual(hits(src), [], hits(src))
        self.assertIn("ASK_FALLBACK_WINDOW_SECONDS = 15.0", src, "窗口常量必须现读得到（裁定 15.0）")
        # 接线两腿同源：武装端带 role、认领端把 role 传下去
        self.assertIn("role_id=", ENGINE_PY.read_text(encoding="utf-8").split("pending_ask_manager.arm(")[1][:200])
        self.assertRegex(DIALOG_PY.read_text(encoding="utf-8"), r"pending_ask_manager\.check\(room,\s*role_id")


if __name__ == "__main__":
    unittest.main(verbosity=2)
