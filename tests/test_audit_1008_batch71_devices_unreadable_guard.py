r"""批71 验收（TDD，先红后绿）：D3＝`devices.json` 读失败回退出厂种子后，写腿拿种子做整份覆盖。

来源（报告本只在 E/出资人面 `E:\NAS\doubao-butler\doc\审计报告\`，权威树⛔ 该子目录；
  分族口径引台账 §85-10 与 `第七轮`「按根因合并」表第一行：读路径失败被当成「没有数据」＝D1 D2 D3 D4 D6 D7 D8 D9）：
  - D3【P3·low】`butler/devices.py`。改前现读（基底＝HEAD，本文件⛔ 别座在途：
    `git status --porcelain butler/devices.py` 空、`git show HEAD:butler/devices.py` md5 == 工作树 md5 == `45b68d28a772`）：
    `load:163-178` 里 `json.loads(self.path.read_text(...))`（:167）解析失败 ⇒ `logger.warning`（:174）
    ＋`self.devices = seed`（:175，出厂 8 台）；`save:180-182`／`_save_held:184-192` 拿这份**不完整**的内存态
    `write_json_atomic`（:190）整份盖盘 ⇒ 用户在 WebUI 自加的设备被出厂表抹掉，永久找不回。
    写腿入口：`upsert:218-227`、`delete:229-236`；HTTP 入口 `api/device_routes.py:30`（upsert）与 `:42`（delete）。
  - 读侧兜底本身是对的（批17 已定：⛔ 抛穿成 500），本批⛔ 动那半；只补另一半：
    「存在但读不出」＝现场还在盘上，写腿必须拒绝拿兜底值做整份覆盖。
  - 判定口径⛔ 第 6 份 try/except：走批70b 收敛好的 `butler/core/atomic_json.read_json_object(path) -> (data, unreadable)`。
  - `if not self.path.exists(): self.save()`（:177-178）＝缺档首次播种，那是「真没有」，⛔ 一并拒掉
    （否则新装环境永远建不起设备表）——本批专门留一条对照腿盯住这格。

覆盖口径：14 条＝**10 条先红**（现读 `FAILED (failures=8, errors=2)`：2 条以 `AttributeError` 形态红
 ＝`unreadable` 位改前根本不存在，8 条以断言形态红＝拒写／返回 None／路由撒谎／源码里还是自家 try/except）
 ＋**4 条放行对照**（② ，绿码上也该绿，⛔ 计入先红）：
  ① test_corrupt_devices_marks_unreadable_after_load /
    test_readable_devices_flag_is_false /
    test_corrupt_devices_upsert_refuses_full_overwrite_and_keeps_bytes /
    test_corrupt_devices_delete_refuses_full_overwrite_and_keeps_bytes /
    test_corrupt_devices_explicit_save_refuses_and_keeps_bytes /
    test_save_returns_true_on_readable_file /
    test_corrupt_devices_refusal_is_logged /
    test_upsert_route_reports_refusal_as_error /
    test_delete_route_reports_refusal_as_error /
    test_load_uses_converged_reader_not_own_try_except
  ② test_missing_file_first_seed_write_still_allowed /
    test_readable_devices_roundtrip_keeps_user_device /
    test_corrupt_devices_read_side_still_falls_back_without_raising /
    test_in_memory_edit_still_applies_during_refusal
"""
from __future__ import annotations

import asyncio
import inspect
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from butler.api import device_routes
from butler import devices as devices_mod
from butler.devices import DeviceRegistry

_USER_MARKER = "MY_LIVING_ROOM_SPEAKER_DO_NOT_LOSE_ME"


class _Recorder:
    """拒绝是用户看不见的动作，日志是它唯一的外部出口 ⇒ 「有没有留痕」必须数得出来。"""

    def __init__(self):
        self.rows: list[tuple[str, str]] = []

    def _log(self, level, msg, *args):
        self.rows.append((level, str(msg) % args if args else str(msg)))

    def error(self, msg, *args):
        self._log("error", msg, *args)

    def warning(self, msg, *args):
        self._log("warning", msg, *args)

    def info(self, msg, *args):
        self._log("info", msg, *args)

    def debug(self, msg, *args):
        self._log("debug", msg, *args)

    def levels(self, level):
        return [m for lv, m in self.rows if lv == level]


class _DevicesCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="b71_"))
        self.addCleanup(self._rm)
        self.dev_path = self.tmp / "devices.json"
        self.settings = types.SimpleNamespace(
            data_dir=str(self.tmp), xiaomi_notify_entity="notify.x_test")
        self.reg = DeviceRegistry(self.settings)

    def _rm(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _corrupt(self) -> bytes:
        # 半截 JSON：文件在、里面有用户设备，就是解析不出来 ⇒ 「读不出来」≠「没有数据」
        raw = ('{"' + _USER_MARKER + '": {"id": "' + _USER_MARKER +
               '", "type": "xiaomi", "room": "客厅", "ha_entity": "notify.a"}, "tv_liv')
        self.dev_path.write_text(raw, encoding="utf-8")
        return self.dev_path.read_bytes()

    def _seeded_ok(self) -> bytes:
        raw = {_USER_MARKER: {"id": _USER_MARKER, "type": "xiaomi", "room": "客厅",
                              "ha_entity": "notify.a"}}
        self.dev_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        return self.dev_path.read_bytes()

    def _patch_route(self, reg):
        p1 = mock.patch.object(device_routes, "guard", lambda request: None)
        p2 = mock.patch.object(device_routes, "get_runtime",
                               lambda: types.SimpleNamespace(devices=reg))
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    # ---- ① 先红 ----

    def test_corrupt_devices_marks_unreadable_after_load(self):
        self._corrupt()
        self.reg.load()
        self.assertTrue(self.reg.unreadable,
                        "D3：坏档 load 完必须留一个「这份内存态不完整」的位，写腿才有得可查")

    def test_readable_devices_flag_is_false(self):
        self._seeded_ok()
        self.reg.load()
        self.assertFalse(self.reg.unreadable,
                         "读得出来的档⛔ 标成 unreadable，否则正常环境永远存不了盘")

    def test_corrupt_devices_upsert_refuses_full_overwrite_and_keeps_bytes(self):
        before = self._corrupt()
        self.reg.load()
        self.reg.upsert({"id": "new_dev", "type": "xiaomi", "room": "书房"})
        self.assertEqual(self.dev_path.read_bytes(), before,
                         "D3：坏档后 upsert＝拿出厂 8 台整份盖掉用户自加的设备，现场必须原样留着")

    def test_corrupt_devices_delete_refuses_full_overwrite_and_keeps_bytes(self):
        before = self._corrupt()
        self.reg.load()
        self.reg.delete("tv_living")
        self.assertEqual(self.dev_path.read_bytes(), before,
                         "D3：delete 走的是同一条 _save_held，拒写口径⛔ 只堵 upsert 一条腿")

    def test_corrupt_devices_explicit_save_refuses_and_keeps_bytes(self):
        before = self._corrupt()
        self.reg.load()
        self.reg.save()
        self.assertEqual(self.dev_path.read_bytes(), before)

    def test_save_returns_true_on_readable_file(self):
        self._seeded_ok()
        self.reg.load()
        self.assertIs(self.reg.save(), True,
                      "写腿要能被调用方判断成败：可读档上 save() 必须真返回 True（⛔ None）")

    def test_corrupt_devices_refusal_is_logged(self):
        self._corrupt()
        self.reg.load()
        rec = _Recorder()
        p = mock.patch.object(devices_mod, "logger", rec)
        p.start()
        self.addCleanup(p.stop)
        self.reg.save()
        self.assertTrue(rec.levels("error"),
                        "拒绝落盘是「保存失败」级别的事件，只在读侧留一条 warning 不够——写侧要自己喊")

    def test_upsert_route_reports_refusal_as_error(self):
        before = self._corrupt()
        self.reg.load()
        self._patch_route(self.reg)

        class _Req:
            async def json(self):
                return {"id": "webui_new", "type": "tv", "room": "客厅"}

        resp = asyncio.run(device_routes.upsert_device(_Req()))
        body = json.loads(resp.body)
        self.assertFalse(body["ok"],
                         "D3：被拒的保存仍回 ok=True＝对 WebUI 撒谎，用户以为设备加上了")
        self.assertEqual(self.dev_path.read_bytes(), before)

    def test_delete_route_reports_refusal_as_error(self):
        before = self._corrupt()
        self.reg.load()
        self._patch_route(self.reg)

        class _Req:
            path_params = {"id": "tv_living"}

        resp = asyncio.run(device_routes.delete_device(_Req()))
        body = json.loads(resp.body)
        self.assertFalse(body["ok"],
                         "D3：delete 的拒写同样⛔ 报成功")
        self.assertEqual(self.dev_path.read_bytes(), before)

    def test_load_uses_converged_reader_not_own_try_except(self):
        src = inspect.getsource(DeviceRegistry.load)
        self.assertIn("read_json_object", src,
                      "「读不出来／本来就没有」的判定必须留在那一处收敛的尺子上，⛔ 每家自己 try/except")

    # ---- ② 放行对照（改前改后都该绿）----

    def test_missing_file_first_seed_write_still_allowed(self):
        self.assertFalse(self.dev_path.exists())
        self.reg.load()
        self.assertTrue(self.dev_path.exists(),
                        "缺档＝真没有，首次播种那一次写⛔ 顺手拒掉（新装环境就建不起表了）")
        data = json.loads(self.dev_path.read_text(encoding="utf-8"))
        self.assertTrue(data, "播种落盘的应是出厂设备表，⛔ 空档")

    def test_readable_devices_roundtrip_keeps_user_device(self):
        self._seeded_ok()
        self.reg.load()
        self.reg.upsert({"id": "second_dev", "type": "xiaomi", "room": "书房"})
        again = DeviceRegistry(self.settings)
        again.load()
        self.assertIsNotNone(again.get(_USER_MARKER))
        self.assertIsNotNone(again.get("second_dev"))

    def test_corrupt_devices_read_side_still_falls_back_without_raising(self):
        self._corrupt()
        try:
            self.reg.load()
        except Exception as e:  # noqa: BLE001 - 这条腿就是在问它会不会抛
            self.fail("读侧兜底⛔ 变成抛穿（批17 口径）：%s" % e)
        self.assertTrue(self.reg.all(), "坏档时管家还得有设备可用＝兜出厂种子，本批⛔ 动这半")

    def test_in_memory_edit_still_applies_during_refusal(self):
        self._corrupt()
        self.reg.load()
        self.reg.upsert({"id": "runtime_only", "type": "tv", "room": "阳台"})
        self.assertIsNotNone(self.reg.get("runtime_only"),
                             "拒的是「整份覆盖写」，⛔ 顺手把编辑功能关掉")


if __name__ == "__main__":
    unittest.main()
