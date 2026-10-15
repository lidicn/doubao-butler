# -*- coding: utf-8 -*-
"""派工单-DB-fired恢复-20261012 的验收门（执行单 四 row 1／2／3／4／6）。

背景：`28310ab`（标题「第四期审计23条缺陷修复」）把 `7f0f9c7`（批135）与 10-08 裁定
已落的 MQTT/status JSON 三处恢复项删掉了。DCD 20261012 裁 Q1=甲（恢复 MQTT），
本门钉的就是「恢复之后必须成立的形状」。

口径边界：
- ⛔ 不 import `butler.app`（重依赖＋启动期副作用）；row 5（app.py import＋调用）与
  row 7（批135 转绿）由 `tests/test_audit_1009_batch135_af_fired_failed.py` 负责。
- row 6（JSON 解码器恰好 1 个）是**护栏格**：回退前后它都可能是绿的，所以配双侧校准
  （合成源码应放行＋合成源码应咬），否则这格零信息。
- MA 侧载荷形状取自 `memory-agent/src/memory_agent/mqtt_bridge.py:99 _build_status_json`
  现读（键＝`state`），⛔ 自己编一份「像 JSON 的」载荷。
"""
from __future__ import annotations

import ast
import json
import pathlib
import tempfile
import unittest

from butler.bus.topics import SUB_TOPICS, adm_status_is_online
from homesdk.adm.status import encode_status

REPO = pathlib.Path(__file__).resolve().parent.parent
MA_STATE_KEY = "state"

# MA 的在线载荷（逐字节同构于 _build_status_json：state/ts/degraded/reasons/version）
MA_ONLINE_JSON = json.dumps(
    {"state": "online", "ts": 1760000000, "degraded": False,
     "reasons": [], "version": "2.7"},
    separators=(",", ":"))
MA_DEGRADED_JSON = json.dumps(
    {"state": "degraded", "ts": 1760000000, "degraded": True,
     "reasons": ["ADM_ERR_BROKER_UNREACHABLE"], "version": "2.7"},
    separators=(",", ":"))
MA_OFFLINE_JSON = json.dumps(
    {"state": "offline", "ts": 1760000000, "degraded": True,
     "reasons": ["ADM_ERR_BROKER_UNREACHABLE"], "version": "2.7"},
    separators=(",", ":"))


def _function_segment(src: str, name: str) -> str:
    """取模块级函数体源码；取不到返回空串（调用方按「缺该函数」处理）。"""
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    return ""


def handwritten_decoder_sites(src: str) -> list[int]:
    """判读腿里手写的 JSON 解码点（行号列表）。

    认两种形状：`json.loads(...)`／`loads(...)`。库单点 `decode_status(...)` ⛔ 不算病灶
    ——它正是 row 6 要求的那一个解码器。
    """
    hits = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
        if name in ("loads", "load"):
            hits.append(node.lineno)
    return hits


def wheel_violations(dockerfile_text: str) -> list[str]:
    """Dockerfile 里 homesdk wheel 的装腿违规（非 0.3.2 的每一行都点名）。"""
    bad = []
    for line in dockerfile_text.splitlines():
        if "homesdk-" in line and "0.3.2-py3-none-any.whl" not in line:
            bad.append(line.strip())
    return bad


# 开关行（row 3）：`28310ab` **新增**的那几枚符号→落点文件（名册只收 diff 的 `+` 腿）。
# 谁整体 revert 那笔，这七枚当场消失——本名册就是那条红。
# ⛔ 拿「修改前后都在」的字样（如 `sys_override` 形参）当标记＝一把不会红的尺。
SWITCH_ROW_SYMBOLS = [
    ("butler/core/security_monitor.py", "mark_undelivered"),      # DB61
    ("butler/core/cron_task.py", "_mask_url_secret"),             # DB56
    ("butler/core/cron_task.py", "_apis_poisoned"),               # DB65
    ("butler/roles/store.py", "_target_path"),                    # DB57
    ("butler/app.py", "mark_undelivered"),                        # DB61 调用腿
    ("butler/tts/manager.py", "gate_tts"),                        # DB64
    ("butler/api/llm_chat.py", "ignored remote system override"),  # DB73
    # DB73b 哨兵形制随批221 变（`bool(body.get(...))` → 共用尺子 `body_bool`）：
    #   本行口径覆盖批202 row 3 登记的那一枚；被 revert 的判据仍是「override_quiet 由调用方显式声明、默认 False」。
    ("butler/api/tts_routes.py", 'body_bool(body, "override_quiet", default=False)'),  # DB73b
]


def missing_symbols(pairs: list[tuple[str, str]], root: pathlib.Path) -> list[str]:
    """返回「文件里找不到该符号」的条目；文件读不到同样算缺。"""
    out = []
    for rel, sym in pairs:
        path = root / rel
        if not path.is_file():
            out.append(f"{rel}:{sym}(文件不在位)")
            continue
        if sym not in path.read_text(encoding="utf-8", errors="replace"):
            out.append(f"{rel}:{sym}")
    return out


class TestStatusReadLeg(unittest.TestCase):
    """row 1／2 ＋ 裁定 三 的三条语义（兼容字面量／degraded 视为在场／坏载荷 fail-closed）。"""

    def test_row1_ma_json_status_is_online(self):
        self.assertTrue(adm_status_is_online(MA_ONLINE_JSON),
                        "MA 的 JSON status 读成离线＝E6 那枚恒 offline 还在")

    def test_row1_homesdk_encoded_json_is_online(self):
        self.assertTrue(adm_status_is_online(encode_status("online", version="2.7")))

    def test_semantic2_degraded_counts_as_present(self):
        self.assertTrue(adm_status_is_online(MA_DEGRADED_JSON),
                        "degraded＝能力降档≠离线，判成离线会让降级成员被当成不在场")
        self.assertTrue(adm_status_is_online({"state": "degraded"}))

    def test_offline_json_is_offline(self):
        self.assertFalse(adm_status_is_online(MA_OFFLINE_JSON))
        self.assertFalse(adm_status_is_online({"state": "offline"}))

    def test_row2_legacy_literal_still_readable(self):
        self.assertTrue(adm_status_is_online("online"))
        self.assertTrue(adm_status_is_online(b"online"))
        self.assertFalse(adm_status_is_online("offline"))
        self.assertFalse(adm_status_is_online(b" offline \n"))

    def test_semantic3_unreadable_fails_closed(self):
        for bad in ("", "   ", "{not-json", b"", None, 123, ["online"]):
            self.assertFalse(adm_status_is_online(bad), f"不可解载荷被判成在场：{bad!r}")

    def test_dict_without_state_falls_back_to_legacy_online_flag(self):
        """dict 直传分支：认 `state` 优先，没有才回退旧 `online` 布尔字段（7f0f9c7 原文）。"""
        self.assertTrue(adm_status_is_online({"online": True}))
        self.assertFalse(adm_status_is_online({"online": False}))


class TestSubscriptionLeg(unittest.TestCase):
    """恢复 3 的 topics 半腿：两枚订阅常量存在且进了 SUB_TOPICS（⛔ 只定义不订阅）。"""

    def test_af_topics_present_and_subscribed(self):
        import butler.bus.topics as topics

        fired = getattr(topics, "SUB_AF_AUTOMATION_FIRED", None)
        failed = getattr(topics, "SUB_AF_AUTOMATION_FAILED", None)
        self.assertEqual(fired, "af/automation/fired", "订阅常量没恢复（或被注释回去）")
        self.assertEqual(failed, "af/automation/failed", "订阅常量没恢复（或被注释回去）")
        self.assertIn(fired, SUB_TOPICS, "常量在但没进 SUB_TOPICS＝订不到")
        self.assertIn(failed, SUB_TOPICS, "常量在但没进 SUB_TOPICS＝订不到")


class TestSingleDecoderRuler(unittest.TestCase):
    """row 6：topics.py 里 JSON 解码器恰好 1 个＝库的 decode_status。"""

    @classmethod
    def setUpClass(cls):
        cls.src = (REPO / "butler" / "bus" / "topics.py").read_text(encoding="utf-8")

    def test_topics_uses_the_library_single_point(self):
        # 批224 DB127：判读单点拆成 adm_status_state（解码＋四形）与 adm_status_is_online（bool 派生），
        # 库解码器仍只有一枚，锚点跟着挪到状态腿。整文件的手写解码器腿在下一条测试里，那条一字未动。
        body = _function_segment(self.src, "adm_status_state")
        self.assertNotEqual(body, "", "判读单点函数不见了")
        self.assertIn("decode_status", body,
                      "adm_status_state 没挂 homesdk.adm.status.decode_status")

    def test_topics_has_no_handwritten_json_decoder(self):
        sites = handwritten_decoder_sites(self.src)
        self.assertEqual(sites, [], f"topics.py 出现手写 JSON 解码器（禁令 3）：行 {sites}")

    def test_ruler_bites_synthetic_second_decoder(self):
        """负控：合成源码里手写第二个解码器，尺子必须咬住。"""
        synth = (
            "import json\n\n"
            "def adm_status_is_online(payload):\n"
            "    obj = json.loads(payload)\n"
            "    return obj.get('state') == 'online'\n"
        )
        self.assertEqual(len(handwritten_decoder_sites(synth)), 1, "尺子不会咬＝row 6 恒绿")

    def test_ruler_passes_clean_synthetic(self):
        """正控：合成源码只调库单点，尺子必须放行（⛔ 把 decode_status 判成病灶）。"""
        synth = (
            "def adm_status_is_online(payload):\n"
            "    from homesdk.adm.status import decode_status\n"
            "    return decode_status(payload).get('state') == 'online'\n"
        )
        self.assertEqual(handwritten_decoder_sites(synth), [], "尺子误伤放行侧")


class TestDockerfileWheelPin(unittest.TestCase):
    """row 4：镜像装的是 0.3.2——`homesdk.adm` 只存在于 0.3.2，装错版则恢复的码 import 就崩。"""

    def test_dockerfile_installs_only_032(self):
        text = (REPO / "Dockerfile").read_text(encoding="utf-8")
        self.assertEqual(wheel_violations(text), [], "Dockerfile 里的 homesdk 装腿不是 0.3.2")
        self.assertIn("COPY vendor/homesdk-0.3.2-py3-none-any.whl", text)
        self.assertIn("pip install --no-cache-dir /tmp/homesdk-0.3.2-py3-none-any.whl", text)

    def test_both_wheels_still_in_vendor(self):
        """恢复 1 的核对项：vendor 里两枚 wheel 并存（⛔ 顺手删 0.3.1，那不在本单授权内）。"""
        for name in ("homesdk-0.3.1-py3-none-any.whl", "homesdk-0.3.2-py3-none-any.whl"):
            self.assertTrue((REPO / "vendor" / name).is_file(), f"vendor/{name} 不在位")

    def test_ruler_bites_wrong_wheel_pin(self):
        bad = (
            "COPY vendor/homesdk-0.3.1-py3-none-any.whl /tmp/homesdk-0.3.1-py3-none-any.whl\n"
            "RUN pip install --no-cache-dir /tmp/homesdk-0.3.1-py3-none-any.whl && "
            "rm -f /tmp/homesdk-0.3.1-py3-none-any.whl\n"
        )
        self.assertEqual(len(wheel_violations(bad)), 2, "尺子不会咬＝row 4 恒绿")


class TestSwitchRow(unittest.TestCase):
    """row 3（开关行）：`28310ab` 的 23 条修复必须逐条仍在——本门红＝整笔被 revert 了。"""

    def test_28310ab_symbols_still_present(self):
        self.assertEqual(missing_symbols(SWITCH_ROW_SYMBOLS, REPO), [],
                         "开关行红：23 条修复里有符号消失了")

    def test_ruler_bites_when_a_fix_is_reverted(self):
        """负控：符号被撤走时尺子要咬（⛔ 报「逐条仍在」却是一把不会红的尺）。"""
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "butler").mkdir()
            (root / "butler" / "one.py").write_text("def kept():\n    return 1\n", encoding="utf-8")
            missing = missing_symbols([("butler/one.py", "kept"),
                                       ("butler/one.py", "gone_symbol"),
                                       ("butler/none.py", "whatever")], root)
            self.assertEqual(missing, ["butler/one.py:gone_symbol", "butler/none.py:whatever(文件不在位)"],
                             missing)


if __name__ == "__main__":
    unittest.main()
