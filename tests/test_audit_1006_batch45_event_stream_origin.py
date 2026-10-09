"""审计 2026-10-06 批45-0：`event_stream` 把 HA 事件的 `origin` 当 dict 用 ⇒ 每一条被监听的 state_changed 都在落库前被丢掉。

**这枚⛔ 来自任何审计报告**（E 面 40 份归档件里 `event_stream` 与 `origin`／`time_fired`／`:223` 的联合命中 0），
是本批 `docker logs` 现读捞出的活体缺陷：近 90 分钟 **19 次** `AttributeError: 'str' object has no attribute 'get'`，
栈帧固定两行——`butler/core/event_stream.py:191 in _connect` ＋ `:223 in _handle_event`。

真形状（现网自证，⛔ 我抄文档）：HA 的 websocket 事件帧里 `event.origin` 是**字符串**（`LOCAL`/`REMOTE`/`CLOUD`），
`time_fired` 挂在事件对象**自己**那一层。而 `:223` 写的是 `event.get("origin", {}).get("time_fired", "")`
⇒ `origin` 取到字符串，紧接着 `.get` 当场抛。抛点之后的 `:239-251`（内存窗口 append、状态快照、`home_events` 落库）
**一行都跑不到**；调用点 `:191` 的 `except Exception` 又把它压成一条 throttled warning ⇒
管家眼里的"家里现在什么状态"永远停在 `:175` 那条启动快照（日志里那句 `initial snapshot: 589 entities`）。
同族前例：批44 组1 的「失败报成功」——本枚是「异常报无事」，且更隐蔽（有 warning，⛔ 有人看）。

⛔ 本档冻结的形状：`ts` 那行是**死码**（变量从未被下游读取，`:230` 用的是 `ts_val = time.time()`），
本批只按"删掉那行死码"修，⛔ 顺手改成"用 HA 的 `time_fired` 定时序"（那是另一枚设计，未裁⛔ 自造）。

基底读数（写这份验收**之前**在权威树现读）：HEAD `8c53450`，ahead 235，
`butler/core/event_stream.py` md5 `a5505389dd9599ed1e88c91fdc98b6ba`／267 行／**267 个 CRLF**（全件行尾＝CRLF；
本文件两面色一致，`b45_lineend_1006.py` 现读。首稿此处记成「CR=0」＝复述未点开，装机件的行尾闸把它咬回来了）；
该文件 mtime `2026-10-04 13:35 local`⇒ 10-04 之后没人碰过，也⛔ 在同树另一座位的在飞清单里。
全件里「对 `.get()` 结果再点 `.get()`」的链式取值现读**恰好 1 处**（就是 `:223`）⇒ 结构腿的红⛔ 被第二处无关命中污染。

RED 阶段该红哪三条：`OriginStringLeg`（真形事件必须进窗口）、`PersistLeg`（真形事件必须落 `home_events` 一行）、
`SourceShapeLeg`（`_handle_event` 里⛔ 再对 `origin` 的取值结果点 `.get`）。其余四条是⛔ 把好路改坏的护栏，
改前改后都必须绿：未变化状态仍跳过／未监听前缀仍早退／`unavailable` 仍不收／**没有 `origin` 键**时仍要收（防我把死码换成 `event["origin"]` 那种新崩点）。

形状尺另配两格合成源码校准（放行面 0 命中／咬合面恰好 1 命中）——AST 尺只跑真文件⛔ 算证过它会咬。

它测不到什么（先在这里认）：
- ⛔ 真 HA 的 websocket 形状。夹具里 `origin` 是字符串这一条由**现网异常自证**（`'str' object has no attribute`），
  ⛔ 我起了真 HA、⛔ 连了 8123。
- 生效要下一次进程启动：`/app/butler` 是 bind mount，**落盘≠加载**（现读 `StartedAt=2026-10-06T15:52:58Z` 早于本笔改盘）。
- `:191` 那条"吞异常只发 throttled warning"的口径本批⛔ 改（改了要连带一批同族腿），本档只在开头陈述它的隐蔽性代价。
- 假件跑的是 `EventStream(None)`＋隔离 `DATA_DIR`，⛔ 声称测到 `home_events` 被 MA 凌晨拉取那一条下游。
"""
from __future__ import annotations

import ast
import asyncio
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (_HERE, os.path.dirname(_HERE)):
    if os.path.isdir(os.path.join(_cand, "butler")):
        sys.path.insert(0, _cand)
        break


def _find_root():
    """源码腿的扫描根：本机＝仓库根，容器内＝/app（tests/ 不在镜像⇒必须走软链面跑）。"""
    for cand in (_HERE, os.path.dirname(_HERE), "/app", os.getcwd()):
        if os.path.isfile(os.path.join(cand, "butler", "core", "event_stream.py")):
            return cand
    raise unittest.SkipTest("找不到含 butler/core/event_stream.py 的根⇒ 源码腿无物可读")


ROOT = _find_root()
ES_SOURCE = os.path.join(ROOT, "butler", "core", "event_stream.py")

ENTITY = "light.ke_ting"


def _ha_event(entity_id=ENTITY, new="on", old="off", origin="LOCAL", with_origin=True):
    """按现网形状造一帧 HA state_changed 事件：origin 是字符串，time_fired 在事件对象自己那层。"""
    ev = {
        "event_type": "state_changed",
        "time_fired": "2026-10-06T15:52:23.123456+00:00",
        "data": {
            "entity_id": entity_id,
            "old_state": {"entity_id": entity_id, "state": old, "attributes": {}},
            "new_state": {"entity_id": entity_id, "state": new,
                          "attributes": {"friendly_name": "客厅灯"}},
        },
    }
    if with_origin:
        ev["origin"] = origin
    return ev


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _import_es():
    try:
        import butler.core.event_stream as es
    except Exception as exc:  # noqa: BLE001
        raise unittest.SkipTest("宿主面缺依赖（%s）⇒ 运行时腿只在容器内跑" % type(exc).__name__)
    return es


def _stream():
    es = _import_es()
    return es.EventStream(None)


class _Harness(unittest.TestCase):
    def setUp(self):
        self.es = _import_es()
        # ⛔ 碰活库：本批一律钉隔离 DATA_DIR（跑法件里 -e DATA_DIR=/tmp/…）；没钉就跳过落库那两条腿
        self.data_dir = os.environ.get("DATA_DIR", "")
        self.s = _stream()


class OriginStringLeg(_Harness):
    """真缺陷腿：origin 是字符串的事件必须进内存窗口（改前 AttributeError）。"""

    def test_state_change_with_str_origin_reaches_the_window(self):
        _run(self.s._handle_event(_ha_event()))
        snap = self.s.get_current_state()
        self.assertIn(ENTITY, snap, "state_changed 没落进快照（origin 是字符串那条崩了）")
        recent = self.s.get_recent(600)
        self.assertEqual(1, len(recent), "滑动窗口条数不对：%s" % recent)
        self.assertEqual("on", recent[0]["state"])


class PersistLeg(_Harness):
    """真缺陷腿：同一条事件必须落 home_events 一行（崩点在 :223，落库在 :244 之后，永远跑不到）。"""

    def setUp(self):
        super().setUp()
        if not self.data_dir.startswith("/tmp"):
            self.skipTest("DATA_DIR 未钉到隔离目录（=%r）⇒ 本腿拒跑，⛔ 写活库" % self.data_dir)
        self.es._ensure_table()

    def test_state_change_writes_a_home_events_row(self):
        # 判据＝**增量**一行：⛔ 绝对行数（同一隔离 DATA_DIR 跑第二遍时，绝对数会把绿读成红——本腿首稿就栽过一次）
        probe = "switch.b45_persist_probe"
        conn = self.es.get_conn()
        sql = "SELECT COUNT(*) FROM home_events WHERE entity_id=?"
        before = conn.execute(sql, (probe,)).fetchone()[0]
        _run(self.s._handle_event(_ha_event(entity_id=probe, new="off", old="on")))
        after = conn.execute(sql, (probe,)).fetchone()[0]
        self.assertEqual(before + 1, after, "home_events 没为这条事件增行：%d -> %d" % (before, after))
        latest = conn.execute(
            "SELECT state, entity_id FROM home_events WHERE entity_id=? ORDER BY rowid DESC LIMIT 1",
            (probe,)).fetchone()
        self.assertEqual(("off", probe), (latest[0], latest[1]), "新增那行的内容不对：%s" % (tuple(latest),))


class MissingOriginLeg(_Harness):
    """⛔ 把好路改坏：删死码之后，事件里连 origin 键都没有也必须照常收（防修成 event["origin"] 那种新崩点）。"""

    def test_event_without_origin_key_still_lands(self):
        _run(self.s._handle_event(_ha_event(new="on", old="off", with_origin=False)))
        self.assertIn(ENTITY, self.s.get_current_state(), "缺 origin 键就把事件丢了")


class UnchangedStateLeg(_Harness):
    """⛔ 把好路改坏：状态没变仍要跳过（这条过滤在崩点之前，改前就绿）。"""

    def test_same_state_does_not_enter_window(self):
        _run(self.s._handle_event(_ha_event(new="on", old="on")))
        self.assertEqual([], self.s.get_recent(600), "未变化的状态被当事件收了")


class PrefixFilterLeg(_Harness):
    """⛔ 把好路改坏：WATCH_PREFIXES 之外前缀早退；unknown/unavailable 也⛔ 收。"""

    def test_unwatched_prefix_is_dropped(self):
        _run(self.s._handle_event(_ha_event(entity_id="sensor.temperature")))
        self.assertEqual([], self.s.get_recent(600))

    def test_unavailable_state_is_dropped(self):
        _run(self.s._handle_event(_ha_event(new="unavailable", old="on")))
        self.assertEqual([], self.s.get_recent(600))


def _origin_get_chain_hits(src_text):
    """形状尺本体：返回「对 `X.get("<name>")` 的结果再点 `.get(`」的链式取值命中数。

    生产件与两格合成源码共用这一把尺（⛔ 两套判据）。
    """
    tree = ast.parse(src_text)
    hits = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"):
            continue
        inner = node.func.value
        if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) \
                and inner.func.attr == "get":
            hits.append(node.lineno)
    return hits


# 合成源码两格：放行面（origin 只取不点）必须 0 命中，咬合面（点第二层 .get）必须恰好 1 命中。
_SYN_ALLOW = '''
async def _handle_event(event):
    origin = event.get("origin", "")
    ts = event.get("time_fired", "")
    return origin, ts
'''

_SYN_BITE = '''
async def _handle_event(event):
    ts = event.get("origin", {}).get("time_fired", "")
    return ts
'''


class RulerCalibrationLeg(unittest.TestCase):
    """自证：这把形状尺先证明自己会咬、也⛔ 误咬（⛔ 只跑真文件就说"尺子判绿"）。"""

    def test_ruler_passes_the_allow_shape(self):
        self.assertEqual([], _origin_get_chain_hits(_SYN_ALLOW), "放行面被咬＝尺子见链就咬，⛔ 分得出真缺陷")

    def test_ruler_bites_the_broken_shape(self):
        self.assertEqual([3], _origin_get_chain_hits(_SYN_BITE), "咬合面没咬住⇒ 这条尺是死尺")


class SourceShapeLeg(unittest.TestCase):
    """结构腿：生产件里⛔ 再出现「对 `.get()` 的结果再点 `.get()`」这种把标量当容器的链式取值。"""

    def test_no_scalar_as_container_chain_in_source(self):
        with open(ES_SOURCE, encoding="utf-8", errors="replace") as f:
            src = f.read()
        hits = _origin_get_chain_hits(src)
        self.assertEqual([], hits, "event_stream.py 仍有链式标量取值：%s" % hits)


if __name__ == "__main__":
    unittest.main(verbosity=2)
