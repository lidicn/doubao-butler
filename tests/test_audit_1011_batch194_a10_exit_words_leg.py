"""批194 A10 门：角色模式退出词必须由 `data/roles` 名册现读生成（⛔ 写死角色名）。

第二份小爱审计报告 A10（High，`doc/审计报告/doubao-butler_小爱音箱专项审计报告_第二轮.md:322`）：
    if any(kw in text for kw in ["关闭贾维斯", "关闭凯撒", "关闭露娜", "退出", "退下", "没事了", "好了退下"]):
写死的三枚名字在 `data/roles/*.json` 里 0 命中（角色收敛后已归档），而 `_match_open_role`
按 `打开{角色名/唤醒词}` 让 6 个真实角色全能进入角色模式 ⇒ **能进不能退**：90 秒窗口内
「关闭小理」这类语义失效，用户以为退了、其实没退，下一句话仍归进入模式时的角色。

本档校三条腿（各配负控，⛔ 只读形状不读行为）：
 1. 行为面：以名册合成夹具驱动 `XiaomiEar._build_exit_keywords()`⇒真实角色名与唤醒词都成 `关闭X`，
    通用词「退出/退下/没事了/好了退下」**⛔ 依赖名册**（空名册也必须在），归档名不再命中；
 2. 接线面：角色模式那段退出判断必须吃生成的表，⛔ 内联字面量；
 3. 同源性：生成腿读 `self.rt.roles.all()`（与 `_match_open_role` 同一个源），⛔ 另起一份名单。

形状尺两侧校准：CLEAN（应放行的合成码）⇒ 0 命中；三枚 SYNTH 各咬自己那一格。
分母守卫：判据必须**在真码里找得到那一行**，找不到即判尺子失效（⛔ 让「正则没匹配」冒充「0 命中」＝假绿）。
"""
import pathlib
import re
import sys
import types
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from butler.core.xiaomi_ear import XiaomiEar  # noqa: E402

EAR = REPO / "butler" / "core" / "xiaomi_ear.py"

# 名册现读（`data/roles/*.json` 的 name 列，2026-10-11 现读 6 枚）
REAL_ROLE_NAMES = ["小艾", "小凯", "小理", "豆包管家", "顾安恒", "晓月"]
REAL_WAKE_WORDS = ["艾米", "凯凯", "理叔", "豆包", "安恒"]
ARCHIVED_NAMES = ["贾维斯", "凯撒", "露娜"]
GENERICS = ["退出", "退下", "没事了", "好了退下"]

# 真码里那一行的形状（写死版与生成版共用同一前缀⇒只比前缀，⛔ 比整行；
# `.+` 吃到行内最后一个 `):`，否则生成版的 `()` 会让判据找不到那一行＝假绿）
EXIT_CHECK = re.compile(r"if any\(kw in text for kw in (.+)\):")
STALE_LIST = re.compile(r"\"关闭(?:贾维斯|凯撒|露娜)")


def hits(src: str) -> list[str]:
    """返回命中的缺陷名。空列表＝这格绿。"""
    out = []
    if STALE_LIST.search(src):
        out.append("hardcoded-archived-role-name-in-exit-words")
    m = EXIT_CHECK.search(src)
    if m is not None and "_build_exit_keywords" not in m.group(1):
        out.append("exit-check-not-from-generated-table")
    return out


def clean_src() -> str:
    return (
        "class XiaomiEar:\n"
        "    ROLE_EXIT_WORDS = (\"退出\",)\n"
        "    def _build_exit_keywords(self):\n"
        "        words = list(self.ROLE_EXIT_WORDS)\n"
        "        for role in self.rt.roles.all():\n"
        "            words.append(\"关闭\" + role.name)\n"
        "        return words\n"
        "    async def handle(self, text):\n"
        "        if any(kw in text for kw in self._build_exit_keywords()):\n"
        "            return\n"
    )


def synth_stale() -> str:
    return clean_src().replace(
        "        words = list(self.ROLE_EXIT_WORDS)\n",
        "        words = list(self.ROLE_EXIT_WORDS)\n"
        "        words.append(\"关闭贾维斯\")\n",
    )


def synth_inline() -> str:
    return clean_src().replace(
        "if any(kw in text for kw in self._build_exit_keywords()):",
        "if any(kw in text for kw in [\"关闭小理\", \"退出\"]):",
    )


def synth_stale_and_inline() -> str:
    return synth_inline().replace(
        "        words = list(self.ROLE_EXIT_WORDS)\n",
        "        words = list(self.ROLE_EXIT_WORDS)\n"
        "        words.append(\"关闭露娜\")\n",
    )


def synth_unrelated_literal() -> str:
    """负控用：码里出现「关闭小理」字面量，但⛔ 在退出词表里（是一行注释）⇒ 尺子不该咬。"""
    return clean_src().replace(
        "    async def handle(self, text):\n",
        "    async def handle(self, text):\n        # 用户说「关闭小理」时应退出\n",
    )


class FakeRole:
    def __init__(self, name, wake_words):
        self.name = name
        self.wake_words = wake_words


class FakeRoles:
    def __init__(self, roles):
        self._roles = roles

    def all(self):
        return list(self._roles)


def make_ear(roles):
    rt = types.SimpleNamespace(roles=FakeRoles(roles))
    return XiaomiEar(rt)


class TestBatch194A10ExitWords(unittest.TestCase):
    maxDiff = None

    # ---- 1. 行为面 ----
    def test_01_generated_table_covers_every_real_role_name(self):
        ear = make_ear([FakeRole(n, []) for n in REAL_ROLE_NAMES])
        words = ear._build_exit_keywords()
        for n in REAL_ROLE_NAMES:
            self.assertIn("关闭" + n, words, f"名册角色「{n}」的退出词缺失")

    def test_02_generated_table_covers_wake_words_too(self):
        ear = make_ear([FakeRole("小艾", ["小艾", "艾米"])])
        words = ear._build_exit_keywords()
        self.assertIn("关闭艾米", words, "唤醒词别名不生成退出词⇒「关闭艾米」退不出去")

    def test_03_generics_survive_empty_roster(self):
        words = make_ear([])._build_exit_keywords()
        for g in GENERICS:
            self.assertIn(g, words, f"通用退出词「{g}」被名册绑架（空名册即失效）")

    def test_04_archived_names_are_gone(self):
        ear = make_ear([FakeRole(n, []) for n in REAL_ROLE_NAMES])
        words = ear._build_exit_keywords() + make_ear([])._build_exit_keywords()
        for a in ARCHIVED_NAMES:
            self.assertNotIn("关闭" + a, words, f"归档角色名「{a}」仍在退出词表里")

    def test_05_predicate_accepts_real_says_rejects_archived(self):
        """把判定式跑成真·行为：真角色名放行、归档名不放行。"""
        ear = make_ear([FakeRole(n, w) for n, w in zip(
            REAL_ROLE_NAMES,
            [["小艾", "艾米"], ["小凯", "凯凯"], ["小理", "理叔"],
             ["豆包管家", "豆包"], ["顾安恒", "安恒"], ["晓月"]])])
        words = ear._build_exit_keywords()
        accepts = lambda t: any(kw in t for kw in words)  # noqa: E731
        self.assertTrue(accepts("关闭小理"))
        self.assertTrue(accepts("关闭豆包管家"))
        self.assertTrue(accepts("好了退下"))
        self.assertFalse(accepts("关闭贾维斯"), "归档名仍生效＝名册没接上")
        self.assertFalse(accepts("今天天气如何"), "退出词误伤普通句")

    # ---- 2. 接线面（真码） ----
    def test_06_ruler_finds_the_exit_check_line_in_production(self):
        """分母守卫：判据在真码里必须**找得到那一行**，否则 0 命中是假的。"""
        src = EAR.read_text(encoding="utf-8")
        self.assertIsNotNone(EXIT_CHECK.search(src),
                            "形状尺在 xiaomi_ear.py 里找不到退出判断行⇒本档判据失效")

    def test_07_production_source_has_no_defect(self):
        src = EAR.read_text(encoding="utf-8")
        self.assertEqual([], hits(src))

    def test_08_generated_leg_shares_the_open_role_source(self):
        """生成腿必须读 `self.rt.roles.all()`——与 `_match_open_role` 同源，⛔ 另起一份名单。"""
        src = EAR.read_text(encoding="utf-8")
        m = re.search(r"def _build_exit_keywords\(self\).*?\n(?=    (?:async )?def |\Z)",
                      src, re.S)
        self.assertIsNotNone(m, "_build_exit_keywords 缺失⇒同源格无从判")
        self.assertIn("self.rt.roles.all()", m.group(0),
                      "生成腿没读名册（另起了名单源）")

    # ---- 3. 尺子两侧校准 ----
    def test_09_clean_synthetic_passes(self):
        self.assertEqual([], hits(clean_src()))

    def test_10_synth_stale_name_is_bitten(self):
        self.assertEqual(["hardcoded-archived-role-name-in-exit-words"], hits(synth_stale()))

    def test_11_synth_inline_list_is_bitten(self):
        self.assertEqual(["exit-check-not-from-generated-table"], hits(synth_inline()))

    def test_12_synth_both_is_bitten_twice(self):
        self.assertEqual(
            ["hardcoded-archived-role-name-in-exit-words", "exit-check-not-from-generated-table"],
            sorted_stable(hits(synth_stale_and_inline())))

    def test_13_unrelated_literal_is_not_bitten(self):
        """负控：注释里出现「关闭小理」⛔ 算内联字面量。"""
        self.assertEqual([], hits(synth_unrelated_literal()))


def sorted_stable(items):
    order = ["hardcoded-archived-role-name-in-exit-words", "exit-check-not-from-generated-table"]
    return [o for o in order if o in items]


if __name__ == "__main__":
    unittest.main()
