"""批194 A1（机械半格）：唤醒词匹配改「句首/断点锚定 + 最长优先」，⛔ 整串 replace。

病灶（第二轮小爱审计 A1，Critical，`doc/审计报告/doubao-butler_小爱音箱专项审计报告_第二轮.md`
§二·A1 与 §三 P0-1；分诊见镜像台账「批193」名册 A1 行）：`match_role_by_text` 三条坏账——
 1. `if w and w in text`＝子串命中⇒「我买了个豆包」被当成叫 butler；
 2. 首个命中即 return⇒无最长优先，`wake_words` 的**列表顺序**决定谁截胡（`豆包` 抢在 `豆包管家` 前就洗坏）；
 3. `text.replace(w, "", 1)` 洗完全串⇒「豆包管家帮我查天气」可能剩「管家帮我查天气」，
    而「小凯，帮我写个道理」连前导逗号都剥不掉，直接喂 LLM。

本档校三腿：
 · 行为面＝报告 §三 P0-1 那四条验收用例逐字钉（T1/T2/T3/T3b），外加边界集、最长优先与顺序无关、
   禁用角色、纯唤醒词、空文本；
 · **已知残留面**＝报告 §六 第 6 条明确「⛔ 用 P0-1 交付时宣称 A1 已修好」的三例
   （凯凯很乖／小凯在不在／小艾今天生日）在锚定版下**仍然命中**⇒本档把它们钉成「仍命中」，
   作为范围边界（那是「称呼 vs 描述」语义问，走 DCD 那单），⛔ 拿它们当通过；
 · 形状尺两侧校准：CLEAN（finditer＋最长比较）⇒ 0 命中，SYNTH（旧三坏账形）⇒ 恰好两枚。

`get_runtime()` 在本档里换成假名册（角色对象只有 id/name/enabled/wake_words 四枚属性），
⛔ 读真 `data/roles/*.json`——名册内容由 `tests/test_audit_1011_batch181_role_roster.py` 那一档管，
本档只管匹配算法；但**名册里真实存在的唤醒词形态**（`豆包管家`+`豆包` 同列、`顾安恒`+`安恒` 同列）
原样搬进夹具，⛔ 自造一套。
"""
import ast
import pathlib
import re
import sys
import types
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import butler.core.dialog as dlg  # noqa: E402

SRC = (REPO / "butler" / "core" / "dialog.py").read_text(encoding="utf-8")

# 名册里真实存在的两对「长词包含短词」，顺序按报告点名的坏账形排（短词在前）
ROLES = [
    {"id": "butler", "name": "豆包管家", "wake_words": ["豆包", "豆包管家"], "enabled": True},
    {"id": "agent_kevin", "name": "小凯", "wake_words": ["小凯", "凯凯"], "enabled": True},
    {"id": "agent_emily", "name": "小艾", "wake_words": ["小艾", "艾米"], "enabled": True},
    {"id": "gu_anheng", "name": "顾安恒", "wake_words": ["安恒", "顾安恒"], "enabled": True},
    {"id": "off_role", "name": "归档角色", "wake_words": ["归档"], "enabled": False},
    {"id": "no_wake", "name": "无唤醒词", "wake_words": [], "enabled": True},
]


def make_runtime(roles=None):
    objs = [types.SimpleNamespace(**r) for r in (roles if roles is not None else ROLES)]
    return types.SimpleNamespace(roles=types.SimpleNamespace(all=lambda: objs))


def match(text, roles=None):
    """在假名册下跑**生产**匹配器（⛔ 复制一份算法进本档）。"""
    manager = dlg.DialogManager.__new__(dlg.DialogManager)
    original = dlg.get_runtime
    dlg.get_runtime = lambda *a, **k: make_runtime(roles)
    try:
        return manager.match_role_by_text(text)
    finally:
        dlg.get_runtime = original


# ---- 形状尺 ----

STALE_REPLACE = re.compile(r'replace\(\s*w\s*,\s*""\s*,\s*1\s*\)')
LONGEST_CMP = re.compile(r'len\(\s*w\s*\)\s*>\s*len\(')
ANCHOR = re.compile(r'finditer|_WAKE_BOUNDARY')


def body_of(src: str, name: str) -> str:
    tree = ast.parse(src)
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        for fn in cls.body:
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == name:
                return ast.get_source_segment(src, fn) or ""
    return ""


def shape_hits(src: str) -> list[str]:
    out = []
    body = body_of(src, "match_role_by_text")
    if not body:
        return ["matcher-body-not-found"]
    if STALE_REPLACE.search(body):
        out.append("wake-word-replace-still-present")
    if not LONGEST_CMP.search(body):
        out.append("longest-priority-missing")
    if not ANCHOR.search(body):
        out.append("no-boundary-anchor")
    return out


CLEAN = '''
class DialogManager:
    def match_role_by_text(self, text):
        for m in re.finditer(re.escape(w), t):
            if m.start() == 0 or _WAKE_BOUNDARY.fullmatch(t[m.start() - 1]):
                if best is None or len(w) > len(best[1]):
                    best = (role, w, m.start())
        return best
'''

SYNTH_LEGACY = '''
class DialogManager:
    def match_role_by_text(self, text):
        for role in rt.roles.all():
            for w in role.wake_words:
                if w and w in text:
                    cleaned = text.replace(w, "", 1).strip()
                    return role, cleaned
        return None, text
'''

SYNTH_NO_BODY = '''
class DialogManager:
    def other_method(self):
        return 1
'''


class TestBatch194WakeMatcher(unittest.TestCase):
    # 一、报告 §三 P0-1 四条验收用例（逐字）
    def test_01_t1_bought_a_doubao_is_not_an_address(self):
        role, cleaned = match("我买了个豆包")
        self.assertIsNone(role)
        self.assertEqual(cleaned, "我买了个豆包")

    def test_02_t2_open_doubao_is_not_an_address(self):
        role, cleaned = match("打开豆包")
        self.assertIsNone(role)
        self.assertEqual(cleaned, "打开豆包")

    def test_03_t3_longest_wake_word_wins(self):
        role, cleaned = match("豆包管家帮我查天气")
        self.assertIsNotNone(role)
        self.assertEqual(role.id, "butler")
        self.assertEqual(cleaned, "帮我查天气")

    def test_04_t3b_leading_comma_is_stripped(self):
        role, cleaned = match("小凯，帮我写个道理")
        self.assertEqual(role.id, "agent_kevin")
        self.assertEqual(cleaned, "帮我写个道理")

    # 二、锚定与最长优先的其余面
    def test_05_longest_priority_is_order_independent(self):
        """同一名册倒序排列⇒命中⛔ 变（旧实现靠列表顺序碰运气）。"""
        reversed_roles = [dict(r) for r in ROLES]
        for r in reversed_roles:
            r["wake_words"] = list(reversed(r["wake_words"]))
        for roles in (ROLES, reversed_roles):
            with self.subTest(order=[x["wake_words"] for x in roles][:1]):
                role, cleaned = match("顾安恒你好", roles)
                self.assertEqual(role.id, "gu_anheng")
                self.assertEqual(cleaned, "你好")

    def test_06_boundary_after_punctuation_counts_as_address(self):
        role, cleaned = match("嗯。豆包，查天气")
        self.assertEqual(role.id, "butler")
        self.assertEqual(cleaned, "查天气")

    def test_07_mid_sentence_is_not_an_address(self):
        for text in ["那小凯几点开会吗", "帮小凯拿个东西"]:
            with self.subTest(text=text):
                role, _ = match(text)
                self.assertIsNone(role)

    def test_08_bare_wake_word_keeps_the_text(self):
        """只喊了名字＝⛔ 空串喂 LLM（报告版取 `cleaned or text`）。"""
        role, cleaned = match("豆包管家")
        self.assertEqual(role.id, "butler")
        self.assertEqual(cleaned, "豆包管家")

    def test_09_empty_and_none_text(self):
        role, cleaned = match("")
        self.assertIsNone(role)
        self.assertEqual(cleaned, "")

    def test_10_disabled_role_never_matches(self):
        role, cleaned = match("归档一下")
        self.assertIsNone(role)

    def test_11_role_without_wake_words_does_not_crash(self):
        role, _ = match("随便说一句", [ROLES[5]])
        self.assertIsNone(role)

    # 三、已知残留面（⛔ 当通过，只钉「仍然命中」这一事实）
    def test_12_reported_residual_three_still_match(self):
        for text, role_id in [("凯凯很乖", "agent_kevin"),
                              ("小凯在不在", "agent_kevin"),
                              ("小艾今天生日", "agent_emily")]:
            with self.subTest(text=text):
                role, _ = match(text)
                self.assertIsNotNone(role, "报告 §六-6 的三例是「未闭合」，⛔ 钉成不命中")
                self.assertEqual(role.id, role_id)

    # 四、形状尺
    def test_13_production_source_clean(self):
        self.assertEqual(shape_hits(SRC), [])

    def test_14_clean_synthetic_passes(self):
        self.assertEqual(shape_hits(CLEAN), [])

    def test_15_synth_legacy_is_bitten(self):
        self.assertEqual(shape_hits(SYNTH_LEGACY),
                         ["wake-word-replace-still-present",
                          "longest-priority-missing",
                          "no-boundary-anchor"])

    def test_16_synth_missing_body_is_bitten(self):
        self.assertEqual(shape_hits(SYNTH_NO_BODY), ["matcher-body-not-found"])

    def test_17_ruler_finds_the_producer_call_sites(self):
        """分母守卫：1 枚定义＋3 枚生产调用点仍在（⛔ 把匹配器改成没人调＝假绿）。"""
        faces = "\n".join((REPO / "butler" / rel).read_text(encoding="utf-8")
                          for rel in ("core/dialog.py", "core/xiaomi_ear.py"))
        self.assertEqual(faces.count("match_role_by_text("), 4)


if __name__ == "__main__":
    unittest.main()
