"""批222 第二遍：给批221 那把 AST 闸补上「盲区」腿，并把全族面做成棘轮。

批221 的闸（第一腿）判据是：`int|float|bool` 的首实参子树里有 `.get()`，**且接收者根名 ∈
`request|body|d|payload`**。这条白名单是当初为了「只圈 HTTP 面」定的，它带来的后果在
`workorders/readings/1015/b464_cast_denominator.txt` 里现读过：

 - 全仓 `int/float` × 任意接收者 `.get()` = 59 枚，⛔ 一枚都在四名册里 ⇒ 闸报 0 命中；
 - 另有 48 枚 `int(value)`／`float(conf)` 形制的裸名铸形，闸根本不看。

⇒ 「族闭」⛔ 能由第一腿的 0 命中宣布。本档第二腿把两种盲区形制都点名，并按现状冻成**棘轮**
（⛔ 零容忍：107 枚面一次修不完，但⛔ 再长）。`bool()` 不在棘轮里：它对任何值都有定义⇒⛔ 抛，
属报告 §三③「显式 null 读成 False」语义族，由 `soft_flag`／`to_bool` 的逐格行为尺管。

四把校准尺（判例：形状尺⛔ 只跑真文件）：
 - 盲区合成源码 ⇒ 第二腿恰好点名 2 枚；
 - 助手形制合成源码 ⇒ 第二腿 0 命中（⛔ 恒红）；
 - 棘轮纯函数：新增文件／同文件变多 ⇒ 红，变少 ⇒ 红并要下调基线；
 - 第一腿的原有三格（咬合／放行／临时树）逐字不变。
"""
from __future__ import annotations

import ast
import io
import pathlib
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
PKG = REPO / "butler"

CASTS = ("int", "float", "bool")
NUM_CASTS = ("int", "float")
ROOTS = ("request", "body", "d", "payload")
# 助手本体里 `int(value)`／`float(value)`／`bool(value)` 是转换的实现，⛔ 算盲区面。
WIDEN_EXCLUDE = {"param.py"}


def _root_name(node):
    while isinstance(node, ast.Attribute):
        node = node.value
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _cast_nodes(tree, casts):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id in casts):
            continue
        if not node.args:
            continue
        yield node


def _get_calls(arg):
    return [s for s in ast.walk(arg)
            if isinstance(s, ast.Call) and isinstance(s.func, ast.Attribute)
            and s.func.attr == "get"]


def bare_casts(src: str):
    """第一腿（批221 原样）：四名册接收者的 `.get()` 喂进铸形——零容忍。"""
    hits = []
    for node in _cast_nodes(ast.parse(src), CASTS):
        if any(_root_name(s.func.value) in ROOTS for s in _get_calls(node.args[0])):
            hits.append(node.lineno)
    return sorted(hits)


def widened_casts(src: str):
    """第二腿：`int`／`float` 的首实参是任意接收者的 `.get()`，或是一枚裸名。"""
    hits = []
    for node in _cast_nodes(ast.parse(src), NUM_CASTS):
        arg = node.args[0]
        if _get_calls(arg) or isinstance(arg, ast.Name):
            hits.append(node.lineno)
    return sorted(hits)


def _rel(path: pathlib.Path, root: pathlib.Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def _py_files(root: pathlib.Path):
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def scan_tree(root: pathlib.Path):
    out = {}
    for path in _py_files(root):
        src = path.read_text(encoding="utf-8", errors="replace")
        try:
            hits = bare_casts(src)
        except SyntaxError:
            continue
        if hits:
            out[_rel(path, root)] = hits
    return out


def scan_widened(root: pathlib.Path, exclude=frozenset()):
    """返回 {相对路径: 枚数}——只数，⛔ 存行号（行号会随改动漂移，枚数才配当基线）。"""
    out = {}
    for path in _py_files(root):
        rel = _rel(path, root)
        if rel in exclude:
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        try:
            hits = widened_casts(src)
        except SyntaxError:
            continue
        if hits:
            out[rel] = len(hits)
    return out


def ratchet_failures(found: dict, baseline: dict):
    """棘轮判定：新文件／变多／变少都算不合格，只有「与基线逐档相等」才放行。"""
    problems = []
    for rel in sorted(set(found) | set(baseline)):
        now, was = found.get(rel, 0), baseline.get(rel, 0)
        if now > was:
            problems.append("%s 基线=%d 现=%d ⇒ 新增盲区铸形" % (rel, was, now))
        elif now < was:
            problems.append("%s 基线=%d 现=%d ⇒ 已修好，把基线下调到 %d" % (rel, was, now, now))
    return problems


# ── 现读基线（`workorders/tmp/b470_gen_widen_baseline.py` 生成，⛔ 手敲）──
# 生成时刻的码面：修完 DB109（locator）＋ DB126（doubao_webhook）之后。
WIDEN_BASELINE = {
    "af_bridge.py": 1,
    "api/decision_routes.py": 1,
    "api/deps.py": 2,
    "api/memory_routes.py": 2,
    "api/trigger_routes.py": 1,
    "bus/mqtt_client.py": 1,
    "config.py": 3,
    "core/agent.py": 1,
    "core/cron_task.py": 7,
    "core/simple_rules.py": 2,
    "core/xiaomi_ear.py": 1,
    "decision/action_router.py": 1,
    "decision/engine.py": 1,
    "guard/push_guard.py": 1,
    "integrations/deskpilot.py": 9,
    "integrations/memory_agent.py": 1,
    "integrations/newapi.py": 3,
    "morning/routine.py": 2,
    "presence/sources.py": 1,
    "proactive/engine.py": 4,
    "skills/engines/_template_/engine.py": 1,
    "skills/engines/autoflow_propose/engine.py": 1,
    "skills/engines/ha_inspection/engine.py": 2,
    "skills/engines/llm_decide/ask.py": 1,
    "skills/engines/llm_decide/engine.py": 3,
    "skills/engines/llm_decide/mock.py": 3,
    "skills/engines/llm_text/engine.py": 2,
    "skills/runner.py": 1,
    "skills/schema.py": 1,
    "store/repo.py": 4,
    "store/write_failures.py": 1,
    "timeseries/anomaly.py": 1,
    "tools/desk_pilot.py": 16,
    "tools/registry.py": 2,
    "tools/schedule.py": 1,
    "tools/tv.py": 7,
    "triggers/engine.py": 1,
    "triggers/health_api.py": 1,
    "triggers/schema.py": 1,
    "tts/queue.py": 3,
}

BITE_FIXTURE = '''
async def list_rows(request, body, payload):
    limit = int(request.query_params.get("limit", 50))
    stream = bool(body.get("stream"))
    conf = float(payload.get("confidence", 0.5))
    ladder = float(payload.get("confidence") or 0.0)
    return limit, stream, conf, ladder
'''

# 批221 闸的两种盲区：接收者⛔ 在四名册里／首实参是裸名。
BLIND_FIXTURE = '''
async def probe(items, value):
    best = None
    for it in items or []:
        best = (room, float(it.get("confidence") or 0.5))
    return int(value)
'''

SPARE_FIXTURE = '''
"""改前形制 `int(request.path_params.get("id", "0"))` 写在说明里不算命中。"""


async def list_rows(request, body, payload):
    # int(request.query_params.get("limit", 50))  ← 注释里的旧形制不算
    limit = query_num(request, "limit", default=50, minimum=1, maximum=500)
    stream = body_bool(body, "stream", default=False)
    conf = to_float(payload.get("confidence"), "confidence", default=0.5)
    room = str(body.get("room", ""))          # str() 不会崩，⛔ 算命中
    keys = len(payload.get("k") or [])        # 非铸形函数，⛔ 算命中
    return limit, stream, conf, room, keys
'''


class TreeCastGateTest(unittest.TestCase):

    def test_ruler_bites_the_bare_form(self):
        self.assertEqual(bare_casts(BITE_FIXTURE), [3, 4, 5, 6],
                         "本尺恒假⇒全仓 0 命中毫无意义")

    def test_ruler_spares_the_helper_and_the_prose(self):
        self.assertEqual(bare_casts(SPARE_FIXTURE), [],
                         "助手形制或注释里的字面被读成命中⇒尺恒红")

    def test_ruler_finds_a_planted_file_in_a_temp_tree(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "m.py").write_text(BITE_FIXTURE, encoding="utf-8")
            got = scan_tree(root)
            self.assertEqual(got, {"m.py": [3, 4, 5, 6]}, got)

    def test_whole_tree_has_no_bare_cast(self):
        if not PKG.is_dir():
            self.skipTest(f"源码树不在位：{PKG}")
        got = scan_tree(PKG)
        total = sum(len(v) for v in got.values())
        detail = "\n".join(f"  {k}:{v}" for k, v in sorted(got.items()))
        self.assertEqual(got, {}, "全仓还剩 %d 枚裸铸形：\n%s" % (total, detail))

    # ── 第二腿：盲区＋棘轮 ──

    def test_widened_leg_names_the_two_blind_shapes(self):
        got = widened_casts(BLIND_FIXTURE)
        self.assertEqual(got, [5, 6],
                         "第二腿恒假⇒基线只是一个没人看的数字：%s" % got)

    def test_widened_leg_spares_the_helper_form(self):
        self.assertEqual(widened_casts(SPARE_FIXTURE), [],
                         "第二腿把助手形制读成命中⇒基线恒红⇒等于没有闸")

    def test_widened_leg_names_the_planted_blind_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "m.py").write_text(BLIND_FIXTURE, encoding="utf-8")
            self.assertEqual(scan_widened(root), {"m.py": 2},
                             "scan_widened 打不出盲区文件⇒棘轮是摆设")

    def test_ratchet_bites_growth_and_spares_equality(self):
        self.assertEqual(ratchet_failures({"m.py": 2}, {"m.py": 2}), [])
        self.assertEqual(len(ratchet_failures({"m.py": 3}, {"m.py": 2})), 1)
        self.assertEqual(len(ratchet_failures({"m.py": 1, "n.py": 4},
                                             {"m.py": 2})), 2,
                         "新增文件／变少都⛔ 报⇒基线会一路烂下去")

    def test_widened_roster_matches_the_frozen_baseline(self):
        if not PKG.is_dir():
            self.skipTest(f"源码树不在位：{PKG}")
        found = scan_widened(PKG, exclude=WIDEN_EXCLUDE)
        problems = ratchet_failures(found, WIDEN_BASELINE)
        detail = "\n".join("  " + p for p in problems)
        self.assertEqual(problems, [],
                         "盲区铸形棘轮不合格（合计 基线=%d 现=%d）：\n%s"
                         % (sum(WIDEN_BASELINE.values()), sum(found.values()), detail))

    def test_the_two_blind_sites_the_narrow_gate_missed_are_gone(self):
        """DB109／DB126 逐枚点名：第一腿的 0 命中⛔ 等于族闭，这两枚必须从第二腿名单里消失。"""
        if not PKG.is_dir():
            self.skipTest(f"源码树不在位：{PKG}")
        for rel, keyword in (("locator.py", "confidence"),
                             ("api/doubao_webhook.py", "temperature")):
            src = io.open(str(PKG / rel), encoding="utf-8", errors="replace").read()
            tree = ast.parse(src)
            casted = [n.lineno for n in _cast_nodes(tree, CASTS)
                      if keyword in ast.dump(n)]
            self.assertEqual(casted, [], "%s 还有把 %s 铸形的行：%s" % (rel, keyword, casted))


if __name__ == "__main__":
    unittest.main()
