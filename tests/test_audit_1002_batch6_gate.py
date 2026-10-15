"""审计 2026-10-02 第六批验收：把「未 await 普查在册 30 枚」的定性落成可复跑的尺。

背景（台账 §6 那条在册名单）：批 5 之后普查尺 `probe_unawaited_coro_calls.py` 留了 30 枚候选，
它自己写明「按末段名匹配 ⇒ 同一枚名字可能有同步同名体，判定权在点开它的人」。本批就是那次点开：
AST 沿 parent 链把每一枚的**包围函数、接收者写法、同名 def 的 async/sync 分布**摆出来后，
再逐族把接收者**解析到具体的类**（`rt.tv` → `app.py:321 rt.tv = tv` → `TVClient.notify:54`），
30 枚全部落在**同步体／外部同步库**上＝这一族的候选名单到此清空。

但「我今天点完了」不是尺子。所以本验收要的是**会咬的门**：

A. **负控（先证尺会咬）**：合成包里埋 4 枚 —— ①async 未 await、②同名 sync 裸调、
   ③`await` 过的 async、④`create_task()` 包的 async。
   门必须给出**逐枚点名**的判定，且各 verdict 计数＝{ASYNC_UNAWAITED:1, SYNC_OK:1, WRAPPED:2}
   （⛔ 只要求「>0」——那等于没测）。
B. **现网点开的调用点名单**：`gate(butler/, SITES)` 必须 0 枚 ASYNC_UNAWAITED、0 枚 UNKNOWN，
   且 SYNC_OK=24 / EXTERNAL_SYNC=9、名册 33 枚（**原读数＝1009-b6 的 20/9、名册 29**：那一名册
   由原 30 减一枚 `subprocess.run` 槽——该调用已重构进 `await ensure_wake_ding`，PM 裁退役；
   sched 两枚因尺子 None-遮蔽盲区修复转 EXTERNAL_SYNC。⛔ 靠"全对"两字过关）。
   名册里的**行号**是活物：批164／165／166／167／170／179／180／186／188／189／190 各挪过若干枚的落点，
   每次重钉都在 `SITES` 旁留一行「本节口径覆盖批X 的落点登记」＋接收者现读点开。
   分布**会动**（批189／190／192 各入册新站点⇒现读 24/9）——动了它必须改本判据与台账，
   ⛔ 顺手把判据改成新读数；逐次来路见下面三条 b1XX 口径注。
   **1011-b189 更新本节口径**：批189（P1-11 关停收尾两条腿）新入册两枚 `app.py:872`／`:877`
   （逐枚 `def_at` 现读为同步体）⇒ 名册 29→31、SYNC_OK 20→22、锚点 12→14；EXTERNAL_SYNC=9 未动。
   **1011-b190 更新本节口径**：批190（合并行 #11 最后两条腿）新入册一枚 `app.py:897`
   （`rt.ha.cancel_watch_tasks()`，`def_at` 现读同步体；同批的 consumer 那枚是 **await**，⛔ 在册），
   五条旧落点整体下移 883／888／900／903／907 ⇒ 名册 31→32、SYNC_OK 22→23、锚点 14→15；
   EXTERNAL_SYNC=9 未动。
   **1011-b192 更新本节口径**：批192（合并行 27＋53 回声冷却腿收编）把 `dialog.py` 里
   「裸改别的模块类属性」那枚跨模块写改成走 API，新调用点 `core/dialog.py:669`
   （`XiaomiEar.note_speak(...)`，`def_at` 现读 `sync`@`xiaomi_ear.py:117`）⇒ 名册 32→33、
   SYNC_OK 23→24、锚点 15→16；`core/dialog.py:527`（`self.tv.notify`）在改动点**上方**，
   按符号现读点开未漂移（⛔ 靠算术），EXTERNAL_SYNC=9 未动。
   **1011-b195 更新本节口径**：批195（DCD Q1 乙 在场房间只观察）在 `DialogManager.__init__` 下方
   净插 14 行（`presence_observe` 计数＋`_observe_presence` 助手），两条 dialog.py 落点整体下移
   513→527／655→669 ⇒ 名册 33、SYNC_OK 24、锚点 16 **计数未动**（调用点本体一字未动，⛔ 靠算术，按符号现读重钉）。
   **1011-b197 更新本节口径**：批197（WebUI 自更新容器侧）在 `butler/app.py` 的 `from butler.api import (...)`
   里净插 `update_routes,`（:42）、在 `*system_routes.routes(),` 下方净插 `*update_routes.routes(),`（:249），
   两枚都在 `async def lifespan` 上方⇒app.py 11 枚落点整体下移 2，逐枚按接收者现读点开
   （504→506／581→583／794→796／809→811／817→819／883→885／888→890／897→899／900→902／903→905／907→909）
   ⇒ 名册 33、SYNC_OK 24、锚点 16 **计数未动**，判定逐枚不变。
   **1015-b221 更新本节口径**：批221（DB138／DB139 参数族收编）在 `butler/app.py` 的 lifespan 上方
   净插 6 行（档顶 `from butler.param import ParamError` 1 行＋`_param_exc` 处理器 5 行），
   `api/agent_routes.py` 档顶 import 组净插 1 行 ⇒ 名册 12 枚行号漂移（app.py 11 枚 511→517／588→594／
   804→810／819→825／827→833／893→899／898→904／907→913／910→916／913→919／917→923；
   `agent_routes.py` 159→160），逐枚按「接收者链＋叶名」现读点开（探针 `workorders/tmp/b419_b6_relocate.py`
   →`workorders/tmp/b419_b6_relocate_face.txt`；`start`／`stop` 两族同名候选 5／7 枚，⛔ 靠行号顺序猜），
   app.py 11 枚包围函数现读均为 `lifespan` ⇒ 名册 33、SYNC_OK 24、锚点 16 **计数未动**，判定逐枚不变。
   **1015-b224 更新本节口径**：批224（DB127 tri-state）在 `butler/app.py` 档顶 import 组净插 1 行
   （`from homesdk.adm.errors import ...`）＋`_consume` 的 adm status 判读段净插 6 行（diff +14/−7），
   两处都在 11 枚上方⇒整体下移；逐枚按「接收者链＋叶名」现读点开（探针 `workorders/tmp/b419_b6_relocate.py`
   →`workorders/readings/1015/b547_b6_relocate_face_1015.txt`；⛔ 靠 +7 算术、⛔ 靠行号顺序猜，
   `start` 族同叶名候选 7 枚／`stop` 5 枚／`shutdown` 2 枚）
   ⇒ 名册 33、SYNC_OK 24、锚点 16 **计数未动**，判定逐枚不变。锚点侧另有 `bus/mqtt_client.py`
   两枚 149→150／158→159（`__init__` 里那段指针注释改两行，def 本体一字未动）。
C. **锚点**：判定所依赖的 16 处 `def` 仍是**同步体**且仍在登记的行号上
   ⇒ 谁把 `TVClient.notify` 改成 `async def` 而不动 5 个调用点，这里当场红。

盲点（自报）：
 1. 解析器只认我实现的那 5 条绑定形状（构造调用 / `self.X=` / `rt.X=` / 模块级 def / 参数注解）；
    认不出一律 `UNKNOWN` 并**算红**，⛔ 静默放过。所以「红」的含义是"得再点一枚"，不是"有 bug"。
 2. `EXTERNAL_SYNC` 表里的 sqlite3／subprocess／APScheduler 三条是**外部库**判定：
    APScheduler 那两枚用容器内 `inspect.iscoroutinefunction` 现读（`start:False shutdown:False`，
    `/usr/local/lib/python3.11/site-packages/apscheduler/schedulers/asyncio.py`）；
    sqlite3/subprocess 是 stdlib 常识＋本仓 `get_conn() -> sqlite3.Connection` 注解，⛔ 容器复测。
 3. 门看的是**源码形状**，⛔ 现网运行时证据；`butler/` 里若引入新的动态派发（getattr 调方法）它看不见。
 4. 容器才有依赖 ⇒ 本档只用 stdlib（ast/pathlib），⛔ SkipTest 路径。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "audit_1002" / "classify_unawaited_1002.py"


def load_mod():
    spec = importlib.util.spec_from_file_location("classify_unawaited", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FIXTURE_TRUTH = {          # 我这边独立写死的期望（⛔ 从尺子的夹具注释里抄回来当"真值"）
    "S1": "SYNC_OK",        # 同名 sync：bridge.start()
    "S2": "ASYNC_UNAWAITED",  # 真缺陷：worker.start() 协程对象丢弃
    "S3": "WRAPPED",        # await 过
    "S4": "WRAPPED",        # create_task 包着
}


def write_fixture(root: pathlib.Path) -> pathlib.Path:
    """夹具源码用尺子自带的那份（同一形状两边不必各写一遍），**期望**只在本档。"""
    mod = load_mod()
    for rel, text in mod._FIX.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root / "pkg"


def fixture_points(mod):
    return mod.fixture_sites(mod._FIX["pkg/main.py"])


class FixtureRulerTest(unittest.TestCase):
    """A：尺子必须先证明自己会咬。"""

    def test_verdicts_match_planted_truth(self):
        mod = load_mod()
        with tempfile.TemporaryDirectory() as td:
            pkg = write_fixture(pathlib.Path(td))
            pts = fixture_points(mod)
            self.assertGreaterEqual(len(pts), 4, f"夹具点位取少了：{pts}")
            rows = mod.gate(pkg, [(p["site"].split(":")[0], int(p["site"].split(":")[1]),
                                   p["name"]) for p in pts])
            by = {r["site"]: r["verdict"] for r in rows}
            got = {p["id"]: by.get(p["site"], "<缺>") for p in pts}
            self.assertEqual(got, FIXTURE_TRUTH, f"逐枚点名不符：{got}")
            counts = mod.verdict_counts(rows)
            self.assertEqual(counts.get("ASYNC_UNAWAITED"), 1, counts)
            self.assertEqual(counts.get("SYNC_OK"), 1, counts)
            self.assertEqual(counts.get("WRAPPED"), 2, counts)
            self.assertEqual(counts.get("UNKNOWN", 0), 0, counts)

    def test_unresolvable_receiver_is_red_not_green(self):
        """解析不出来 ⇒ UNKNOWN（算红）。⛔ 把「我看不出」记成「没问题」。"""
        mod = load_mod()
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "pkg").mkdir()
            (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
            (root / "pkg" / "mystery.py").write_text(
                "async def lifespan(thing):\n"
                "    thing.start()\n",
                encoding="utf-8")
            rows = mod.gate(root / "pkg", [("mystery.py", 2, "start")])
            self.assertEqual(rows[0]["verdict"], "UNKNOWN", rows)

    def test_self_check_passes(self):
        mod = load_mod()
        with tempfile.TemporaryDirectory() as td:
            mod.self_check(pathlib.Path(td))


class ProductionRosterTest(unittest.TestCase):
    """B：今天点开的 30 枚，分布要复现得出来。"""

    @classmethod
    def setUpClass(cls):
        if not SCRIPT.exists():
            raise unittest.SkipTest(f"尺子不在位：{SCRIPT}")
        cls.mod = load_mod()
        cls.pkg = REPO / "butler"
        if not cls.pkg.is_dir():
            raise unittest.SkipTest(f"源码树不在位：{cls.pkg}")
        cls.rows = cls.mod.gate(cls.pkg, cls.mod.SITES)

    def test_no_async_unawaited_and_no_unknown(self):
        bad = [r for r in self.rows
               if r["verdict"] in ("ASYNC_UNAWAITED", "UNKNOWN")]
        self.assertEqual(bad, [], "这些枚还得再点：" + "\n".join(map(str, bad)))

    def test_distribution_equals_todays_readings(self):
        counts = self.mod.verdict_counts(self.rows)
        # 1011-b190：名册 31→32／SYNC_OK 22→23（本批新入册的 watch 关停腿 `app.py:897`，
        #   `def_at` 现读为同步体；`consumer` 那枚是 **await**，⛔ 进本名册）。
        # 1011-b192：名册 32→33／SYNC_OK 23→24（本批新入册的冷却写腿 `core/dialog.py:669`
        #   `XiaomiEar.note_speak`，`def_at` 现读 `xiaomi_ear.py:117`＝同步 classmethod）。
        #   批195 在 `__init__` 下方净插 14 行 ⇒ 655→669，名册计数与判定形制未动。
        self.assertEqual(counts.get("SYNC_OK"), 24, counts)
        self.assertEqual(counts.get("EXTERNAL_SYNC"), 9, counts)
        self.assertEqual(sum(counts.values()), 33, counts)

    def test_roster_sites_all_classified(self):
        self.assertEqual(len(self.rows), len(self.mod.SITES),
                         f"sites={len(self.mod.SITES)} rows={len(self.rows)}")


ANCHORS = [
    ("integrations/tv.py", "TVClient", "notify", 54),
    ("bus/mqtt_client.py", "MQTTClient", "start", 150),  # 批138 re-anchor 149→146（will 腿并进 advertise）；1013-b207R2 re-anchor 146→149（`59a0beb` 补回 28310ab 删掉的三腿——**这枚 def 体自己也回了 LWT 两行注释**，锚点只撑 kind=sync，`def_at` 现读仍 sync⇒判定未变）；1015-b224 re-anchor 149→150（批224 把 `__init__` 里那段「单点仍在 adm_status_is_online」的注释改成两行，def 本体一字未动，`def_at` 现读 kind=sync）
    ("bus/mqtt_client.py", "MQTTClient", "stop", 159),   # 批138 re-anchor 155→156；1013-b207R2 re-anchor 156→158（同上，`def_at` 现读 kind=sync）；1015-b224 re-anchor 158→159（同上，那段注释净 +1 整体下移）
    ("integrations/ilink/bridge.py", "ILinkBridge", "start", 59),  # 1013-b213 re-anchor 56→59（批213 会话档收编：顶部 `atomic_json` import 1 行＋`__init__` 里 `_unreadable_users` 名册 2 注释＋1 赋值，`def` 本体一字未动，`def_at` 现读 kind=sync）
    ("integrations/ilink/bridge.py", "ILinkBridge", "stop", 70),   # 1013-b213 re-anchor 67→70（同上，净插 3 行整体下移）
    ("core/event_stream.py", "EventStream", "start", 97),   # 1011-b201 re-anchor 96→97（批201 DB47 顶部净插 1 行 import，def 本体一字未动，门自己现读报的数）
    # 批189 新锚两枚：关停腿 app.py:883／:888（1011-b190 重钉后；批197 后 :885／:890；1015-b221 后
    #   现读 :899／:904，见 `classify_unawaited_1002.py` 的 1015-b221-REANCHOR 注）的判定撑在这两个 def 的
    #   「同步」性质上，谁把它们翻成 `async def` 而不动调用点，这里当场红（`def_at` 现读均 kind=sync）。
    ("core/event_stream.py", "EventStream", "stop", 105),  # 1011-b201 re-anchor 104→105（批201 DB47 顶部净插 1 行 import，def 本体一字未动）
    ("integrations/ha.py", "HAClient", "stop_xiaomi_token_refresh", 84),  # 1013-b214 re-anchor 67→84（批211·V42/V43 在 `class HAClient` 上方净插模块级 `_forget_task_when_done()`＋`__init__` 里逐实例建表两行注释，`def` 本体一字未动，`def_at` 现读 kind=sync）
    # 批190 新锚一枚：`app.py:897`（`rt.ha.cancel_watch_tasks()`，批197 后 :899；1015-b221 后现读 :913）
    #   同一条形状撑在 def 的同步性质上。
    ("integrations/ha.py", "HAClient", "cancel_watch_tasks", 96),  # 1013-b214 re-anchor 79→96（同上，整体下移 17 行；批211 只把 docstring 里「类属性」两句改成现形制，`def` 仍是同步体）
    # 批192 新锚一枚：`core/dialog.py:669`（`XiaomiEar.note_speak(...)`）撑在这枚 classmethod 的同步性质上。
    ("core/xiaomi_ear.py", "XiaomiEar", "note_speak", 134),  # 批194 A10 re-anchor 117→119（类体新入 ROLE_EXIT_WORDS 两行通用退出词）；批194 A9 re-anchor 119→133（模块级 AUDIOBOOK_COMMANDS 表＋两枚切段常量净插 14 行，def 本体一字未动，`def_at` 现读 kind=sync）
    ("core/xiaomi_ear.py", "XiaomiEar", "start", 45),   # 批166 re-anchor 29→30（件五乙：头注「点名才答」占两行）；批194 A9 re-anchor 30→44（同上，模块级净插 14 行）
    ("core/xiaomi_ear.py", "XiaomiEar", "stop", 52),    # 批166 re-anchor 36→37（同上）；批194 A9 re-anchor 37→51（同上）
    ("core/aliases.py", "AliasStore", "delete_alias", 99),  # 批164 re-anchor 93→99（D5：别名路径改成惰性 alias_file()）
    ("core/cron_task.py", "CronTaskExecutor", "register_api", 170),  # 批180 re-anchor 140→143（顶部导入 url_guard 净插 3 行，def 本体一字未动）；1013-b207R3 re-anchor 143→170（`c5a3c3a` 按 parent 文本重建＋补回 `_resolve_headers`/`_recheck_url` 两枚，两条 def 本体回到批180 那遍的形制，`def_at` 现读 kind=sync）
    ("core/cron_task.py", "CronTaskExecutor", "unregister_api", 189),  # 批180 re-anchor 159→162（同上；批179 那次是 docstring 变两行）；1013-b207R3 re-anchor 162→189（同上）
    ("store/write_failures.py", "", "record", 93),  # 批188 re-anchor 87→93（新增 `close_conn` 释放腿 6 行；def 本体一字未动，`def_at` 现读 kind=sync）
]


class AnchorTest(unittest.TestCase):
    """C：16 处判定锚点——被这些 def 的「同步」性质撑住的调用点，⛔ 无人守护地翻成 async。"""

    def test_anchors_are_still_sync_at_recorded_lines(self):
        mod = load_mod()
        drift = []
        for rel, cls, name, line in ANCHORS:
            path = REPO / "butler" / rel
            if not path.exists():
                drift.append(f"MISSING {rel}")
                continue
            got = mod.def_at(path, name, cls)
            if got is None:
                drift.append(f"NO-DEF {rel} {cls}.{name}")
            elif got["kind"] != "sync":
                drift.append(f"FLIPPED {rel}:{got['lineno']} {cls}.{name} -> {got['kind']}")
            elif got["lineno"] != line:
                drift.append(f"MOVED {rel} {cls}.{name} {line}->{got['lineno']}")
        self.assertEqual(drift, [], "\n".join(drift))


    def test_anchor_ruler_distinguishes_sync_from_async(self):
        """变异腿（打在**合成件**上，⛔ 改生产码）：锚点尺必须分得出 def／async def。"""
        mod = load_mod()
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "mut.py"
            p.write_text(
                "class C:\n"
                "    def m(self):\n        pass\n\n"
                "    async def a(self):\n        pass\n",
                encoding="utf-8")
            self.assertEqual(mod.def_at(p, "m", "C")["kind"], "sync")
            self.assertEqual(mod.def_at(p, "a", "C")["kind"], "async")


if __name__ == "__main__":
    unittest.main()
