"""批194 A6＋A7①＋cron 合成一处：设备级回声窗口的登记点**下沉到 `_emit_devices`，且只登记 `ok=True` 的小爱**。

病灶（第二轮小爱审计 A6/A7①，`doc/审计报告/doubao-butler_小爱音箱专项审计报告_第二轮.md`；
分诊名册见镜像台账「批193」§二十九）：登记腿原在 `speak_as_role` 尾部，
`for d in devices: XiaomiEar.note_speak(d.id, ...)`，两重错：
 1. **A7① 登记全部 resolve 出的设备**：下发失败（`ok=False`）的那台也被算成「刚播过」⇒
    窗口内（下界 15s，长句按 `_est_speak_secs` 张开）那台音箱的真人话音被耳腿吞掉；
 2. **A6 旁路**：`_emit_devices` 有三个调用方（`dialog.py` 的 `speak_as_role`／`speak` 与
    `cron_task.py` 的定时播报），登记只挂在其一 ⇒ 走另两路的播报**根本没有设备级窗口**。

本档校四腿：
 · 行为面：`_emit_devices` 返回后，`XiaomiEar._echo_cooldown` 里只有 `ok=True` 的**小爱**台；
   电视台不入表（那台不走小爱耳腿）；`hold_s` 取 `_est_speak_secs(text)`；
 · 汇聚面：单独调 `_emit_devices`（cron 的形状）也必须登记；
 · 唯一登记点：`speak_as_role` 体内⛔ 再出现 `XiaomiEar.note_speak`，全文件只许一枚调用；
 · 形状尺两侧校准：CLEAN（应放行的合成码）⇒ 0 命中，三枚 SYNTH 各咬自己那一格。

`XiaomiEar._echo_cooldown` 是**类属性**（跨实例共享）⇒ 每例前后快照／还原，⛔ 让上一例污染下一例。
"""
import ast
import pathlib
import sys
import time
import types
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from butler.core.dialog import DialogManager  # noqa: E402
from butler.core.xiaomi_ear import XiaomiEar  # noqa: E402

DIALOG = REPO / "butler" / "core" / "dialog.py"
CALL = "XiaomiEar.note_speak("


def func_body(src: str, name: str) -> str:
    """取顶层类里那枚函数/协程函数的源码段；找不到返回空串。"""
    tree = ast.parse(src)
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        for fn in cls.body:
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == name:
                return ast.get_source_segment(src, fn) or ""
    return ""


def hits(src: str) -> list[str]:
    """形状尺判据：返回缺陷名列表，空＝绿。"""
    out = []
    emit = func_body(src, "_emit_devices")
    role = func_body(src, "speak_as_role")
    if emit and CALL not in emit:
        out.append("registration-missing-in-emit-devices")
    if role and CALL in role:
        out.append("registration-still-in-speak-as-role")
    if src.count(CALL) > 1:
        out.append("registration-not-single-point")
    if not emit:
        out.append("emit-devices-body-not-found")
    return out


CLEAN = '''
class DialogManager:
    async def _emit_devices(self, text, devices, role):
        from butler.core.xiaomi_ear import XiaomiEar
        for rec in out:
            if rec["ok"]:
                XiaomiEar.note_speak(rec["device"], hold_s=self._est_speak_secs(text))
        return out
    async def speak_as_role(self, role, text, room, member):
        out = await self._emit_devices(text, devices, role)
        return {"spoken": True, "devices": out}
'''
SYNTH_NO_SINK = CLEAN.replace(
    '        from butler.core.xiaomi_ear import XiaomiEar\n'
    '        for rec in out:\n'
    '            if rec["ok"]:\n'
    '                XiaomiEar.note_speak(rec["device"], hold_s=self._est_speak_secs(text))\n',
    '        pass\n')
SYNTH_OLD_SITE = CLEAN.replace(
    '        out = await self._emit_devices(text, devices, role)\n'
    '        return {"spoken": True, "devices": out}',
    '        out = await self._emit_devices(text, devices, role)\n'
    '        XiaomiEar.note_speak(d.id, hold_s=self._est_speak_secs(text))\n'
    '        return {"spoken": True, "devices": out}')
SYNTH_DOUBLE = CLEAN.replace(
    '        return {"spoken": True, "devices": out}',
    '        XiaomiEar.note_speak("x", 1)\n'
    '        return {"spoken": True, "devices": out}')
SYNTH_NO_EMIT = CLEAN.replace("async def _emit_devices(self, text, devices, role):",
                              "async def _emit_something_else(self, text, devices, role):")


def dev(dtype, did, play_mode="notify_text", entity="notify.x", player=""):
    return types.SimpleNamespace(type=dtype, id=did, play_mode=play_mode,
                                 ha_entity=entity, ha_player_entity=player, enabled=True)


ROLE = types.SimpleNamespace(id="butler", name="豆包管家", voice="zh-CN-X",
                             tts_backend="edge", nowvoice_voice="")


class FakeHA:
    def __init__(self, fail_entity_ids=()):
        self.failed = set(fail_entity_ids)

    async def notify_message(self, message, entity_id=None):
        if entity_id in self.failed:
            return "http 400: boom"
        return "ok"

    async def get_xiaoai_id(self, ent):
        return None

    async def tts_play_url(self, url, ent):
        return "ok"

    async def schedule_media_stop(self, ent, secs):
        return None

    def play_url(self, url):
        return None


class FakeTTS:
    async def synthesize(self, text, **kw):
        return types.SimpleNamespace(public_url="http://x/y.mp3")


class FakeTV:
    def play_url(self, url):
        return None


def make_dm(fail_entity_ids=()):
    dm = DialogManager.__new__(DialogManager)   # ⛔ 跑 12 参构造：本档只驱动 _emit_devices
    dm.s = types.SimpleNamespace(speak_target="ha")
    dm.mqtt = None
    dm.tts = FakeTTS()
    dm.tv = FakeTV()
    dm.ha = FakeHA(fail_entity_ids)
    dm._echo_until = 0.0
    return dm


def run(coro):
    loop = __import__("asyncio").new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestBatch194NoteSpeakSink(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self._saved = dict(XiaomiEar._echo_cooldown)
        XiaomiEar._echo_cooldown.clear()

    def tearDown(self):
        XiaomiEar._echo_cooldown.clear()
        XiaomiEar._echo_cooldown.update(self._saved)

    # ---- 行为面 ----
    def test_01_only_ok_devices_get_window(self):
        dm = make_dm(fail_entity_ids={"notify.bad"})
        run(dm._emit_devices("你好", [dev("xiaomi", "x_ok", entity="notify.ok"),
                                      dev("xiaomi", "x_bad", entity="notify.bad")], ROLE))
        self.assertIn("x_ok", XiaomiEar._echo_cooldown, "播成功的小爱没登记窗口")
        self.assertNotIn("x_bad", XiaomiEar._echo_cooldown,
                         "下发失败的小爱仍登记⇒A7① 原病灶")

    def test_02_emit_devices_alone_registers_for_cron_shape(self):
        """cron 那路只调 `_emit_devices`（`cron_task.py:483`）⇒ 汇聚点必须自己登记。"""
        dm = make_dm()
        run(dm._emit_devices("定时播报", [dev("xiaomi", "x1", entity="notify.a")], ROLE))
        self.assertIn("x1", XiaomiEar._echo_cooldown,
                      "_emit_devices 不登记⇒走 cron／speak 两路仍无设备级窗口（A6 旁路）")

    def test_03_hold_follows_estimated_length(self):
        dm = make_dm()
        text = "一" * 100          # _est_speak_secs ⇒ 100*0.32 = 32.0s，高于 15s 下界
        before = time.time()
        run(dm._emit_devices(text, [dev("xiaomi", "x1", entity="notify.a")], ROLE))
        held = XiaomiEar._echo_cooldown["x1"] - before
        self.assertTrue(31.0 <= held <= 33.0, f"窗口时长 {held:.2f}s 不跟文本长度张开")

    def test_04_tv_devices_do_not_enter_xiaomi_ear_table(self):
        dm = make_dm()
        run(dm._emit_devices("电视的话", [dev("tv", "tv1", entity="")], ROLE))
        self.assertNotIn("tv1", XiaomiEar._echo_cooldown,
                         "电视台进了小爱耳腿的冷却表（那台没有 conversation 传感器）")

    def test_05_failed_notify_returns_ok_false_receipt(self):
        """底座自检：夹具真的能造出 ok=False 的回执——⛔ 让 test_01 的「不在表里」来自回执没造出来。"""
        dm = make_dm(fail_entity_ids={"notify.bad"})
        out = run(dm._emit_devices("你好", [dev("xiaomi", "x_bad", entity="notify.bad")], ROLE))
        self.assertEqual([False], [r["ok"] for r in out], "夹具没能造出失败回执⇒整档判据空转")

    # ---- 汇聚面（码面） ----
    def test_06_production_source_clean(self):
        self.assertEqual([], hits(DIALOG.read_text(encoding="utf-8")))

    def test_07_cron_caller_still_goes_through_the_sink(self):
        src = (REPO / "butler" / "core" / "cron_task.py").read_text(encoding="utf-8")
        self.assertIn("._emit_devices(", src,
                      "cron 不再走 `_emit_devices`⇒本档的汇聚面结论过期，需重开分诊")

    # ---- 尺子校准 ----
    def test_08_clean_synthetic_passes(self):
        self.assertEqual([], hits(CLEAN))

    def test_09_synth_no_sink_is_bitten(self):
        self.assertEqual(["registration-missing-in-emit-devices"], hits(SYNTH_NO_SINK))

    def test_10_synth_old_site_is_bitten(self):
        """登记留在老位＝汇聚面没接上？⛔ 这里只判「老位还在」＋「两处登记」，
        `_emit_devices` 里那一枚 CLEAN 已有⇒missing 不该出现（假红方向要钉死）。"""
        self.assertEqual(["registration-still-in-speak-as-role",
                          "registration-not-single-point"], hits(SYNTH_OLD_SITE))

    def test_11_synth_double_is_bitten(self):
        self.assertEqual(["registration-still-in-speak-as-role",
                          "registration-not-single-point"], hits(SYNTH_DOUBLE))

    def test_12_synth_emit_body_missing_is_bitten(self):
        """分母守卫：尺子找不到 `_emit_devices` 本体时必须自己红，⛔ 拿「0 命中」冒充绿。"""
        self.assertEqual(["emit-devices-body-not-found"], hits(SYNTH_NO_EMIT))


if __name__ == "__main__":
    unittest.main()
