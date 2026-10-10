r"""批70b 验收（TDD，先红后绿）：「读不出来」≠「没有数据」⇒ 读侧兜底空之后，写腿必须拒绝对全量覆盖。

来源（`doc/审计报告/doubao-butler_第一轮审计报告.md`，该目录**只存在于 E 盘工作副本，权威树没有**；
  同号轮次的旧 20 篇序列判定⛔ 沿用，见台账 §84-1）：
  - D1【P0】`butler/api/config_routes.py`：`_load_cfg()` 解析失败 `return {}`（:25-28），
    `_save_cfg()` tmp+`os.replace` 整份覆盖（:31-40）⇒ 坏档后改一句招呼语就把 persona/members 抹掉。
    改前现读（基底面＝`git archive HEAD butler`，⛔ 拿 E 镜像当基底，尺 `workorders/tools/b70b_fingerprint_1008.py`）：
    `_load_cfg:18-28` / `_save_cfg:31-40` / 三个调用点 `:80 :104 :123`（改后：`_load_cfg:23`、`_save_cfg:37`、
    拒写判断 `:92 :117 :137`）全是
    `cfg = _load_cfg(); …; _save_cfg(cfg); return ok({"saved": True})`（**保存结果无人看**）。
  - D2【P0】`butler/roles/state.py`：`read_role_state()` 缺档与坏档都返 `{}`（:29-38），
    `merge_and_bind()` 锁内重读→并一条→整份写回（:53-64）⇒ 坏档时其余角色的 conversation_id 全没。
  - D4【P0】`butler/core/aliases.py`：`_load()` 解析失败 `self.aliases = {}`（:28-30），
    `_save()` 整份 dump（:32-37），`learn()` 命中即 `_save()`（:67）⇒ 坏档后学一条别名把整库清零。
  - D6【high】`butler/core/fast_routes.py`：构造期读失败**已经**做对（:42-46 兜底内置且⛔ 写回，
    注释原文「⛔ 在这里 `_save()`：盖回去＝抹掉现场」）；漏的是**下一次命中**——
    `match()` :124-129 给规则加 `hits` 后 `self._save()` ⇒ 内置规则整份盖掉坏档现场，
    用户自加规则永久找不回。所以本批给 `_save()` 加闸，⛔ 改构造期那段注释。

为什么打包一批：四处是**同一条不变量的同一半**——读侧兜底空值是对的（⛔ 抛穿成 500，批17 已定），
但兜底之后这份内存状态**不完整**，拿它做「整份覆盖写」的素材就是把「读不出来」当成「本来就没有」。
判定口径收敛到一处：`butler/core/atomic_json.py` 新增 `read_json_object(path) -> (data, unreadable)`
（缺档＝真没有 → `({}, False)`；存在但读不出/解析失败/顶层不是对象 → `({}, True)`），
四个写手各自持一个 `unreadable` 位（⛔ 再来第 6 份 try/except 口径）。

覆盖口径：本文件 20 条腿＝**13 条先红**（4 条以 `ImportError`/`AttributeError` 形态红：新符号与新返回值改前不存在；
9 条以断言形态红）＋**7 条放行对照**（② ，绿码上也该绿，⛔ 计入先红）：
  ① test_read_json_object_missing_file_is_not_unreadable /
    test_read_json_object_valid_object /
    test_read_json_object_corrupt_file_is_unreadable /
    test_read_json_object_non_object_top_level_is_unreadable /
    test_corrupt_config_refuses_full_overwrite_and_keeps_bytes /
    test_corrupt_config_persona_route_returns_error /
    test_corrupt_config_refusal_is_logged /
    test_merge_and_bind_refuses_when_state_unreadable /
    test_merge_and_bind_refusal_is_logged /
    test_corrupt_alias_store_refuses_learn_save /
    test_corrupt_alias_store_refusal_is_logged /
    test_corrupt_fast_routes_first_match_does_not_write_builtins /
    test_fast_routes_refusal_at_match_is_logged
  ② test_readable_config_still_saves /
    test_readable_config_persona_route_still_returns_ok /
    test_merge_and_bind_valid_file_merges /
    test_merge_and_bind_missing_file_writes_first_binding /
    test_valid_alias_store_learn_lands /
    test_missing_fast_routes_file_seeds_builtins /
    test_valid_fast_routes_match_persists_and_keeps_custom_rule

它测不到什么：
  - 真·掉电／SIGKILL 落在 tmp 写与 rename 之间（那是批17 的原子性腿，本批⛔ 重复）；
  - 拒绝之后**怎么把坏档捞回来**：本批只保证现场不被盖掉，人工恢复路径（备份／dumps 复核）是另一件事；
  - 拒绝事件的**告警出口**：目前只有 logger.error（无指标／无 Bark 腿），登记为残留；
  - `butler/api/doubao_webhook.py:70` 那种硬编码 `/app/data/role_state.json` 的**第三个读手**（表行 95 T-05 家族，另单）；
  - `_save()` 拒绝后调用方是否把自己的「成功」话术改掉——本批只验 `config_routes` 三个路由之一（persona），
    `save_member`/`save_wakeup` 走同一条 `_save_cfg` 返回值，逐条再写一遍是同一枚缺陷的第 2、3 个副本；
  - 多进程并发（两处只有 `threading.Lock`，跨进程丢更新⛔ 在本批射程内）。
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from butler.api import config_routes
from butler.core import aliases as aliases_mod
from butler.core import fast_routes as fast_routes_mod
from butler.core.fast_routes import FastRouteStore
from butler.roles import state as state_mod


def _read_json_object():
    # 新符号：改前这 4 条腿以 ImportError 形态红（⛔ 写在模块顶层，那会把对照腿一起打死）
    from butler.core.atomic_json import read_json_object
    return read_json_object


class _Recorder:
    """日志是唯一可观察的拒绝出口，所以「有没有留痕」必须能数出来。"""

    def __init__(self):
        self.rows: list[tuple[str, str]] = []

    def _log(self, level, msg, *args):
        self.rows.append((level, str(msg) % args if args else str(msg)))

    def error(self, msg, *args):
        self._log("error", msg, *args)

    def warning(self, msg, *args):
        self._log("warning", msg, *args)

    def info(self, msg, *args):
        self._log("info", msg, *args)

    def debug(self, msg, *args):
        self._log("debug", msg, *args)

    def levels(self, level):
        return [m for lv, m in self.rows if lv == level]


class _TmpDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="b70b_"))
        self.addCleanup(self._rm)

    def _rm(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)


class _ConfigCase(_TmpDirCase):
    def setUp(self):
        super().setUp()
        self.cfg_path = self.tmp / "config.json"
        fake = types.SimpleNamespace(data_dir=str(self.tmp), _load_persona=lambda: None)
        p = mock.patch.object(config_routes, "get_settings", lambda: fake)
        p.start()
        self.addCleanup(p.stop)
        # 兜底位是模块级的，逐条复位，⛔ 让上一条腿的「读不出来」串进下一条
        setattr(config_routes, "_cfg_unreadable", False)
        self.addCleanup(setattr, config_routes, "_cfg_unreadable", False)


class TestReadJsonObject(_TmpDirCase):
    def test_read_json_object_missing_file_is_not_unreadable(self):
        rj = _read_json_object()
        data, unreadable = rj(self.tmp / "nope.json")
        self.assertEqual(data, {})
        self.assertFalse(unreadable, "文件不存在＝真的没有，允许首次整份写入")

    def test_read_json_object_valid_object(self):
        p = self.tmp / "ok.json"
        p.write_text(json.dumps({"persona": "P", "members": [{"id": "m1"}]}, ensure_ascii=False),
                    encoding="utf-8")
        data, unreadable = _read_json_object()(p)
        self.assertEqual(data["persona"], "P")
        self.assertFalse(unreadable)

    def test_read_json_object_corrupt_file_is_unreadable(self):
        p = self.tmp / "bad.json"
        p.write_text('{"persona": "P", "members": [', encoding="utf-8")
        data, unreadable = _read_json_object()(p)
        self.assertEqual(data, {})
        self.assertTrue(unreadable, "半截 JSON＝读不出来，⛔ 当成空配置")

    def test_read_json_object_non_object_top_level_is_unreadable(self):
        p = self.tmp / "list.json"
        p.write_text('["不是对象"]', encoding="utf-8")
        data, unreadable = _read_json_object()(p)
        self.assertEqual(data, {})
        self.assertTrue(unreadable, "顶层不是 dict：调用方拿它当配置用会当场炸，同判「读不出来」")


class TestConfigRoutes(_ConfigCase):
    def test_corrupt_config_refuses_full_overwrite_and_keeps_bytes(self):
        self.cfg_path.write_text('{"persona": "小豆", "members": [{"id": "kevin"}', encoding="utf-8")
        before = self.cfg_path.read_bytes()
        self.assertEqual(config_routes._load_cfg(), {}, "读侧兜底空 dict 的口径⛔ 改（批17 已定）")
        config_routes._save_cfg({"greeting_template": "你好"})
        self.assertEqual(self.cfg_path.read_bytes(), before,
                         "D1：坏档后一次改招呼语就把 persona/members 盖没了——本条要求拒写且盘上一个字节不动")
        self.assertNotIn(b"greeting_template", self.cfg_path.read_bytes())

    def test_corrupt_config_persona_route_returns_error(self):
        self.cfg_path.write_text('{"persona": "小豆"} {', encoding="utf-8")
        before = self.cfg_path.read_bytes()
        p = mock.patch.object(config_routes, "guard", lambda request: None)
        p.start()
        self.addCleanup(p.stop)

        class _Req:
            async def json(self):
                return {"system": "新人格"}

        resp = asyncio.run(config_routes.save_persona(_Req()))
        body = json.loads(resp.body)
        self.assertFalse(body["ok"],
                         "D1：保存被拒却回 ok=True＝对 WebUI 撒谎，用户以为改成功了")
        self.assertEqual(self.cfg_path.read_bytes(), before, "拒绝必须保住坏档现场")

    def test_corrupt_config_refusal_is_logged(self):
        self.cfg_path.write_text('{"persona": "小豆", "members": [', encoding="utf-8")
        config_routes._load_cfg()
        rec = _Recorder()
        p = mock.patch.object(config_routes, "logger", rec)
        p.start()
        self.addCleanup(p.stop)
        config_routes._save_cfg({"greeting_template": "你好"})
        self.assertTrue(rec.levels("error"),
                        "拒绝写入是「保存失败」的用户可见事件，只在读侧留痕不够——写侧必须自己喊一声")

    def test_readable_config_still_saves(self):
        self.cfg_path.write_text(json.dumps({"persona": "小豆", "members": [{"id": "kevin"}]},
                                            ensure_ascii=False), encoding="utf-8")
        cfg = config_routes._load_cfg()
        cfg["greeting_template"] = "欢迎回家"
        config_routes._save_cfg(cfg)
        disk = json.loads(self.cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(disk["greeting_template"], "欢迎回家")
        self.assertEqual(disk["persona"], "小豆", "② 放行对照：读得出就必须写得进，⛔ 把闸做成永久关")

    def test_readable_config_persona_route_still_returns_ok(self):
        self.cfg_path.write_text(json.dumps({"persona": "小豆"}, ensure_ascii=False), encoding="utf-8")
        p = mock.patch.object(config_routes, "guard", lambda request: None)
        p.start()
        self.addCleanup(p.stop)

        class _Req:
            async def json(self):
                return {"system": "人格 X"}

        resp = asyncio.run(config_routes.save_persona(_Req()))
        body = json.loads(resp.body)
        self.assertTrue(body["ok"], "② 放行对照：正常档保存照旧回 ok")
        self.assertEqual(json.loads(self.cfg_path.read_text(encoding="utf-8"))["system"], "人格 X")


class TestRoleState(_TmpDirCase):
    def setUp(self):
        super().setUp()
        self.path = self.tmp / "role_state.json"

    def test_merge_and_bind_refuses_when_state_unreadable(self):
        self.path.write_text('{"kevin": {"conversation_id": "sess-A"', encoding="utf-8")
        before = self.path.read_bytes()
        state_mod.merge_and_bind(self.path, "emily", "cid-C")
        self.assertIn(b"sess-A", self.path.read_bytes(),
                      "D2：坏档时 merge 读回 {} 再整份写回 ⇒ 其余角色的续聊绑定全没（跨端续聊＝R8）")
        self.assertEqual(self.path.read_bytes(), before, "拒绝写入必须一个字节不动")
        self.assertNotIn("cid-C", self.path.read_text(encoding="utf-8"))

    def test_merge_and_bind_refusal_is_logged(self):
        self.path.write_text('{"kevin": {"conversation_id": "sess-A"', encoding="utf-8")
        rec = _Recorder()
        p = mock.patch.object(state_mod, "logger", rec)
        p.start()
        self.addCleanup(p.stop)
        state_mod.merge_and_bind(self.path, "emily", "cid-C")
        self.assertTrue(rec.levels("error"),
                        "⛔ 抛异常（feeder 的调用面依赖不抛），所以 ERROR 日志是唯一的拒绝出口")

    def test_merge_and_bind_valid_file_merges(self):
        self.path.write_text(json.dumps({"kevin": {"conversation_id": "sess-A"}}, ensure_ascii=False),
                            encoding="utf-8")
        out = state_mod.merge_and_bind(self.path, "emily", "cid-B")
        disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(disk["kevin"]["conversation_id"], "sess-A", "② 放行对照：并一条不许盖掉另一条")
        self.assertEqual(disk["emily"]["conversation_id"], "cid-B")
        self.assertEqual(out["emily"]["conversation_id"], "cid-B")

    def test_merge_and_bind_missing_file_writes_first_binding(self):
        out = state_mod.merge_and_bind(self.path, "kevin", "sess-A")
        self.assertEqual(out["kevin"]["conversation_id"], "sess-A",
                         "② 放行对照：档不存在＝真没有，首次绑定必须落盘")


class TestAliasStore(_TmpDirCase):
    def _point(self, path: Path):
        p = mock.patch.object(aliases_mod, "alias_file", lambda: path)
        p.start()
        self.addCleanup(p.stop)

    def test_corrupt_alias_store_refuses_learn_save(self):
        f = self.tmp / "device_aliases.json"
        f.write_text('{"客厅灯": {"entity_id": "light.living" , "count": 9', encoding="utf-8")
        self._point(f)
        before = f.read_bytes()
        aliases_mod.AliasStore().learn("打开书房灯", "light.study", "light", "turn_on")
        self.assertIn("客厅灯", f.read_text(encoding="utf-8"),
                      "D4：坏档后学一条别名＝整库清零，用户要重新教一遍所有设备")
        self.assertEqual(f.read_bytes(), before)

    def test_corrupt_alias_store_refusal_is_logged(self):
        f = self.tmp / "device_aliases.json"
        f.write_text('{"客厅灯": {"entity_id": ', encoding="utf-8")
        self._point(f)
        store = aliases_mod.AliasStore()
        rec = _Recorder()
        p = mock.patch.object(aliases_mod, "logger", rec)
        p.start()
        self.addCleanup(p.stop)
        store.learn("打开书房灯", "light.study", "light", "turn_on")
        self.assertTrue(rec.levels("error"), "写侧拒绝必须留痕")

    def test_valid_alias_store_learn_lands(self):
        f = self.tmp / "device_aliases.json"
        f.write_text(json.dumps({"客厅灯": {"entity_id": "light.living", "domain": "light",
                                          "service": "turn_on", "count": 3}}, ensure_ascii=False),
                    encoding="utf-8")
        self._point(f)
        aliases_mod.AliasStore().learn("打开书房灯", "light.study", "light", "turn_on")
        disk = json.loads(f.read_text(encoding="utf-8"))
        self.assertIn("客厅灯", disk, "② 放行对照：读得出就必须学得进")
        self.assertEqual(len(disk), 2)


class TestFastRoutes(_TmpDirCase):
    def _point(self, path: Path):
        p = mock.patch.object(fast_routes_mod, "routes_file", lambda: path)
        p.start()
        self.addCleanup(p.stop)

    def test_corrupt_fast_routes_first_match_does_not_write_builtins(self):
        f = self.tmp / "fast_routes.json"
        f.write_text('[{"id": "my_rule", "keywords": ["天气", "自定义", ', encoding="utf-8")
        before = f.read_bytes()
        self._point(f)
        store = FastRouteStore()
        self.assertEqual(store.list_routes()[0]["source"], "builtin",
                         "构造期兜底内置的既有正解⛔ 变（表行 94 T-04，批17 已修那一半）")
        store.match("今天天气怎么样")
        self.assertEqual(f.read_bytes(), before,
                         "D6：构造期注释写着「⛔ 在这里 _save()＝抹掉现场」，但 match() 加完 hits 就 save ⇒ "
                         "第一次命中把内置规则整份盖回坏档，用户自加规则找不回")

    def test_fast_routes_refusal_at_match_is_logged(self):
        f = self.tmp / "fast_routes.json"
        f.write_text('[{"id": "my_rule", "keywords": [', encoding="utf-8")
        self._point(f)
        store = FastRouteStore()
        rec = _Recorder()
        p = mock.patch.object(fast_routes_mod, "logger", rec)
        p.start()
        self.addCleanup(p.stop)
        store.match("现在几点了")
        self.assertTrue(rec.levels("error"), "命中计数被拒＝写侧拒绝，必须留痕")

    def test_missing_fast_routes_file_seeds_builtins(self):
        f = self.tmp / "fast_routes.json"
        self._point(f)
        FastRouteStore()
        self.assertTrue(f.exists(), "② 放行对照：档不存在＝真没有，首次播种内置规则必须落盘")
        self.assertEqual(len(json.loads(f.read_text(encoding="utf-8"))), 6)

    def test_valid_fast_routes_match_persists_and_keeps_custom_rule(self):
        f = self.tmp / "fast_routes.json"
        custom = {"id": "my_rule", "keywords": ["天气"], "exclude_keywords": [],
                  "tool": "get_weather", "tool_args": {"type": "realtime"},
                  "enabled": True, "source": "manual"}
        f.write_text(json.dumps([custom], ensure_ascii=False), encoding="utf-8")
        self._point(f)
        store = FastRouteStore()
        hit = store.match("今天天气怎么样")
        self.assertIsNotNone(hit)
        disk = json.loads(f.read_text(encoding="utf-8"))
        self.assertEqual(len(disk), 1)
        self.assertEqual(disk[0]["id"], "my_rule", "② 放行对照：正常档命中后 hits 落盘，自加规则⛔ 掉")
        self.assertEqual(disk[0]["hits"], 1)


if __name__ == "__main__":
    unittest.main()
