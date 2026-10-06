"""批14（台账 §14-6 登记的假绿）：`status()` 读冷却用的键形制和 fire 侧不是一把。

现读坐标（权威树 `/vol1/1000/docker/doubao-butler`，`nl -ba` 打印）：
  · 写侧 `butler/triggers/engine.py:358`（`_fire`）与 `:244`（`_match`）：
    `cd_key = trig["id"] + (":" + member if member else "")`；
  · 读侧 `:525`：`self._last_fired.get(trig["id"], 0)` —— 裸 id。
⇒ 凡事件带 `member`（现网 MQTT 事件基本都带），`_last_fired` 里只有 `id:member` 这种键，
`status()` 永远读到 0，`/api/status` 对每个 member 维触发器都报「冷却剩 0 秒／从没火过」。

本文件 7 枚腿（AST 现读：FirePathTest 3＋NewestKeyTest 3＋DisplayVsGate 1），跑在**未修的码**
上＝4 红 3 绿（红：member 真火、取最新、显示与门同向、兄弟那条自己也得算得出来；绿＝给修法上笼头的对照，⛔ 拿「谁都不算」换「不算别人的」、无键不许崩、裸 id 那条路不许改坏、
过期不许报负）。RED 原件＝`workorders/readings/1002b14/red_batch14_first_run.txt`。

键前缀必须是 `id` 或 `id + ":"`，⛔ 光 `startswith(id)`：兄弟触发器 `trig_ext` 的
`trig_ext:alice` 会被 naive 前缀匹配算到 `trig` 头上。
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
import unittest

import butler.config as config
from butler.config import Settings
from butler.store import db as db_mod
from butler.triggers.engine import TriggerEngine

TID = "trig_b14"
CD = 300


def make_trigger(**kw):
    t = {"id": TID, "name": "批14 冷却键", "event": "person_enter",
         "enabled": True, "priority": 50, "conditions": {}, "exclusive": True,
         "cooldown_sec": CD, "resources": [],
         "actions": [{"skill": "s1", "params": {}}]}
    t.update(kw)
    return t


class FakeStore:
    def __init__(self, trigs):
        self._t = [dict(x) for x in trigs]

    def list(self):
        return [dict(x) for x in self._t]


class FakeRunner:
    async def run(self, skill_id, source="api", dry_run=False, payload=None, force=False):
        return {"ok": True, "status": "ok"}


class FakeRT:
    def __init__(self, data_dir):
        self.runner = FakeRunner()
        self.data_dir = data_dir


class Scaffold(unittest.TestCase):
    """真临时库：`status()` 本身不碰库，但第 1 枚走**真火路径**（`_fire` 会 `_save_cooldowns`），
    ⛔ 让它写进现网 DATA_DIR。`config._settings` 钉 tmp + `db_mod._conn = None`。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="butler_b14_")
        self.saved_settings = config._settings
        config._settings = Settings(data_dir=self.tmp)
        db_mod._conn = None
        db_mod.get_conn()

    def tearDown(self):
        db_mod._conn = None
        config._settings = self.saved_settings
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build_engine(self, *trigs):
        eng = TriggerEngine(FakeStore(list(trigs) or [make_trigger()]))
        eng.set_runtime(FakeRT(self.tmp))
        return eng

    def fire(self, eng, member="alice"):
        payload = {} if member is None else {"member": member}
        payload["room"] = "客厅"
        return asyncio.run(eng.handle_event("person_enter", payload))

    def one(self, eng):
        return eng.status()[TID]


class FirePathTest(Scaffold):
    def test_member_fired_trigger_reports_cooldown_remaining(self):
        """§14-6 的验收本体：真火一次（带 member）后，`status()` 必须说「还在冷却」。

        未修的码在这里给 `remaining=0.0 / last_fired_ago=None`＝面板谎报「随时可再火」，
        而 `_match()` 同一时刻会真的把它拦下来（`:252` 用的是 `cd_key`）——显示与执行相反。
        """
        eng = self.build_engine()
        results = self.fire(eng, member="alice")
        self.assertTrue(results, "火路径没执行＝这条腿变成空转绿")
        self.assertIn(TID + ":alice", eng._last_fired,
                      "写侧键形制变了（本文件全作废，先回来重读 :358）")
        st = self.one(eng)
        self.assertGreater(st["cooldown_remaining"], 0.0,
                           "带 member 的触发器刚火完，status 却报没在冷却（§14-6 那枚假绿）")
        self.assertIsNotNone(st["last_fired_ago"],
                             "刚火过的触发器报「从没火过」")
        self.assertLess(st["last_fired_ago"], CD,
                        "冷却剩余与「多久前火过」必须同向（一个说刚火过、一个说还早＝又一把打架）")

    def test_bare_id_key_still_counts(self):
        """⛔ 修好 member 一侧时把无 member 那条路改坏：事件不带 member ⇒ 写侧键＝裸 id（`:358` 的 else 支）。"""
        eng = self.build_engine()
        self.fire(eng, member=None)
        self.assertIn(TID, eng._last_fired, "无 member 时写侧键应是裸 id，不是了就去重读 :358")
        self.assertGreater(self.one(eng)["cooldown_remaining"], 0.0)

    def test_expired_cooldown_reports_zero_not_negative(self):
        eng = self.build_engine()
        eng._last_fired = {TID + ":alice": time.time() - (CD + 700)}
        self.assertEqual(self.one(eng)["cooldown_remaining"], 0.0)


class NewestKeyTest(Scaffold):
    def test_newest_key_wins_among_members(self):
        """同一名下多枚 member 键 ⇒ 取**最新**那枚（最保守：⛔ 对刚火过的那位谎报「随时可火」）。

        这里直接喂 `_last_fired`：`status()` 是纯显示函数，进程内这张表就是它的输入，
        真跑三次火路径也只能拿到三次几乎相同的 epoch，分不清「取最新」和「取第一枚」。
        """
        now = time.time()
        eng = self.build_engine()
        eng._last_fired = {TID + ":bob": now - 120.0,
                           TID: now - 260.0,
                           TID + ":alice": now - 20.0}
        st = self.one(eng)
        self.assertAlmostEqual(st["cooldown_remaining"], CD - 20.0, delta=2.0,
                               msg="剩余应按最新一枚（alice, 20s 前）算")
        self.assertAlmostEqual(st["last_fired_ago"], 20.0, delta=2.0)

    def test_sibling_prefix_is_not_counted(self):
        """键前缀笼头：兄弟触发器 `trig_ext` 的冷却⛔ 算到 `trig` 头上（naive `startswith(id)` 会）。"""
        now = time.time()
        eng = self.build_engine(make_trigger(),
                                make_trigger(id=TID + "_ext", name="兄弟"))
        eng._last_fired = {TID + "_ext:alice": now - 5.0}
        self.assertEqual(eng.status()[TID]["cooldown_remaining"], 0.0,
                         "前缀匹配吃进了兄弟触发器的 id:member 键")
        self.assertGreater(eng.status()[TID + "_ext"]["cooldown_remaining"], 0.0,
                           "兄弟自己那条得算得出来，⛔ 用「谁都不算」换「不算别人的」")

    def test_empty_cooldown_map_does_not_crash(self):
        eng = self.build_engine()
        st = eng.status()
        self.assertEqual(st[TID]["cooldown_remaining"], 0.0)
        self.assertIsNone(st[TID]["last_fired_ago"])


class DisplayVsGateConsistencyTest(Scaffold):
    def test_status_and_the_gate_that_runs_in_the_same_direction(self):
        """把显示和执行钉在一起：`_match()` 拦下的那一刻，`status()` ⛔ 说「随时可再火」。

        这枚是本次事故的真判据（⛔ 只钉键形制文本）：现网就是这两侧打架——门在拦、面板在绿。
        注意口径边界：member 维冷却是逐位的，`status()` 只有触发器一级，取最新＝最保守；
        「alice 刚火过、bob 现在能火」这种合法情形**不算**本腿的失败（见 §15 的口径说明）。
        """
        now = time.time()
        eng = self.build_engine()
        eng._last_fired = {TID + ":alice": now - 20.0}
        blocked = eng._match("person_enter", {"member": "alice"})
        self.assertEqual(blocked, [], "门此刻没在拦＝这条腿变成空对照，回去重读 :252")
        self.assertGreater(eng.status()[TID]["cooldown_remaining"], 0.0,
                           "门在拦、status 却说没在冷却（§14-6 那枚假绿的形状）")


if __name__ == "__main__":
    unittest.main()
