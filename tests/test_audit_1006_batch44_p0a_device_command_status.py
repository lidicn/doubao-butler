#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批44 组2 验收（先红后绿）：P0-A／V45＝`butler/api/doubao_webhook.py` 的 `execute_device_command` 七处 HA POST 全丢响应。

缺陷（第十九轮 P1-48 ＋ 最终审计 P0-A，V45 那枚成立）：
  部署树现读：该函数体内 `await client.post(...)` 共 **7 处**（:324 :333 :353 :360 :381 :394 :405），
  返回值**一处都没接**。httpx 对 401/404/500 ⛔ 抛 ⇒ 函数走完 `try` 落进 `:430 else`，
  把 `{"success": True}` 记进自进化学习数据（＝失败样本当成功样本训练），手机侧也收到"一切正常"。
  同文件 `query_device_state:208` 早已是正确腿（`status_code >= 400` 就回错误文本）
  ⇒ ⛔ 这是口径分歧，是同一文件里两种写法；本批照 :208 收敛。

本批修法＝⛔ 换控制流语义：发 HA 服务那一处接住响应、非 2xx 抛 `HACommandRejected`，
  让既有的 `:412 except`（warning ＋ `success: False` 记账 ＋ 失败 bark 推送）自然接住它。

腿面（每格都要"真走到那一条 HA 调用"，⛔ 只断 mock 自己）：
  源码形状 3＝⛔ 函数体内有接不住的 post／凡向 `/api/services/` 发令的函数必须有人比过状态码
              ／我照的正确腿锚点（`:208` 那句）仍在盘上
  尺子校准 4＝同一把形状尺在四段合成源码上：裸发令要红、helper 直吃要绿、先赋值再交 helper 要绿、
              非 HA 服务（豆包 LLM 那一跳）⛔ 在册（⛔ 只跑真文件＝末两格永不现形）
  真缺陷腿 3＝light 401、climate 第一跳 401（后一跳 500 类同）、media_player 404 各记 False 且恰好一条失败推送
  半程腿 1＝第一跳 200、第二跳 500 ⇒ 整单按失败记（⛔ 部分成功写进成功账）
  ⛔ 把好路改坏 2＝全 200 时成功一条、⛔ 推送、light 只一跳且 URL/载荷逐字对上
  未在册旁路 1＝换台走 `rt.tv.zap`、⛔ 打 HA（本批⛔ 动它，防被顺手改坏）
  自证 2＝我用的状态码必须躺在 `integrations/ha.py` 自己的失败映射里；`ha.py` 把非 2xx 当真失败

跑法：运行时腿要 `starlette`（宿主侧缺，同批42 那档）⇒ 整档只在容器内跑全；
  宿主侧⛔ import 也能跑源码形状＋自证腿（读文件），运行时腿显式 SkipTest 带标记。
  容器根只读 ⇒ 只能 `python3 /dev/stdin < 本件`，此时 `__file__` 取不到源码路径 ⇒ `_find_root()` 现探。
  `DATA_DIR` 钉临时目录，⛔ 碰现网库。

⛔ 本批⛔ 动的两件事（所以本档⛔ 断它们）：
  1. `handle_webhook` 的 `{"ok": True, "device_commands": N}` 返回形状＝对 doubao2api 的契约，另开单；
  2. 换台（`rt.tv.zap`）与场景步（`_execute_step`）两条腿的记账口径。
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import types
import unittest

_MODULE = "butler/api/doubao_webhook.py"
_HA_MODULE = "butler/integrations/ha.py"


def _find_root() -> pathlib.Path:
    """容器内 `/dev/stdin` 跑法下 `__file__` ⛔ 是源码路径 ⇒ 候选根逐个现探。"""
    cands = []
    try:
        here = pathlib.Path(__file__).resolve()
        if str(here) != "/dev/stdin":
            cands += [here.parents[1], here.parents[2]]
    except OSError:
        pass
    cands += [pathlib.Path("/app"), pathlib.Path.cwd()]
    for c in cands:
        try:
            if (c / _MODULE).is_file() and (c / _HA_MODULE).is_file():
                return c
        except OSError:
            continue
    raise unittest.SkipTest("探不到 %s 的源码根（跑法或挂载面变了）⇒ 本档形状腿无读数" % _MODULE)


ROOT = _find_root()
SRC = (ROOT / _MODULE).read_text(encoding="utf-8")
HA_SRC = (ROOT / _HA_MODULE).read_text(encoding="utf-8")
_TREE = ast.parse(SRC)


def _iter_fns(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            yield node


def _fn_by_name(tree, name):
    for fn in _iter_fns(tree):
        if fn.name == name:
            return fn
    raise AssertionError("函数 %s 不在源码里（文件被搬过？）" % name)


def _parent_map(root_node):
    pm = {}
    for parent in ast.walk(root_node):
        for child in ast.iter_child_nodes(parent):
            pm.setdefault(id(child), parent)
    return pm


_STATUS_MARK = "status_code >= 400"
_HA_SERVICE_MARK = "/api/services/"


def _ha_service_posts(fn, src) -> list:
    """函数体内所有 `await <某客户端>.post(...)`，且 URL 里带 `/api/services/`。

    只按 HA 服务口径筛：同文件 `handle_webhook` 也发一跳 `post`，那是豆包 LLM，⛔ 本单管辖。
    """
    out = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call) \
                and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "post":
            if _HA_SERVICE_MARK in (ast.get_source_segment(src, node.value) or ""):
                out.append(node)
    return out


def _names_handed_to_checker(fn, checkers) -> set:
    """被真交给「比过状态码那个函数」吃掉的变量名（含嵌套在子表达式里的）。"""
    names = set()
    for call in ast.walk(fn):
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in checkers:
            for arg in list(call.args) + [kw.value for kw in call.keywords]:
                for sub in ast.walk(arg):
                    if isinstance(sub, ast.Name):
                        names.add(sub.id)
    return names


def _ha_post_offenders(src) -> tuple:
    """返回 (发令却没人判定状态码的位置列表, 函数体内真比过状态码的函数名集合)。

    放行三种姿势（⛔ 认我这一种修法）：函数体内联比 `status_code`、把 `await post(...)` 直接喂给
    比对函数、先赋值再把这个变量交给比对函数。`print(await post(...))` ⛔ 放行——
    被交给的名字必须躺在第一个返回值那个集合里，集合来自真实源码，⛔ 我口述。
    """
    tree = ast.parse(src)
    checkers = {fn.name for fn in _iter_fns(tree)
                if _STATUS_MARK in (ast.get_source_segment(src, fn) or "")}
    offenders = []
    for fn in _iter_fns(tree):
        posts = _ha_service_posts(fn, src)
        if not posts:
            continue
        inline = _STATUS_MARK in (ast.get_source_segment(src, fn) or "")
        pm = _parent_map(fn)
        handed = _names_handed_to_checker(fn, checkers)
        for node in posts:
            where = "%s:%s" % (fn.name, getattr(node, "lineno", -1))
            if inline:
                continue
            parent = pm.get(id(node))
            if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name) \
                    and parent.func.id in checkers:
                continue
            if isinstance(parent, ast.Assign):
                targets = {t.id for t in parent.targets if isinstance(t, ast.Name)}
                if targets & handed:
                    continue
            offenders.append(where)
    return sorted(offenders), checkers


class SourceShapeLeg(unittest.TestCase):
    """改前两枚必红（第 3 枚是锚点自证，改前改后都要绿）。"""

    def test_no_post_response_is_dropped_in_device_command(self):
        fn = _fn_by_name(_TREE, "execute_device_command")
        pm = _parent_map(fn)
        dropped = []
        for node in _ha_service_posts(fn, SRC):
            parent = pm.get(id(node))
            if isinstance(parent, ast.Expr):          # 裸一行 await post(...)＝把响应丢了
                dropped.append(getattr(node, "lineno", -1))
        self.assertEqual([], dropped,
                         "这些行的 HA 响应没人接（失败也会被记成成功）：%s" % dropped)

    def test_every_ha_service_poster_checks_status(self):
        offenders, checkers = _ha_post_offenders(SRC)
        self.assertIn("query_device_state", checkers,
                      "锚点漂移：同文件那把正确腿（:208）现在不比状态码了")
        self.assertEqual([], offenders,
                         "这些 HA 发令没人判定状态码（失败会被记成成功）：%s" % offenders)

    def test_convention_anchor_still_present(self):
        """我照的是同文件既有正确腿（:208），不是我自己发明的姿势。"""
        self.assertIn(_STATUS_MARK, SRC)
        q = _fn_by_name(_TREE, "query_device_state")
        self.assertIn(_STATUS_MARK, ast.get_source_segment(SRC, q) or "")


_SYN_HELPER = '''
def _check(resp):
    if resp.status_code >= 400:
        raise ValueError("rejected")


async def f():
    async with C() as client:
%s
'''

_SYN_BARE = _SYN_HELPER % '        await client.post(f"{URL}/api/services/light/turn_on", json={})'
_SYN_DIRECT = _SYN_HELPER % '        _check(await client.post(f"{URL}/api/services/light/turn_on", json={}))'
_SYN_ASSIGNED = _SYN_HELPER % (
    '        r = await client.post(f"{URL}/api/services/light/turn_on", json={})\n'
    '        _check(r)'
)
_SYN_INLINE = _SYN_HELPER % (
    '        r = await client.post(f"{URL}/api/services/light/turn_on", json={})\n'
    '        if r.status_code >= 400:\n'
    '            raise ValueError("rejected")'
)
_SYN_PRINT = _SYN_HELPER % '        print(await client.post(f"{URL}/api/services/light/turn_on", json={}))'
_SYN_SWALLOW = _SYN_HELPER % (
    '        r = await client.post(f"{URL}/api/services/light/turn_on", json={})\n'
    '        print(r)'
)
_SYN_LLM = '''
async def f():
    async with C() as client:
        await client.post("https://llm.example/v1/chat", json={})
'''


class RulerCalibrationLeg(unittest.TestCase):
    """形状尺的四段合成源码：⛔ 认修法的要放行、该咬的必须咬得住。

    为什么必须有这档：形状尺一旦写成恒绿（例如「接住响应」就算过），改前改后都绿＝那条 PASS 零信息。
    """

    def test_ruler_passes_every_accepted_shape(self):
        for name in ("_SYN_DIRECT", "_SYN_ASSIGNED", "_SYN_INLINE"):
            offenders = _ha_post_offenders(globals()[name])[0]
            self.assertEqual([], offenders, "%s 是放行姿势，却报了：%s" % (name, offenders))

    def test_ruler_bites_on_dropped_and_fake_handling(self):
        for name in ("_SYN_BARE", "_SYN_PRINT", "_SYN_SWALLOW"):
            offenders = _ha_post_offenders(globals()[name])[0]
            self.assertEqual(1, len(offenders), "%s 应当被咬住，现读数：%s" % (name, offenders))

    def test_ruler_scope_is_ha_service_only(self):
        """豆包 LLM 那一跳⛔ 本单管辖：按 URL 筛，否则 `handle_webhook:642` 会被冤枉成 P0-A。"""
        self.assertEqual([], _ha_post_offenders(_SYN_LLM)[0])
        offenders = _ha_post_offenders(SRC)[0]
        self.assertFalse([o for o in offenders if o.startswith("handle_webhook:")],
                         "非 HA 服务发令被拉进了本单：%s" % offenders)

    def test_checker_set_comes_from_real_source(self):
        """被交给的函数名必须躺在「真比过状态码」那个集合里，集合来自现读源码，⛔ 我口述。"""
        checkers = _ha_post_offenders(_SYN_HELPER % "        r = await x()")[1]
        self.assertEqual({"_check"}, checkers)


class FakeResp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.text = "stub body %s" % status_code

    def json(self):
        return {}


class FakeClient:
    """唯一外圈假件：只假在 HTTP 传输那一层，被测函数照常跑。"""

    def __init__(self, responder, holder) -> None:
        self._responder = responder
        self._holder = holder

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None, **kw):
        self._holder["posts"].append((url, json))
        return FakeResp(self._responder(url, len(self._holder["posts"])))


def _make_rt():
    class Evo:
        def __init__(self):
            self.records = []

        def record_learning_data(self, kind, payload):
            self.records.append((kind, payload))

    class Bark:
        def __init__(self):
            self.calls = []

        async def push(self, body: str = "", title: str = "", **kw):
            self.calls.append((title, body))

    class Tv:
        def __init__(self):
            self.calls = []

        async def zap(self, channel):
            self.calls.append(channel)
            return (True, "ok")

    return types.SimpleNamespace(
        # 夹具占位（⛔ 真凭据）：短值 + 独占一行，免把凭据形状写进要进 git 的文件
        settings=types.SimpleNamespace(
            ha_url="http://ha.stub:8123",
            ha_token="TOK",
        ),
        self_evolution=Evo(),
        bark=Bark(),
        tv=Tv(),
    )


try:
    import butler.api.doubao_webhook as dwb
    _IMPORT_ERR = None
except ModuleNotFoundError as _e:  # 宿主侧走这一格（缺 starlette）
    dwb = None
    _IMPORT_ERR = _e


class _RuntimeMixin:
    def setUp(self):
        if dwb is None:
            self.skipTest("批44 组2 宿主侧缺依赖（%s）⇒ 运行时腿只在容器内跑" % _IMPORT_ERR)
        self.rt = _make_rt()
        self.saved_rt = dwb.get_runtime
        self.saved_client = dwb.httpx.AsyncClient
        self.holder = {"posts": []}
        dwb.get_runtime = lambda: self.rt
        self.addCleanup(self._restore)

    def _restore(self):
        dwb.get_runtime = self.saved_rt
        dwb.httpx.AsyncClient = self.saved_client

    def _wire(self, responder):
        holder = self.holder
        dwb.httpx.AsyncClient = lambda *a, **k: FakeClient(responder, holder)

    def _run(self, params):
        return asyncio.run(dwb.execute_device_command(dict(params)))

    def _success_records(self):
        return [p for _, p in self.rt.self_evolution.records if p.get("success") is True]

    def _failure_records(self):
        return [p for _, p in self.rt.self_evolution.records if p.get("success") is False]


class FailureLeg(_RuntimeMixin, unittest.TestCase):
    """真缺陷腿：HA 回了 4xx，⛔ 再记成功账，且必须恰好一条失败推送（手机得知道设备没动）。"""

    def _assert_changed(self, params, url_part, code):
        self._wire(lambda url, n: code)
        self._run(params)
        self.assertTrue(self.holder["posts"], "HA 调用腿没走到，本条读数无意义")
        self.assertIn(url_part, self.holder["posts"][0][0])
        self.assertEqual([], self._success_records(),
                         "HA 回了 %s 仍记成功账：%s" % (code, self.rt.self_evolution.records))
        failed = self._failure_records()
        self.assertEqual(1, len(failed), "失败记账要恰好一条，现 %d 条" % len(failed))
        self.assertIn(str(code), str(self.rt.bark.calls),
                      "失败推送里要能指到那一跳的返回码")
        self.assertEqual(1, len(self.rt.bark.calls), "失败推送要恰好一条")

    def test_light_401_is_recorded_as_failure(self):
        self._assert_changed({"device": "客厅主灯", "action": "打开",
                              "entity_id": "light.philips_1"}, "light/turn_on", 401)

    def test_climate_500_is_recorded_as_failure(self):
        self._assert_changed({"device": "书房空调", "action": "打开",
                              "entity_id": "climate.lumi_1", "temperature": 26},
                             "climate/set_hvac_mode", 500)

    def test_media_player_404_is_recorded_as_failure(self):
        self._assert_changed({"device": "客厅电视", "action": "关闭",
                              "entity_id": "media_player.rmh1"}, "media_player/turn_off", 404)


class PartialFailureLeg(_RuntimeMixin, unittest.TestCase):
    """空调「打开」是两跳：第一跳 200、第二跳 500 ⇒ 整单按失败记。"""

    def test_second_hop_failure_does_not_record_success(self):
        self._wire(lambda url, n: 200 if "set_hvac_mode" in url else 500)
        self._run({"device": "书房空调", "action": "打开",
                   "entity_id": "climate.lumi_1", "temperature": 26})
        self.assertEqual(2, len(self.holder["posts"]), "两跳都要真发（不然这格没测到半程）")
        self.assertEqual([], self._success_records())
        self.assertEqual(1, len(self._failure_records()))


class SuccessLeg(_RuntimeMixin, unittest.TestCase):
    """⛔ 把好路改坏：全 200 时一切照旧——成功一条、⛔ 推送、light 只一跳且 URL/载荷逐字对上。"""

    def test_all_200_still_records_success_and_stays_quiet(self):
        self._wire(lambda url, n: 200)
        self._run({"device": "客厅主灯", "action": "打开", "entity_id": "light.philips_1"})
        self.assertEqual([("http://ha.stub:8123/api/services/light/turn_on",
                           {"entity_id": "light.philips_1"})], self.holder["posts"])
        self.assertEqual(1, len(self._success_records()))
        self.assertEqual([], self._failure_records())
        self.assertEqual([], self.rt.bark.calls, "成功不该推手机")

    def test_unknown_domain_falls_back_to_homeassistant_service(self):
        """`:320-322` 那支 `homeassistant/turn_on`：⛔ 实体前缀也照发，本批口径不变。"""
        self._wire(lambda url, n: 200)
        self._run({"device": "插座", "action": "打开", "entity_id": "switch.plug_9"})
        self.assertIn("/api/services/homeassistant/turn_on", self.holder["posts"][0][0])
        self.assertEqual(1, len(self._success_records()))


class ZapBypassLeg(_RuntimeMixin, unittest.TestCase):
    """换台那支⛔ 是 HA 腿：本批⛔ 动它，但它必须⛔ 被顺手打到 HA。"""

    def test_zap_never_posts_to_ha(self):
        self._wire(lambda url, n: 200)
        self._run({"device": "客厅电视", "action": "换台",
                   "entity_id": "media_player.rmh1", "channel": "深圳卫视"})
        self.assertEqual([], self.holder["posts"])
        self.assertEqual(["深圳卫视"], self.rt.tv.calls)
        self.assertEqual(1, len(self._success_records()), "换台那支的记账口径⛔ 改")


class SelfProofLeg(unittest.TestCase):
    """假件⛔ 是我编的形状：用到的状态码必须躺在 `ha.py` 自己的失败映射里。"""

    def test_tested_codes_exist_in_ha_source(self):
        for code in ("401", "403", "404"):
            self.assertIn(code, HA_SRC,
                          "ha.py 里没有 %s 这一档 ⇒ 我拿它当失败形状是自造的" % code)

    def test_ha_source_treats_non_2xx_as_failure(self):
        self.assertIn("HAEntityNotFound", HA_SRC)
        self.assertIn("status_code=404", HA_SRC.replace(" ", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
