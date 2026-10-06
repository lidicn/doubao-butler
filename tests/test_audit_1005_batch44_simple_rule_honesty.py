"""审计 2026-10-05 批44 组1 验收（V50 ＋ 逐枚点开时现捞的同形哑腿）：简单规则的设备控制把 HA 的返回值丢掉，失败也照播「好的，开了」。

对应台账 §48-7 第 1 条的 V50 腿（`butler/core/dialog.py:390`）。本批点开同一段时发现**第二枚同形哑腿**：
`:393` 那条 `rt.ha` 为假的路径只打了一条 `logger.warning` 就往下走，照样播报成功文案——它不在任何报告里，
是本批现读的产物，口径随本批入台账。

真形状（`butler/integrations/ha.py:233-249`）：`call_service` 返回的是**字符串**——
成功 `ok (200)`，失败 `HA token 未配置` / `http <code>: <body>` / `error: <exc>`；它⛔ 抛异常（异常被它自己吞成字符串）。
调用点 `await` 之后把那个串**扔了** ⇒ 只有真抛异常那条路（`:394`）会改口，HA 明确回绝的那三条全被当成功播出去。

后果：用户说「开灯」，HA 因 payload 里没有实体而回绝（`data` 恒为 `{}`），管家回答「好的，开了。」
＝账实不符＋一次误命中无从发现。P0-A／批26 是同一族，本枚是这族在简单规则路径上的落点。

`data` 恒空那一格 2026-10-06 由 DCD 裁 **A′**（`E:\\NAS\\关键决策部\\decisions\\20261006-AF诊断型与DB简单规则与DPP三件-裁定.md` §二）
并已落码（批44 组5，`tests/test_audit_1006_batch44_p0g_ask_before_command.py`）⇒ 本档原先"⛔ 断 data"的冻结**撤销**，
`SuccessLeg` 现在**正向断** `data == {"entity_id": …}`。DCD 判例 #2：测试锁定的是行为快照、不是设计正确性——
上游证明设计错时，锁定测试必须允许改写。原先"⛔ 把未裁的缺陷写进期望值"那条纪律⛔ 作废，它管的是**未裁**的形状。

基底读数（写这份验收**之前**在权威树现读，⛔ 事后补）：HEAD `05ad80b`（`1791234967`），
`main...origin/main` ahead 224，两枚被测文件 CR=0（`butler/core/dialog.py` 784 行／`butler/core/simple_rules.py` 130 行），
读数件 `workorders/readings/1005_b44/b44_baseline_1005.txt`。

RED 阶段实测（`b44_red_run3_1005.txt`）：**6 条红**，比本档预设的 5 条多 1 条——多出来的那条是**本档自己的缺陷**：
成功文案我按报告散文写成 `好的，开了。`，真形是 `simple_rules.py:67-68` 的 f-string（`好的，打开了。`）。
已改成从 `_check_device_control` 现取，⛔ 再钉字面量（否则下次改文案会把一条好腿判成缺陷）。
其余 5 条改前改后都必须绿：xiaoai 静默路／异常路／调用点计数／两条自证腿——它们钉的是「⛔ 把好路改坏」与「运行时腿⛔ 读到空气」。

重造前基底（2026-10-06 改这份**之前**在权威树现读）：HEAD `139db90`／ahead 233，本档 md5 `cdc88e5fcc2942b5d3f7fc47634a4cae`／333 行／CR 0，
备份 `/tmp/b44g5_sibling_preedit_1006`（md5 同值＝还原腿在）。

连带重造（2026-10-06，A′ 落地之后）：A′ 把裸「开灯」改判成"认不出实体⇒ 只回追问、⛔ 发命令"，于是本档原来的
`RULE_TEXT = "开灯"` **再也走不到 HA**（现读：容器内 `Ran 11 tests … FAILED (failures=6, errors=1)`，
读数 `workorders/readings/1006_b44g5/b44g5_sibling_red_1006.txt`）。这⛔ 是 V50 修好了，是本档的**喂料**被 A′ 断了——
失败文案腿一旦落在"回问句"那一档，`ha.calls` 恒空，六条腿全在测空气＝假绿。改法四条：
① `RULE_TEXT` 换成带房间线索的「打开客厅的灯」（认出实体才发令，HA 返回值才有东西可丢）；
② `FakeHa` 补 `get_states`（`_resolve_entity` 靠它读状态），每腿加"解析腿真走过"的自证，⛔ 让 entity_id 凭空出现；
③ `MissingHaLeg` 的期望从 `FAIL_MARK` 改成追问句——A′ 后 HA 缺席在 `simple_rules._resolve_device_entity` 那层就转成问句，
⛔ 再走到 `dialog.py` 的 `_DEVICE_FAIL_REPLY` 分支；
④ 新增 `ScopeShiftLeg` 把"裸「开灯」⛔ 发 HA"钉进本档，防下一任把 `RULE_TEXT` 改回裸词而不自知。
xiaoai 静默路／调用点计数／两条自证腿的**判据**⛔ 动（它们本来就是绿的）。
顺带重定位一处散文里的行号：成功文案的 f-string 现在 `simple_rules.py:109`（10-05 写本档时在 `:67-68`，A′ 那次改动把它下移了）。

它测不到什么（先在这里认，别等收窗再补）：
- `rt.ha` 是假件 ⇒ 本档⛔ 声称真 HA 在真实网络下的形状；假件喂的四个串是 `ha.py` 里的 return 字面量，
  `FakeShapeIsRealLeg` 用源码逐字对账，但**没跑过真 HA**。
- 语气闸（「别开灯」「我是不是忘关客厅的灯了」⛔ 当命令）与 entity_id 解析得对不对都在批44 组5 那 16 条腿里，本档⛔ 重复判——
  本档只管"走到 HA 之后⛔ 把返回值丢掉"这一族；`ScopeShiftLeg` 只钉"喂料还接得上 HA"这一件事。
- 生效要那次授权 `docker restart`：`/app/butler` 是 rw bind mount，落盘≠加载。
- 宿主若无 `butler.config`／`httpx` 等依赖，运行时腿整档跳过（跳过⛔ 计入通过；容器内跑才是全量）。
"""
from __future__ import annotations

import ast
import asyncio
import os
import sys
import types
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (_HERE, os.path.dirname(_HERE)):
    if os.path.isdir(os.path.join(_cand, "butler")):
        sys.path.insert(0, _cand)
        break

def _find_root():
    """源码腿的扫描根：本机＝仓库根，容器内＝/app（/app/butler 是 rw bind mount）。"""
    for cand in (_HERE, os.path.dirname(_HERE), "/app", os.getcwd()):
        if os.path.isfile(os.path.join(cand, "butler", "integrations", "ha.py")):
            return cand
    raise unittest.SkipTest("找不到含 butler/integrations/ha.py 的根⇒ 源码腿无物可读")


ROOT = _find_root()
HA_SOURCE = os.path.join(ROOT, "butler", "integrations", "ha.py")
DIALOG_SOURCE = os.path.join(ROOT, "butler", "core", "dialog.py")

FAIL_MARK = "出了点问题"
ASK_MARK = "哪个房间"
SUCCESS_MARK = "好的"
# A′ 连带：喂**带房间线索**的短语——认出实体才发令，HA 的返回值才有东西可丢（裸「开灯」现在停在追问那一档）。
RULE_TEXT = "打开客厅的灯"
BARE_TEXT = "开灯"
RESOLVED_ENTITY = "light.ke_ting"


def _rule_reply():
    """成功文案从规则本体取（`simple_rules.py:109` 的 f-string），⛔ 在本档写死字面量。"""
    from butler.core.simple_rules import _check_device_control
    hit = _check_device_control(RULE_TEXT)
    if hit is None:
        raise unittest.SkipTest("「%s」不再命中设备控制规则⇒ 本档判据要重看" % RULE_TEXT)
    return hit[0]

_OK = "ok (200)"
_HTTP500 = "http 500: Failed to call service light/turn_on. entity_id is a required..."
_NO_TOKEN = "HA token 未配置"
_ERR = "error: all attempts failed"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _import_dialog():
    try:
        import butler.core.dialog as dlg
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest("批44 宿主侧缺依赖（%s）⇒ 本档只在容器内跑" % type(exc).__name__)
    return dlg


def _state(eid, name):
    return {"entity_id": eid, "attributes": {"friendly_name": name}}


# 喂给正路匹配器 `_resolve_entity` 的最小状态表；"客厅"必须命中 light.ke_ting（假件⛔ 证明真 HA 的实体命名）
STATES = [_state("light.ke_ting", "客厅灯"), _state("light.woshi", "卧室灯")]


class FakeHa:
    """A′ 后本档的假件要两条腿：`get_states`（认实体）＋ `call_service`（V50 判的那次调用）。"""

    def __init__(self, result=None, raises=None, states=None):
        self.result = result
        self.raises = raises
        self.states = list(STATES if states is None else states)
        self.calls = []
        self.get_states_calls = 0

    async def get_states(self):
        self.get_states_calls += 1
        return self.states

    async def call_service(self, domain, service, data=None):
        self.calls.append((domain, service, dict(data or {})))
        if self.raises is not None:
            raise self.raises
        return self.result


class FakeState:
    def __init__(self):
        self.states = []

    def set_state(self, state):
        self.states.append(state)


class Harness(unittest.TestCase):
    """把 on_wakeup 走到简单规则分支所需的最小面钉住：⛔ 起 DB、⛔ 起 LLM、⛔ 真发声。"""

    def setUp(self):
        self.dlg = _import_dialog()
        self.saved_runtime = self.dlg.get_runtime
        role = types.SimpleNamespace(
            id="butler", name="豆包管家", enabled=True, scope="shared",
            bound_rooms=[], presence_rooms=["*"], member="家人", system="",
        )
        self.role = role
        self.spoken = []

        async def _speak_as_role(r, reply, room, member, source_device=""):
            self.spoken.append(reply)
            return reply

        async def _speak_transition(fn):
            await fn()
            return list(self.spoken)

        obj = self.dlg.DialogManager.__new__(self.dlg.DialogManager)
        obj._echo_until = 0.0
        obj._pending_skill_desc = {}
        obj.state = FakeState()
        obj.speak_as_role = _speak_as_role
        obj._speak_transition = _speak_transition
        obj._spawn_idle = lambda: None
        self.d = obj

        from butler.skills.engines.llm_decide.ask import pending_ask_manager as pam
        self.pam = pam
        self.saved_check = pam.check
        pam.check = lambda room: None
        self.addCleanup(lambda: setattr(pam, "check", self.saved_check))
        self.addCleanup(lambda: setattr(self.dlg, "get_runtime", self.saved_runtime))

    def _wire(self, ha):
        rt = types.SimpleNamespace(roles={"butler": self.role}, skill_creator=None, ha=ha)
        self.dlg.get_runtime = lambda: rt
        return rt

    def _wakeup(self, message=RULE_TEXT, source="active"):
        return _run(self.d.on_wakeup("butler", "客厅", message, member="家人", source=source))


class FailureResultLeg(Harness):
    """真缺陷腿：HA 回绝的三种返回串都必须改口。"""

    def _assert_changed(self, result):
        ha = FakeHa(result=result)
        self._wire(ha)
        out = self._wakeup()
        self.assertEqual(1, len(ha.calls), "HA 调用腿没走到，本条读数无意义")
        self.assertGreaterEqual(ha.get_states_calls, 1, "解析腿没走⇒ entity_id 是凭空出现的")
        self.assertEqual({"entity_id": RESOLVED_ENTITY}, ha.calls[0][2],
                         "发给 HA 的 payload ⛔ 点名设备＝A′ 被回退，本条读的就不是 V50 那一格了")
        self.assertNotEqual(_rule_reply(), out["reply"], "HA 回绝仍原样播规则的确认文案")
        self.assertIn(FAIL_MARK, out["reply"], "HA 回绝仍播成功文案：%r" % out["reply"])
        self.assertEqual([out["reply"]], self.spoken, "播报文案与返回值不一致（说的和写的两样）")
        return out

    def test_http_500_changes_the_reply(self):
        self._assert_changed(_HTTP500)

    def test_missing_token_changes_the_reply(self):
        self._assert_changed(_NO_TOKEN)

    def test_error_string_changes_the_reply(self):
        self._assert_changed(_ERR)


class MissingHaLeg(Harness):
    """同形哑腿（本批现捞）：`rt.ha` 为假时⛔ 报成功。

    A′ 后这格的**形状变了**：HA 句柄缺失在 `simple_rules._resolve_device_entity` 那层就判成"认不出"，
    于是回的是追问句（`ASK_MARK`），⛔ 再走到 `dialog.py` 里 `_DEVICE_FAIL_REPLY` 那条（组1 修的 `else` 分支现在是防御腿）。
    所以本格的断言⛔ 钉那句文案——钉的是「⛔ 把没做成说成做成了」这条属性：两种形状（追问／失败）都算合格，
    播规则确认文案才算缺陷。A′ 那个形状本身由 `test_audit_1006_batch44_p0g_ask_before_command.py` 那 16 条腿钉。
    """

    def test_ha_absent_does_not_claim_success(self):
        self._wire(None)
        out = self._wakeup()
        self.assertTrue(out.get("simple_rule"), "没走简单规则分支⇒ 本条读数无意义：%s" % out)
        self.assertNotEqual(_rule_reply(), out["reply"], "HA 缺席却播规则的确认文案：%r" % out["reply"])
        self.assertNotIn(SUCCESS_MARK, out["reply"], "HA 缺席却报成功：%r" % out["reply"])
        self.assertEqual([out["reply"]], self.spoken)


class ErrorLeg(Harness):
    """改前就绿、改后必须仍绿：真抛异常那条路本来就会改口。"""

    def test_raised_exception_changes_reply(self):
        ha = FakeHa(raises=RuntimeError("boom"))
        self._wire(ha)
        out = self._wakeup()
        self.assertIn(FAIL_MARK, out["reply"])


class SuccessLeg(Harness):
    """⛔ 把好路改坏：`ok (200)` 仍回原成功文案。A′ 已裁已落⇒ 本腿**正向断** `data` 带 entity_id（原先"⛔ 断 data"的冻结撤销）。"""

    def test_ok_result_keeps_success_reply(self):
        expected = _rule_reply()
        self.assertNotIn(FAIL_MARK, expected, "成功文案里含失败标记⇒ 本档两条判据互相抵消，尺废了")
        ha = FakeHa(result=_OK)
        self._wire(ha)
        out = self._wakeup()
        self.assertEqual(expected, out["reply"])
        self.assertEqual([expected], self.spoken)
        self.assertEqual(("light", "turn_on"), ha.calls[0][:2])
        self.assertEqual(1, len(ha.calls))
        self.assertEqual({"entity_id": RESOLVED_ENTITY}, ha.calls[0][2],
                         "认出了实体却没把 entity_id 带进 payload")
        self.assertGreaterEqual(ha.get_states_calls, 1, "解析腿没走⇒ entity_id 是凭空出现的")
        self.assertNotIn(ASK_MARK, out["reply"], "认出了实体仍在问房间：%r" % out["reply"])


class ScopeShiftLeg(Harness):
    """A′ 连带的护栏（本档重造的因由钉成腿）：⛔ 下次有人把 `RULE_TEXT` 改回裸「开灯」而不自知。

    裸词那格的 A′ 判据本身在批44 组5 那 16 条腿里（已逐腿先红，`b44g5_red1_1006.txt`）；本类只钉两件事：
    ① 本档六条 HA 腿的**喂料前提**——带线索的短语必须真走到 `call_service`；② 裸「开灯」⛔ 走到 HA。
    """

    def test_hint_bearing_rule_text_still_reaches_the_ha_call(self):
        from butler.core.simple_rules import _check_device_control
        hit = _check_device_control(RULE_TEXT)
        self.assertIsNotNone(hit, "「%s」不再命中设备规则⇒ 本档判据要重看" % RULE_TEXT)
        self.assertEqual("call_service", hit[1].get("action"),
                         "「%s」没走到发令档（%s）⇒ 本档六条 HA 腿全在测空气＝假绿"
                         % (RULE_TEXT, hit[1].get("action")))

    def test_bare_phrase_no_longer_reaches_ha(self):
        ha = FakeHa(result=_OK)
        self._wire(ha)
        out = self._wakeup(message=BARE_TEXT)
        self.assertEqual([], ha.calls, "裸「开灯」仍发不点名设备的全域命令＝A′ 被回退")
        self.assertEqual(0, ha.get_states_calls, "裸命令没有设备线索，本不该去查 HA 状态")
        self.assertIn(ASK_MARK, out["reply"], "裸「开灯」没回追问：%r" % out["reply"])
        self.assertNotEqual(_rule_reply(), out["reply"], "认不出实体仍播规则的确认文案")


class XiaoaiSilentLeg(Harness):
    """⛔ 把好路改坏：小爱来源由小爱原生处理，本侧⛔ 再调 HA、⛔ 播报。"""

    def test_xiaoai_source_calls_nothing_and_stays_silent(self):
        ha = FakeHa(result=_OK)
        self._wire(ha)
        out = self._wakeup(source="xiaoai")
        self.assertEqual([], ha.calls)
        self.assertEqual([], self.spoken)
        self.assertEqual([], out["spoken"])
        self.assertTrue(out.get("silent"))


class SourceShapeLeg(unittest.TestCase):
    """结构腿：`on_wakeup` 里对 `call_service` 的 await 必须被读取（赋值或参与判断），⛔ 裸语句丢弃返回值。"""

    def _call_service_nodes(self):
        src = open(DIALOG_SOURCE, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.AsyncFunctionDef) and n.name == "on_wakeup"), None)
        self.assertIsNotNone(fn, "on_wakeup 找不到了（判据要重看）")
        parents = {}
        for node in ast.walk(fn):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        hits = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "call_service":
                hits.append((node, parents))
        return hits

    def test_there_is_exactly_one_call_service_site(self):
        hits = self._call_service_nodes()
        self.assertEqual(1, len(hits), "call_service 调用点数量变了：%s" % [h[0].lineno for h in hits])

    def test_return_value_is_bound_not_dropped(self):
        node, parents = self._call_service_nodes()[0]
        cur = node
        bound = False
        while cur in parents:
            cur = parents[cur]
            if isinstance(cur, ast.Assign):
                bound = True
                break
            if isinstance(cur, (ast.BoolOp, ast.Compare, ast.If, ast.UnaryOp, ast.Return)):
                bound = True
                break
            if isinstance(cur, ast.Expr):
                break
        self.assertTrue(bound, "call_service 的返回值在 :%d 被丢弃（裸 await 语句）" % node.lineno)


class SelfProofLeg(unittest.TestCase):
    """自证：假件喂的串必须真是 ha.py 里的 return 字面量；运行时腿必须真走到简单规则分支。"""

    def test_fake_return_shapes_exist_in_ha_source(self):
        src = open(HA_SOURCE, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "call_service"), None)
        self.assertIsNotNone(fn, "ha.call_service 找不到了")
        literals = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Return):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    literals.append(node.value.value)
                elif isinstance(node.value, ast.JoinedStr):
                    literals.append("".join(
                        v.value if isinstance(v, ast.Constant) else "<>" for v in node.value.values))
        blob = " ".join(literals)
        for probe in ("ok", "http", "HA token 未配置", "error"):
            self.assertIn(probe, blob, "假件形状 %r 在 ha.call_service 的 return 里找不到：%s" % (probe, literals))

    def test_runtime_legs_actually_reach_the_simple_rule_branch(self):
        dlg = _import_dialog()
        ha = FakeHa(result=_OK)
        role = types.SimpleNamespace(
            id="butler", name="豆包管家", enabled=True, scope="shared",
            bound_rooms=[], presence_rooms=["*"], member="家人", system="")
        spoken = []

        async def _speak_as_role(r, reply, room, member, source_device=""):
            spoken.append(reply)
            return reply

        async def _speak_transition(fn):
            await fn()
            return list(spoken)

        obj = dlg.DialogManager.__new__(dlg.DialogManager)
        obj._echo_until = 0.0
        obj._pending_skill_desc = {}
        obj.state = FakeState()
        obj.speak_as_role = _speak_as_role
        obj._speak_transition = _speak_transition
        obj._spawn_idle = lambda: None
        saved = dlg.get_runtime
        from butler.skills.engines.llm_decide.ask import pending_ask_manager as pam
        saved_check = pam.check
        dlg.get_runtime = lambda: types.SimpleNamespace(roles={"butler": role}, skill_creator=None, ha=ha)
        pam.check = lambda room: None
        try:
            out = _run(obj.on_wakeup("butler", "客厅", RULE_TEXT, member="家人", source="active"))
        finally:
            dlg.get_runtime = saved
            pam.check = saved_check
        self.assertTrue(out.get("simple_rule"), "没走简单规则分支，运行时腿全是空转：%s" % out)
        self.assertEqual(1, len(ha.calls), "HA 调用腿没被调用")
        self.assertGreaterEqual(ha.get_states_calls, 1, "解析腿没走⇒ 本条只证明了播报")


if __name__ == "__main__":
    unittest.main(verbosity=2)
