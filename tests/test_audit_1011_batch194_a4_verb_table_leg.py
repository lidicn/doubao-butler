"""批194 A4：设备动词表**只增不删**——补「把X关/开了」名词在前的形＋「X调N度」温控形。

病灶（第二轮小爱审计 A4，`doc/审计报告/doubao-butler_小爱音箱专项审计报告_第二轮.md`；
分诊名册见镜像台账「批193」§二十九）：`_DEVICE_ON_PATTERNS` 六条句式全是**动词在前**
（`开.*灯`／`关.*空调`／`关闭.*电视`），而小爱话术里高频的是**名词在前**的「把」字形
（把灯关了／把客厅灯关掉／把电视打开）⇒ 落不进规则就走 LLM。温控「空调调到 26 度」
整族没有。

裁定的口径是**只增不删**（`doc/审计报告分诊台账-20261002.md` 批193 A4 行）：
 · 原有 6 条⛔ 改动、⛔ 调序 ⇒ 本档把它们**逐条钉在原位**（形状尺 + 行为钉各一把）；
 · 新形一律**追加在原 6 条之后** ⇒ 老命中永远先命中老句式；
 · 「X调N度」要带 `data={"temperature": N}`，而 `check_simple_command` 原来是
   `action["data"] = {"entity_id": entity_id}` **整键覆盖**＝把已经填好的 temperature 抹掉
   ⇒ 同笔改成合并。
 · ⛔ 追报告那 24 例当全集：亮度／音量族（灯亮一点／音量调大／把空调调亮）⛔ 本格，
   它们要的是 `brightness_pct`／`volume_set` 新载荷而非动词表增补 ⇒ 本档只把它们**钉成
   「仍不匹配」**，作为范围边界，不是作为通过。

本档校五腿：
 1. 行为钉：原 6 条句式（含空线索⇒追问）逐条与批194 之前的读数同形；
 2. 新形：把字族 8 例、关掉电视、温控 3 例命中正确的 domain/service/hint/data；
 3. 陷阱：「把灯开关打开」⛔ 被当成关灯（`(?!开关)` 那一腿）；语气闸与范围外三例仍 None；
 4. 汇聚腿：真走 `check_simple_command`＋真 `_resolve_entity`（假 HA 状态表，⛔ 打桩匹配器），
    证「认得出⇒data 同时带 entity_id 与 temperature」「认不出⇒只回追问且⛔ 发令」；
 5. 形状尺两侧校准：CLEAN（原 6 条在前＋尾部追加）⇒ 0 命中；三枚 SYNTH 各咬自己那一格。
"""
import ast
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import butler.core.simple_rules as sr  # noqa: E402

SRC = (REPO / "butler" / "core" / "simple_rules.py").read_text(encoding="utf-8")

# 原 6 条＝(匹配式, 抽取式, domain, service)，⛔ 改、⛔ 挪位
ORIGINAL_SIX = [
    ("开.*灯", "开(.*?)灯", "light", "turn_on"),
    ("关.*灯", "关(.*?)灯", "light", "turn_off"),
    ("开.*空调", "开(.*?)空调", "climate", "turn_on"),
    ("关.*空调", "关(.*?)空调", "climate", "turn_off"),
    ("打开.*电视", "打开(.*?)电视", "media_player", "turn_on"),
    ("关闭.*电视", "关闭(.*?)电视", "media_player", "turn_off"),
]


# ---- 形状尺：钉「只增不删」----

def _call_str(node):
    """`re.compile("X")` ⇒ "X"；不是这个形状返回 None。

    ⚠ 函数名在 AST 里是 `Attribute(attr="compile")`（`re.compile`）⛔ 是 `Name(id=...)`：
    只取 `id` 会让整把尺对真码报「读不到」＝一把恒红的尺。
    """
    if isinstance(node, ast.Call):
        fname = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if fname == "compile" and len(node.args) == 1 and isinstance(node.args[0], ast.Constant):
            return node.args[0].value
    return None


def first_six(src: str):
    """读 `_DEVICE_ON_PATTERNS` 赋值里**前 6 枚**元组的四元内容；读不到返回 None。"""
    tree = ast.parse(src)
    for node in tree.body:
        if not (isinstance(node, ast.Assign)
                and any(getattr(t, "id", "") == "_DEVICE_ON_PATTERNS" for t in node.targets)):
            continue
        if not isinstance(node.value, ast.List):
            return None
        rows = []
        for item in node.value.elts[:6]:
            if not (isinstance(item, ast.Tuple) and len(item.elts) == 4):
                return None
            pat = _call_str(item.elts[0])
            extract = _call_str(item.elts[1])
            domain = item.elts[2].value if isinstance(item.elts[2], ast.Constant) else None
            service = item.elts[3].value if isinstance(item.elts[3], ast.Constant) else None
            if None in (pat, extract, domain, service):
                return None
            rows.append((pat, extract, domain, service))
        return rows
    return None


def shape_hits(src: str) -> list[str]:
    """形状尺判据：返回缺陷名列表，空＝绿。"""
    rows = first_six(src)
    if rows is None:
        return ["verb-table-shape-not-found"]
    if rows != ORIGINAL_SIX:
        return ["first-six-verb-patterns-changed"]
    return []


# 原 6 条在前、尾部追加新形＝应放行
CLEAN = '''
_DEVICE_ON_PATTERNS = [
    (re.compile(r"开.*灯"), re.compile(r"开(.*?)灯"), "light", "turn_on"),
    (re.compile(r"关.*灯"), re.compile(r"关(.*?)灯"), "light", "turn_off"),
    (re.compile(r"开.*空调"), re.compile(r"开(.*?)空调"), "climate", "turn_on"),
    (re.compile(r"关.*空调"), re.compile(r"关(.*?)空调"), "climate", "turn_off"),
    (re.compile(r"打开.*电视"), re.compile(r"打开(.*?)电视"), "media_player", "turn_on"),
    (re.compile(r"关闭.*电视"), re.compile(r"关闭(.*?)电视"), "media_player", "turn_off"),
    (re.compile(r"把.*灯.*关"), re.compile(r"把(.*?)灯"), "light", "turn_off"),
]
'''

# 把原第 2 条改成宽形（＝动了原有命中）
SYNTH_MODIFIED = CLEAN.replace(
    '(re.compile(r"关.*灯"), re.compile(r"关(.*?)灯"), "light", "turn_off"),',
    '(re.compile(r"关"), re.compile(r"关(.*?)灯"), "light", "turn_off"),')

# 删掉原第 5 条（＝只删不增）
SYNTH_DELETED = CLEAN.replace(
    '    (re.compile(r"打开.*电视"), re.compile(r"打开(.*?)电视"), "media_player", "turn_on"),\n', "")

# 原 6 条被整体挪到尾部（＝新形抢在前面）
SYNTH_MOVED = '''
_DEVICE_ON_PATTERNS = [
    (re.compile(r"把.*灯.*关"), re.compile(r"把(.*?)灯"), "light", "turn_off"),
    (re.compile(r"开.*灯"), re.compile(r"开(.*?)灯"), "light", "turn_on"),
    (re.compile(r"关.*灯"), re.compile(r"关(.*?)灯"), "light", "turn_off"),
    (re.compile(r"开.*空调"), re.compile(r"开(.*?)空调"), "climate", "turn_on"),
    (re.compile(r"关.*空调"), re.compile(r"关(.*?)空调"), "climate", "turn_off"),
    (re.compile(r"打开.*电视"), re.compile(r"打开(.*?)电视"), "media_player", "turn_on"),
    (re.compile(r"关闭.*电视"), re.compile(r"关闭(.*?)电视"), "media_player", "turn_off"),
]
'''

# 尺子看不见的那一格：整个变量不在这个形状里
SYNTH_UNRELATED = '''
VERBS = ["开灯", "关灯"]
'''


# ---- 真 HA 状态表（跑真匹配器，⛔ 打桩 `_resolve_entity`）----

STATES = [
    {"entity_id": "light.living_room", "attributes": {"friendly_name": "客厅灯"}},
    {"entity_id": "light.bedroom", "attributes": {"friendly_name": "卧室灯"}},
    {"entity_id": "climate.living_room", "attributes": {"friendly_name": "客厅空调"}},
    {"entity_id": "media_player.tv", "attributes": {"friendly_name": "客厅电视"}},
]


class FakeHA:
    async def get_states(self):
        return list(STATES)


def make_agent():
    import types
    return types.SimpleNamespace(ha=FakeHA())


def run(coro):
    loop = __import__("asyncio").new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestBatch194VerbTable(unittest.TestCase):
    # 1) 原有 6 条句式的命中⛔ 变（这一组在批194 之前就是绿的＝回归钉）
    def test_01_existing_verb_forms_unchanged(self):
        cases = [
            ("开客厅灯", "好的，打开了。", "call_service", "light", "turn_on", "客厅"),
            ("关客厅灯", "好的，关闭了。", "call_service", "light", "turn_off", "客厅"),
            ("开卧室空调", "好的，打开了。", "call_service", "climate", "turn_on", "卧室"),
            ("关客厅空调", "好的，关闭了。", "call_service", "climate", "turn_off", "客厅"),
            ("打开书房电视", "好的，打开了。", "call_service", "media_player", "turn_on", "书房"),
            ("关闭客厅电视", "好的，关闭了。", "call_service", "media_player", "turn_off", "客厅"),
        ]
        for text, reply, action, domain, service, hint in cases:
            with self.subTest(text=text):
                result = sr._check_device_control(text)
                self.assertIsNotNone(result, f"{text} 不再命中")
                got_reply, got = result
                self.assertEqual(got_reply, reply)
                self.assertEqual(got["action"], action)
                self.assertEqual(got["domain"], domain)
                self.assertEqual(got["service"], service)
                self.assertEqual(got["device_hint"], hint)

    def test_02_existing_bare_forms_still_ask_device(self):
        for text, verb, noun in [("开灯", "开", "灯"), ("关灯", "关", "灯"),
                                 ("开空调", "开", "空调"), ("打开电视", "开", "电视")]:
            with self.subTest(text=text):
                reply, got = sr._check_device_control(text)
                self.assertEqual(got["action"], "ask_device")
                self.assertEqual(reply, f"要{verb}哪个房间的{noun}？")
                self.assertNotIn("entity_id", got.get("data", {}))

    # 2) 新形：「把」字族（名词在前）
    def test_03_ba_form_off(self):
        cases = [
            ("把客厅灯关掉", "light", "turn_off", "客厅"),
            ("把客厅的灯关了", "light", "turn_off", "客厅"),
            ("把卧室灯关闭", "light", "turn_off", "卧室"),
            ("把客厅空调关了", "climate", "turn_off", "客厅"),
            ("把客厅电视关掉", "media_player", "turn_off", "客厅"),
        ]
        for text, domain, service, hint in cases:
            with self.subTest(text=text):
                result = sr._check_device_control(text)
                self.assertIsNotNone(result, f"{text} 未命中")
                got = result[1]
                self.assertEqual(got["action"], "call_service")
                self.assertEqual(got["domain"], domain)
                self.assertEqual(got["service"], service)
                self.assertEqual(got["device_hint"], hint)

    def test_04_ba_form_on(self):
        cases = [
            ("把客厅灯打开", "light", "turn_on", "客厅"),
            ("把客厅的灯开了", "light", "turn_on", "客厅"),
            ("把客厅空调打开", "climate", "turn_on", "客厅"),
            ("把客厅电视打开", "media_player", "turn_on", "客厅"),
        ]
        for text, domain, service, hint in cases:
            with self.subTest(text=text):
                got = sr._check_device_control(text)
                self.assertIsNotNone(got, f"{text} 未命中")
                self.assertEqual(got[1]["action"], "call_service")
                self.assertEqual(got[1]["domain"], domain)
                self.assertEqual(got[1]["service"], service)
                self.assertEqual(got[1]["device_hint"], hint)

    def test_05_ba_form_without_room_asks_instead_of_commanding(self):
        for text, domain, service in [("把灯关了", "light", "turn_off"),
                                      ("把灯关掉", "light", "turn_off"),
                                      ("把空调关了", "climate", "turn_off")]:
            with self.subTest(text=text):
                got = sr._check_device_control(text)
                self.assertIsNotNone(got, f"{text} 未命中")
                self.assertEqual(got[1]["action"], "ask_device")
                self.assertEqual(got[1]["domain"], domain)
                self.assertEqual(got[1]["service"], service)

    def test_06_guan_diao_tv_variant(self):
        result = sr._check_device_control("关掉客厅电视")
        self.assertIsNotNone(result, "关掉客厅电视 未命中")
        self.assertEqual(result[1]["domain"], "media_player")
        self.assertEqual(result[1]["service"], "turn_off")
        self.assertEqual(result[1]["device_hint"], "客厅")

    # 3) 「X调N度」温控族
    def test_07_temperature_rule(self):
        cases = [
            ("把客厅空调调到26度", 26, "call_service", "客厅", "好的，调到 26 度了。"),
            ("客厅空调调至26.5度", 26.5, "call_service", "客厅", "好的，调到 26.5 度了。"),
            ("空调调到26度", None, "ask_device", "", "要把哪个房间的空调调到 26 度？"),
        ]
        for text, temp, action, hint, reply in cases:
            with self.subTest(text=text):
                result = run(sr.check_simple_command(text, make_agent()))
                self.assertIsNotNone(result, f"{text} 未命中任何规则")
                got = result[1]
                self.assertEqual(got["action"], action)
                self.assertEqual(got["domain"], "climate")
                self.assertEqual(got["service"], "set_temperature")
                self.assertEqual(result[0], reply)
                if action == "call_service":
                    self.assertEqual(got["data"].get("temperature"), temp)
                    self.assertEqual(got["device_hint"], hint)

    # 4) 陷阱：「灯开关」⛔ 被读成关灯
    def test_08_switch_noun_is_not_an_off_command(self):
        for text in ["把灯开关打开", "灯开关关了没？", "把空调开关按下", "把电视开关打开"]:
            with self.subTest(text=text):
                self.assertIsNone(sr._check_device_control(text))

    def test_09_tone_gate_still_excludes_ba_form(self):
        for text in ["是不是把客厅灯关了", "把客厅灯关了没有", "要不要把空调关了"]:
            with self.subTest(text=text):
                self.assertIsNone(sr._check_device_control(text))

    def test_10_out_of_scope_families_stay_unmatched(self):
        """亮度／音量族⛔ 本格范围＝钉成「仍走 LLM」，不是钉成通过。"""
        for text in ["灯亮一点", "音量调大", "把空调调亮", "客厅灯暗一点"]:
            with self.subTest(text=text):
                self.assertIsNone(run(sr.check_simple_command(text, make_agent())))

    # 5) 汇聚腿：真匹配器 + data 合并（原覆盖写会把 temperature 抹掉）
    def test_11_ruler_fixture_resolves_real_entity(self):
        """分母守卫：假状态表确实能被**真**匹配器认出，否则后面的绿是桩子给的。"""
        self.assertEqual(run(sr._resolve_device_entity(make_agent(), "客厅", "light")),
                         "light.living_room")
        self.assertEqual(run(sr._resolve_device_entity(make_agent(), "客厅", "climate")),
                         "climate.living_room")
        self.assertEqual(run(sr._resolve_device_entity(make_agent(), "次卧", "light")), "")

    def test_12_entity_id_merged_not_clobbered(self):
        got = run(sr.check_simple_command("把客厅空调调到26度", make_agent()))[1]
        self.assertEqual(got["action"], "call_service")
        self.assertEqual(got["data"], {"temperature": 26, "entity_id": "climate.living_room"})

    def test_13_plain_toggle_still_only_carries_entity_id(self):
        got = run(sr.check_simple_command("把客厅灯关掉", make_agent()))[1]
        self.assertEqual(got["action"], "call_service")
        self.assertEqual(got["data"], {"entity_id": "light.living_room"})

    def test_14_unresolved_entity_does_not_issue_command(self):
        cases = [
            ("把次卧空调调到26度", "要把哪个房间的空调调温度？"),
            ("把次卧灯关掉", "要关哪个房间的灯？"),
        ]
        for text, reply in cases:
            with self.subTest(text=text):
                result = run(sr.check_simple_command(text, make_agent()))
                self.assertIsNotNone(result, f"{text} 掉进 LLM")
                self.assertEqual(result[1]["action"], "ask_device")
                self.assertEqual(result[0], reply)
                self.assertNotIn("entity_id", result[1].get("data", {}))

    # 6) 形状尺：只增不删
    def test_15_production_source_first_six_pinned(self):
        self.assertEqual(shape_hits(SRC), [])

    def test_16_clean_synthetic_passes(self):
        self.assertEqual(shape_hits(CLEAN), [])

    def test_17_synth_modified_is_bitten(self):
        self.assertEqual(shape_hits(SYNTH_MODIFIED), ["first-six-verb-patterns-changed"])

    def test_18_synth_deleted_is_bitten(self):
        self.assertEqual(shape_hits(SYNTH_DELETED), ["first-six-verb-patterns-changed"])

    def test_19_synth_moved_is_bitten(self):
        self.assertEqual(shape_hits(SYNTH_MOVED), ["first-six-verb-patterns-changed"])

    def test_20_synth_unrelated_is_bitten(self):
        self.assertEqual(shape_hits(SYNTH_UNRELATED), ["verb-table-shape-not-found"])


if __name__ == "__main__":
    unittest.main()
