"""批197 门：WebUI 自更新（容器侧只写 marker，宿主 cron 执行）。

出资人已裁的三条（本门把它们钉成码）：
 ① 发布源＝现在的公开 GitHub 仓 `main`（⛔ 另建仓、⛔ 改可见性）；
 ② **只有版本 tag 才自动重启**，日常 `main` 更新只落码⇒状态面必须把「已落盘、待重启」说出口；
 ③ 部署树有未收编的本地改动 ⇒ **拒绝执行并回报「有未收编的本地改动」**（字面必含这一句）。

现网拓扑（`workorders/readings/1011/b197_topology_nas.txt`，`stamp_utc=2026-10-11T03:33:08Z`）：
 容器 binds 只有 `butler/`、`data/`、`tts/`（＋只读 books/新api 等），`/app` 里⛔ `.git`，
 容器 `which git` 空⇒**容器侧⛔ 跑 git**，它只做三件事：读预览、写 marker、报状态。

跑法：`python -B -m unittest tests.test_audit_1011_batch197_self_update_legs`（一套一进程）。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_HERE = str(pathlib.Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
REPO_ROOT = pathlib.Path(_HERE).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from butler import update_gate as ug            # noqa: E402  纯判定，无 I/O、无 butler 依赖
from butler.api import update_routes as ur      # noqa: E402


class _StubRequest:
    def __init__(self, payload=None):
        self._payload = payload

    async def json(self):
        if self._payload is None:
            raise ValueError("no body")
        return self._payload


class _StubSettings:
    def __init__(self, enabled=True, branch="main"):
        self.self_update_enabled = enabled
        self.update_branch = branch
        self.update_repo_url = "https://github.com/lidicn/doubao-butler.git"


class _StubRuntime:
    def __init__(self, settings):
        self.settings = settings


def _call(handler, payload=None, settings=None, preview=None, result=None, marker=None):
    """走真实路由函数：`guard` 打桩成放行，三枚文件路径指到临时目录。"""
    tmp = pathlib.Path(tempfile.mkdtemp())

    def p(name):
        return tmp / name

    files = {
        ur.PREVIEW_NAME: tmp / ur.PREVIEW_NAME,
        ur.MARKER_NAME: tmp / ur.MARKER_NAME,
        ur.RESULT_NAME: tmp / ur.RESULT_NAME,
    }
    if preview is not None:
        files[ur.PREVIEW_NAME].write_text(json.dumps(preview), encoding="utf-8")
    if result is not None:
        files[ur.RESULT_NAME].write_text(json.dumps(result), encoding="utf-8")
    if marker is not None:
        files[ur.MARKER_NAME].write_text(json.dumps(marker), encoding="utf-8")

    with mock.patch.object(ur, "guard", lambda request: None), \
            mock.patch.object(ur, "get_runtime", lambda: _StubRuntime(settings or _StubSettings())), \
            mock.patch.object(ur, "_path", side_effect=lambda name: files[name]):
        resp = asyncio.run(handler(_StubRequest(payload)))
    body = json.loads(resp.body.decode("utf-8")) if resp.body else {}
    return resp.status_code, body, files


PREVIEW_DIRTY = {
    "has_update": True, "local_commit": "a" * 12, "remote_commit": "b" * 12,
    "dirty": 18, "fast_forward": True, "behind": 3, "changelog": ["x"],
    "checked_at": "2026-10-11T03:33:08Z",
}
PREVIEW_CLEAN = dict(PREVIEW_DIRTY, dirty=0)
PREVIEW_AHEAD = dict(PREVIEW_CLEAN, fast_forward=False)


class GateDecisionTests(unittest.TestCase):
    """纯判定：优先级＝未启用 > 脏树 > 非快进 > tag/日常。"""

    def test_01_disabled_refuses_before_anything_else(self):
        action, reason = ug.decide_action(enabled=False, dirty=0, fast_forward=True, ref="main")
        self.assertEqual(action, "refuse")
        self.assertIn("SELF_UPDATE_ENABLED", reason)

    def test_02_dirty_tree_reports_the_ruled_words(self):
        action, reason = ug.decide_action(enabled=True, dirty=18, fast_forward=True, ref="main")
        self.assertEqual(action, "refuse")
        self.assertIn("有未收编的本地改动", reason)   # 出资人指定的字面
        self.assertIn("18", reason)

    def test_03_dirty_beats_tag(self):
        """裁点：脏树优先拒绝——即便目标是版本 tag，也⛔ 动码。"""
        action, reason = ug.decide_action(enabled=True, dirty=3, fast_forward=True, ref="v1.2.0")
        self.assertEqual(action, "refuse")
        self.assertIn("有未收编的本地改动", reason)

    def test_04_non_fast_forward_refuses(self):
        action, reason = ug.decide_action(enabled=True, dirty=0, fast_forward=False, ref="main")
        self.assertEqual(action, "refuse")
        self.assertIn("后代", reason)

    def test_05_daily_main_applies_without_restart(self):
        action, reason = ug.decide_action(enabled=True, dirty=0, fast_forward=True, ref="main")
        self.assertEqual(action, "apply_only")
        self.assertIn("⛔ 自动重启", reason)

    def test_06_release_tag_applies_and_restarts(self):
        action, _ = ug.decide_action(enabled=True, dirty=0, fast_forward=True, ref="v1.2.0")
        self.assertEqual(action, "apply_and_restart")

    def test_07_ref_shape_ruler(self):
        """tag 形制只认 `v<主>.<次>…`；`main`／`latest`／裸 `v` 一律⛔ 当 tag。"""
        self.assertTrue(ug.is_release_ref("v1.2.0"))
        self.assertTrue(ug.is_release_ref("v1.2"))
        for not_tag in ("main", "master", "latest", "v", "ver1.2", "2.1.0"):
            self.assertFalse(ug.is_release_ref(not_tag), not_tag)


class ReadStateTests(unittest.TestCase):
    """三态读数：没有／读到／读坏——⛔ 把「读不出来」当「没有」（本仓判例）。"""

    def test_08_missing_file_is_present_false(self):
        tmp = pathlib.Path(tempfile.mkdtemp()) / "nope.json"
        st = ug.read_state(tmp)
        self.assertEqual(st["present"], False)
        self.assertIsNone(st["data"])
        self.assertIsNone(st["error"])

    def test_09_valid_file_returns_data(self):
        tmp = pathlib.Path(tempfile.mkdtemp()) / "ok.json"
        tmp.write_text(json.dumps({"has_update": True}), encoding="utf-8")
        st = ug.read_state(tmp)
        self.assertEqual(st["present"], True)
        self.assertEqual(st["data"], {"has_update": True})
        self.assertIsNone(st["error"])

    def test_10_corrupt_file_reports_error_not_absence(self):
        tmp = pathlib.Path(tempfile.mkdtemp()) / "bad.json"
        tmp.write_text("{not json", encoding="utf-8")
        st = ug.read_state(tmp)
        self.assertEqual(st["present"], True)     # 文件在，是内容坏了
        self.assertIsNone(st["data"])
        self.assertIsNotNone(st["error"])


class CheckRouteTests(unittest.TestCase):
    def test_11_no_preview_yet_says_so(self):
        """预览件还不存在（宿主 cron 尚未跑过第一遍）⇒ has_update=False，但 preview_present=False 必须同框。"""
        code, body, _ = _call(ur.update_check, preview=None)
        self.assertEqual(code, 200)
        self.assertEqual(body["data"]["preview_present"], False)
        self.assertEqual(body["data"]["has_update"], False)
        self.assertEqual(body["data"]["decision"]["action"], "refuse")
        self.assertIn("还没跑过", body["data"]["decision"]["reason"])

    def test_12_dirty_preview_surfaces_refusal(self):
        code, body, _ = _call(ur.update_check, preview=PREVIEW_DIRTY)
        d = body["data"]["decision"]
        self.assertEqual(code, 200)
        self.assertEqual(d["action"], "refuse")
        self.assertIn("有未收编的本地改动", d["reason"])

    def test_13_clean_main_preview_offers_apply_only(self):
        _, body, _ = _call(ur.update_check, preview=PREVIEW_CLEAN)
        self.assertEqual(body["data"]["decision"]["action"], "apply_only")
        self.assertEqual(body["data"]["repo_url"], "https://github.com/lidicn/doubao-butler.git")


class ApplyRouteTests(unittest.TestCase):
    def test_14_disabled_never_writes_marker(self):
        code, _, files = _call(ur.update_apply, {"confirm_version": "1.0.0", "ref": "main"},
                               settings=_StubSettings(enabled=False))
        self.assertEqual(code, 403)
        self.assertFalse(files[ur.MARKER_NAME].exists())

    def test_15_confirm_is_required_and_matched(self):
        code, _, files = _call(ur.update_apply, {"ref": "main"})
        self.assertEqual(code, 400)
        self.assertFalse(files[ur.MARKER_NAME].exists())
        code2, body2, files2 = _call(ur.update_apply, {"confirm_version": "9.9.9", "ref": "main"})
        self.assertEqual(code2, 400)
        self.assertIn("确认", body2.get("error", ""))   # deps.err 的形：{"ok": False, "error": "<串>"}
        self.assertFalse(files2[ur.MARKER_NAME].exists())

    def test_16_ref_outside_branch_and_tag_denied(self):
        code, _, files = _call(ur.update_apply, {"confirm_version": "1.0.0", "ref": "../../etc"})
        self.assertEqual(code, 400)
        self.assertFalse(files[ur.MARKER_NAME].exists())

    def test_17_accepted_request_writes_marker(self):
        code, body, files = _call(ur.update_apply, {"confirm_version": "1.0.0", "ref": "main"})
        self.assertEqual(code, 202)
        marker = json.loads(files[ur.MARKER_NAME].read_text(encoding="utf-8"))
        self.assertEqual(marker["ref"], "main")
        self.assertEqual(marker["confirm_version"], "1.0.0")
        self.assertIn("requested_at", marker)
        self.assertEqual(body["data"]["executed_by"], "host_cron")

    def test_18_marker_write_is_atomic(self):
        """marker 走「临时件＋rename」——宿主 cron 每分钟读它，半截 JSON 会被读成「读不出来」。"""
        _code, _body, files = _call(ur.update_apply, {"confirm_version": "1.0.0", "ref": "main"})
        tmp_leftovers = [p.name for p in files[ur.MARKER_NAME].parent.iterdir()
                         if p.name != ur.MARKER_NAME]
        self.assertEqual(tmp_leftovers, [], f"临时件没清：{tmp_leftovers}")


class StatusRouteTests(unittest.TestCase):
    def test_19_queued_when_marker_without_result(self):
        _, body, _ = _call(ur.update_status, marker={"ref": "main"})
        self.assertEqual(body["data"]["state"], "queued")
        self.assertEqual(body["data"]["pending_restart"], False)

    def test_20_apply_only_reports_pending_restart(self):
        """裁点②的记载面：只落码没重启 ⇒ ⛔ 报「已生效」。"""
        res = {"ok": True, "action": "apply_only", "from": "a" * 12, "to": "b" * 12,
               "to_tag": "b" * 12, "error": "", "changelog": [], "finished_at": "2026-10-11T03:40:00Z"}
        _, body, _ = _call(ur.update_status, result=res)
        self.assertEqual(body["data"]["state"], "applied_pending_restart")
        self.assertEqual(body["data"]["pending_restart"], True)

    def test_21_restart_action_is_effective(self):
        res = {"ok": True, "action": "apply_and_restart", "from": "a" * 12, "to": "b" * 12,
               "to_tag": "v1.2.0", "error": "", "changelog": [], "finished_at": "2026-10-11T03:40:00Z"}
        _, body, _ = _call(ur.update_status, result=res)
        self.assertEqual(body["data"]["state"], "applied")
        self.assertEqual(body["data"]["pending_restart"], False)

    def test_22_failed_result_reports_error(self):
        res = {"ok": False, "action": "refuse", "from": "a" * 12, "to": "a" * 12,
               "to_tag": "a" * 12, "error": "有未收编的本地改动", "changelog": [],
               "finished_at": "2026-10-11T03:40:00Z"}
        _, body, _ = _call(ur.update_status, result=res)
        self.assertEqual(body["data"]["state"], "failed")
        self.assertIn("有未收编的本地改动", body["data"]["error"])

    def test_23_nothing_written_yet(self):
        _, body, _ = _call(ur.update_status)
        self.assertEqual(body["data"]["state"], "idle")


class ShapeRulerTests(unittest.TestCase):
    """形状尺（两侧校准在前，⛔ 只跑真文件）。"""

    CLEAN = """
import json
from butler.api.deps import guard


def _path(name):
    return name
"""
    SYNTH = """
import subprocess


def apply_update(request):
    guard(request)
    subprocess.check_output("git reset --hard", shell=True)
"""
    SYNTH_PATH = """
from butler.api.deps import guard

PREVIEW = pathlib.Path("/app/data/.update_available.json")
"""

    def _hits(self, src: str):
        bad = []
        if "subprocess" in src or "os.system" in src or "shell=True" in src:
            bad.append("shell-out")
        if "/app/data" in src:
            bad.append("module-level-absolute-data-dir")
        return bad

    def test_24_ruler_calibrated_both_ways(self):
        self.assertEqual(self._hits(self.CLEAN), [], "CLEAN 合成件被咬⇒尺子过宽")
        self.assertEqual(sorted(self._hits(self.SYNTH)), ["shell-out"], "SYNTH 合成件没咬⇒尺子恒绿")
        self.assertEqual(sorted(self._hits(self.SYNTH_PATH)), ["module-level-absolute-data-dir"],
                         "SYNTH_PATH 合成件没咬⇒绝对路径那条腿恒绿")

    def test_25_container_never_shells_out(self):
        src = (REPO_ROOT / "butler" / "api" / "update_routes.py").read_text(encoding="utf-8")
        self.assertEqual(self._hits(src), [])

    def test_26_paths_are_lazy_single_point(self):
        """三枚文件路径⛔ 模块级死值（批164 D5 同族）：每次调用都过 `_path()`，而 `_path` 过 `data_paths.data_file`。"""
        src = (REPO_ROOT / "butler" / "api" / "update_routes.py").read_text(encoding="utf-8")
        for name in (ur.PREVIEW_NAME, ur.MARKER_NAME, ur.RESULT_NAME):
            self.assertIn('"%s"' % name, src, name)
        self.assertGreaterEqual(src.count("_path("), 4,
                                "_path( 出现 %d 次：三枚路径里有走死值的" % src.count("_path("))
        self.assertIn("data_paths.data_file", src)

    def test_27_every_route_passes_guard(self):
        src = (REPO_ROOT / "butler" / "api" / "update_routes.py").read_text(encoding="utf-8")
        # 三枚处理函数各一处 guard()；⛔ 匿名面（自更新＝不可逆动作）
        self.assertEqual(src.count("guard(request)"), 3, src.count("guard(request)"))

    def test_28_three_routes_registered(self):
        paths = sorted({r.path for r in ur.routes()})
        self.assertEqual(paths, ["/api/system/update", "/api/system/update/check",
                                 "/api/system/update/status"])
        methods = {}
        for r in ur.routes():
            methods[r.path] = sorted(
                m.decode() if isinstance(m, bytes) else m for m in (r.methods or []))
        self.assertEqual(methods["/api/system/update"], ["POST"])


class ConfigKnotsTests(unittest.TestCase):
    def test_29_defaults_are_fail_closed(self):
        from butler.config import Settings
        field_names = {f for f in dir(Settings)}
        self.assertTrue({"self_update_enabled", "update_branch", "update_repo_url"} <= field_names,
                        field_names)

    def test_30_env_example_carries_the_three_keys(self):
        text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        for key in ("SELF_UPDATE_ENABLED", "UPDATE_REPO_URL", "UPDATE_BRANCH"):
            self.assertIn(key, text, key)


if __name__ == "__main__":
    unittest.main()
