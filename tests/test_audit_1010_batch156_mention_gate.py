#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批156（DCD 2026-10-10 件五 乙）：小爱来源「没点名⛔ 接话」。

病灶现读：`butler/core/xiaomi_ear.py:215` —— 唤醒词没匹配上时，只要该房间的默认角色
`scope=private`，整句原文照样交给 `dialog.on_wakeup` ⇒ 用户对小爱说「把台灯关掉」，
管家以为在跟自己说话，答一段、播出来（耳朵 88h 听到 1,912 句，闸门只认下 62 句，
其中 60 次就是这么开口的）。

授权范围（裁定原文，越界即违规）：只动「是否发声」这一处判断 + 一行留痕日志；
⛔ 动 TTS 引擎、⛔ 动队列、⛔ 动设备号口径、⛔ 动 `device_aliases.json`（26 条学习条目＝用户数据）。
因此本档除行为腿外还挂两条结构腿：`on_wakeup` 调用点只准少不准多、耳朵里⛔ 出现 tts/device_aliases。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import sys
import time
import types
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (_HERE, os.path.dirname(_HERE)):
    if os.path.isdir(os.path.join(_cand, "butler")):
        sys.path.insert(0, _cand)
        break

try:
    from butler.core import xiaomi_ear as xe
except Exception as _exc:  # noqa: BLE001
    raise unittest.SkipTest("批156 宿主侧缺依赖（%s）⇒ 本档只在容器内跑" % type(_exc).__name__)

MARK = "ear mention-suppressed"
ENTITY = "sensor.ear_b156"
DEV_ID = "ear_b156_dev"
ROOM = "study"
TEXT = "把台灯关掉"
WAKE = "小管家"
EVENT = {"data": {"entity_id": ENTITY, "new_state": {"state": TEXT}}}
SMAP = {ENTITY: types.SimpleNamespace(id=DEV_ID, room=ROOM, wake_button="")}


class FakeDialog:
    """只记「谁被叫到、被问过什么」，⛔ 复现真角色匹配。"""

    def __init__(self, wake_role=None, room_role=None):
        self.wakeups = []
        self.consulted = []
        self._wake = wake_role
        self._room = room_role

    def match_role_by_text(self, text):
        self.consulted.append("text")
        if self._wake is None:
            return None, text
        return self._wake, text.replace(WAKE, "", 1).strip()

    def match_role_by_room(self, room):
        self.consulted.append("room")
        return self._room

    async def on_wakeup(self, role_id, room, text, source_device="", source=""):
        self.wakeups.append((role_id, room, text, source_device, source))


def fake_role(rid, scope="private"):
    return types.SimpleNamespace(id=rid, scope=scope)


def make_ear(dialog, role_mode=None):
    obj = xe.XiaomiEar.__new__(xe.XiaomiEar)
    # 类属性是共享可变字典：⛔ 让本档的写入漏进别的用例
    obj._echo_cooldown = {}
    obj._role_mode = dict(role_mode or {})
    obj.rt = types.SimpleNamespace(
        ha=None, dialog=dialog, audiobook=None,
        roles=types.SimpleNamespace(all=lambda: []))
    return obj


class _Cap(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@contextlib.contextmanager
def cap_logs():
    cap = _Cap()
    lg = xe.logger
    prev = lg.level
    lg.addHandler(cap)
    lg.setLevel(logging.DEBUG)
    try:
        yield cap
    finally:
        lg.removeHandler(cap)
        lg.setLevel(prev)


def mark_hits(cap):
    return [r for r in cap.records if MARK in r.getMessage()]


def entry_hits(cap):
    return [r for r in cap.records
            if r.getMessage().startswith("ear [") and ENTITY in r.getMessage()]


def run_event(dialog, role_mode=None):
    obj = make_ear(dialog, role_mode)
    with cap_logs() as cap:
        asyncio.run(obj._handle_event(EVENT, SMAP))
    return obj, cap


class MentionGateTest(unittest.TestCase):
    def test_positive_control_reached_the_matching_stage(self):
        """正对照：确实走到唤醒词匹配之后，⛔「没开口」其实是在更早的格子里回掉了。"""
        dialog = FakeDialog(wake_role=None, room_role=fake_role("assistant"))
        _obj, cap = run_event(dialog)
        self.assertEqual(["text", "room"], dialog.consulted,
                         "耳朵没走到「唤醒词→房间默认角色」这两问＝夹具测的是别处")
        self.assertEqual(1, len(entry_hits(cap)), "入口留痕必须恰好 1 条")

    def test_unmentioned_text_is_not_answered(self):
        """本件的靶：没点名＝⛔ 交给对话引擎。"""
        dialog = FakeDialog(wake_role=None, room_role=fake_role("assistant"))
        _obj, _cap = run_event(dialog)
        self.assertEqual([], dialog.wakeups,
                         "没点名却开口了：on_wakeup 收到 %r" % (dialog.wakeups,))

    def test_suppression_leaves_exactly_one_log_line(self):
        """留痕一行：挡下来的每一句都要能数到，⛔ 静默。"""
        dialog = FakeDialog(wake_role=None, room_role=fake_role("assistant"))
        _obj, cap = run_event(dialog)
        hits = mark_hits(cap)
        self.assertEqual(1, len(hits), "抑制留痕应恰好 1 条，实得 %d 条" % len(hits))
        msg = hits[0].getMessage()
        self.assertIn(DEV_ID, msg)
        self.assertIn(ROOM, msg)

    def test_shared_room_default_role_stays_quiet(self):
        """⛔ 把留痕的分母扩大：本来就不会答的（非私人角色）照旧无声。"""
        dialog = FakeDialog(wake_role=None, room_role=fake_role("butler", scope="shared"))
        _obj, cap = run_event(dialog)
        self.assertEqual([], dialog.wakeups)
        self.assertEqual(0, len(mark_hits(cap)))

    def test_no_default_role_room_stays_quiet(self):
        dialog = FakeDialog(wake_role=None, room_role=None)
        _obj, cap = run_event(dialog)
        self.assertEqual([], dialog.wakeups)
        self.assertEqual(0, len(mark_hits(cap)))

    def test_wake_word_still_gets_answered(self):
        """点名那一腿⛔ 被我一起掐掉：唤醒词命中照旧交给对话引擎。"""
        dialog = FakeDialog(wake_role=fake_role("jarvis"), room_role=fake_role("assistant"))
        _obj, cap = run_event(dialog)
        self.assertEqual([("jarvis", ROOM, TEXT, DEV_ID, "xiaoai")], dialog.wakeups)
        self.assertEqual(0, len(mark_hits(cap)))

    def test_role_mode_followup_still_gets_answered(self):
        """角色模式是用户自己「打开贾维斯」起的续话，属已点名：本件⛔ 动它。"""
        dialog = FakeDialog(wake_role=None, room_role=fake_role("assistant"))
        mode = {DEV_ID: {"role_id": "jarvis", "expires_at": time.time() + 60}}
        _obj, cap = run_event(dialog, role_mode=mode)
        self.assertEqual([("jarvis", ROOM, TEXT, DEV_ID, "xiaoai")], dialog.wakeups)
        self.assertEqual([], dialog.consulted, "角色模式里⛔ 再问一遍房间默认角色")
        self.assertEqual(0, len(mark_hits(cap)))


class ScopeBoundaryTest(unittest.TestCase):
    """授权范围钉成尺子：只准掐这一处，越界（改 TTS／动别名表／加发声出口）先红。"""

    def setUp(self):
        with open(os.path.abspath(xe.__file__), encoding="utf-8") as f:
            self.src = f.read()

    def test_ear_has_exactly_two_answer_outlets(self):
        self.assertEqual(2, len(re.findall(r"\bself\.rt\.dialog\.on_wakeup\b", self.src)),
                         "耳朵里的发声出口数量变了：授权只动那一处判断")

    def test_ear_touches_no_tts_or_alias_surface(self):
        """负控在下一条：同一个计数器对确实存在的词必须给出非 0。"""
        for banned in ("device_aliases", "butler.tts", "from butler import tts"):
            self.assertNotIn(banned, self.src, "越界：耳朵里出现了 %s" % banned)

    def test_counter_is_not_a_constant_zero(self):
        """尺子会咬：同形计数对「一定存在」的词必须数到 >0。"""
        self.assertGreater(len(re.findall(r"\bself\.rt\.dialog\.on_wakeup\b", self.src)), 0)


class RulerBitesTest(unittest.TestCase):
    def test_capture_sees_a_hand_written_mark(self):
        with cap_logs() as cap:
            xe.logger.info("%s [%s|%s] text=%s", MARK, DEV_ID, ROOM, "手工探针")
        self.assertEqual(1, len(mark_hits(cap)))

    def test_capture_ignores_other_lines(self):
        with cap_logs() as cap:
            xe.logger.info("ear echo-suppressed [%s]", DEV_ID)
        self.assertEqual(0, len(mark_hits(cap)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
