"""批194 A14 门：`butler/integrations/ha.py` 里 `intelligent_speaker` 尾部那 19 行不可达死码必须⛔ 在盘上（第二轮小爱审计报告 A14，批193 分诊判「在位」）。

死码形状（现读自批193 尺 3 `workorders/tools/b193_a14_ast_leg_1011.py`）：函数 :328 起，:333-340 的 `try/except` **两条分支都 return**，
其后 :342-360 还挂着**上一版 `notify_message` 的整个函数体**——开头是一句中段的裸字符串（第二个 docstring），
里面读 `message`，而本函数形参只有 `(entity_id, text, execute)`⇒ **`message` 是未绑定的自由名**（跑到了必 NameError，但它到不了）。
真身 `notify_message` 在 :362/:366 那条腿上，本批⛔ 动它。

三枚判据（一把尺三个孔，各自配合成源码两侧校准）：
 `unbound-message-load`  函数体内 load 了从未绑定的 `message`；
 `mid-body-bare-string`  docstring 之后函数体**中段**又出现裸字符串语句；
 `stale-notify-body-present` 那段死码的特征串仍在；
两枚活腿判据（证明我删的是死码⛔ 删错了活路）：
 `live-call-service-arg-missing`  `intelligent_speaker` 仍走 `call_service("xiaomi_miot", "intelligent_speaker", …execute…)`；
 `delegation-to-intelligent-speaker-missing`  `_notify_message_inner` 仍委托 `intelligent_speaker`。

跑法：「python -B -m unittest tests.test_audit_1011_batch194_a14_dead_code_leg」（一套一进程）。
"""
from __future__ import annotations

import ast
import builtins
import os
import pathlib
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (_HERE, os.path.dirname(_HERE)):
    if os.path.isdir(os.path.join(_cand, "butler")):
        sys.path.insert(0, _cand)
        break

REPO = pathlib.Path(_HERE).resolve().parent
HA_PY = REPO / "butler" / "integrations" / "ha.py"

STALE_MARK = 'data = {"message": message}'
FN = "intelligent_speaker"


def _module_level_names(tree) -> set:
    out = set(dir(builtins))
    for node in tree.body:
        targets = []
        if isinstance(node, (ast.Assign,)):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            targets = [node.name]
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            targets = [(a.asname or a.name.split(".")[0]) for a in node.names]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        out.update(targets)
    return out


def _bound_names(fn) -> set:
    """形参＋体内一切 Store/Del 目标＋嵌套 def＋except 别名＋体内 import 别名。"""
    bound = {a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs}
    if fn.args.vararg:
        bound.add(fn.args.vararg.arg)
    if fn.args.kwarg:
        bound.add(fn.args.kwarg.arg)
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            bound.update((a.asname or a.name.split(".")[0]) for a in node.names)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
    return bound


def free_loads(fn, module_names) -> list:
    """本函数里被读、却⛔ 绑定、也⛔ 是模块级名／内建名的名字＝未绑定 load（死码的形状之一）。"""
    bound = _bound_names(fn)
    out = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in bound and node.id not in module_names and node.id not in out:
                out.append(node.id)
    return out


def bare_string_lines(fn) -> list:
    """docstring **之外**的裸字符串语句行号（中段出现＝第二个函数头残留）。"""
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return [n.lineno for n in body
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)]


def find_fn(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def hits(src: str) -> list:
    """⛔ 靠行号——三枚死码判据＋两枚活腿判据，全按符号与形状取。"""
    tree = ast.parse(src)
    module_names = _module_level_names(tree)
    out = []
    fn = find_fn(tree, FN)
    if fn is None:
        return ["function-missing"]
    if "message" in free_loads(fn, module_names):
        out.append("unbound-message-load")
    if bare_string_lines(fn):
        out.append("mid-body-bare-string")
    if STALE_MARK in src:
        out.append("stale-notify-body-present")
    # 活腿：删死码⛔ 把活路一起删了（判据吃 ast.unparse 的产物⇒ 比 token⛔ 比带引号的字面量，unparse 用单引号）
    body_src = ast.unparse(fn)
    if 'self.call_service' not in body_src or 'xiaomi_miot' not in body_src \
            or 'intelligent_speaker' not in body_src:
        out.append("live-call-service-arg-missing")
    inner = find_fn(tree, "_notify_message_inner")
    if inner is None or "intelligent_speaker(" not in ast.unparse(inner):
        out.append("delegation-to-intelligent-speaker-missing")
    return out


# ---- 两侧校准（⛔ 只跑真文件：恒绿与恒红都不改变真文件读数）----
CLEAN = '''
import httpx

async def _outer():
    return "ok"


class HAClient:

    async def intelligent_speaker(self, entity_id: str, text: str, execute: bool = False) -> str:
        """小爱音箱直读。"""
        if not self.s.ha_token or not entity_id:
            return "no token/entity"
        try:
            return await self.call_service(
                "xiaomi_miot", "intelligent_speaker",
                {"entity_id": entity_id, "text": text, "execute": execute},
            )
        except Exception as e:
            return f"error: {e}"

    async def _notify_message_inner(self, message: str, entity_id=None) -> str:
        return await self.intelligent_speaker(entity_id, message, execute=True)
'''

STALE_TAIL = '''
        """通过 HA notify 平台让小爱音箱播报文本。"""
        if not self.s.ha_token:
            return "HA token 未配置"
        data = {"message": message}
        if entity_id:
            data["entity_id"] = entity_id
        try:
            return "ok"
        except Exception as e:
            return f"error: {e}"
'''

# SYNTH-①：死码整段挂回（三枚死码判据应一起咬，活腿两枚⛔ 咬）
SYNTH_DEAD = CLEAN.replace(
    '            return f"error: {e}"\n\n    async def _notify_message_inner',
    '            return f"error: {e}"\n' + STALE_TAIL + '\n    async def _notify_message_inner')
# SYNTH-②：只把活路的 call_service 换成别的＝⛔ 死码，动错了腿
SYNTH_LIVE_BROKEN = CLEAN.replace('await self.call_service(', 'await self.other_service(')
# SYNTH-③：只把委托腿拆掉
SYNTH_DELEG_BROKEN = CLEAN.replace('return await self.intelligent_speaker(', 'return await self.something_else(')

CALIB = (
    ("CLEAN-应放行", CLEAN, []),
    ("SYNTH-死码挂回", SYNTH_DEAD,
     ["unbound-message-load", "mid-body-bare-string", "stale-notify-body-present"]),
    ("SYNTH-活路 call_service 被换", SYNTH_LIVE_BROKEN, ["live-call-service-arg-missing"]),
    ("SYNTH-委托腿被拆", SYNTH_DELEG_BROKEN, ["delegation-to-intelligent-speaker-missing"]),
)


def _client_cls():
    mod = __import__("butler.integrations.ha", fromlist=["*"])
    return mod, mod.HAClient


class _FakeSettings:
    ha_token = "test-token"
    ha_url = "http://ha.example:8123"


class _NoToken:
    ha_token = ""
    ha_url = "http://ha.example:8123"


class DeadCodeShapeLeg(unittest.TestCase):
    def test_01_real_file_has_no_dead_code(self):
        self.assertEqual([], hits(HA_PY.read_text(encoding="utf-8", errors="replace")),
                         "A14 判据现读不符⇒点开 ha.py 看 intelligent_speaker 尾部")

    def test_02_free_name_message_is_not_loaded(self):
        tree = ast.parse(HA_PY.read_text(encoding="utf-8", errors="replace"))
        fn = find_fn(tree, FN)
        self.assertIsNotNone(fn, "intelligent_speaker 不在了⇒本档判据要重看")
        self.assertNotIn("message", free_loads(fn, _module_level_names(tree)),
                         "本函数形参没有 message，读它＝死码")

    def test_03_no_bare_string_after_docstring(self):
        tree = ast.parse(HA_PY.read_text(encoding="utf-8", errors="replace"))
        self.assertEqual([], bare_string_lines(find_fn(tree, FN)),
                         "函数体中段还有裸字符串＝第二个函数头没删净")


class LiveLegsStillWork(unittest.TestCase):
    """⛔ 只证「死码没了」——还得证活着的两条腿照旧走（删错了比留着死码更贵）。"""

    def setUp(self):
        self.mod, self.HAClient = _client_cls()

    def _run(self, coro):
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()   # ⛔ 关⇒ ResourceWarning 混进输出，噪音会把「输出干净」这条验收判成红

    def test_04_happy_path_calls_call_service_with_execute(self):
        seen = []

        class C(self.HAClient):
            async def call_service(self, domain, service, data):
                seen.append((domain, service, data))
                return "ok (200)"

        out = self._run(C(_FakeSettings()).intelligent_speaker("media_player.x", "你好", execute=True))
        self.assertEqual("ok (200)", out)
        self.assertEqual([("xiaomi_miot", "intelligent_speaker",
                           {"entity_id": "media_player.x", "text": "你好", "execute": True})], seen)

    def test_05_missing_token_or_entity_returns_guard_string(self):
        c = self.HAClient(_NoToken())
        self.assertEqual("no token/entity", self._run(c.intelligent_speaker("media_player.x", "你好")))
        c2 = self.HAClient(_FakeSettings())
        self.assertEqual("no token/entity", self._run(c2.intelligent_speaker("", "你好")))

    def test_06_exception_path_is_error_string_not_nameerror(self):
        class C(self.HAClient):
            async def call_service(self, domain, service, data):
                raise RuntimeError("boom")

        out = self._run(C(_FakeSettings()).intelligent_speaker("media_player.x", "你好"))
        self.assertTrue(str(out).startswith("error:"), "异常腿返回值形制变了⇒上游 startswith('ok') 判据要重看")

    def test_07_notify_message_still_delegates(self):
        calls = []

        class C(self.HAClient):
            async def intelligent_speaker(self, entity_id, text, execute=False):
                calls.append((entity_id, text, execute))
                return "ok (200)"

            def _play_lock(self, key):
                class _N:
                    async def __aenter__(self_inner):
                        return None

                    async def __aexit__(self_inner, *exc):
                        return False
                return _N()

        out = self._run(C(_FakeSettings()).notify_message("播报一句", "media_player.x"))
        self.assertEqual([("media_player.x", "播报一句", True)], calls)
        self.assertEqual("ok", out, "委托腿断了＝A14 那笔删错了对象")


class RulerCalibrationLeg(unittest.TestCase):
    def test_08_ruler_calibrated_both_ways(self):
        for label, src, want in CALIB:
            self.assertEqual(sorted(want), sorted(hits(src)), "%s 校准不符" % label)

    def test_09_claim_is_written_in_the_gate(self):
        doc = __doc__ or ""
        self.assertIn("不可达死码", doc)
        self.assertIn("真身", doc, "「notify_message 真身⛔ 动」这条边界⛔ 丢")


if __name__ == "__main__":
    unittest.main(verbosity=2)
