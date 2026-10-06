r"""批17 验收（TDD，先红后绿）：整份 JSON 的就地截断写 + 读侧解析失败语义。

来源（`doc/审计报告/_缺陷汇总_供审阅.md`，:N＝该文件正文行号；该目录**只存在于 E 盘工作副本，权威树没有**）：
  - 表行 45 P1-13 (R3)（:65）「无锁 + 裸 write_text 非原子 → 崩溃留截断 JSON，用户角色/设备配置静默重置为出厂」。
    权威树现读＝`butler/devices.py:175`、`butler/roles/store.py:177-178`（均为 `Path.write_text(json.dumps(...))` 就地截断写）。
    「无锁」那一半⛔ 在本批：它要先量并发路径（同一份登记表到底被谁在什么线程/进程里同时写），是另一批的量，见台账 §18 ⛔清单。
  - 表行 109 M-09 (RB)（:129）「role_state.json 非原子覆写 + 只捕 FileNotFoundError → 写坏后投喂接口全线 500 且不自愈」。
    权威树现读＝`butler/memory/feeder.py:51-54`（`with open(path,"w")` + `json.dump` 增量截断）、`:43-49`（只捕 FileNotFoundError）。
    **同一份 role_state.json 还有第二个写手**：`butler/roles/state.py:43-48`（`RoleConversationStore._save`，就地 write_text）——
    报表点名的是 feeder 那半，state.py 我按「同源扩展」一并做（⛔ 报表未逐字点名，见台账 §18-4）。
  - 表行 94 T-04 (RB)（:114）「fast_routes.json 解析失败回退空列表且不回填内置规则 → 快速路由永久静默失效」。
    权威树现读＝`butler/core/fast_routes.py:31-35`（`except Exception: self.routes = []`，且无 logger）。
  - 表行 127 T-06 (RB)（:147）「快速路由每次命中都在 async 热路径同步截断写整个规则文件」。
    本批只做它的**损坏半**（`:41-44` 的 `open(...,"w") + json.dump`）；**热路径同步写本身⛔ 动**（那是性能/节流设计，另议）。
    表行 95 T-05（:115，构造期 mkdir 无保护 + 硬编码 /app/data）本批⛔ 动：硬编码路径是 28 处家族、范围未裁。

为什么同一批：四条报表行是**同一条不变量的两个面**——
  写侧：新内容没写完之前，旧内容必须还在（ tmp 落字节 → 一次 rename 顶上去）；
  读侧：文件读不成时，必须有**明确的兜底值 + 一条告警**，⛔ 静默归零、⛔ 把异常抛穿到接口 500。
本批同时引入 `butler/core/atomic_json.py`：仓内已有 5 份各写各的原子实现
  （`core/aliases.py:35-37`、`api/config_routes.py:36-38`、`api/deps.py:56-59`、`triggers/store.py:90-92`、`skills/store.py:158-160`，
  且临时文件命名两种写法并存：`.json.tmp` 与 `.tmp`）⇒ 再手写第 6 种才是问题。
  旧 5 份本批⛔ 归队（它们已经是对的，动它们只给本窗口的重启面加风险），登记为后续单。

⛔ 两处做「序列化失败」腿的原因（不是偷懒）：`devices.py:175`/`roles/store.py:177`/`roles/state.py:46` 写的是
`write_text(json.dumps(...))`——序列化在**打开目标文件之前**就完成了，注入不可序列化值⛔ 能截断目标（今天的代码也过那条腿，
那是恒真断言）。它们暴露的是 `open(w)`→`write` 之间被杀／磁盘满，测不到那一瞬，只能测「提名为独立可失败的一步」
⇒ 用 patch `os.replace` 抛异常来等效（同 批16 leg 12 的口径）。
只有 `feeder.py:53`/`fast_routes.py:43` 是 `open(w)`+`json.dump` 增量写，**真能造出半截文件**，故各留一条实字节腿。

覆盖口径：本文件 20 条腿＝13 条先红（标 ①，含 2 条以 `ImportError` 形态红的新模块腿）＋7 条笼头对照（标 ②，绿码上也该绿，⛔ 计入先红）：
  ① test_devices_save_promotion_failure_keeps_previous_content /
    test_role_write_promotion_failure_keeps_previous_content /
    test_role_state_save_promotion_failure_keeps_previous_content /
    test_feeder_role_state_partial_write_keeps_previous_content /
    test_feeder_role_state_promotion_failure_keeps_previous_content /
    test_fast_routes_partial_write_keeps_previous_content /
    test_fast_routes_promotion_failure_keeps_previous_content /
    test_feeder_read_role_state_survives_corrupt_json /
    test_feeder_get_conversation_id_returns_none_on_corrupt_state /
    test_fast_routes_corrupt_file_falls_back_to_builtins /
    test_fast_routes_non_list_json_falls_back_to_builtins /
    test_atomic_helper_keeps_target_when_serialization_fails /
    test_atomic_helper_writes_file_and_removes_temp_sibling
  ② test_devices_save_roundtrips_and_leaves_no_temp_sibling /
    test_role_write_roundtrips_and_leaves_no_temp_sibling /
    test_role_state_save_roundtrips_and_leaves_no_temp_sibling /
    test_feeder_role_state_roundtrips_and_leaves_no_temp_sibling /
    test_fast_routes_first_run_seeds_builtins /
    test_fast_routes_corrupt_file_not_overwritten_at_construction /
    test_role_registry_glob_ignores_temp_sibling

它测不到什么：真·掉电／SIGKILL 落在 write 与 rename 之间那一瞬（腿用 patch 让 rename 抛异常来等效，证的是
「目标文件不会被就地截断」这条不变量，⛔ 等于 fsync 级持久性——本批补法⛔ 做 fsync，理由见台账 §18-4）；
多进程／多线程并发写同一份文件的丢更新（`role_state.json` 两个写手的 lost-update 是**另一枚缺陷**，报表未点名，见 §18 ⛔清单）；
corrupt 后「回填内置」会不会把用户自加规则永久抹掉（腿只断「构造后 routes 是 6 条内置」，⛔ 断之后还能不能找回）；
`/app/data` 硬编码在容器外的行为（T-05 本批⛔ 动）；热路径每次命中同步写盘的真实耗时（T-06 性能半⛔ 动）。
"""
from __future__ import annotations

import json
import shutil
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import butler.core.fast_routes as fast_routes_mod
from butler.core.fast_routes import FastRouteStore
from butler.devices import Device, DeviceRegistry
from butler.memory.feeder import MemoryFeeder
from butler.roles.state import RoleConversationStore
from butler.roles.store import Role, RoleRegistry

BUILTIN_IDS = [
    "weather_realtime", "weather_rain", "weather_daily",
    "capability_discovery", "current_time", "presence",
]


def _dumps(obj) -> str:
    """与各站点写盘**逐字相同**的序列化口径：ensure_ascii=False, indent=2。"""
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _rt(data_dir: str):
    """MemoryFeeder 只需要 rt.settings.data_dir。"""
    return types.SimpleNamespace(settings=types.SimpleNamespace(data_dir=data_dir))


def _settings(data_dir: str):
    """DeviceRegistry 直接收 Settings，只用 .data_dir。"""
    return types.SimpleNamespace(data_dir=data_dir)


class Batch17AtomicJsonWrites(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="b17_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    # ---------- 共用断言 ----------

    def _no_temp_sibling(self, directory: Path):
        residue = sorted(p.name for p in directory.glob("*.tmp"))
        self.assertEqual(residue, [], "临时文件残留（NAS 规范：⛔ .bak/.tmp 堆积）")

    def _corrupt(self, path: Path) -> str:
        path.write_text("{ not json at all", encoding="utf-8")
        return "{ not json at all"

    # ---------- ① 先红：写侧「提名独立可失败 ⇒ 旧版存活」 ----------

    def test_devices_save_promotion_failure_keeps_previous_content(self):
        reg = DeviceRegistry(_settings(str(self.tmp)))
        reg.devices = {"tv_old": Device("tv_old", "tv", room="客厅")}
        reg.save()
        target = self.tmp / "devices.json"
        old = target.read_text(encoding="utf-8")

        reg.devices = {"tv_new": Device("tv_new", "tv", room="卧室")}
        with mock.patch("os.replace", side_effect=OSError("promotion boom")):
            reg.save()  # devices.save 自吞异常，只 warning

        self.assertEqual(target.read_text(encoding="utf-8"), old,
                         "rename 失败后 devices.json 被就地换成了新版 → 写盘不是原子的")
        self._no_temp_sibling(self.tmp)

    def test_role_write_promotion_failure_keeps_previous_content(self):
        reg = RoleRegistry(str(self.tmp))
        reg.dir.mkdir(parents=True, exist_ok=True)
        target = reg.dir / "r1.json"
        reg._write(Role(id="r1", name="旧名"))
        old = target.read_text(encoding="utf-8")

        with mock.patch("os.replace", side_effect=OSError("promotion boom")):
            reg._write(Role(id="r1", name="新名"))  # _write 自吞异常

        self.assertEqual(target.read_text(encoding="utf-8"), old,
                         "rename 失败后角色文件被就地换成了新版 → 写盘不是原子的")
        self._no_temp_sibling(reg.dir)

    def test_role_state_save_promotion_failure_keeps_previous_content(self):
        store = RoleConversationStore(str(self.tmp))
        target = self.tmp / "role_state.json"
        store.set("butler", "cid-old")
        old = target.read_text(encoding="utf-8")

        with mock.patch("os.replace", side_effect=OSError("promotion boom")):
            store.set("luna", "cid-new")  # _save 自吞异常

        self.assertEqual(target.read_text(encoding="utf-8"), old,
                         "rename 失败后 role_state.json 被就地换成了新版 → 写盘不是原子的")
        self._no_temp_sibling(self.tmp)

    def test_feeder_role_state_partial_write_keeps_previous_content(self):
        feeder = MemoryFeeder(_rt(str(self.tmp)))
        target = self.tmp / "role_state.json"
        feeder._write_role_state({"butler": {"conversation_id": "c1"}})
        old = target.read_text(encoding="utf-8")

        bad = {"luna": {"conversation_id": {"not-serializable"}}}
        with self.assertRaises(TypeError):  # ⛔  newly swallow：异常必须照抛
            feeder._write_role_state(bad)

        self.assertEqual(target.read_text(encoding="utf-8"), old,
                         "序列化中途失败把 role_state.json 截断成半截 JSON（feeder.py:53 的 open(w)+json.dump）")
        self._no_temp_sibling(self.tmp)

    def test_feeder_role_state_promotion_failure_keeps_previous_content(self):
        feeder = MemoryFeeder(_rt(str(self.tmp)))
        target = self.tmp / "role_state.json"
        feeder._write_role_state({"butler": {"conversation_id": "c1"}})
        old = target.read_text(encoding="utf-8")

        with mock.patch("os.replace", side_effect=OSError("promotion boom")):
            with self.assertRaises(OSError):
                feeder._write_role_state({"luna": {"conversation_id": "c2"}})

        self.assertEqual(target.read_text(encoding="utf-8"), old)
        self._no_temp_sibling(self.tmp)

    def test_fast_routes_partial_write_keeps_previous_content(self):
        route_file = self.tmp / "fast_routes.json"
        store = self._fast_store(route_file)
        store.routes = [{"id": "keep", "keywords": ["留着"], "tool": "x"}]
        store._save()
        old = route_file.read_text(encoding="utf-8")

        store.routes = [{"id": "boom", "keywords": {"not-serializable"}}]
        with self.assertRaises(TypeError):
            store._save()

        self.assertEqual(route_file.read_text(encoding="utf-8"), old,
                         "序列化中途失败把 fast_routes.json 截断（fast_routes.py:43 的 open(w)+json.dump）")
        self._no_temp_sibling(self.tmp)

    def test_fast_routes_promotion_failure_keeps_previous_content(self):
        route_file = self.tmp / "fast_routes.json"
        store = self._fast_store(route_file)
        store.routes = [{"id": "keep", "keywords": ["留着"], "tool": "x"}]
        store._save()
        old = route_file.read_text(encoding="utf-8")

        with mock.patch("os.replace", side_effect=OSError("promotion boom")):
            with self.assertRaises(OSError):
                store._save()  # 新写法里 _save 不吞异常

        self.assertEqual(route_file.read_text(encoding="utf-8"), old)
        self._no_temp_sibling(self.tmp)

    # ---------- ① 先红：读侧解析失败语义 ----------

    def test_feeder_read_role_state_survives_corrupt_json(self):
        feeder = MemoryFeeder(_rt(str(self.tmp)))
        self._corrupt(self.tmp / "role_state.json")

        self.assertEqual(feeder._read_role_state(), {},
                         "role_state.json 损坏时 _read_role_state 应兜底为空 dict（M-09：⛔ 抛穿成 500）")

    def test_feeder_get_conversation_id_returns_none_on_corrupt_state(self):
        feeder = MemoryFeeder(_rt(str(self.tmp)))
        self._corrupt(self.tmp / "role_state.json")

        self.assertIsNone(feeder.get_conversation_id("butler"),
                          "损坏的 role_state.json 让 get_conversation_id 抛穿 → 投喂接口 500（M-09）")

    def test_fast_routes_corrupt_file_falls_back_to_builtins(self):
        route_file = self.tmp / "fast_routes.json"
        self._corrupt(route_file)
        store = self._fast_store(route_file)

        self.assertEqual([r["id"] for r in store.routes], BUILTIN_IDS,
                         "解析失败回退空列表 = 快速路由永久静默失效（T-04：要回填内置规则）")

    def test_fast_routes_non_list_json_falls_back_to_builtins(self):
        route_file = self.tmp / "fast_routes.json"
        route_file.write_text('{"id": "not a list"}', encoding="utf-8")
        store = self._fast_store(route_file)

        self.assertIsInstance(store.routes, list,
                              "整份文件是 JSON 对象而非数组时 routes 直接变成 dict（T-04 同源扩展）")
        self.assertEqual([r["id"] for r in store.routes], BUILTIN_IDS,
                         "routes 不是列表时⛔ 回填内置")

    def test_atomic_helper_writes_file_and_removes_temp_sibling(self):
        from butler.core.atomic_json import write_json_atomic  # 批17 新增模块：今日以 ImportError 形态红

        target = self.tmp / "cfg.json"
        write_json_atomic(target, {"a": 1, "中": "文"})

        self.assertEqual(target.read_text(encoding="utf-8"), _dumps({"a": 1, "中": "文"}))
        self._no_temp_sibling(self.tmp)

    def test_atomic_helper_keeps_target_when_serialization_fails(self):
        from butler.core.atomic_json import write_json_atomic

        target = self.tmp / "cfg.json"
        write_json_atomic(target, {"a": 1})
        old = target.read_text(encoding="utf-8")

        with self.assertRaises(TypeError):
            write_json_atomic(target, {"a": {"not-serializable"}})

        self.assertEqual(target.read_text(encoding="utf-8"), old)
        self._no_temp_sibling(self.tmp)

    # ---------- ② 笼头对照：绿码上也必须绿（⛔ 计入先红） ----------

    def test_devices_save_roundtrips_and_leaves_no_temp_sibling(self):
        reg = DeviceRegistry(_settings(str(self.tmp)))
        reg.devices = {"tv_a": Device("tv_a", "tv", room="客厅"),
                       "xiao_b": Device("xiao_b", "xiaomi", room="卧室", enabled=False)}
        reg.save()

        target = self.tmp / "devices.json"
        self.assertEqual(target.read_text(encoding="utf-8"), _dumps(
            {d.id: d.to_dict() for d in reg.devices.values()}))
        self.assertEqual(sorted(json.loads(target.read_text(encoding="utf-8"))), ["tv_a", "xiao_b"])
        self._no_temp_sibling(self.tmp)

    def test_role_write_roundtrips_and_leaves_no_temp_sibling(self):
        reg = RoleRegistry(str(self.tmp))
        reg.dir.mkdir(parents=True, exist_ok=True)
        role = Role(id="jarvis", name="贾维斯", gender="男", skills=["weather"])
        reg._write(role)

        target = reg.dir / "jarvis.json"
        self.assertEqual(target.read_text(encoding="utf-8"), _dumps(role.to_dict()))
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["name"], "贾维斯")
        self._no_temp_sibling(reg.dir)

    def test_role_state_save_roundtrips_and_leaves_no_temp_sibling(self):
        store = RoleConversationStore(str(self.tmp))
        store.set("butler", "cid-1")
        store.set("caesar", "cid-2")
        store.set("luna", "")  # 空 cid 直接 return，⛔ 覆盖已有绑定

        reloaded = RoleConversationStore(str(self.tmp))
        reloaded.load()
        self.assertEqual(reloaded.get("butler"), "cid-1")
        self.assertEqual(reloaded.get("caesar"), "cid-2")
        self.assertIsNone(reloaded.get("luna"))
        self._no_temp_sibling(self.tmp)

    def test_feeder_role_state_roundtrips_and_leaves_no_temp_sibling(self):
        feeder = MemoryFeeder(_rt(str(self.tmp)))
        state = {"butler": {"conversation_id": "c1", "updated_at": "2026-10-02T00:00:00"}}
        feeder._write_role_state(state)

        target = self.tmp / "role_state.json"
        self.assertEqual(target.read_text(encoding="utf-8"), _dumps(state))
        self.assertEqual(feeder._read_role_state(), state)
        self._no_temp_sibling(self.tmp)

    def test_fast_routes_first_run_seeds_builtins(self):
        route_file = self.tmp / "fast_routes.json"
        store = self._fast_store(route_file)

        self.assertEqual([r["id"] for r in store.routes], BUILTIN_IDS)
        self.assertEqual(json.loads(route_file.read_text(encoding="utf-8")), store.routes)
        self._no_temp_sibling(self.tmp)

    def test_fast_routes_corrupt_file_not_overwritten_at_construction(self):
        route_file = self.tmp / "fast_routes.json"
        raw = self._corrupt(route_file)
        self._fast_store(route_file)

        self.assertEqual(route_file.read_text(encoding="utf-8"), raw,
                         "构造期就把兜底结果盖回损坏文件 = 抹掉现场，人工无从判断丢了哪些规则")

    def test_role_registry_glob_ignores_temp_sibling(self):
        reg = RoleRegistry(str(self.tmp))
        reg.dir.mkdir(parents=True, exist_ok=True)
        (reg.dir / "jarvis.json").write_text(_dumps(Role(id="jarvis", name="贾维斯").to_dict()), encoding="utf-8")
        (reg.dir / "ghost.json.tmp").write_text("{ half written", encoding="utf-8")

        reg.load()

        self.assertNotIn("ghost", reg.roles,
                         "临时兄弟文件命名成 `.json.tmp` 正是为了不被 load() 的 `*.json` 扫到")
        self.assertIn("jarvis", reg.roles)
        self.assertEqual(reg.roles["jarvis"].name, "贾维斯")

    # ---------- 辅助 ----------

    def _fast_store(self, route_file: Path) -> FastRouteStore:
        patcher = mock.patch.object(fast_routes_mod, "ROUTES_FILE", route_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        return FastRouteStore()


if __name__ == "__main__":
    unittest.main(verbosity=2)
