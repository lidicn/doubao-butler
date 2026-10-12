"""主题白名单闸（契约表 §五 最后一行：「代码里出现的 topic 必须在本表登记，未登记的判红」）。

分母从两处权威源枚举，⛔ 手挑清单（手挑＝假绿通道）：
  1) `butler/bus/topics.py` 的全部字符串常量＋`skill_done_topic()` 的模板；
  2) AST 扫 `butler/**/*.py` 里 `publish/_publish/_publish_raw/subscribe` 的**字面量**主题参数。
分子＝契约表 §1.1/§1.2/§1.3 逐条照抄的登记集，加上一张「家庭内网主题-待 DCD 登记」名单
（DB↔TV/技能面，契约表 v1.0 只写了 ADM 三仓之间的面，压根没提 tv/*）。
名单是「待登记」不是「已豁免」：DB 已不再用的名字要删（test_04 反咬），新增的主题不在名单里直接判红。
第三张表 `FAMILY_PREFIXES`＝只用来拼主题的家族前缀（`tv/livingroom`），入表资格由 test_07 现推，
⛔ 手挑消音：前缀推不出任何真主题就不许进表。
"""
import ast
import os
import unittest
import zipfile
from pathlib import Path

import butler as _butler_pkg
import butler.bus.topics as topics

REPO = Path(_butler_pkg.__file__).resolve().parent.parent

# —— 契约表 v1.0 登记集（出处：§1.1 状态类 / §1.2 事件类 / §1.3 公共收件箱，逐条照抄）——
REGISTERED = {
    "adm/doubao-butler/status", "adm/doubao-butler/caps",
    "adm/memory-agent/status", "adm/memory-agent/caps",
    "adm/autoforge/status", "adm/autoforge/caps",
    "ma/insights", "ma/presence", "ma/device-health", "ma/alerts",
    "af/automation/fired", "af/automation/failed",
    "butler/status/state", "butler/dialog/event",
    "butler/inbox/speak", "butler/inbox/notify", "butler/inbox/tv",
}
# —— 表里没登记、但 DB 一直在用的家庭内网主题（TV 端与技能面）。
#      出处＝`E:\\NAS\\关键决策部\\inbox\\20261001-DB-重启窗判读与第二窗申请-决策申请.md` §十一-3；
#      DCD 把它们并进表之后，此名单应逐条删空。
PENDING_FAMILY_TOPICS = {
    "tv/livingroom/status", "tv/livingroom/face", "tv/livingroom/face/fused",
    "tv/livingroom/experiment", "tv/livingroom/result", "tv/livingroom/wake",
    "tv/livingroom/cmd/tts", "tv/livingroom/cmd/notify",
    "tv/livingroom/control",
    "butler/event/+", "butler/trigger/+",
    "butler/speak/out", "butler/skill/*/done",
}
# —— 家族前缀：topics.py 里只被 f-string 拼进真主题、本身从不 publish/subscribe 的串。
#      入表资格由 test_07 现场推导（必须是某条已登记/待登记主题的 `前缀 + "/"`），⛔ 当消音名单用。
FAMILY_PREFIXES = {"tv/livingroom"}

# —— 已上代码、契约表没登记、且**已投 DCD 待裁**的主题。这不是一张免罪符：
#      每条必须指着仓内一份真实存在的决策申请件（test_09 现验：文件在＋文内提到这条主题），
#      且这条主题真的还在代码里（test_10 反咬：死路径删了表就要跟着清）。
#      DCD 判「登记」→ 并进 REGISTERED；判「不许用」→ 代码里删掉，这张表随之清空。
DCD_REQUEST_DIR = "doc/决策申请"
# DCD 2026-10-02《homesdk记账与AF-DB-DPP六件-裁定》§二 A：那条 sdd 前缀的旧主题判「不许用」
# （0 订阅方死井，B 登记／C 砍功能都驳回），代码已改插 tv/livingroom/cmd/notify 活井 ⇒ 本表清空。
# 表空≠覆盖面已验：test_09/test_10 各自打印条目数，新增待裁项必须指着真实申请件。
PENDING_DCD_DECISION = {}


def topic_constants_in_code() -> dict:
    """主题串 → 出处（`topics.py 常量名` 或 `文件名:行号`）。"""
    found = {}
    for name in dir(topics):
        if name.isupper():
            val = getattr(topics, name)
            if isinstance(val, str) and "/" in val:
                found[val] = "topics.%s" % name
    found[topics.skill_done_topic("*")] = "topics.skill_done_topic()"
    for py in sorted((REPO / "butler").rglob("*.py")):
        if "__pycache__" in py.parts or py == REPO / "butler" / "bus" / "topics.py":
            continue
        tree = ast.parse(py.read_text(encoding="utf-8-sig"), filename=str(py))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("publish", "_publish", "_publish_raw", "subscribe")
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and "/" in node.args[0].value):
                found.setdefault(node.args[0].value, "%s:%d" % (py.name, node.lineno))
    return found


class TopicWhitelistTest(unittest.TestCase):
    def setUp(self):
        self.topics = topic_constants_in_code()

    def test_00_denominator_is_not_zero(self):
        self.assertGreaterEqual(len(self.topics), 20,
                                "只枚举到 %d 条主题＝这把尺的分母不对：%s" % (len(self.topics), sorted(self.topics)))

    def test_01_every_topic_is_registered_or_pending(self):
        allowed = (REGISTERED | PENDING_FAMILY_TOPICS | FAMILY_PREFIXES
                   | set(PENDING_DCD_DECISION))
        if PENDING_DCD_DECISION:
            print("[pending-dcd] 挂着待 DCD 裁的未登记主题 %d 条：%s（这条豁免由 test_09/10 监督，⛔ 当已登记）"
                  % (len(PENDING_DCD_DECISION), sorted(PENDING_DCD_DECISION)))
        unregistered = sorted(t for t in self.topics if t not in allowed)
        self.assertEqual(unregistered, [],
                         "未登记主题上了代码（§五 判红）：%s" % unregistered)

    def test_02_registered_subset_reaches_the_code(self):
        """表里点给 DB 的那几条必须真在码里出现——反向证明白名单不是摆设。"""
        for t in ("adm/doubao-butler/status", "adm/doubao-butler/caps",
                  "butler/inbox/speak", "butler/inbox/notify", "butler/inbox/tv",
                  "ma/presence", "ma/device-health", "butler/status/state", "butler/dialog/event"):
            self.assertIn(t, self.topics, "表里登记的 %s 在 DB 代码里找不到出处＝订阅/发布掉了这条腿" % t)

    def test_03_negative_control_the_ruler_bites(self):
        probe = dict(self.topics)
        fake = "zzz/never-registered-anywhere"
        probe[fake] = "负控自造"
        four = REGISTERED | PENDING_FAMILY_TOPICS | FAMILY_PREFIXES | set(PENDING_DCD_DECISION)
        self.assertNotIn(fake, four, "自造主题竟进了任一名单＝这把尺恒绿")
        self.assertTrue([x for x in probe if x not in four],
                        "这条负控恒真＝test_01 是摆设")

    def test_04_pending_list_has_no_dead_entries(self):
        for t in sorted(PENDING_FAMILY_TOPICS):
            self.assertTrue(any(x == t or (t.endswith("/*") and x.startswith(t[:-1])) or
                                (t.endswith("#") and x.startswith(t[:-1])) or
                                (t.endswith("+") and x.startswith(t[:-1]))
                                for x in self.topics),
                            "%s 在代码里已经没有对应物，请从待登记名单删掉" % t)


    def test_07_family_prefix_rule_is_derivable(self):
        """前缀豁免不是手挑：每条必须真能推出 ≥1 条已登记/待登记主题。推不出＝我在开后门。"""
        allowed = REGISTERED | PENDING_FAMILY_TOPICS
        self.assertTrue(FAMILY_PREFIXES, "前缀表空了＝豁免腿撤了，test_01 的分母要重新看")
        for p in sorted(FAMILY_PREFIXES):
            kids = sorted(t for t in allowed if t.startswith(p + "/"))
            self.assertTrue(kids, "%s 推不出任何已登记主题＝它是一条真主题，不许进前缀豁免" % p)
            print("[prefix] %s -> %d 条真主题 %s" % (p, len(kids), kids[:4]))

    def test_08_negative_control_prefix_rule_bites(self):
        """负控：`sdd/` 推不出任何真主题 ⇒ 它**没资格**进豁免表。
        若这条恒真（sdd 推得出东西）＝豁免表是摆设，test_01 的绿不算数。"""
        allowed = REGISTERED | PENDING_FAMILY_TOPICS
        self.assertFalse([t for t in allowed if t.startswith("sdd/")],
                         "『sdd/』居然能推出真主题＝这把豁免尺恒真，请重看 test_07")
        self.assertNotIn("sdd", FAMILY_PREFIXES, "sdd 进了前缀表＝把死发布洗成豁免")
        self.assertNotIn("sdd/notify", allowed | FAMILY_PREFIXES,
                         "sdd/notify 进了任一名单＝test_01 再也咬不到它")


    def test_09_pending_dcd_entries_point_at_a_real_request(self):
        print("[pending-dcd] 待裁表条目数=%d（0＝本轮无待裁项，⛔ 读成『白名单已全验』）"
              % len(PENDING_DCD_DECISION))
        """待裁豁免的每一条都得指着一份真存在的申请件，且件里确实提到这条主题。"""
        four = REGISTERED | PENDING_FAMILY_TOPICS | FAMILY_PREFIXES
        for topic, fname in sorted(PENDING_DCD_DECISION.items()):
            self.assertNotIn(topic, four, "%s 既挂着待裁又躺在别的名单里＝双面豁免" % topic)
            doc = REPO / DCD_REQUEST_DIR / fname
            if not doc.exists():
                self.skipTest("决策申请件在本运行环境不可见（%s）＝本腿未量，⛔ 记成绿" % doc)
                return
            text = doc.read_text(encoding="utf-8-sig", errors="replace")
            self.assertIn(topic, text,
                          "%s 的申请件正文里没提这条主题＝豁免挂错了单子" % topic)
            print("[pending-dcd] %s -> %s（申请件在，正文提到这条主题）" % (topic, fname))

    def test_10_pending_dcd_has_no_dead_entries(self):
        """反咬：待裁表里的主题若已经从代码里消失，这张表必须当场清掉。"""
        print("[pending-dcd] test_10 逐条检查条目数=%d" % len(PENDING_DCD_DECISION))
        for topic in sorted(PENDING_DCD_DECISION):
            self.assertIn(topic, self.topics,
                          "%s 在代码里已经没有出处＝请删掉这条待裁豁免（⛔ 拿旧债挡新读数）" % topic)


class HomesdkAlignmentTest(unittest.TestCase):
    """能读到 0.3.1 的 wheel 就跟机器化权威源对账；读不到一律 skip 并写「未量」，⛔ 当绿。"""

    DEFAULT_WHEEL = "/vol1/1000/docker/libs/homesdk/homesdk-0.3.1-py3-none-any.whl"

    def setUp(self):
        # 空串也算「没给」：docker exec -e K="" 会让 os.environ.get(k, default) 返回 ''，
        # 而 Path('') 是「存在」的当前目录——skip 跳不到，异常会把环境缺口报成契约红。
        self.wheel = (os.environ.get("HOMESDK_WHEEL") or "").strip() or self.DEFAULT_WHEEL
        if not Path(self.wheel).is_file():
            self.skipTest("HOMESDK_WHEEL 指不到 %s＝本腿未量（⛔ 记成绿）" % self.wheel)
            return
        with zipfile.ZipFile(self.wheel) as z:
            self.presence = z.read("homesdk/presence.py").decode("utf-8")

    def test_05_inbox_topics_are_exactly_three(self):
        src = self.presence
        block = src.split("INBOX_TOPICS")[1].split(")")[0]
        for t in ("butler/inbox/speak", "butler/inbox/notify", "butler/inbox/tv"):
            self.assertIn(t, block)

    def test_06_status_is_online_compares_the_literal(self):
        """卡片 2 的字面量口径不是我自己定的：0.3.1 的 `is_online()` 就比 `val == "online"`。"""
        self.assertIn('== "online"', self.presence)
        self.assertTrue(topics.adm_status_is_online("online"))


if __name__ == "__main__":
    unittest.main()
