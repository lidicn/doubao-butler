"""批194 A9：电子书指令匹配从「子串吞句」收紧成「整段相等」。

病灶（第二轮小爱审计 A9，High，`doc/审计报告/doubao-butler_小爱音箱专项审计报告_第二轮.md`
§二·A9 与 §六 T13/T14；分诊见镜像台账「批193」名册 A9 行）：`_match_audiobook_cmd` 用
`any(k in t for k in [...])`——
 1. 「继续/暂停/等一下」出现在**任何句子里**都中招：「继续开灯」被读成 resume（想开灯，
    电子书被恢复了），「往后翻」「往前」被读成 next/prev（本是翻页与方位语义）；
 2. 族序＝隐式优先级，「顺序错」由表序决定而非语义决定。
而这条支路在 `audiobook.state.playing` 时**优先于唤醒词、优先于角色模式**执行，被它 `return`
掉的那句话管家全程不答⇒「答非所问／没内容」。

本档校三腿：
 · 行为面＝报告 §二·A9 那张 10 例复现表逐字钉（含 T13「继续开灯⇒None」、T14「往后⇒None」），
   外加呼格段（「豆包，暂停」）、尾随语气词（「暂停吧」）、纯标点（「继续！」）三条**不该被收窄**的面；
 · 判据面＝五族关键词表可现读，且⛔ 靠族序决定归属（把族序打乱⇒判定逐条不变）；
   报告点名的两枚方位词（往后／往前）按 T14 **摘出表**——摘的是关键词，⛔ 改判据形制；
 · 形状尺两侧校准：CLEAN（整段相等形）⇒ 0 命中，SYNTH（旧 `k in t` 形）⇒ 恰好一枚。

⛔ 本格范围：`:159-175` 那条「播放中优先于唤醒词／角色模式」的**支路顺序**——名册写死它不动，
本档只钉「收紧后它仍在那儿」（test_15），⛔ 借本格顺手调优先级。
"""
import ast
import pathlib
import random
import re
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import butler.core.xiaomi_ear as ear  # noqa: E402

SRC = (REPO / "butler" / "core" / "xiaomi_ear.py").read_text(encoding="utf-8")
CELLS = 15

FAMILIES = ("stop", "pause", "resume", "next", "prev")


def cmd(text: str):
    """跑**生产**匹配器（⛔ 在本档复制一份算法）。"""
    return ear.XiaomiEar.__new__(ear.XiaomiEar)._match_audiobook_cmd(text)


# ---- 形状尺 ----

SUBSTRING = re.compile(r'\bin\s+t\b')


def body_of(src: str, name: str) -> str:
    tree = ast.parse(src)
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        for fn in cls.body:
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == name:
                return ast.get_source_segment(src, fn) or ""
    return ""


def shape_hits(src: str) -> list[str]:
    body = body_of(src, "_match_audiobook_cmd")
    if not body:
        return ["matcher-body-not-found"]
    hits = []
    if SUBSTRING.search(body):
        hits.append("audiobook-substring-match-still-present")
    return hits


CLEAN = '''
class XiaomiEar:
    def _match_audiobook_cmd(self, text):
        for seg in _AB_SEG.split(text):
            for action, words in AUDIOBOOK_COMMANDS:
                if seg in words:
                    return action
        return None
'''

SYNTH_LEGACY = '''
class XiaomiEar:
    def _match_audiobook_cmd(self, text):
        t = text.strip()
        if any(k in t for k in ["停止播放", "闭嘴"]):
            return "stop"
        return None
'''

SYNTH_NO_BODY = '''
class XiaomiEar:
    def other_method(self):
        return 1
'''


class TestBatch194AudiobookMatcher(unittest.TestCase):
    # 一、五族的整句命令⛔ 被收窄（报告 §二·A9 复现表的「应仍命中」侧）
    def test_01_five_families_whole_sentence_still_control(self):
        for text, want in [("停止播放", "stop"), ("闭嘴", "stop"), ("别念了", "stop"),
                           ("暂停", "pause"), ("等一下", "pause"), ("先别播", "pause"),
                           ("继续", "resume"), ("接着听", "resume"),
                           ("下一章", "next"), ("跳过", "next"),
                           ("上一章", "prev"), ("重听", "prev")]:
            with self.subTest(text=text):
                self.assertEqual(cmd(text), want)

    # 二、报告点名的三条病灶（T13 与 §二·A9 复现表）
    def test_02_t13_device_sentence_is_none(self):
        for text in ["继续开灯", "把空调关掉", "关掉", "再放一遍", "暂停一下我去拿个东西"]:
            with self.subTest(text=text):
                self.assertIsNone(cmd(text))

    def test_03_t14_bare_directional_is_none(self):
        for text in ["往后", "往前", "往后翻", "往前翻", "回到上一个页面"]:
            with self.subTest(text=text):
                self.assertIsNone(cmd(text))

    def test_04_keyword_inside_a_longer_sentence_is_none(self):
        for text in ["我先洗个手等一下", "继续讲个故事", "暂停的音乐有点吵", "你说的继续是什么意思"]:
            with self.subTest(text=text):
                self.assertIsNone(cmd(text))

    # 三、⛔ 被顺手收窄的三面：呼格段、尾随语气词、纯标点
    def test_05_vocative_segment_still_controls(self):
        for text, want in [("豆包，暂停", "pause"), ("小凯，闭嘴", "stop"),
                           ("管家。下一章", "next"), ("等一下，我来了", "pause")]:
            with self.subTest(text=text):
                self.assertEqual(cmd(text), want)

    def test_06_trailing_modal_particle_stripped(self):
        for text, want in [("暂停吧", "pause"), ("闭嘴啊", "stop"), ("继续呀", "resume"),
                           ("下一章呢", "next")]:
            with self.subTest(text=text):
                self.assertEqual(cmd(text), want)

    def test_07_punctuation_and_whitespace_wrapped(self):
        for text, want in [("继续！", "resume"), ("。暂停。", "pause"), ("  闭嘴  ", "stop"),
                           ("暂停？", "pause")]:
            with self.subTest(text=text):
                self.assertEqual(cmd(text), want)

    def test_08_empty_and_plain_text(self):
        self.assertIsNone(cmd(""))
        self.assertIsNone(cmd("   "))
        self.assertIsNone(cmd("今天天气怎么样"))

    # 四、判据面：族序⛔ 决定归属；表可现读；两枚方位词按 T14 摘出
    def test_09_family_order_does_not_decide_the_verdict(self):
        table = list(ear.AUDIOBOOK_COMMANDS)
        self.assertEqual(sorted(a for a, _ in table), sorted(FAMILIES))
        texts = ["暂停", "继续", "闭嘴", "下一章", "上一章", "豆包，暂停", "继续开灯", "往后"]
        baseline = {t: cmd(t) for t in texts}
        for seed in range(6):
            shuffled = table[:]
            random.Random(seed).shuffle(shuffled)
            ear.AUDIOBOOK_COMMANDS = tuple(shuffled)
            try:
                for t in texts:
                    with self.subTest(seed=seed, text=t):
                        self.assertEqual(cmd(t), baseline[t])
            finally:
                ear.AUDIOBOOK_COMMANDS = tuple(table)

    def test_10_reported_directional_words_are_out_of_the_table(self):
        words = {w for _, group in ear.AUDIOBOOK_COMMANDS for w in group}
        for dead in ["往后", "往前"]:
            self.assertNotIn(dead, words, f"{dead} 仍在表里⇒裸方位词还是会吃掉普通话")
        for kept in ["下一章", "上一章", "暂停", "继续", "停止播放"]:
            self.assertIn(kept, words)

    def test_11_table_is_the_only_source_of_keywords(self):
        """⛔ 再出现「表外硬编码」的 `k in t` 支路：五族关键词全在 AUDIOBOOK_COMMANDS 里。"""
        for action, group in ear.AUDIOBOOK_COMMANDS:
            self.assertTrue(group, f"{action} 族空表")
            self.assertTrue(all(w and w.strip() == w for w in group),
                            f"{action} 族有空白/带空格的关键词：{group}")

    # 五、形状尺与分母守卫
    def test_12_production_source_clean(self):
        self.assertEqual(shape_hits(SRC), [])

    def test_13_clean_synthetic_passes(self):
        self.assertEqual(shape_hits(CLEAN), [])

    def test_14_synth_legacy_is_bitten(self):
        self.assertEqual(shape_hits(SYNTH_LEGACY),
                         ["audiobook-substring-match-still-present"])
        self.assertEqual(shape_hits(SYNTH_NO_BODY), ["matcher-body-not-found"])

    def test_15_playback_branch_order_is_untouched(self):
        """名册写死：播放中那一支仍排在唤醒词与角色模式**之前**，本格⛔ 调优先级。

        三枚探针先 grep 过段名本身才写进来（`ab.state.playing`／`match_role_by_text`／
        `# === 角色模式` 在 `xiaomi_ear.py` 现读各 1／1／1 命中）。
        """
        src = SRC
        ab = src.index("ab.state.playing")
        wake = src.index("match_role_by_text")
        mode = src.index("# === 角色模式")
        self.assertLess(ab, wake, "电子书支路被挪到唤醒词之后⇒支路顺序被动了（本格范围外）")
        self.assertLess(ab, mode, "电子书支路被挪到角色模式之后⇒支路顺序被动了（本格范围外）")
        self.assertGreaterEqual(src.count("_match_audiobook_cmd("), 2,
                                "调用点或定义消失⇒匹配器成了没人调的假绿")


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    n = loader.countTests(suite)
    if n != CELLS:
        raise SystemExit("CELLS 对账失败：名册 %d ≠ 本档实收 %d（漏收＝discover 收了不跑）" % (CELLS, n))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
