"""批201 DB45 门：`_send_to_dp` 发送失败时 fall-through 返回 None（第三期审计报告 第十三轮 · medium）。

现读 `butler/tools/desk_pilot.py`：
  `_send_to_tp`（:403-411）三条出口齐全——空消息 return、`if ok: return ✅`、末尾 `return ❌`；
  `_send_to_dp`（:413-420）只有前两条，`if ok` 之后**没有失败腿**⇒函数签名写着 `-> str`，
  实际在发送失败时返回 None（调度层把 None 交给上层＝「什么都没发生」）。
修法＝补上那条失败腿，与 sibling 同形制。

形状尺按 **AST 归属**判（⛔ 字符串计数）：凡调用 `_deskpilot_pm_send` 的函数，
函数体最后一条顶层语句必须是一条 `return`——那一行就是「失败也要说话」的凭证。
"""
import ast
import asyncio
import pathlib
import unittest

from butler.tools import desk_pilot

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROD = ROOT / "butler" / "tools" / "desk_pilot.py"


def pm_send_legs_without_trailing_return(src: str) -> list[str]:
    """返回「调了 PM 管道，但函数体最后一条顶层语句⛔ 不是 return」的函数名。"""
    bad = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.body:
            continue
        calls_pm = any(isinstance(c, ast.Call)
                       and getattr(c.func, "id", "") == "_deskpilot_pm_send"
                       for c in ast.walk(node))
        if not calls_pm:
            continue
        if not isinstance(node.body[-1], ast.Return):
            bad.append(node.name)
    return bad


class _Stub:
    def __init__(self, ok: bool, err: str):
        self.ok = ok
        self.err = err

    def __call__(self, target, message, press_enter=True):
        async def _coro():
            return self.ok, self.err
        return _coro()


class TestBatch201Db45PmSendReturnLeg(unittest.TestCase):

    def setUp(self):
        self._orig = desk_pilot._deskpilot_pm_send
        self.addCleanup(setattr, desk_pilot, "_deskpilot_pm_send", self._orig)

    def _stub(self, ok: bool, err: str = ""):
        desk_pilot._deskpilot_pm_send = _Stub(ok, err)

    def test_01_dp_failure_returns_text_not_none(self):
        self._stub(False, "锁屏中")
        got = asyncio.run(desk_pilot._send_to_dp(None, {"message": "交接一下"}))
        self.assertIsNotNone(got, "发送失败返回 None＝上层读成『什么都没发生』")
        self.assertIsInstance(got, str)
        self.assertIn("失败", got)
        self.assertIn("锁屏中", got, "拒绝原因必须带回来，⛔ 只说「失败了」")

    def test_02_dp_success_still_reports(self):
        self._stub(True)
        got = asyncio.run(desk_pilot._send_to_dp(None, {"message": "交接一下"}))
        self.assertIn("✅", got)

    def test_03_tp_sibling_leg_unchanged(self):
        self._stub(False, "连不上")
        got = asyncio.run(desk_pilot._send_to_tp(None, {"message": "hi"}))
        self.assertIn("失败", got)

    def test_04_empty_message_still_rejected(self):
        self._stub(True)
        got = asyncio.run(desk_pilot._send_to_dp(None, {"message": "   "}))
        self.assertIn("不能为空", got)

    # ── 形状尺：本文件所有 PM 腿都要以 return 收尾 ────────
    def test_05_no_pm_leg_falls_through_to_none(self):
        src = PROD.read_text(encoding="utf-8")
        self.assertEqual(pm_send_legs_without_trailing_return(src), [],
                         "有 PM 管道腿走到底不说话（返回 None）")

    def test_06_ruler_bites_synthetic_and_passes_clean(self):
        synth_bad = ('async def _send_to_dp(agent, args):\n'
                     '    ok, err = await _deskpilot_pm_send("DP", "m")\n'
                     '    if ok:\n'
                     '        return "✅"\n')
        self.assertEqual(pm_send_legs_without_trailing_return(synth_bad), ["_send_to_dp"],
                         "尺子咬不住 fall-through⇒test_05 是摆设")
        synth_good = ('async def _send_to_dp(agent, args):\n'
                      '    ok, err = await _deskpilot_pm_send("DP", "m")\n'
                      '    if ok:\n'
                      '        return "✅"\n'
                      '    return f"❌ 失败：{err}"\n')
        self.assertEqual(pm_send_legs_without_trailing_return(synth_good), [])

    def test_07_ruler_only_watchess_pm_legs(self):
        """尺子⛔ 把所有函数都管上：不调 PM 管道的函数不该被它判。"""
        synth = 'def unrelated(x):\n    print(x)\n'
        self.assertEqual(pm_send_legs_without_trailing_return(synth), [])


if __name__ == "__main__":
    unittest.main()
