#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批168（路线图 v2.4「设备巡检定时报告内容修复」）：`ha_inspection` 的报告文本⛔ 再说谎。

现网取证（2026-10-10T15:27Z，只读 GET `/api/states`，⛔ 任何写操作）：
  · `HA|states=2889`；按现行逻辑复算出的报告文本 **len=241**，与容器日志
    `scheduled device inspection done, bark pushed (241 chars)` 三条**逐字对账**
    ⇒ 本档对引擎逻辑的复算＝现网同一把尺。
  · `BATTERY_BUCKETS|{'ok':46,'zero':3,'low':3,'unavailable':13,'unknown':2,'Charging':2}`
    ⇒ 现行谓词 `if 0 < level < battery_threshold:` 把 **3 台 0%（＝彻底没电）**整个吞掉，
      当日报告只说「3 个设备低电量」；另有 **15 枚电量传感器读数取不到**
      （`unavailable`/`unknown` 走 `except: pass`）＝「读不出来」被报成「没有」。
      电量传感器属 `sensor` 域、在 `_DEFAULT_IGNORE_DOMAINS` 里 ⇒ 这 15 枚⛔ 出现在掉线清单，
      即「全屋最像失联的一批设备」在报告里**完全隐身**。
  · `BAD|total=87` 中 `switch`=59、`media_player`=6…，且 `DEVICE_PREFIX|groups=21|members_total=50`
    ⇒ 一台音箱的多个虚拟开关各占一行（`小爱触屏音箱×3`、`Mix 3 … Voice Assistant` 一串），
      报告用「87 个设备掉线＋前 5 个实体名」占满了本来最该说话的位置。
  · `EMPTY_LIGHT|current=0|substring=0`：占用传感器的 `friendly_name` 是设备名
      （`领普人体存在传感器ES5 Occupancy`），灯名是 `卧室吸顶灯  灯` ⇒
      `name.split()[0]` 抽出的「房间」永不互等，**无人开灯这一项在本户结构性失效**，
      而报告仍写「所有设备正常」＝把一个从没跑成的检测算成了已检通过。

⇒ 本批改四条说谎腿：0% 要报、取不到读数要报、按**设备**归并、跑不成的检测标「无法判定」；
  ⛔ 动检测项名单、⛔ 动阈值默认值、⛔ 动 `ignore_domains` 语义（见授权边界腿）。
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (_HERE, os.path.dirname(_HERE)):
    if os.path.isdir(os.path.join(_cand, "butler")):
        sys.path.insert(0, _cand)
        break

from butler.skills.engines.ha_inspection.engine import HAInspectionEngine  # noqa: E402
from butler.skills.runner_types import SkillContext  # noqa: E402

ENGINE_PATH = os.path.join(_HERE, "..", "butler", "skills",
                           "engines", "ha_inspection", "engine.py")

BRAIN = {"checks": "all", "battery_threshold": 20}


class _HA:
    def __init__(self, states):
        self._states = states
        self.calls = 0

    async def get_states(self):
        self.calls += 1
        return self._states


class _RT:
    def __init__(self, states):
        self.ha = _HA(states)


def _st(eid, state, name=None, **attrs):
    a = dict(attrs)
    if name is not None:
        a["friendly_name"] = name
    return {"entity_id": eid, "state": state, "attributes": a}


def _run(states, brain=None):
    ctx = SkillContext(skill={"brain": brain or dict(BRAIN)}, source="scheduler", rt=_RT(states))
    return asyncio.new_event_loop().run_until_complete(HAInspectionEngine().run(ctx))


class InspectionReportContentTest(unittest.TestCase):

    def test_01_zero_percent_battery_is_reported(self):
        """0%＝彻底没电，现行 `0 < level` 把它吞了（现网 3 台）。"""
        r = _run([
            _st("switch.dev1", "on", "客厅主灯"),
            _st("sensor.bed_1", "0", "卧室夜灯  充电电池 电池电量", device_class="battery"),
        ])
        self.assertIn("0%", r.text, r.text)
        self.assertIn("电量已耗尽", r.text, r.text)
        self.assertIn("卧室夜灯", r.text, r.text)
        self.assertEqual(1, r.meta.get("dead_battery_count"), r.meta)

    def test_02_low_battery_still_reported(self):
        """回归闸：低电量档⛔ 因为加了 0% 档而变哑。"""
        r = _run([
            _st("switch.dev1", "on", "客厅主灯"),
            _st("sensor.rel_1", "10", "小米人体传感器2  充电电池 电池电量", device_class="battery"),
        ])
        self.assertIn("低电量", r.text, r.text)
        self.assertIn("10%", r.text, r.text)

    def test_03_threshold_is_respected_on_both_sides(self):
        states = [
            _st("sensor.a", "25", "A 电量", device_class="battery"),
            _st("sensor.b", "35", "B 电量", device_class="battery"),
        ]
        r = _run(states, brain={"checks": "all", "battery_threshold": 30})
        self.assertIn("A 电量", r.text, r.text)
        self.assertNotIn("B 电量(35", r.text, r.text)

    def test_04_battery_without_reading_is_reported_as_unreadable(self):
        """现网 15 枚电量传感器 `unavailable`/`unknown`＝读不出来，⛔ 报成「没有」。"""
        r = _run([
            _st("switch.dev1", "on", "客厅主灯"),
            _st("sensor.b1", "unavailable", "门窗传感器  电池", device_class="battery"),
            _st("sensor.b2", "unknown", "水位传感器  电池", device_class="battery"),
        ])
        self.assertIn("读不出来", r.text, r.text)
        self.assertEqual(2, r.meta.get("battery_no_reading_count"), r.meta)

    def test_05_offline_entities_are_merged_per_device(self):
        """一台音箱的 3 个虚拟开关＝1 台设备，⛔ 占 3 行（现网 87 实体→58 组）。"""
        r = _run([
            _st("switch.s1", "unavailable", "小爱触屏音箱  闹钟 开关"),
            _st("switch.s2", "unavailable", "小爱触屏音箱  唤醒提示音"),
            _st("switch.s3", "unavailable", "小爱触屏音箱  静音麦克风"),
            _st("switch.s4", "unavailable", "电信路由插座 Plug"),
        ])
        self.assertIn("2 台设备掉线", r.text, r.text)
        self.assertIn("4 处没信号", r.text, r.text)
        self.assertEqual(1, r.text.count("小爱触屏音箱"), r.text)
        self.assertEqual(2, r.meta.get("offline_device_groups"), r.meta)
        self.assertEqual(4, r.meta.get("offline_count"), r.meta)

    def test_06_unattributable_light_is_not_reported_as_normal(self):
        """本户「无人开灯」结构性跑不成 ⇒ 报告⛔ 再写「所有设备正常」。"""
        r = _run([
            _st("binary_sensor.p1", "off", "领普人体存在传感器ES5 Occupancy", device_class="occupancy"),
            _st("light.l1", "on", "卧室吸顶灯  灯"),
        ])
        self.assertNotIn("所有设备正常", r.text, r.text)
        self.assertIn("判断不了房间有没有人", r.text, r.text)
        self.assertEqual(1, r.meta.get("light_room_indeterminate"), r.meta)

    def test_07_empty_states_is_a_data_source_failure(self):
        """`get_states()` 回空＝数据源出问题，⛔ 是「巡检完成」。"""
        r = _run([])
        self.assertIn("数据源", r.text, r.text)
        self.assertNotIn("巡检完成", r.text, r.text)

    def test_08_all_clean_stays_short_and_silent(self):
        """回归闸：一切正常时⛔ 硬塞「判断不了」「读不出来」这类噪声行。"""
        r = _run([
            _st("switch.dev1", "off", "客厅主灯"),
            _st("sensor.b1", "90", "遥控器 电量", device_class="battery"),
        ])
        self.assertIn("未发现异常", r.text, r.text)
        for noise in ("判断不了", "读不出来", "掉线", "0%"):
            self.assertNotIn(noise, r.text, r.text)

    def test_09_light_on_in_occupied_room_is_not_flagged(self):
        """正对照：房间有人时开灯⛔ 进「判断不了」之外的任何异常行。"""
        r = _run([
            _st("binary_sensor.p1", "on", "客厅 人体传感器", device_class="occupancy"),
            _st("light.l1", "on", "客厅 灯"),
        ])
        self.assertNotIn("无人房间开灯", r.text, r.text)

    def test_14_report_text_has_no_developer_jargon(self):
        """出资人读这条推送：四档同时触发时，文案里⛔ 出现术语。"""
        r = _run([
            _st("switch.s1", "unavailable", "小爱触屏音箱  闹钟 开关"),
            _st("switch.s2", "unavailable", "小爱触屏音箱  静音麦克风"),
            _st("sensor.b1", "0", "卧室夜灯  充电电池 电池电量", device_class="battery"),
            _st("sensor.b2", "10", "门窗传感器  充电电池 电池电量", device_class="battery"),
            _st("sensor.b3", "unavailable", "水位传感器  电池", device_class="battery"),
            _st("binary_sensor.p1", "off", "领普人体存在传感器ES5 Occupancy",
                device_class="occupancy"),
            _st("light.l1", "on", "卧室吸顶灯  灯"),
        ])
        for word in ("实体", "状态点", "occupancy", "sensor", "domain", "404",
                     "数据面", "数据源", "⛔"):
            self.assertNotIn(word, r.text, "%s 在文案里：%s" % (word, r.text))
        self.assertEqual(5, r.meta.get("issues_count"), r.meta)


class ShapeRulerTest(unittest.TestCase):
    """形状尺：四条说谎腿的落点⛔ 漂回旧形制；两把校准（旧形状必须咬、缺新形状必须报）。"""

    NEEDLES = (
        "level <= 0",
        "offline_groups",
        "battery_no_reading_count",
        "light_room_indeterminate",
    )
    FORBIDDEN = (
        re.compile(r"\bif 0 < level < battery_threshold:"),
        re.compile(r"设备巡检完成，所有设备正常，无异常。"),
    )

    def _src(self):
        with open(ENGINE_PATH, encoding="utf-8") as fp:
            return fp.read()

    def test_10_new_shape_is_present(self):
        src = self._src()
        for n in self.NEEDLES:
            self.assertTrue(n in src, "缺新形状落点：%s" % n)

    def test_11_ruler_bites_the_old_shape(self):
        """负控：同一把尺对旧形状必须咬住（⛔ 恒绿尺）。"""
        old = (
            "        if 0 < level < battery_threshold:\n"
            '            text = "设备巡检完成，所有设备正常，无异常。"\n'
        )
        bites = [p.pattern for p in self.FORBIDDEN if p.search(old)]
        self.assertEqual(2, len(bites), bites)
        missing = [n for n in self.NEEDLES if n not in old]
        self.assertEqual(4, len(missing), missing)

    def test_12_current_source_has_no_forbidden_shape(self):
        src = self._src()
        for p in self.FORBIDDEN:
            self.assertIsNone(p.search(src), "旧形状回来了：%s" % p.pattern)

    def test_13_authorized_boundary_untouched(self):
        """授权边界：只改报告内容四条腿，检测项名单／默认阈值／忽略域语义⛔ 一起动。"""
        src = self._src()
        for token in ('"offline"', '"low_battery"', '"empty_room_light"',
                      "battery_threshold\") or 20", '"sensor", "binary_sensor"',
                      "_OFFLINE_CHECK_DOMAINS"):
            self.assertTrue(token in src, "越界或锚点丢失：%s" % token)


if __name__ == "__main__":
    unittest.main()
