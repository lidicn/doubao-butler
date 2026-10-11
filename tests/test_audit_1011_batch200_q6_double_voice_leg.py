"""批200 Q6 门（乙）：「同句双声」＝**设计**，认下来并写进门（DCD 20261011 七问裁定 §3 Q6）。

裁定原文口径：
 「Q6（乙）⛔ 不改 _emit_devices 分发。加一格门认领『同句双声＝设计』。⛔ 不得顺手加 room 优先过滤。」
所以本档⛔ 带任何生产码改动——它的产物是**一格门**：下一任看到同一句在两台小爱上都出声时，
指得到这里，知道那是裁过的设计而不是缺陷，也⛔ 顺手改成「只挑一台」。

同句多台的另一半配套腿也在本档钉住：小爱出声会自捕回声⇒ `suppress_echo` 只在**进循环前**设一次窗口
（每台都设＝窗口被反复续期，等于永久抑制）。

跑法：`python -B -m unittest tests.test_audit_1011_batch200_q6_double_voice_leg`（一套一进程）。
"""
from __future__ import annotations

import ast
import asyncio
import os
import sys
import types
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (_HERE, os.path.dirname(_HERE)):
    if os.path.isdir(os.path.join(_cand, "butler")):
        sys.path.insert(0, _cand)
        break


def _find_root():
    for cand in (_HERE, os.path.dirname(_HERE), "/app", os.getcwd()):
        if os.path.isfile(os.path.join(cand, "butler", "core", "dialog.py")):
            return cand
    raise unittest.SkipTest("找不到 butler/core/dialog.py⇒ 本档无物可判")


ROOT = _find_root()
DIALOG_SOURCE = os.path.join(ROOT, "butler", "core", "dialog.py")
TEXT = "已经帮你打开了"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _import_dialog():
    try:
        import butler.core.dialog as dlg
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest("宿主侧缺依赖（%s）⇒ 运行时腿只在容器内跑" % type(exc).__name__)
    return dlg


class FakeDevice:
    def __init__(self, did, room, dtype="xiaomi", play_mode="notify_text"):
        self.id = did
        self.room = room
        self.type = dtype
        self.play_mode = play_mode
        self.ha_entity = "notify." + did
        self.ha_player_entity = ""
        self.voice_override = ""


class FakeHa:
    def __init__(self):
        self.notify_calls = []

    async def notify_message(self, text, entity=None):
        self.notify_calls.append((text, entity))
        return "ok"

    async def schedule_media_stop(self, entity, secs):
        return "ok"


class Harness(unittest.TestCase):
    def setUp(self):
        self.dlg = _import_dialog()
        self.ha = FakeHa()
        self.echo_sets = []
        obj = self.dlg.DialogManager.__new__(self.dlg.DialogManager)
        obj.s = types.SimpleNamespace(speak_target="http",
                                      xiaomi_service_token="", xiaomi_user_id="")
        obj.ha = self.ha
        obj.tts = None
        obj.mqtt = None
        obj.tv = None
        obj.suppress_echo = lambda text: self.echo_sets.append(text)
        obj._est_speak_secs = lambda text: 1.0
        self.d = obj
        self.role = types.SimpleNamespace(id="butler", name="豆包管家", voice="zh-CN-XiaoxiaoNeural",
                                          tts_backend="edge", nowvoice_voice="")

    def _emit(self, devices):
        return _run(self.d._emit_devices(TEXT, devices, self.role))


class DoubleVoiceIsByDesignLeg(Harness):
    def test_01_two_xiaomi_devices_both_speak_the_same_text(self):
        # 这就是被裁认下的那一格：同一句话在两台上都出声⇒⛔ 缺陷
        devs = [FakeDevice("xiao_living", "客厅"), FakeDevice("xiao_study", "书房")]
        out = self._emit(devs)
        self.assertEqual(["xiao_living", "xiao_study"], [r["device"] for r in out], out)
        self.assertTrue(all(r["ok"] for r in out), out)
        self.assertEqual([(TEXT, "notify.xiao_living"), (TEXT, "notify.xiao_study")],
                         self.ha.notify_calls, "同句只发了一台⇒Q6 那一档被改成了单点分发")

    def test_02_room_difference_does_not_pick_one(self):
        # 裁定的禁令：⛔ 顺手加 room 优先过滤（房间信息此刻本来就不可靠）
        devs = [FakeDevice("xiao_living", "客厅"), FakeDevice("xiao_master", "主卧室")]
        out = self._emit(devs)
        self.assertEqual(2, len(out), out)
        self.assertEqual(2, len(self.ha.notify_calls))

    def test_03_echo_suppression_set_once_before_the_loop(self):
        # 双声的配套腿：⛔ 挪进循环⇒ 窗口被每台续期＝事实上永久抑制
        devs = [FakeDevice("xiao_living", "客厅"), FakeDevice("xiao_study", "书房"),
                FakeDevice("xiao_master", "主卧室")]
        self._emit(devs)
        self.assertEqual([TEXT], self.echo_sets,
                         "suppress_echo 现读被调 %d 次（应 1 次，且只在进循环前）" % len(self.echo_sets))
        self.assertEqual(3, len(self.ha.notify_calls), "出声腿没走到⇒ 本格在测空气")

    def test_04_three_devices_three_receipts_no_silent_drop(self):
        # 「双声」扩到三台也一样：回执条数必须＝台数（⛔ 吞掉某台还报成功）
        devs = [FakeDevice("d1", "客厅"), FakeDevice("d2", "书房"), FakeDevice("d3", "卫生间")]
        out = self._emit(devs)
        self.assertEqual(["d1", "d2", "d3"], [r["device"] for r in out])


CLEAN_LOOP = '''async def _emit_devices(self, text, devices, role):
    """小爱分支：一律由 butler 经 HA 直驱。"""
    if any(d.type == "xiaomi" for d in devices):
        self.suppress_echo(text)
    for dev in devices:
        out.append(await self.ha.notify_message(text, dev.ha_entity))
    return out
'''
SYNTH_FIRST_ONLY = CLEAN_LOOP.replace("for dev in devices:", "for dev in devices[:1]:")
SYNTH_BREAK = CLEAN_LOOP.replace(
    "        out.append(await self.ha.notify_message(text, dev.ha_entity))\n",
    "        out.append(await self.ha.notify_message(text, dev.ha_entity))\n        break\n")
SYNTH_ROOM_PRIORITY = CLEAN_LOOP.replace(
    "for dev in devices:", "for dev in sorted(devices, key=lambda d: d.room):")
SYNTH_ECHO_IN_LOOP = CLEAN_LOOP.replace(
    '    if any(d.type == "xiaomi" for d in devices):\n        self.suppress_echo(text)\n',
    "").replace("    for dev in devices:\n",
                "    for dev in devices:\n        self.suppress_echo(text)\n")


def emit_region(src: str) -> str:
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.AsyncFunctionDef) and n.name == "_emit_devices"), None)
    if fn is None:
        raise AssertionError("_emit_devices 找不到了⇒ 判据要重看")
    return ast.get_source_segment(src, fn) or ""


def _loop_pos(region: str) -> int:
    """分发循环那行的起点：按「for dev in 」前缀找，⛔ 钉死 `devices` 这个写法（SYNTH 会改成 devices[:1]／sorted(...)）。"""
    for line_no, ln in enumerate(region.splitlines()):
        if ln.strip().startswith("for dev in "):
            return region.index(ln.strip())
    return -1


def hits(region: str) -> list:
    out = []
    loop_pos = _loop_pos(region)
    if "devices[:1]" in region or "devices[0]" in region:
        out.append("single-device-distribution")
    echo_pos = region.find("self.suppress_echo(")
    if loop_pos >= 0 and echo_pos >= 0 and echo_pos > loop_pos:
        out.append("echo-window-renewed-per-device")
    if "sorted(" in region and "room" in region:
        out.append("room-priority-filter")
    if loop_pos >= 0 and "break" in region[loop_pos:]:
        out.append("loop-breaks-after-one")
    return out


CALIB = (
    ("CLEAN-应放行", CLEAN_LOOP, []),
    ("SYNTH-只发第一台", SYNTH_FIRST_ONLY, ["single-device-distribution"]),
    ("SYNTH-发完就 break", SYNTH_BREAK, ["loop-breaks-after-one"]),
    ("SYNTH-按房间排序（甲的顺手腿）", SYNTH_ROOM_PRIORITY, ["room-priority-filter"]),
    ("SYNTH-抑制窗口挪进循环", SYNTH_ECHO_IN_LOOP, ["echo-window-renewed-per-device"]),
)


class ShapeRulerLeg(unittest.TestCase):
    def test_05_ruler_calibrated_both_ways(self):
        for label, src, want in CALIB:
            self.assertEqual(want, hits(src), "%s 校准不符" % label)

    def test_06_real_region_on_judgment(self):
        region = emit_region(open(DIALOG_SOURCE, encoding="utf-8", errors="replace").read())
        self.assertIn("for dev in devices:", region, "分发循环换了形制⇒ 本档判据要重看")
        self.assertEqual([], hits(region), hits(region))

    def test_07_claim_is_written_into_the_gate(self):
        # 裁定要的是「认下＝设计，写进门」：这句话被删掉⇒ 下一任又会把它当缺陷报回来
        doc = __doc__ or ""
        self.assertIn("同句双声", doc, "本档 docstring 里那句认账不见了")
        self.assertIn("⛔ 不改 _emit_devices 分发", doc, "裁定的禁令没跟着留档")


if __name__ == "__main__":
    unittest.main(verbosity=2)
