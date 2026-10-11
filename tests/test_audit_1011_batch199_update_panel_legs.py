"""批199（WebUI 自更新面板）：三面（api.js / app.js / index.html）的形状门＋容器路由与前端路径的同源核对。

为什么要门（owner 2026-10-11：「以后只允许从 webui 更新 DB」）：面板是这条线的唯一入口，
它一旦把「只落码没重启」念成「已生效」，或把确认版本填成前端自己的 `APP_VERSION` 字面量，
用户就会点下一轮更新而⛔ 知道代码还没换。这两类谎都是形状可判的⇒ 钉成门。

三面各有一段用 `批199 BEGIN/END` 圈起来的块，尺子只吃块内文本（⛔ 全文件扫：
`APP_VERSION: "v1.4.0"` 是别的功能的老值，拿它当本批命中＝假红）。

两侧校准（形状尺⛔ 只跑真文件，见 [[shape-rulers-need-synthetic-calibration]]）：
`CLEAN_*` 合成件必须 0 命中，三枚 `SYNTH_*` 各咬自己那一格。

盲点（自报）：
 1. 本门是形状＋同源核对，⛔ 证明浏览器真会渲染（Alpine 表达式只在浏览器里求值）；
    活体验收＝装机窗内打开「设置」页点一次「检查更新」，看预览件缺失时是否给出 `decision.reason` 那句人话。
 2. `tests/` 不在容器里，本档⛔ 现网跑（容器⛔ git、⛔ tests），窗口单里另列。
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from butler.api import update_routes  # noqa: E402  纯路由表，⛔ 依赖 butler.app

STATIC = REPO_ROOT / "butler" / "static"
API_JS = STATIC / "js" / "api.js"
APP_JS = STATIC / "js" / "app.js"
INDEX_HTML = STATIC / "index.html"

BEGIN = "批199 BEGIN"
END = "批199 END"

# 后端三枚路径＝前端唯一的口径来源（⛔ 手抄第二套）
CHECK_PATH = "/api/system/update/check"
APPLY_PATH = "/api/system/update"
STATUS_PATH = "/api/system/update/status"

PENDING_LABEL = "未生效"


def block(text: str, label: str) -> str:
    """取出 `批199 BEGIN … END` 之间的块文本；缺标记直接判失败（⛔ 当成空串让后面的断言全绿）。"""
    if BEGIN not in text or END not in text:
        raise AssertionError("%s 缺 %s/%s 标记（本批三面各一块，⛔ 只在一面）" % (label, BEGIN, END))
    start = text.index(BEGIN)
    stop = text.index(END, start)
    if stop <= start:
        raise AssertionError("%s 的 END 出现在 BEGIN 之前" % label)
    return text[start:stop]


class ShapeRuler:
    """块内形状尺：返回命中名单（空＝合格）。"""

    @staticmethod
    def hits(app_block: str, html_block: str, api_block: str):
        bad = []
        joined_app = app_block + html_block
        # 1) 确认版本必须取自后端 check 响应（前端 APP_VERSION 是 09-21 那种「两个物理文件」老路）
        if re.search(r"\bAPP_VERSION\b", app_block) or re.search(r"\bAPP_VERSION\b", api_block):
            bad.append("confirm-uses-front-end-app-version")
        if "upd.version" not in app_block and "update.version" not in app_block:
            bad.append("confirm-version-not-sourced-from-check")
        # 2) 「只落码」⛔ 念成「已生效」
        if "applied_pending_restart" in joined_app and PENDING_LABEL not in joined_app:
            bad.append("pending-restart-labelled-effective")
        if re.search(r"已生效", joined_app):
            bad.append("claims-effective-without-restart")
        # 3) 拒执原因必须上屏（预览件没跑过／脏树／非快进全靠这一句人话）
        if "decision.reason" not in html_block:
            bad.append("refusal-reason-not-rendered")
        # 4) 点下去之前就得知道⛔ 会被拒（按钮跟着 decision.action 走）
        if "decision.action" not in html_block:
            bad.append("apply-button-not-gated-on-decision")
        # 5) 轮询要有关腿（⛔ 每次进设置页多挂一个 interval）
        if "setInterval" in app_block and "clearInterval" not in app_block:
            bad.append("poll-interval-never-cleared")
        # 6) 面板⛔ 出现容器绝对路径字面量（批164 D5 同族：路径只从后端读）
        if "/app/" in joined_app:
            bad.append("hardcoded-container-path")
        return bad


CLEAN_APP = """
// 批199 BEGIN
updTimer: null,
async loadUpdate() {
  const r = await API.updateCheck();
  this.upd = r && r.data ? r.data : null;
  if (this.updTimer) clearInterval(this.updTimer);
  this.updTimer = setInterval(() => this.pollUpdate(), 5000);
},
async applyUpdate() {
  if (!this.upd || this.upd.decision.action !== "apply") return;
  const r = await API.updateApply(this.upd.branch, this.upd.version);
  this.showToast(r && r.ok ? "已请求，宿主 cron 一分钟一轮" : (r && r.error || "请求被拒"));
},
updateStateLabel(st) {
  if (st.state === "applied_pending_restart") return "已落码未生效，等下次重启";
  return st.state === "applied" ? "已应用并重启" : st.state;
},
// 批199 END
"""
CLEAN_HTML = """
<!-- 批199 BEGIN -->
<div x-text="$store.app.upd && $store.app.upd.decision.reason"></div>
<button :disabled="!$store.app.upd || $store.app.upd.decision.action !== 'apply'"
        @click="$store.app.applyUpdate()">确认更新</button>
<!-- 批199 END -->
"""
CLEAN_API = """
// 批199 BEGIN
updateCheck() { return this._req("GET", "/api/system/update/check"); },
updateApply(ref, confirmVersion) { return this._req("POST", "/api/system/update", { ref, confirm_version: confirmVersion }); },
updateStatus() { return this._req("GET", "/api/system/update/status"); },
// 批199 END
"""

SYNTH_CLAIMS_EFFECTIVE = """
// 批199 BEGIN
updateStateLabel(st) { return st.state === "applied_pending_restart" ? "已生效" : st.state; },
// 批199 END
"""
SYNTH_FRONT_END_VERSION = """
// 批199 BEGIN
async applyUpdate() { return API.updateApply(this.upd.branch, this.APP_VERSION); },
// 批199 END
"""
SYNTH_LEAKY_PATH = """
<!-- 批199 BEGIN -->
<div x-text="$store.app.upd && $store.app.upd.decision.reason"></div>
<button :disabled="$store.app.upd.decision.action !== 'apply'" @click="$store.app.applyUpdate()">确认更新</button>
<div>预览件：<code>/app/data/.update_available.json</code></div>
<!-- 批199 END -->
"""


class RouteParityTests(unittest.TestCase):
    """前端抄的路径＝后端真实路由表（同源，⛔ 两套口径）。"""

    def _backend_routes(self):
        out = {}
        for r in update_routes.routes():
            methods = set()
            for m in (r.methods or []):
                methods.add(m.decode() if isinstance(m, bytes) else m)
            out[r.path] = methods
        return out

    def test_01_backend_exposes_the_three_paths(self):
        routes = self._backend_routes()
        # Starlette 给 GET 路由自动并上 HEAD⇒只比「是否含该动词」，⛔ 比全集（比全集＝假红）
        self.assertIn("GET", routes.get(CHECK_PATH, set()), routes)
        self.assertIn("GET", routes.get(STATUS_PATH, set()), routes)
        self.assertIn("POST", routes.get(APPLY_PATH, set()), routes)
        self.assertNotIn("GET", routes.get(APPLY_PATH, set()),
                         "写接口能被 GET 命中＝ CSRF 预取的形状")

    def test_02_api_js_block_calls_each_with_matching_method(self):
        src = API_JS.read_text(encoding="utf-8", errors="replace")
        blk = block(src, "api.js")
        for path, verb in ((CHECK_PATH, "GET"), (APPLY_PATH, "POST"), (STATUS_PATH, "GET")):
            # 带引号精确匹配：`/api/system/update` 是 `/api/system/update/check` 的前缀，
            # ⛔ 裸 `path in line`（会把 check 那行当成 apply 那行⇒假红）
            quoted = '"%s"' % path
            lines = [l for l in blk.splitlines() if quoted in l]
            self.assertTrue(lines, "api.js 批199 块里没有精确串 %s" % quoted)
            self.assertIn('"%s"' % verb, lines[0], "方法不匹配：%s" % lines[0].strip())

    def test_03_api_js_block_invents_no_fourth_path(self):
        blk = block(API_JS.read_text(encoding="utf-8", errors="replace"), "api.js")
        known = {CHECK_PATH, APPLY_PATH, STATUS_PATH} | set(self._backend_routes())
        for path in re.findall(r'"/api/[^"]+"', blk):
            self.assertIn(path.strip('"'), known, "api.js 指到后端不存在的 %s" % path)


class PanelShapeTests(unittest.TestCase):
    def test_04_three_faces_each_have_a_batch199_block(self):
        for path in (API_JS, APP_JS, INDEX_HTML):
            text = path.read_text(encoding="utf-8", errors="replace")
            self.assertEqual(text.count(BEGIN), 1, "%s 的 BEGIN 标记数不是 1" % path.name)
            self.assertEqual(text.count(END), 1, "%s 的 END 标记数不是 1" % path.name)

    def test_05_real_blocks_pass_the_shape_ruler(self):
        hits = ShapeRuler.hits(
            block(APP_JS.read_text(encoding="utf-8", errors="replace"), "app.js"),
            block(INDEX_HTML.read_text(encoding="utf-8", errors="replace"), "index.html"),
            block(API_JS.read_text(encoding="utf-8", errors="replace"), "api.js"),
        )
        self.assertEqual(hits, [], "批199 面板形状不合格：%s" % hits)

    def test_06_panel_shows_the_fork_facts(self):
        """脏树／非快进／落后几笔／检查时刻——这四枚是「点了会不会被拒」的全部依据。"""
        html = block(INDEX_HTML.read_text(encoding="utf-8", errors="replace"), "index.html")
        for field in ("behind", "dirty", "fast_forward", "checked_at", "changelog"):
            self.assertIn(field, html, "面板没显示 %s" % field)

    def test_07_settings_view_loads_the_panel(self):
        """进「设置」页要真去取一次预览（app.js 的 switchView 用形参 `v`，⛔ 我按 `view` 写判据＝假红）。"""
        app = APP_JS.read_text(encoding="utf-8", errors="replace")
        self.assertRegex(app, r'v === "settings"[\s\S]{0,160}loadUpdate')

    def test_08_version_displayed_comes_from_backend_payload(self):
        html = block(INDEX_HTML.read_text(encoding="utf-8", errors="replace"), "index.html")
        self.assertIn("upd.version", html, "面板版本号⛔ 取自后端响应")


class RulerCalibrationTests(unittest.TestCase):
    """两侧校准在前：放行合成件⇒0 命中；三枚合成坏件各咬自己那一格。"""

    def test_09_clean_synth_passes(self):
        self.assertEqual(ShapeRuler.hits(CLEAN_APP, CLEAN_HTML, CLEAN_API), [],
                         "CLEAN 合成件被咬⇒尺子过宽")

    def test_10_synth_claims_effective_bites(self):
        hits = ShapeRuler.hits(SYNTH_CLAIMS_EFFECTIVE, "<!-- 批199 BEGIN -->x<!-- 批199 END -->", CLEAN_API)
        self.assertIn("claims-effective-without-restart", hits, hits)

    def test_11_synth_front_end_version_bites(self):
        hits = ShapeRuler.hits(SYNTH_FRONT_END_VERSION, CLEAN_HTML, CLEAN_API)
        self.assertIn("confirm-uses-front-end-app-version", hits, hits)

    def test_12_synth_hardcoded_container_path_bites(self):
        hits = ShapeRuler.hits(CLEAN_APP, SYNTH_LEAKY_PATH, CLEAN_API)
        self.assertEqual(hits, ["hardcoded-container-path"], hits)


if __name__ == "__main__":
    unittest.main(verbosity=2)
