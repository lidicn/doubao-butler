"""DCD 20261104「房间定义统一到 HA area_id 三仓契约」—— DB 侧边界层验收。

覆盖 T1/T3/T5：canonical 键=area_id、设备绑 area_id（修 Kevin/Emily 两处分叉）、
旧中文名双读、空 room 语义不回归、区外文本解析不到返回 None（`AREA_UNRESOLVED` 的信号面）。

跑法：`python -B -m unittest tests.test_areas_boundary`
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import types
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from butler.areas import ROOM_TO_AREA_ID, display_room, resolve_room  # noqa: E402
from butler.devices import Device, DeviceRegistry  # noqa: E402


def _settings(data_dir):
    # _seed() 读 self.s.xiaomi_notify_entity（默认主小爱 notify 实体），测试里给空串即可
    return types.SimpleNamespace(data_dir=data_dir, xiaomi_notify_entity="")


class AreaResolveTests(unittest.TestCase):
    def test_01_display_name_and_alias_map_to_area_id(self):
        self.assertEqual(resolve_room("书房"), "shu_fang")
        # 两处与 HA 不一致的旧中文名（T3 要修的分叉）
        self.assertEqual(resolve_room("Kevin房间"), "fang_jian")
        self.assertEqual(resolve_room("Emily房间"), "wo_shi")
        # HA area_name 双读
        self.assertEqual(resolve_room("卧室"), "wo_shi")
        self.assertEqual(resolve_room("房间"), "fang_jian")
        self.assertEqual(resolve_room("主卧"), "zhu_wo_shi")  # AF 旧词过渡别名

    def test_02_area_id_passthrough_and_strip(self):
        self.assertEqual(resolve_room("shu_fang"), "shu_fang")
        self.assertEqual(resolve_room("  ke_ting  "), "ke_ting")

    def test_03_unresolved_returns_none(self):
        # AF 旧 AREA_WORDS 里 HA 没有的区 + 空输入 ⇒ None（契约信号）
        for text in ("次卧", "阳台", "玄关", "餐厅", "", None, "   "):
            self.assertIsNone(resolve_room(text), text)

    def test_04_display_room(self):
        self.assertEqual(display_room("shu_fang"), "书房")
        self.assertIsNone(display_room("no_such_area"))
        self.assertIsNone(display_room(""))

    def test_05_table_has_no_duplicate_area_ids_for_aliases(self):
        # 别名不得覆盖成第二套键值：两处分叉必须指向 HA 真 id
        self.assertEqual(ROOM_TO_AREA_ID["Kevin房间"], "fang_jian")
        self.assertEqual(ROOM_TO_AREA_ID["Emily房间"], "wo_shi")


class DeviceBindingTests(unittest.TestCase):
    def test_06_seed_has_area_id_for_every_device(self):
        reg = DeviceRegistry(_settings(str(REPO_ROOT / "data")))
        seed = reg._seed()
        self.assertEqual(len(seed), 9)
        for did, dev in seed.items():
            self.assertTrue(dev.area_id, did)
        self.assertEqual(seed["xiao_kevin"].area_id, "fang_jian")
        self.assertEqual(seed["xiao_emily"].area_id, "wo_shi")
        self.assertEqual(seed["xiao_study"].area_id, "shu_fang")
        self.assertEqual(seed["tv_living"].area_id, "ke_ting")

    def test_07_display_name_unchanged(self):
        # Q4 甲：230 处中文串留显示层，⛔ 顺手改成 HA area_name
        reg = DeviceRegistry(_settings(str(REPO_ROOT / "data")))
        seed = reg._seed()
        self.assertEqual(seed["xiao_kevin"].room, "Kevin房间")
        self.assertEqual(seed["xiao_emily"].room, "Emily房间")

    def test_08_device_roundtrip_area_id(self):
        d = Device("x", "xiaomi", room="书房", area_id="shu_fang")
        self.assertEqual(Device.from_dict(d.to_dict()).area_id, "shu_fang")
        self.assertEqual(Device.from_dict(d.to_dict()).room, "书房")

    def test_09_load_backfills_and_persists_old_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "devices.json"
            p.write_text(
                json.dumps({
                    "xiao_study": {"id": "xiao_study", "type": "xiaomi", "room": "书房", "enabled": True},
                }),
                encoding="utf-8",
            )
            reg = DeviceRegistry(_settings(td))
            reg.load()
            self.assertEqual(reg.get("xiao_study").area_id, "shu_fang")
            saved = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(saved["xiao_study"]["area_id"], "shu_fang")


class DualReadTests(unittest.TestCase):
    def _reg(self):
        reg = DeviceRegistry(_settings(str(REPO_ROOT / "data")))
        reg.devices = {
            "xiao_study": Device("xiao_study", "xiaomi", room="书房", area_id="shu_fang", enabled=True),
            "xiao_kevin": Device("xiao_kevin", "xiaomi", room="Kevin房间", area_id="fang_jian", enabled=True),
        }
        return reg

    def test_10_resolve_by_area_id(self):
        reg = self._reg()
        ids = ["xiao_study", "xiao_kevin"]
        self.assertEqual([d.id for d in reg.resolve(ids, "shu_fang")], ["xiao_study"])
        self.assertEqual([d.id for d in reg.resolve(ids, "fang_jian")], ["xiao_kevin"])

    def test_11_resolve_by_legacy_chinese(self):
        reg = self._reg()
        ids = ["xiao_study", "xiao_kevin"]
        self.assertEqual([d.id for d in reg.resolve(ids, "书房")], ["xiao_study"])
        self.assertEqual([d.id for d in reg.resolve(ids, "Kevin房间")], ["xiao_kevin"])

    def test_12_empty_room_still_unfiltered(self):
        reg = self._reg()
        self.assertEqual(len(reg.resolve(["xiao_study", "xiao_kevin"], "")), 2)
        self.assertEqual(len(reg.resolve(["xiao_study", "xiao_kevin"], None)), 2)
        self.assertEqual(len(reg.resolve(["xiao_study", "xiao_kevin"], "*")), 2)

    def test_13_unresolved_room_left_untouched(self):
        reg = self._reg()
        self.assertEqual(reg._canonical_room("次卧"), "次卧")

    def test_14_player_entity_by_area_id_and_display(self):
        from butler.tts.adapter import TTSManagerSpeaker

        reg = DeviceRegistry(_settings(str(REPO_ROOT / "data")))
        reg.devices = {
            "xiao_study": Device(
                "xiao_study", "xiaomi", room="书房", area_id="shu_fang",
                ha_player_entity="media_player.xiao_x08a", enabled=True,
            ),
        }
        sp = TTSManagerSpeaker(manager=None, devices=reg)
        self.assertEqual(sp._player_entity("shu_fang"), "media_player.xiao_x08a")
        self.assertEqual(sp._player_entity("书房"), "media_player.xiao_x08a")


class InboxAreaRejectTests(unittest.TestCase):
    def test_15_inbox_resolver(self):
        from butler.bus.inbox import _resolve_area_id

        reg = DeviceRegistry(_settings(str(REPO_ROOT / "data")))
        reg.devices = {"xiao_study": Device("xiao_study", "xiaomi", room="书房", area_id="shu_fang")}
        rt = types.SimpleNamespace(devices=reg)
        self.assertEqual(_resolve_area_id("书房", rt), "shu_fang")
        self.assertEqual(_resolve_area_id("shu_fang", rt), "shu_fang")
        self.assertIsNone(_resolve_area_id("次卧", rt))
        self.assertIsNone(_resolve_area_id("", rt))


if __name__ == "__main__":
    unittest.main(verbosity=2)
