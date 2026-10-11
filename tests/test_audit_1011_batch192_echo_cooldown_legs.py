"""批192（合并行 27＋53）：回声抑制两把闸的时长口径分叉＋冷却字典只写不清。

现读基底（`stamp=2026-10-11T01:21:00Z`，HEAD `b48cd2b`，两文件 crlf=0）：

- `butler/core/dialog.py` md5_12 `0a577ce641bf`／797 行
- `butler/core/xiaomi_ear.py` md5_12 `ade83493af20`／246 行

批191 分诊判这两枚**在位**（台账 §二十七，`workorders/读数件-批165-166-镜像面-20261010.md:899`），证据：

1. **27（P2-3 R1）两把闸口径互不相干**：
   - 全局腿 `dialog.py:538 secs = max(8.0, len(text) * 0.4 + 6.0)`、`:539 self._echo_until = max(...)`，
     在**派发前**（`:558`）盖戳 ⇒ 起点含合成耗时；
   - 设备腿 `dialog.py:650` 注释写「6 秒内忽略」、真身在 `:653 XiaomiEar._echo_cooldown[d.id] = time.time()`，
     位于 `_emit_devices` **返回之后**；读侧 `xiaomi_ear.py:135 if time.time() - last_speak < 15.0`
     ⇒ 一条按文本长度、一条按硬编码 15.0，且后者的起点既不是播报开始也不是播报结束。
     本仓另已有**唯一一处**文本→时长估算器 `_est_speak_secs`（`dialog.py:543-546`，media_stop 轮询阈值在用，
     调用点 `role_routes.py:98,103,107`／`dialog.py:603,608,612`）⇒ 冷却腿绕开它另起一形。
2. **53（P2-14 R4）只写不清**：`xiaomi_ear.py:108 _echo_cooldown: dict = {}`（类体）全仓**无删除点**
   （补尺件 `workorders/readings/1011/b191_followup_1011.txt` `### 53` 段：写点 `dialog.py:653`、
   读点 `xiaomi_ear.py:134`，0 枚 `del`／`pop`／`clear`）；同文件 `_role_mode` 反倒有 `:168`／`:185` 两枚 `del`
   ⇒ 同族两枚不同判。

本批改的是**冷却腿**：把「盖戳时刻＋硬编码 15.0」改成「过期时刻＝now ＋ max(具名下界, 本条文本估算时长)」，
时长口径收编到 `_est_speak_secs` 单源，读侧按过期时刻判定并在写侧清扫陈旧键。

⛔ 本批动全局腿 `suppress_echo` 的公式：它的 `0.4`（2.5 字/秒）是 docstring 里写明取slow的保守值，
且起点在合成前——把它改窄会重新打开路线图 154「主卧室小爱回声自触发」那一格
（`doc/00-路线图/豆包管家版本开发路线图_v2.4.md:381`，码面已落·现网复判待窗）。
`test_16` 因此钉的是「全局腿⛔ 被本批收窄」，属**反回归**格。

判据钉**行为**（窗口真的按文本长度张开、陈旧键真的被清、过期戳真的放行）也钉**形状**
（15.0 字面量收编成具名常量、写侧走 API ⛔ 裸下标赋值、注释与真身对齐）。
形状尺配两侧合成校准（`test_01`-`test_04`：CLEAN 合成⇒0 命中，SYNTH 合成⇒恰好 1 命中），
⛔ 只跑真文件。行号是活物：`test_17` 是批6 名册锚点（批195 重钉后 `core/dialog.py:527 notify`）的漂移哨兵。

`butler/core/dialog.py` 与 `butler/core/xiaomi_ear.py` 都**可以** import（两档模块级不建库连接；
批156／批44 同形状已在跑），但⛔ import `butler/app.py`（二次 import＝在停摆库上开连接）。

跑法：`python3 -B -m unittest tests.test_audit_1011_batch192_echo_cooldown_legs`
"""
from __future__ import annotations

import ast
import os
import pathlib
import re
import sys
import time
import types
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = pathlib.Path(_HERE).parent
for _cand in (_HERE, REPO_ROOT):
    if os.path.isdir(os.path.join(_cand, "butler")):
        sys.path.insert(0, _cand)
        break

# 现网⛔ 四把键（config 有启动硬门）：这里只放**明显是假的**占位值，⛔ 抄任何真凭据。
for _k, _v in (("DOUBAO_API_KEY", "dummy1"), ("DESKPILOT_API_TOKEN", "dummy2"),
               ("TASK_REPORT_TOKEN", "dummy3"), ("BUTLER_WEB_PASSWORD", "dummy4")):
    os.environ.setdefault(_k, _v)


def _skip_if_missing(exc: Exception) -> None:
    raise unittest.SkipTest("批192 宿主侧缺依赖（%s: %s）⇒ 本档只在容器内跑"
                            % (type(exc).__name__, exc))


try:
    from butler.core import xiaomi_ear as xe
except Exception as _exc:  # noqa: BLE001
    _skip_if_missing(_exc)

try:
    import butler.core.dialog as dlg
except Exception as _exc:  # noqa: BLE001
    _skip_if_missing(_exc)

EAR_PY = pathlib.Path(os.path.abspath(xe.__file__))
DIALOG_PY = REPO_ROOT / "butler" / "core" / "dialog.py"

CELLS = 17
FLOOR = 15.0                      # 现读＝xiaomi_ear.py:135 那枚硬编码下界，本批只换写法⛔ 换数值
CONST_NAME = "ECHO_COOLDOWN_S"
ENTITY = "sensor.ear_b192"
DEV_ID = "ear_b192_dev"
OTHER_ID = "ear_b192_other"
ROOM = "bedroom"
TEXT = "主人，客厅的灯已经关了"
EVENT = {"data": {"entity_id": ENTITY, "new_state": {"state": TEXT}}}
SMAP = {ENTITY: types.SimpleNamespace(id=DEV_ID, room=ROOM, wake_button="")}

# 形状尺①：15.0 裸字面量（具名常量那一行除外）
_BARE = re.compile(r"(?<![\w.])15\.0\b")


def bare_threshold_lines(src: str) -> list:
    return [(i, ln.strip()) for i, ln in enumerate(src.splitlines(), 1)
            if _BARE.search(ln) and CONST_NAME not in ln]


def constant_mentions(src: str) -> int:
    return len(re.findall(r"\b%s\b" % CONST_NAME, src))


def _chain(node):
    """`XiaomiEar.note_speak` → ["XiaomiEar","note_speak"]；不是纯名字链⇒ None。"""
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return list(reversed(parts))
    return None


def _func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def write_leg_shape(src: str) -> dict:
    """`speak_as_role` 里冷却腿的形状：{"note_speak": [(lineno, 带估算器?)], "direct": [lineno…]}。

    `direct`＝`XiaomiEar._echo_cooldown[...] = …` 这种跨模块裸下标写（合并行 53 点名的形状）。
    """
    out = {"note_speak": [], "direct": []}
    fn = _func(ast.parse(src), "speak_as_role")
    if fn is None:
        return out
    for item in ast.walk(fn):
        if isinstance(item, ast.Call):
            chain = _chain(item.func)
            if chain and chain[-1] == "note_speak":
                has_est = any(isinstance(n, ast.Attribute) and n.attr == "_est_speak_secs"
                              for n in ast.walk(item))
                out["note_speak"].append((item.lineno, has_est))
        elif isinstance(item, ast.Assign):
            for tgt in item.targets:
                if isinstance(tgt, ast.Subscript) and \
                        _chain(tgt.value) == ["XiaomiEar", "_echo_cooldown"]:
                    out["direct"].append(item.lineno)
    return out


CLEAN_SYNTH = '''
class XiaomiEar:
    ECHO_COOLDOWN_S = 15.0

    async def _handle_event(self, event, sensor_map):
        left = self._echo_cooldown.get(dev.id, 0.0)
        if time.time() < left:
            return
'''

BARE_SYNTH = '''
class XiaomiEar:
    async def _handle_event(self, event, sensor_map):
        if time.time() - last_speak < 15.0:
            return
'''

OLD_WRITE_SYNTH = '''
class DialogManager:
    async def speak_as_role(self, text, role):
        from butler.core.xiaomi_ear import XiaomiEar
        for d in devices:
            XiaomiEar._echo_cooldown[d.id] = time.time()
'''

NEW_WRITE_SYNTH = '''
class DialogManager:
    async def speak_as_role(self, text, role):
        from butler.core.xiaomi_ear import XiaomiEar
        for d in devices:
            XiaomiEar.note_speak(d.id, hold_s=self._est_speak_secs(text))
'''


def make_ear(dialog, cooldown=None):
    obj = xe.XiaomiEar.__new__(xe.XiaomiEar)
    # 类属性是共享可变字典：⛔ 让本档的写入漏进别的用例（ tearDown 另清类字典）
    obj._echo_cooldown = dict(cooldown or {})
    obj._role_mode = {}
    obj.rt = types.SimpleNamespace(
        ha=None, dialog=dialog, audiobook=None,
        roles=types.SimpleNamespace(all=lambda: []))
    return obj


class FakeDialog:
    def __init__(self):
        self.consulted = []

    def match_role_by_text(self, text):
        self.consulted.append("text")
        return None, text

    def match_role_by_room(self, room):
        self.consulted.append("room")
        return None


def run(coro):
    import asyncio
    return asyncio.run(coro)


class RulerCalibrationTest(unittest.TestCase):
    """形状尺先证明会咬（⛔ 拿一把恒绿的尺子宣布修好了）。"""

    def test_01_bare_threshold_ruler_passes_clean_synthetic(self):
        self.assertEqual(0, len(bare_threshold_lines(CLEAN_SYNTH)),
                         "合成 CLEAN 里 15.0 只出现在具名常量定义行⇒应放行")

    def test_02_bare_threshold_ruler_bites_synthetic(self):
        hits = bare_threshold_lines(BARE_SYNTH)
        self.assertEqual(1, len(hits), "合成 SYNTH 把 15.0 写回判断行⇒必须咬：现读 %s" % hits)

    def test_03_write_leg_ruler_bites_the_defect_shape(self):
        """负控：把**缺陷形状**喂给尺子（合成件，⛔ 拿真文件当校准——它改完就不红了）。"""
        shape = write_leg_shape(OLD_WRITE_SYNTH)
        self.assertEqual(1, len(shape["direct"]),
                         "合成旧形状的裸下标写没被认出来⇒门是恒绿的假门")
        self.assertEqual([], shape["note_speak"], "合成旧形状里没有 note_speak，认出来就是尺子坏了")

    def test_04_write_leg_ruler_passes_fixed_synthetic(self):
        shape = write_leg_shape(NEW_WRITE_SYNTH)
        self.assertEqual([], shape["direct"], "合成修复形状里的裸下标写被误判")
        self.assertEqual(1, len(shape["note_speak"]), "合成修复形状里的 note_speak 调用没被认出来")
        self.assertTrue(shape["note_speak"][0][1],
                        "合成修复形状的 note_speak 没带 _est_speak_secs 也算通过⇒口径判据是假的")


class ShapeConvergenceTest(unittest.TestCase):
    def test_05_named_constant_carries_the_current_floor(self):
        """下界数值⛔ 被本批顺手改掉：具名常量必须存在且等于现读的 15.0。"""
        self.assertTrue(hasattr(xe.XiaomiEar, CONST_NAME),
                        "%s 尚未定义⇒冷却窗口没有单一真源" % CONST_NAME)
        self.assertEqual(FLOOR, float(getattr(xe.XiaomiEar, CONST_NAME)),
                         "下界数值漂移（现读＝15.0，改动它属另一枚决策，不在本批）")

    def test_06_ear_source_has_no_bare_threshold(self):
        src = EAR_PY.read_text(encoding="utf-8")
        hits = bare_threshold_lines(src)
        self.assertEqual([], hits, "xiaomi_ear.py 仍有裸 15.0 字面量：现读 %s" % hits)
        self.assertGreaterEqual(constant_mentions(src), 2,
                                "%s 至少要「定义一次＋使用一次」，只定义不使用＝没有单一真源" % CONST_NAME)

    def test_07_dialog_write_leg_goes_through_the_api(self):
        shape = write_leg_shape(DIALOG_PY.read_text(encoding="utf-8"))
        self.assertEqual([], shape["direct"],
                         "写侧仍在裸改别的模块的类属性（合并行 53 点名的跨模块隐式耦合）：%s" % shape["direct"])
        self.assertEqual(1, len(shape["note_speak"]),
                         "冷却腿应恰有一枚 `XiaomiEar.note_speak(...)`：现读 %s" % shape["note_speak"])
        self.assertTrue(shape["note_speak"][0][1],
                         "note_speak 的时长⛔ 取自 `_est_speak_secs`＝两把闸口径仍各算各的")

    def test_08_stale_six_second_comment_is_gone(self):
        src = DIALOG_PY.read_text(encoding="utf-8")
        self.assertNotIn("6 秒内忽略", src,
                         "注释仍写「6 秒内忽略」而真身是 15.0 下界＋文本时长（合并行 27 的记载面分叉）")
        # 正对照：同一把尺对「一定存在」的句子必须数到东西，否则上面的 NotIn 是恒真
        self.assertGreater(len(re.findall(r"回声抑制", src)), 0,
                           "正对照落空＝这条腿在测空气")


class NoteSpeakBehaviorTest(unittest.TestCase):
    """窗口形状：过期时刻＝now ＋ max(下界, 文本估算时长)，且写侧顺带清陈旧键。"""

    def setUp(self):
        self.saved = dict(xe.XiaomiEar._echo_cooldown)
        xe.XiaomiEar._echo_cooldown.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        xe.XiaomiEar._echo_cooldown.clear()
        xe.XiaomiEar._echo_cooldown.update(self.saved)

    def test_09_floor_when_no_duration_given(self):
        now = time.time()
        xe.XiaomiEar.note_speak(DEV_ID)
        held = xe.XiaomiEar._echo_cooldown[DEV_ID] - now
        self.assertGreaterEqual(held, FLOOR - 0.5, "短播报应至少吃到下界：现读 %.2fs" % held)
        self.assertLess(held, FLOOR + 2.0, "下界被抬高（现读 %.2fs）＝⛔ 顺手放宽抑制窗" % held)

    def test_10_window_covers_the_announcement_length(self):
        """长播报（100 字≈32s）：15 秒的硬编码下界会在播放中途放开＝自问自答那条路。"""
        now = time.time()
        est = dlg.DialogManager._est_speak_secs("字" * 100)
        self.assertGreater(est, FLOOR, "夹具没造出超过下界的时长⇒这格什么都没测")
        xe.XiaomiEar.note_speak(DEV_ID, hold_s=est)
        self.assertGreaterEqual(xe.XiaomiEar._echo_cooldown[DEV_ID] - now, est - 0.5,
                                "冷却窗口没跟着文本时长张开（合并行 27 的冷却腿）")

    def test_11_short_broadcast_does_not_pull_the_window_back(self):
        """与全局腿同口径：max() 语义⇒一条短播报不能把正在播的长窗口拉回。"""
        now = time.time()
        xe.XiaomiEar.note_speak(DEV_ID, hold_s=60.0)
        first = xe.XiaomiEar._echo_cooldown[DEV_ID]
        xe.XiaomiEar.note_speak(DEV_ID, hold_s=1.0)
        self.assertEqual(first, xe.XiaomiEar._echo_cooldown[DEV_ID],
                         "窗口被后一条短播报改小＝播放中途放开（应与 `suppress_echo` 的 max() 同形）")
        self.assertGreaterEqual(first - now, 60.0 - 0.5)

    def test_12_expired_keys_are_pruned_on_write(self):
        xe.XiaomiEar._echo_cooldown[OTHER_ID] = time.time() - 1.0     # 陈旧戳（下线设备永不再被读）
        xe.XiaomiEar.note_speak(DEV_ID)
        self.assertNotIn(OTHER_ID, xe.XiaomiEar._echo_cooldown,
                         "合并行 53：全仓无清理点⇒写侧顺手淘汰过期键")

    def test_13_live_keys_survive_the_prune(self):
        xe.XiaomiEar._echo_cooldown[OTHER_ID] = time.time() + 30.0
        xe.XiaomiEar.note_speak(DEV_ID)
        self.assertIn(OTHER_ID, xe.XiaomiEar._echo_cooldown,
                      "清扫把还在窗口里的设备也删了＝正对照：这格证明 test_12 不是恒真")


class EarReadLegBehaviorTest(unittest.TestCase):
    """读侧按**过期时刻**判定：过期＝放行，未过期＝丢弃且不进路由。"""

    def test_14_suppressed_device_never_reaches_dialog(self):
        d = FakeDialog()
        ear = make_ear(d, {DEV_ID: time.time() + 30.0})
        run(ear._handle_event(EVENT, SMAP))
        self.assertEqual([], d.consulted,
                         "窗口内的 ear 事件仍然走到路由＝回声会变成新一轮对话")

    def test_15_expired_stamp_is_not_suppressed(self):
        d = FakeDialog()
        ear = make_ear(d, {DEV_ID: time.time() - 1.0})
        run(ear._handle_event(EVENT, SMAP))
        self.assertIn("text", d.consulted,
                      "时间戳语义是「过期时刻」⇒ 过去时刻应放行；"
                      "本条在旧形状下是「1 秒前刚播报过」＝被压住（合并行 27 起点分叉的反证格）")


class GlobalLegNotShrunkTest(unittest.TestCase):
    """反回归：本批⛔ 动全局腿的公式（收窄它会重开路线图 154 那格）。"""

    def test_16_suppress_echo_keeps_floor_and_grows_with_length(self):
        obj = dlg.DialogManager.__new__(dlg.DialogManager)
        obj._echo_until = 0.0
        base = time.time()
        obj.suppress_echo("短")
        first = obj._echo_until - base
        self.assertGreaterEqual(first, 8.0 - 0.5, "全局窗下界 8.0 被改动")
        obj2 = dlg.DialogManager.__new__(dlg.DialogManager)
        obj2._echo_until = 0.0
        second = obj2.suppress_echo("字" * 100) or (obj2._echo_until - base)
        self.assertGreater(second, first, "长文本的全局窗没有比短文本更久⇒估算腿被写死")
        self.assertGreaterEqual(second, 40.0,
                                "100 字的全局窗应≈46s（0.4×len+6），收窄即回归 154 那格：现读 %.1fs" % second)


class AnchorSentinelTest(unittest.TestCase):
    """批6 名册锚点哨兵：`("core/dialog.py", 527, "notify")` 行号漂了要当场咬，⛔ 靠算术猜。

    批195 re-anchor 513→527：`_observe_presence` 助手在 `__init__` 下方净插 14 行，调用点本体一字未动。
    """

    def test_17_batch6_dialog_anchor_still_names_the_same_line(self):
        lines = DIALOG_PY.read_text(encoding="utf-8").splitlines()
        self.assertIn("self.tv.notify", lines[526],
                      "dialog.py:527 已不是 `self.tv.notify`⇒批6 名册要按符号重钉（⛔ 加减行号）")


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    n = loader.countTests(suite)
    if n != CELLS:
        raise SystemExit("CELLS 对账失败：名册 %d ≠ 本档实收 %d（漏收＝discover 收了不跑）" % (CELLS, n))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
