"""批181 · P1-10「角色表 6 vs 5」收编（镜像面）。

在册名＝《缺陷汇总》P1-10：投喂面四处（`memory/feeder.py`、`api/memory_routes.py`、
`memory/extractor.py`、`store/db.py` 注释）都写着 **6** 枚角色 id，而唯一真源
`roles/store.py:DEFAULT_ROLES` 只播 **5** 枚——`gu_anheng`（顾安恒）从来没有角色文件。
设计面在 `doc/06-场景设计/顾安恒专属对话.md`：他是**豆包 APP 里的文字对话角色**
（链路＝webhook 取帧→视觉分析→推回对话），⛔ 电视输出设备、⛔ 唤醒词。

三条纪律：
1. 名单必须**从推导取**（`ALL_ROLE_IDS = tuple(DEFAULT_ROLES 的 id)`），⛔ 再抄一份字面量——
   本次的洞就是抄出来的：抄的那几份是 6，真源是 5，谁也没报错。
2. 播种要过真实 `RoleRegistry`（临时目录），⛔ 只查那个 dict 字面量——落盘与「归档不复活」
   是 `ensure_defaults` 的行为，不是常量的属性。
3. 「读不出来≠没有」：角色缺席时 `webhook:789` 裸 `role.system` 抛 AttributeError，
   被同一层的 `except Exception` 吞成一条 warning ⇒ LLM 兜底腿对这个角色**从来没通过**。
   本门用真函数（`_role_system`）钉两条腿，⛔ 拿形状尺当行为证据。

形状尺（第 3、5 格用）配两侧合成校准：应放行合成件 ⇒ 0 命中，应咬合成件 ⇒ 恰好 1 命中，
且两例都走同一个 `scan_literal_roster()` 本体（⛔ 另开算术复制品）。
"""
from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from pathlib import Path

from butler.memory import feeder
from butler.roles import store as role_store
from butler.roles.store import DEFAULT_ROLES, Role, RoleRegistry

REPO = Path(__file__).resolve().parents[1]
ROSTER_MEMBERS = ("butler", "jarvis", "caesar", "luna", "gu_anheng", "xiaoyue")


def _literal_roster_hits(text: str) -> list[str]:
    """数「把 6 枚 id 抄成字面量」的形：同一段里出现 >=4 枚 id 字面量 ⇒ 是一份抄件。"""
    quoted = [m for m in ROSTER_MEMBERS if '"%s"' % m in text or "'%s'" % m in text]
    return quoted


def scan_literal_roster(source: str) -> int:
    """尺子本体：返回「抄件」命中的**行**数（0＝没有抄件）。"""
    hits = 0
    for line in source.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if len(_literal_roster_hits(line)) >= 4:
            hits += 1
    return hits


class SeedingTests(unittest.TestCase):
    """第 1 格：真源里到底有几枚。"""

    def test_default_roles_seed_the_sixth_role(self):
        ids = {d["id"] for d in DEFAULT_ROLES}
        self.assertIn("gu_anheng", ids, f"DEFAULT_ROLES 只播了 {sorted(ids)}")

    def test_gu_anheng_is_a_text_only_role(self):
        """他是 APP 文字对话角色 ⇒ ⛔ 输出设备、⛔ 唤醒词（否则电视端会多一个应答名字）。"""
        d = next((x for x in DEFAULT_ROLES if x["id"] == "gu_anheng"), None)
        self.assertIsNotNone(d, "gu_anheng 没播种")
        self.assertEqual(d["name"], "顾安恒")
        self.assertEqual(d["output_devices"], [])
        self.assertEqual(d["wake_words"], [])
        self.assertEqual(d["presence_rooms"], [])
        self.assertFalse(d["is_primary"])
        self.assertTrue(d["enabled"])

    def test_registry_load_lands_gu_anheng_on_disk(self):
        """真 `RoleRegistry`（临时目录）：load() 之后 get() 拿得到，且盘上有那一枚文件。"""
        with tempfile.TemporaryDirectory() as tmp:
            reg = RoleRegistry(tmp)
            reg.load()
            role = reg.get("gu_anheng")
            self.assertIsNotNone(role, "load() 后仍然查无此角色")
            self.assertEqual(role.name, "顾安恒")
            f = Path(tmp) / "roles" / "gu_anheng.json"
            self.assertTrue(f.is_file(), f"盘上没有 {f}")
            self.assertEqual(json.loads(f.read_text(encoding="utf-8"))["id"], "gu_anheng")

    def test_archived_role_is_not_resurrected(self):
        """正向对照（钉「播种不是无脑塞」）：归档过的角色 ⛔ 被 ensure_defaults 复活。"""
        with tempfile.TemporaryDirectory() as tmp:
            ad = Path(tmp) / "roles" / "_archive"
            ad.mkdir(parents=True)
            (ad / "gu_anheng.json").write_text(
                json.dumps({"id": "gu_anheng", "name": "顾安恒"}), encoding="utf-8")
            reg = RoleRegistry(tmp)
            reg.load()
            self.assertIsNone(reg.get("gu_anheng"), "归档区里的角色被复活了")
            self.assertFalse((Path(tmp) / "roles" / "gu_anheng.json").exists())


class SingleSourceRosterTests(unittest.TestCase):
    """第 2 格：名单从推导取，抄件一律算缺陷。"""

    def test_all_role_ids_derives_from_default_roles(self):
        self.assertEqual(set(role_store.ALL_ROLE_IDS), {d["id"] for d in DEFAULT_ROLES})
        self.assertEqual(len(role_store.ALL_ROLE_IDS), 6)

    def test_feeder_uses_the_derived_roster(self):
        self.assertEqual(set(feeder.FEEDABLE_ROLES), set(role_store.ALL_ROLE_IDS),
                         "投喂名单与真源分叉")

    def test_no_literal_copy_of_the_roster_left(self):
        """三处引用面 ⛔ 留字面量抄件（抄一次就多一个会漂的地方）。"""
        offenders = []
        for rel in ("butler/memory/feeder.py", "butler/api/memory_routes.py",
                    "butler/memory/extractor.py"):
            text = (REPO / rel).read_text(encoding="utf-8")
            if scan_literal_roster(text):
                offenders.append(rel)
        self.assertEqual(offenders, [], f"这些地方还自己抄了一份角色名单：{offenders}")


class RulerCalibrationTests(unittest.TestCase):
    """形状尺两侧校准（⛔ 只有尺子能过 ⇒ 改尺子）。"""

    def test_ruler_passes_synthetic_clean(self):
        clean = ("from butler.roles.store import ALL_ROLE_IDS\n"
                 "ALLOWED = set(ALL_ROLE_IDS)\n"
                 "def check(name):\n"
                 "    return name in ALLOWED\n")
        self.assertEqual(scan_literal_roster(clean), 0)

    def test_ruler_bites_synthetic_literal_once(self):
        dirty = ('ALLOWED = ("butler", "jarvis", "caesar", "luna", "gu_anheng", "xiaoyue")\n')
        self.assertEqual(scan_literal_roster(dirty), 1)

    def test_ruler_bites_the_real_line_it_was_cut_for(self):
        """校准用**现读抄下来的那一行**（`memory_routes.py:329`，2026-10-10T19:15Z），
        ⛔ 拿改完之后的源码当"应咬"证据（那会让本门自相矛盾）。"""
        real_line = ('    if role not in ("butler", "jarvis", "caesar", "luna", '
                     '"gu_anheng", "xiaoyue"):\n')
        self.assertEqual(scan_literal_roster(real_line), 1)


class MissingRoleFallbackTests(unittest.TestCase):
    """第 3 格：角色缺席时 LLM 兜底腿⛔ 静默失败。"""

    def test_system_for_present_role_returns_its_prompt(self):
        from butler.api import doubao_webhook as wh
        role = Role(id="butler", name="豆包管家", system="你是管家")
        self.assertEqual(wh._role_system(role), "你是管家")

    def test_system_for_missing_role_returns_empty(self):
        """改前：`role.system` 在 role=None 时抛 AttributeError ⇒ 被 except 吞成 warning。"""
        from butler.api import doubao_webhook as wh
        self.assertEqual(wh._role_system(None), "")

    def test_llm_fallback_leg_uses_the_guarded_helper(self):
        """形状腿：`role.system` 裸取⛔ 留在 webhook 里（有校准格证明这把尺会咬）。"""
        text = (REPO / "butler/api/doubao_webhook.py").read_text(encoding="utf-8")
        bare = [ln for ln in text.splitlines()
                if "role.system" in ln and "_role_system(" not in ln
                and not ln.lstrip().startswith("#")]
        self.assertEqual(bare, [], f"还有裸 deref：{[b.strip()[:60] for b in bare]}")
