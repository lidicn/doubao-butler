"""批162 验收（先红）：审计 D 族「读不出来被当成没有」在盘上的四条残留腿。

审计口径（`doc/审计报告/doubao-butler_最终轮审计报告.md` §二：22 项里第一族 10 项＝同一个语义问题）：
    **「读不出来」被等价成了「没有」**，于是下一次整份覆盖写把盘上现场抹掉。
仓里早就有这把收敛尺子（批70b）：`butler/core/atomic_json.py:26 read_json_object`（返回 `(data, unreadable)`）
＋ `write_json_atomic` ＋ 写侧拒写闸（样板见 `butler/roles/state.py:76 _save`）。本批把⛔ 没收编的落点收进来：

- D8 `cron_task._load_secrets`／D5 同族 `_load_apis`：坏档 → `self._secrets = {}` → `register_api()` 整份盖回
  ＝Secret Vault 与 API 注册表一起清空。
- D9 `cron_task._load_logs` 外层 except → `self._execution_log = []` → 超限那次 `_rewrite_logs()`
  用 `open(path, "w")` 把执行日志整本截掉。
- D2 feeder 腿／D18②：`memory/feeder.py` 是 `role_state.json` 的第二写手，读失败兜 `{}` 后
  `bind_conversation()` 照样整份写回＝其余角色的 conversation_id 全没（跨端续聊 R8）。
- D7 `ilink/client.py`：`_save_session` 就地 `write_text` 截断写＝自己造坏档（坏档的另一半成因）。

⛔ 本批动的东西：⛔ 现网、⛔ 容器、⛔ 报告里 D16（homesdk vendor 递归，跨仓）与 D17（SSRF，已投 DCD）。
ilink 那两处在盘上是**完整的显式写入**（扫码成功落新档 / errcode -14 主动清档）⇒ 本批只把它的治疗截断写换成
原子写＋读侧收编，⛔ 套拒写闸（套了会把再登录也拒掉），这条差异如实登记在台账，⛔ 冒充"整族已闭"。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP_ROOT = tempfile.mkdtemp(prefix="butler_test_batch162_")
CORRUPT = "{this is not json"


def _tmp(name: str) -> str:
    d = os.path.join(_TMP_ROOT, name)
    os.makedirs(d, exist_ok=True)
    return d


def _settings(data_dir: str):
    return types.SimpleNamespace(data_dir=data_dir)


def _executor(data_dir: str):
    from butler.core.cron_task import CronTaskExecutor
    return CronTaskExecutor(_settings(data_dir))


def _close_async_client(client) -> None:
    import asyncio
    asyncio.run(client._http.aclose())


class CronVaultAndRegistry(unittest.TestCase):
    """D8＋同族 _load_apis：坏档之后⛔ 拿不完整的内存态整份覆盖。"""

    def setUp(self):
        self.dir = _tmp("cron_" + self._testMethodName)

    def test_corrupt_secrets_file_bytes_survive_register_api(self):
        dd = os.path.join(self.dir, "cron_task")
        os.makedirs(dd, exist_ok=True)
        secrets = Path(dd) / "secrets.json"
        secrets.write_text(CORRUPT, encoding="utf-8")
        ex = _executor(self.dir)
        self.assertTrue(ex._secrets_unreadable,
                        "secrets.json 存在但读不出＝_secrets_unreadable 必须置真（现场还在盘上）")
        ex.register_api("wx", "http://x/{secret:token}", secret="newvalue")
        self.assertEqual(secrets.read_text(encoding="utf-8"), CORRUPT,
                         "坏档必须原样留在盘上：整份覆盖＝把⛔ 读得出的最后一份现场抹掉")

    def test_corrupt_apis_file_bytes_survive_register_api(self):
        dd = os.path.join(self.dir, "cron_task")
        os.makedirs(dd, exist_ok=True)
        apis = Path(dd) / "apis.json"
        apis.write_text(CORRUPT, encoding="utf-8")
        ex = _executor(self.dir)
        self.assertTrue(ex._apis_unreadable,
                        "apis.json 读不出＝_apis_unreadable 必须置真")
        ex.register_api("wx", "http://x")
        self.assertEqual(apis.read_text(encoding="utf-8"), CORRUPT,
                         "API 注册表同理：拒整份覆盖，⛔ 让新注册把旧注册表顶掉")

    def test_missing_files_still_seed_on_first_write(self):
        """缺档＝真没有 ⇒ 首次落盘照旧（⛔ 拒写闸把新装环境也闸死）。"""
        ex = _executor(self.dir)
        self.assertFalse(ex._secrets_unreadable)
        self.assertFalse(ex._apis_unreadable)
        ex.register_api("wx", "http://x/{secret:token}", secret="v1")
        dd = Path(os.path.join(self.dir, "cron_task"))
        saved = json.loads((dd / "secrets.json").read_text(encoding="utf-8"))
        self.assertEqual(saved.get("token"), "v1", "新装环境首次写入必须落盘")
        apis = json.loads((dd / "apis.json").read_text(encoding="utf-8"))
        self.assertIn("wx", apis.get("apis", {}), "新装环境首次注册表必须落盘")

    def test_valid_files_load_and_merge(self):
        dd = Path(os.path.join(self.dir, "cron_task"))
        os.makedirs(dd, exist_ok=True)
        (dd / "secrets.json").write_text(json.dumps({"token": "old"}), encoding="utf-8")
        (dd / "apis.json").write_text(json.dumps({"apis": {"keep": {"url": "http://k"}}}),
                                      encoding="utf-8")
        ex = _executor(self.dir)
        self.assertFalse(ex._secrets_unreadable)
        ex.register_api("wx", "http://x", secret="new")
        saved = json.loads((dd / "secrets.json").read_text(encoding="utf-8"))
        self.assertEqual(saved.get("token"), "new")
        apis = json.loads((dd / "apis.json").read_text(encoding="utf-8"))
        self.assertIn("keep", apis.get("apis", {}), "健康档上正常的整份写回⛔ 丢旧注册")


class CronExecutionLog(unittest.TestCase):
    """D9：整本 JSONL 读不动时，追加照旧、裁剪那次整本重写必须收手。"""

    def setUp(self):
        self.dir = _tmp("cronlog_" + self._testMethodName)
        self.dd = os.path.join(self.dir, "cron_task")
        os.makedirs(self.dd, exist_ok=True)

    def test_unreadable_log_skips_rewrite_but_keeps_append(self):
        log = Path(self.dd) / "execution_logs.jsonl"
        garbage = b"\xff\xfe not utf-8 at all\n"
        log.write_bytes(garbage)
        ex = _executor(self.dir)
        self.assertTrue(ex._logs_unreadable,
                        "日志整本读不出＝_logs_unreadable 置真（单行解析失败⛔ 触发它，那种照旧跳行）")
        ex._max_log_size = 2
        ex._execution_log = [{"a": 1}, {"a": 2}, {"a": 3}]
        ex._append_log({"a": 4})
        after = log.read_bytes()
        self.assertTrue(after.startswith(garbage),
                        "追加腿保留：append 本身不毁数据；⛔ 一坏档就从此不再记账")
        self.assertNotEqual(after, b"", "整本重写必须收手（open(path,'w') 会把这一本截没）")
        self.assertIn(b'{"a": 4}', after, "新记录还是要记得上")

    def test_readable_log_still_trims_by_rewrite(self):
        """健康档那一路：裁剪照旧发生（本批只换写法，⛔ 把行为改成"不再裁剪"）。

        走真实入口 `_log_execution`：它先把新条目并进内存缓存，再调 `_append_log`，
        超过上限就整本重写成上限条数。
        """
        log = Path(self.dd) / "execution_logs.jsonl"
        log.write_text(json.dumps({"a": 1}) + "\n" + json.dumps({"a": 2}) + "\n",
                       encoding="utf-8")
        ex = _executor(self.dir)
        self.assertFalse(ex._logs_unreadable)
        ex._max_log_size = 2
        ex._log_execution("s3", {"status": "success"})
        lines = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2, "内存超上限→整本重写成上限条数")
        self.assertEqual([ln.get("a") for ln in lines], [2, None], "留下的是最新两条，最旧那条被裁掉")
        self.assertEqual(lines[-1].get("skill_id"), "s3")

    def test_rewrite_leaves_no_tmp_sibling(self):
        """原子重写⛔ 留孤儿 tmp（`开发规范.md` 禁 .bak 堆积）。"""
        log = Path(self.dd) / "execution_logs.jsonl"
        log.write_text(json.dumps({"a": 1}) + "\n", encoding="utf-8")
        ex = _executor(self.dir)
        ex._rewrite_logs()
        siblings = [p.name for p in Path(self.dd).iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(siblings, [], f"重写后残留临时文件：{siblings}")


class FeederRoleStateSecondWriter(unittest.TestCase):
    """D2 feeder 腿／D18：role_state.json 的第二写手必须走带拒写闸的那一份实现。"""

    def setUp(self):
        self.dir = _tmp("feeder_" + self._testMethodName)
        self.state = Path(self.dir) / "role_state.json"

    def _feeder(self):
        from butler.memory.feeder import MemoryFeeder
        rt = types.SimpleNamespace(settings=_settings(self.dir))
        return MemoryFeeder(rt)

    def test_bind_refuses_when_state_unreadable(self):
        self.state.write_text(CORRUPT, encoding="utf-8")
        self._feeder().bind_conversation("jarvis", "cid-new")
        self.assertEqual(self.state.read_text(encoding="utf-8"), CORRUPT,
                         "坏档上 bind_conversation 整份写回＝其余角色的绑定全没，必须拒写")

    def test_bind_preserves_other_roles_on_healthy_state(self):
        self.state.write_text(json.dumps({"luna": {"conversation_id": "cid-luna"}}),
                              encoding="utf-8")
        self._feeder().bind_conversation("jarvis", "cid-jarvis")
        st = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(st["jarvis"]["conversation_id"], "cid-jarvis")
        self.assertEqual(st["luna"]["conversation_id"], "cid-luna",
                         "健康档上第二条绑定⛔ 顶掉别人的")

    def test_read_role_state_still_returns_empty_dict_on_corrupt(self):
        """批17 口径不动：读侧兜底空 dict，⛔ 抛穿成接口 500。"""
        self.state.write_text(CORRUPT, encoding="utf-8")
        self.assertEqual(self._feeder()._read_role_state(), {})

    def test_overview_on_corrupt_state_touches_nothing(self):
        """只读那一腿：WebUI 概况⛔ 因读坏档反手改盘（批17 口径：兜底空、留痕、不动现场）。"""
        self.state.write_text(CORRUPT, encoding="utf-8")
        self._feeder().overview()
        self.assertEqual(self.state.read_text(encoding="utf-8"), CORRUPT,
                         "overview 是读侧，盘上那半截 JSON 必须原样还在")


class ILinkSessionWriter(unittest.TestCase):
    """D7：截断写换原子写＋读侧收编（两处写入都是完整的显式落档 ⇒ ⛔ 套拒写闸）。"""

    def setUp(self):
        self.dir = _tmp("ilink_" + self._testMethodName)

    def _client(self):
        from butler.integrations.ilink.client import ILinkClient
        c = ILinkClient(session_dir=self.dir)
        self.addCleanup(_close_async_client, c)
        return c

    def test_corrupt_session_marks_unreadable_and_keeps_bytes(self):
        f = Path(self.dir) / "session.json"
        f.write_text(CORRUPT, encoding="utf-8")
        c = self._client()
        self.assertTrue(c._session_unreadable,
                        "session.json 读不出要留痕（内存态是不完整的，运维得知道）")
        self.assertEqual(f.read_text(encoding="utf-8"), CORRUPT)

    def test_deliberate_login_still_writes_after_unreadable(self):
        """再登录＝完整的新档 ⇒ ⛔ 被拒写闸误伤；落档后 unreadable 要归零。"""
        f = Path(self.dir) / "session.json"
        f.write_text(CORRUPT, encoding="utf-8")
        c = self._client()
        c._session = {"bot_token": "t", "ilink_bot_id": "b", "baseurl": "u",
                      "ilink_user_id": "u1", "logged_in_at": 1.0}
        c._save_session()
        self.assertFalse(c._session_unreadable, "主动落新档＝内存态已完整，标记必须清掉")
        self.assertEqual(json.loads(f.read_text(encoding="utf-8")).get("ilink_bot_id"), "b")
        self.assertEqual([p.name for p in Path(self.dir).iterdir()
                          if p.name.endswith(".tmp")], [], "⛔ 留 tmp 兄弟件")

    def test_missing_session_is_not_unreadable(self):
        c = self._client()
        self.assertFalse(c._session_unreadable, "缺档＝真没有（新装），⛔ 报成读失败")


class ShapeRulerOnTheseThreeFiles(unittest.TestCase):
    """形状尺：三处落点⛔ 再出现"就地截断写"。配合成校准，⛔ 一把恒绿的尺。"""

    NEEDLES = (
        "self._secrets_file.write_text",
        "self._apis_file.write_text",
        "self.session_file.write_text",
        'open(self._log_file, "w"',
    )

    def _hits(self, source: str) -> list[int]:
        return [i + 1 for i, ln in enumerate(source.splitlines())
                for n in self.NEEDLES if n in ln]

    def test_ruler_bites_synthetic_source(self):
        synthetic = "x = 1\n" + "\n".join(self.NEEDLES) + "\ny = 2\n"
        self.assertEqual(self._hits(synthetic), [2, 3, 4, 5],
                         "尺子先证明它会咬：合成源码四行必须四行命中")

    def test_three_sites_have_no_truncating_writes(self):
        offenders = {}
        for rel in ("butler/core/cron_task.py", "butler/memory/feeder.py",
                    "butler/integrations/ilink/client.py"):
            p = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / rel
            hits = self._hits(p.read_text(encoding="utf-8"))
            if hits:
                offenders[rel] = hits
        self.assertEqual(offenders, {}, f"仍有就地截断写：{offenders}")

    def test_sites_use_the_converged_ruler(self):
        root = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        needles = {
            "butler/core/cron_task.py": [
                "from butler.core.atomic_json import",
                "read_json_object", "write_json_atomic", "write_lines_atomic",
                "self._secrets_unreadable", "self._apis_unreadable", "self._logs_unreadable"],
            "butler/memory/feeder.py": [
                "from butler.roles.state import merge_and_bind, read_role_state"],
            "butler/integrations/ilink/client.py": [
                "from butler.core.atomic_json import read_json_object, write_json_atomic",
                "self._session_unreadable"],
        }
        missing = {}
        for rel, ns in needles.items():
            src = (root / rel).read_text(encoding="utf-8")
            absent = [n for n in ns if n not in src]
            if absent:
                missing[rel] = absent
        self.assertEqual(missing, {}, f"未收编到收敛尺子：{missing}")


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(_TMP_ROOT, ignore_errors=True)
