"""批212（NAS 线 `cabac7c`＋`a8b6437` 的 LAN 基址腿收编）：内网地址⛔ 写死在运行码里。

现读基线（2026-10-13 09:46Z，`grep -rn "8090" butler/ --include=*.py` 与 `grep -n "1984" …`）：

    butler/skills/engines/llm_decide/ask.py:77        tv_base = "http://192.168.2.200:8090"
    butler/api/doubao_webhook.py:768                  frame_url = f"http://192.168.2.200:1984/api/frame.jpeg…"
    butler/config.py:138/194                          tvpilot_http_url / go2rtc_base_url（**两把键早就在**）
    butler/skills/engines/camera_vlm/engine.py:233     base = (rt.settings.go2rtc_base_url or "").rstrip("/")

⇒ 同一件事的三条腿里只有 `camera_vlm` 走了 settings，另两条把 IP＋端口写死在函数体里。
这是「配置记载面」的半腿（批184 一族）：换 NAS 内网地址＝改 `.env` 一次的事，现在要改码＋重建镜像；
而 `.env` 里那两个键此刻是**死键**（除 camera_vlm 外没人读），运维面看得到却改不动＝误导。
b221 的符号尺把这两处判成「★E 全树 0 出现此符号」（`_tv_base`／`_go2rtc_frame_url`）。

本批收法照 NAS 线口径（同名 helper、同 rstrip／未配置＝跳过并响一声），并加一把**全树形状尺**：
`butler/**/*.py` 里出现写死的 `http://10.|172.16-31.|192.168.` 字面量＝红，`butler/config.py` 的
默认值列豁免（那是键的默认值，本来就是给人看的）。

★ 形状尺两侧合成校准（⛔ 只跑真文件：恒绿与恒红都不改变真文件读数）：
  应咬＝合成件里写死内网地址 ⇒ 必须报缺；应放行＝合成件读 settings ⇒ 必须 0 命中。
本档 import 两枚纯函数模块（`…llm_decide.ask`／`api.doubao_webhook`，实测可装载、⛔ `butler.app`
＝二次 import 在停摆库上开连接）。
"""
from __future__ import annotations

import ast
import os
import pathlib
import re
import sys
import types
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

for _k, _v in (("DOUBAO_API_KEY", "dummy1"), ("DESKPILOT_API_TOKEN", "dummy2"),
               ("TASK_REPORT_TOKEN", "dummy3"), ("BUTLER_WEB_PASSWORD", "dummy4")):
    os.environ.setdefault(_k, _v)

import butler.api.doubao_webhook as webhook_mod  # noqa: E402
import butler.skills.engines.llm_decide.ask as ask_mod  # noqa: E402

ASK_PY = REPO_ROOT / "butler" / "skills" / "engines" / "llm_decide" / "ask.py"
WEBHOOK_PY = REPO_ROOT / "butler" / "api" / "doubao_webhook.py"

# 写死的内网 http 基址（v4 三段私有＋回环）；端口可选
HARD_LAN = re.compile(r"https?://(?:192\.168\.|10\.|172\.(?:1[6-9]|2\d|3[01])\.|127\.0\.0\.1)")


def _read(path):
    return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


class TvBaseFromSettings(unittest.TestCase):
    """`_tv_base(rt)`：TV 基址取自 `settings.tvpilot_http_url`；未配置＝空串。"""

    def test_reads_settings_and_strips_trailing_slash(self):
        fn = getattr(ask_mod, "_tv_base", None)
        self.assertIsNotNone(fn, "ask.py 没有 _tv_base＝TV 基址仍在写死")
        rt = types.SimpleNamespace(settings=types.SimpleNamespace(tvpilot_http_url="http://10.0.0.9:8090/"))
        self.assertEqual(fn(rt), "http://10.0.0.9:8090")

    def test_missing_settings_yields_empty_string(self):
        fn = getattr(ask_mod, "_tv_base", None)
        self.assertIsNotNone(fn, "ask.py 没有 _tv_base＝TV 基址仍在写死")
        self.assertEqual(fn(types.SimpleNamespace(settings=None)), "")
        self.assertEqual(fn(types.SimpleNamespace(settings=types.SimpleNamespace())), "")


class FrameUrlFromSettings(unittest.TestCase):
    """`_go2rtc_frame_url(rt, src)`：照 `settings.go2rtc_base_url` 拼，未配置＝None。"""

    def test_builds_from_base_and_quotes_src(self):
        fn = getattr(webhook_mod, "_go2rtc_frame_url", None)
        self.assertIsNotNone(fn, "doubao_webhook.py 没有 _go2rtc_frame_url＝取帧地址仍在写死")
        rt = types.SimpleNamespace(settings=types.SimpleNamespace(go2rtc_base_url="http://10.0.0.9:1984/"))
        self.assertEqual(fn(rt, "cam_客厅"), "http://10.0.0.9:1984/api/frame.jpeg?src=cam_%E5%AE%A2%E5%8E%85")

    def test_unconfigured_returns_none(self):
        fn = getattr(webhook_mod, "_go2rtc_frame_url", None)
        self.assertIsNotNone(fn, "doubao_webhook.py 没有 _go2rtc_frame_url＝取帧地址仍在写死")
        rt = types.SimpleNamespace(settings=types.SimpleNamespace(go2rtc_base_url=""))
        self.assertIsNone(fn(rt, "cam_x"))

    def test_call_site_branches_on_none(self):
        """未配置那条腿必须有人接：⛔ 把 None 当 URL 发给 notify（推一张空图＝静默失败）。"""
        src = _read(WEBHOOK_PY)
        self.assertIn("_go2rtc_frame_url(rt", src, "webhook 取帧点没调 helper")
        self.assertIn("frame_url is None", src, "取帧地址为 None 时没有跳过分支")


class ArmSiteNoLongerHardcoded(unittest.TestCase):
    """`PendingAskManager.arm()` 的 TV 腿必须走 helper，并接住未配置那一档。"""

    def _arm_body(self):
        tree = ast.parse(_read(ASK_PY))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "arm":
                return node
        self.fail("ask.py 里找不到 PendingAskManager.arm＝探测口径失效")

    def test_arm_calls_tv_base_helper(self):
        src = ast.get_source_segment(_read(ASK_PY), self._arm_body()) or ""
        self.assertIn("_tv_base(rt)", src, "arm() 里 TV 基址没走 settings")


class NoHardcodedLanUrlInCode(unittest.TestCase):
    """形状尺两格：⛔ 本批两处回退；⛔ 全树**新增**写死地址（其余 9 处按名册冻结）。"""

    # 现读名册（2026-10-13 09:47Z，同一把 `_hits` 尺跑出来的，⛔ 手抄）：本批只收两处，
    # 其余登记为债＝af_bridge／app(2)／deskpilot(文档行)／autoflow_propose(2)／registry。
    # 名册**冻结**＝再多一处或同档多一行都要红，
    # 这样这把尺既有牙又⛔ 一出生就恒绿／恒红（判据旁写正则原文：HARD_LAN）。
    # 1013-b215 更新本节口径：批215（NAS 线 `2292b0e` 天气基址收编）销掉
    #   `core/briefing.py`(1)＋`tools/registry.py`(1) 两行写死的 `…:3000/api/weather`
    #   ⇒ 名册 9→7 行、档数 9→7（两档从名册**整条移除**，⛔ 留 0 值占位）。
    # 1013-b220 更新本节口径：批220（NAS 线 `15eeab5` Bark 头像基址收编）销掉
    #   `api/bark_routes.py`(1) 那行 `os.environ.get("BUTLER_BASE_URL", "http://…:8095")`
    #   ⇒ 名册 7→6 行、档数 7→6（本尺 10:53:11Z 先咬过一回：树面 6 vs 名册 7＝少＝名册该同步）。
    ROSTER = {"butler/af_bridge.py": 1,
              "butler/api/doubao_webhook.py": 1,
              "butler/app.py": 2,
              "butler/integrations/deskpilot.py": 1,
              "butler/skills/engines/autoflow_propose/engine.py": 2,
              "butler/skills/engines/llm_decide/ask.py": 1}

    def _hits(self, text):
        return [ln.strip() for ln in text.split("\n") if HARD_LAN.search(ln)
                and not ln.strip().startswith("#")]

    def _tree(self):
        out = {}
        for path in sorted((REPO_ROOT / "butler").rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix().replace("\\", "/")
            if rel == "butler/config.py":
                continue
            n = len(self._hits(_read(path)))
            if n:
                out[rel] = n
        return out

    def test_ruler_bites_on_synthetic_hardcoded(self):
        src = 'url = "http://192.168.2.200:8090/api/ask/beep"\n'
        self.assertEqual(len(self._hits(src)), 1, "形状尺认不出写死的内网地址＝恒绿")

    def test_ruler_passes_on_synthetic_settings_read(self):
        src = 'base = (rt.settings.tvpilot_http_url or "").rstrip("/")\nurl = f"{base}/api/ask/beep"\n'
        self.assertEqual(len(self._hits(src)), 0, "形状尺把 settings 读法当成写死地址＝恒红")

    def test_ruler_passes_on_synthetic_comment(self):
        src = '# 曾经写死 http://192.168.2.200:1984 过\n'
        self.assertEqual(len(self._hits(src)), 0, "注释行不该算命中（否则文书里提一次就永久红）")

    def test_ruler_ignores_other_synthetic_forms(self):
        """★ 应放行第三格：公网域名／env 读法⛔ 算写死内网（否则名册外的正常码全被判红）。"""
        src = ('u1 = "https://api.doubao.com/v1"\n'
               'u2 = os.environ.get("AUTOFLOW_URL", "http://localhost:8000")\n')
        self.assertEqual(len(self._hits(src)), 0, "把公网/localhost 读法也算成命中＝判据过宽")

    def test_in_scope_files_clean(self):
        for path in (ASK_PY, WEBHOOK_PY):
            rel = path.relative_to(REPO_ROOT).as_posix().replace("\\", "/")
            with self.subTest(rel=rel):
                hits = self._hits(_read(path))
                self.assertEqual(hits, [], "%s 仍写死内网地址（应走 settings）：%s" % (rel, hits))

    def test_tree_roster_frozen(self):
        """名册⛔ 增长：新增一枚档／同档多一行都要红；本批收的两处已从名册里**消失**。"""
        got = self._tree()
        expected = {k: v for k, v in self.ROSTER.items()
                    if k not in ("butler/skills/engines/llm_decide/ask.py",
                                 "butler/api/doubao_webhook.py")}
        self.assertEqual(got, expected,
                         "写死内网地址的面变了：多＝扩散（应走 settings）；少＝名册该同步更新")


if __name__ == "__main__":
    unittest.main()
