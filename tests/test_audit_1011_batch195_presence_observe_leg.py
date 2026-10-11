"""批195：角色「在场房间」presence_rooms 越界语义落地（DCD 裁定 20261011-DB-角色在场房间越界语义）。

裁定两档：
- **Q1 乙**：判定⛔ 改（保持不拦截），把记载面收编成「只记录、不拦截」＋新增计数 `presence_observe`。
  ⛔ 不动相邻那枚 `bound_rooms` 闸（它是真拒绝，语义不同）。
- **Q2 甲**：`presence_rooms=[]` 读作全屋（装载语义不变），但⛔ 让播种字面依赖 `from_dict` 的
  `or ["*"]` 暗逻辑 ⇒ `roles/store.py` 顾安恒那枚 `[]` 改 `["*"]`，字面与装载值同读一个值。
  ⛔ 删 `from_dict` 那层 `or ["*"]`（防御性，`None`／空串都该落全屋）。

开工前复跑（裁定 §7 三腿，件 `workorders/readings/1011/b195_preflight_1011.txt`，
`stamp=2026-10-11T03:06:35Z`，HEAD `58c36f0`）：`:315-317` 三行现读＝计算并只 log、⛔ 无 return/raise；
`from_dict:60` 现读＝`list(d.get("presence_rooms", ["*"]) or ["*"])`；播种 6 枚现读
＝`["*"]`／`["厨房","客厅"]`／`["书房"]`／`["Kevin房间"]`／`["Emily房间"]`／`[]`（`:125` 顾安恒）。
⇒ 与申请件一致，本裁成立，可以落地。

判据钉**行为**（越界真的加计数、在册房间真的不加、`["*"]` 真的不加）也钉**形状**
（`on_wakeup` 里那一格⛔ 出现带值的 Return／Raise；调用点走具名方法）与**记载面**
（日志文案⛔ 拒绝式措辞）。形状尺配两侧合成校准（`test_06`：CLEAN 合成⇒0，SYNTH 合成⇒恰好 1），
⛔ 只跑真文件。两处同值口径（播种字面 vs 批181 门断言）由 `test_10` 钉住——裁定明写「两处口径必须一致」。

`butler/core/dialog.py` 与 `butler/roles/store.py` 都可 import（模块级不建库连接），⛔ import `butler/app.py`。

跑法：`python3 -B -m unittest tests.test_audit_1011_batch195_presence_observe_leg`
"""
from __future__ import annotations

import ast
import os
import pathlib
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = pathlib.Path(_HERE).parent
for _cand in (_HERE, REPO_ROOT):
    if os.path.isdir(os.path.join(_cand, "butler")):
        sys.path.insert(0, _cand)
        break

# 现网⛔ 四把键（config 有启动硬门）：只放明显是假的占位值，⛔ 抄任何真凭据。
for _k, _v in (("DOUBAO_API_KEY", "dummy1"), ("DESKPILOT_API_TOKEN", "dummy2"),
               ("TASK_REPORT_TOKEN", "dummy3"), ("BUTLER_WEB_PASSWORD", "dummy4")):
    os.environ.setdefault(_k, _v)


def _skip_if_missing(exc: Exception) -> None:
    raise unittest.SkipTest("批195 宿主侧缺依赖（%s: %s）⇒ 本档只在容器内跑"
                            % (type(exc).__name__, exc))


try:
    import butler.core.dialog as dlg
    from butler.core.dialog import DialogManager
    from butler.roles.store import DEFAULT_ROLES, Role
except Exception as _exc:  # noqa: BLE001
    _skip_if_missing(_exc)

DIALOG_PY = pathlib.Path(os.path.abspath(dlg.__file__))
B181_GATE = pathlib.Path(_HERE) / "test_audit_1011_batch181_role_roster.py"

REJECTION_WORDS = ("越界", "不允许", "只能在", "rejected", "denied", "outside", "拒绝")


def _role(role_id: str, presence_rooms: list[str]) -> Role:
    return Role(id=role_id, name=role_id, presence_rooms=list(presence_rooms))


class _Stub:
    """假 self：只带计数位，用来单独驱动 `_observe_presence`（⛔ 拖起整条 on_wakeup）。"""

    def __init__(self) -> None:
        self.presence_observe = 0


def _refs(node: ast.AST) -> set[str]:
    """条件里出现的名字：`room` 是 Name，`role.presence_rooms` 是 Attribute。

    只收 Name 会让整把尺对真码恒盲（本档第一版就是这么红的——假红方向反了）。
    """
    out = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
    out |= {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
    return out


def _presence_blockers(src_text: str) -> list[str]:
    """`on_wakeup` 里「条件引用 presence_rooms 且体内出现带值 Return／Raise」的落点。

    带值 Return 才算拦截：观测方法内部的裸 `return`（提前结束观察）⛔ 是拒绝。
    """
    tree = ast.parse(src_text)
    out: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name != "on_wakeup":
            continue
        for stmt in ast.walk(fn):
            if not isinstance(stmt, ast.If):
                continue
            if "presence_rooms" not in _refs(stmt.test):
                continue
            for body_stmt in stmt.body:
                for inner in ast.walk(body_stmt):
                    if isinstance(inner, ast.Raise) or (
                            isinstance(inner, ast.Return) and inner.value is not None):
                        out.append("%s:%d" % (fn.name, inner.lineno))
    return out


def _counter_inits(src_text: str, attr: str = "presence_observe") -> list[int]:
    """`DialogManager.__init__` 里对 `self.<attr>` 的赋值行号（Assign 与 AnnAssign 都算）。

    真码写的是 `self.presence_observe: int = 0`＝AnnAssign；只认 Assign 会把已落地的计数读成缺初始化
    （本档第一版就是这么红的）。多类文件里必须锁定到 DialogManager 那一枚 __init__。
    """
    tree = ast.parse(src_text)
    cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "DialogManager"), None)
    if cls is None:
        return []
    init = next((n for n in cls.body
                 if isinstance(n, ast.FunctionDef) and n.name == "__init__"), None)
    if init is None:
        return []
    out: list[int] = []
    for node in ast.walk(init):
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if any(isinstance(t, ast.Attribute) and t.attr == attr for t in targets):
            out.append(node.lineno)
    return out


class PresenceObserveBehavior(unittest.TestCase):
    def test_01_out_of_room_counts_and_logs_observe_only(self):
        stub = _Stub()
        with self.assertLogs("butler.dialog", level="INFO") as cap:
            DialogManager._observe_presence(stub, _role("xiaoyue", ["书房"]), "客厅")
        self.assertEqual(stub.presence_observe, 1, "越界唤醒必须 +1")
        text = " ".join(cap.output)
        self.assertIn("presence_observe", text)

    def test_02_message_carries_no_rejection_wording(self):
        stub = _Stub()
        with self.assertLogs("butler.dialog", level="INFO") as cap:
            DialogManager._observe_presence(stub, _role("xiaoyue", ["书房"]), "客厅")
        text = " ".join(cap.output)
        for word in REJECTION_WORDS:
            self.assertNotIn(word, text, "记载面仍在说拒绝：措辞 %r 出现" % word)
        self.assertIn("只记录", text, "文案必须写明只记录、不拦截")

    def test_03_in_room_never_counts(self):
        stub = _Stub()
        for room in ("书房",):
            with self.subTest(room=room):
                DialogManager._observe_presence(stub, _role("xiaoyue", ["书房"]), room)
        self.assertEqual(stub.presence_observe, 0, "在册房间不该计数")

    def test_04_star_empty_and_blank_room_never_count(self):
        stub = _Stub()
        cases = (
            (_role("butler", ["*"]), "客厅"),
            (_role("butler", ["*"]), ""),
            (_role("legacy", []), "客厅"),
            (_role("xiaoyue", ["书房"]), ""),
        )
        for role, room in cases:
            with self.subTest(role=role.id, room=room):
                DialogManager._observe_presence(stub, role, room)
        self.assertEqual(stub.presence_observe, 0)

    def test_05_out_of_room_returns_none_and_never_raises(self):
        stub = _Stub()
        self.assertIsNone(DialogManager._observe_presence(stub, _role("xiaoyue", ["书房"]), "客厅"))
        self.assertIsNone(DialogManager._observe_presence(stub, _role("luna", ["厨房", "客厅"]), "Kevin房间"))
        self.assertEqual(stub.presence_observe, 2, "两条都该只计数、⛔ 中断调用方")


class PresenceGateShape(unittest.TestCase):
    def setUp(self):
        self.src = DIALOG_PY.read_text(encoding="utf-8")

    def test_06_shape_ruler_is_calibrated_both_ways(self):
        clean = (
            "class D:\n"
            "    async def on_wakeup(self, role, room):\n"
            "        self._observe_presence(role, room)\n"
            "        return {'ok': True}\n"
        )
        dirty = (
            "class D:\n"
            "    async def on_wakeup(self, role, room):\n"
            "        if room not in role.presence_rooms:\n"
            "            return {'ok': False, 'error': 'scope_private'}\n"
        )
        self.assertEqual(_presence_blockers(clean), [], "CLEAN 合成⇒尺子不该咬（假红）")
        self.assertEqual(len(_presence_blockers(dirty)), 1, "SYNTH 合成⇒尺子必须恰好咬 1（负控）")
        # 第三格：观测方法内部的裸 return（提前结束观察）⛔ 算拦截——否则真码会被自己的早退判红
        bare = (
            "class D:\n"
            "    async def on_wakeup(self, role, room):\n"
            "        if room not in role.presence_rooms:\n"
            "            return\n"
        )
        self.assertEqual(_presence_blockers(bare), [], "裸 return 被算成拦截＝尺子过宽（假红）")

    def test_07_real_on_wakeup_has_zero_blockers(self):
        self.assertEqual(
            _presence_blockers(self.src), [],
            "裁定 Q1 乙：判定⛔ 改，越界那格出现带值 Return／Raise 就是把「只观察」拗成「拒绝」")

    def test_08_on_wakeup_delegates_to_named_observer(self):
        tree = ast.parse(self.src)
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.AsyncFunctionDef) and n.name == "on_wakeup"), None)
        self.assertIsNotNone(fn, "on_wakeup 找不着⇒本门失效")
        calls = [n for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "_observe_presence"]
        self.assertEqual(len(calls), 1, "调用点应恰好一枚（下放到具名方法，便于单独驱动）")

    def test_09_counter_initialized_in_init(self):
        real = _counter_inits(self.src)
        self.assertEqual(len(real), 1, "计数位必须在 __init__ 里初始化（⛔ 首条越界时才凭空造）")
        # 同一把尺的两侧校准：⛔ 只跑真文件（恒绿说明不了它会咬）
        with_init = (
            "class DialogManager:\n"
            "    def __init__(self):\n"
            "        self.presence_observe: int = 0\n"
        )
        without_init = (
            "class DialogManager:\n"
            "    def __init__(self):\n"
            "        self.other = 0\n"
        )
        self.assertEqual(len(_counter_inits(with_init)), 1, "AnnAssign 形态必须认得")
        self.assertEqual(_counter_inits(without_init), [], "缺初始化⇒尺子该判 0")

    def test_11_bound_rooms_gate_stays_a_real_gate(self):
        """裁定明写⛔ 动相邻那枚——它今天真拒绝，本批必须一字不改地继续真拒绝。"""
        tree = ast.parse(self.src)
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name == "on_wakeup"), None)
        self.assertIsNotNone(fn)
        hits = []
        for stmt in ast.walk(fn):
            if isinstance(stmt, ast.If) and "bound_rooms" in _refs(stmt.test):
                for inner in ast.walk(stmt):
                    if isinstance(inner, ast.Constant) and inner.value == "scope_private":
                        hits.append(inner.lineno)
        self.assertTrue(hits, "bound_rooms 的 scope_private 拒绝腿没了⇒本批越权")


class SeedDictConsistency(unittest.TestCase):
    def _seed(self, role_id: str) -> dict:
        return next((x for x in DEFAULT_ROLES if x["id"] == role_id), {})

    def test_14_seed_literal_for_gu_anheng_is_star(self):
        self.assertEqual(self._seed("gu_anheng").get("presence_rooms"), ["*"],
                         "裁定 Q2 甲＋播种订正：字面与装载值必须同读一个值")

    def test_10_batch181_gate_asserts_the_same_value(self):
        text = B181_GATE.read_text(encoding="utf-8")
        self.assertIn('d["presence_rooms"], ["*"]', text,
                      "批181 门仍断言旧字面⇒两处口径分叉（裁定 §3 验收 2）")

    def test_12_no_seed_entry_relies_on_the_flattening_leg(self):
        bad = [d["id"] for d in DEFAULT_ROLES if d.get("presence_rooms") in ([], None, "")]
        self.assertEqual(bad, [], "这些播种条目仍在依赖 from_dict 的 or ['*'] 暗逻辑")

    def test_13_from_dict_flattening_stays(self):
        """⛔ 删防御腿：`[]`／`None`／缺省都该落全屋（裁定 §2 明写不主张删）。"""
        for raw in ([], None):
            with self.subTest(raw=raw):
                self.assertEqual(Role.from_dict({"id": "x", "presence_rooms": raw}).presence_rooms,
                                 ["*"])
        self.assertEqual(Role.from_dict({"id": "x"}).presence_rooms, ["*"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
