"""批215（step3 搬码队列第 4 笔）：NAS 线 `2292b0e` 的天气基址收编。

缺口现读（`workorders/判定表-step3-逐笔-20261013.md` §一-2292b0e）：
- `.env.example:81` **已**登记 `WEATHER_API_URL`，而 `butler/config.py` 全文 grep `WEATHER_API_URL`
  ＝0 命中 ⇒ 文档里的键**没有读取点**（批184 配置记载面收编的同族：记载与代码两面分叉）。
- 两枚活调用点写死内网地址：`butler/core/briefing.py:98`、`butler/tools/registry.py:930`
  各一份 `http://192.168.2.200:3000/api/weather` ⇒ 换天气服务要改两档码＋重打镜像。

判据（⛔ 只测"现在长什么样"，要测"改对了才绿"）：
1. `Settings` 有 `weather_api_url` 字段，且默认值与 `.env.example` 那条**逐字同值**（⛔ 两面各写一份）；
2. 两枚调用点各自读到 `settings.weather_api_url`；
3. 两枚调用点⛔ 残留写死内网地址（复用批212 那把 `HARD_LAN` 尺的口径：剥注释行）；
4. 尺子自己先过校准：写死样⛔ 咬、settings 读法⛔ 放行、注释样⛔ 放行。
"""
from __future__ import annotations

import ast
import pathlib
import re
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_PY = REPO_ROOT / "butler" / "config.py"
BRIEFING_PY = REPO_ROOT / "butler" / "core" / "briefing.py"
REGISTRY_PY = REPO_ROOT / "butler" / "tools" / "registry.py"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

HARD_LAN = re.compile(r"https?://(?:192\.168\.|10\.|172\.(?:1[6-9]|2\d|3[01])\.|127\.0\.0\.1)")


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


def _lan_hits(text: str) -> list[str]:
    return [ln.strip() for ln in text.split("\n")
            if HARD_LAN.search(ln) and not ln.strip().startswith("#")]


def _attr_chains(source: str) -> set[str]:
    """该档里所有属性访问链的尾两节（`s.weather_api_url`／`get_settings.weather_api_url`）。"""
    out = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute):
            head = node.value
            prefix = ""
            if isinstance(head, ast.Name):
                prefix = head.id
            elif isinstance(head, ast.Call) and isinstance(head.func, ast.Name):
                prefix = head.func.id
            out.add(f"{prefix}.{node.attr}" if prefix else node.attr)
    return out


def _attr_names(source: str) -> set[str]:
    """该档里被**读过**的属性名（⛔ 吃字符串／注释：写进 f-string 的字面量不算读法）。"""
    return {node.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute)}


class WeatherUrlDeclared(unittest.TestCase):
    def test_settings_field_exists(self):
        src = _read(CONFIG_PY)
        self.assertRegex(src, r"(?m)^\s{4}weather_api_url:\s*str\s*=",
                         "config.py 的 Settings 里没有 weather_api_url 字段＝天气基址⛔ 可配")

    def test_loader_reads_the_documented_env_key(self):
        src = _read(CONFIG_PY)
        self.assertIn('WEATHER_API_URL', src,
                      ".env.example 里在册的 WEATHER_API_URL 在 config.py ⛔ 有读取点＝死键")

    def test_default_matches_env_example_verbatim(self):
        cfg = re.search(r'_env\("WEATHER_API_URL",\s*"([^"]+)"\)', _read(CONFIG_PY))
        self.assertIsNotNone(cfg, "取不到 config.py 的 WEATHER_API_URL 默认值")
        example = re.search(r"(?m)^WEATHER_API_URL=(.+)$", _read(ENV_EXAMPLE))
        self.assertIsNotNone(example, ".env.example 里没有 WEATHER_API_URL")
        self.assertEqual(cfg.group(1), example.group(1).strip(),
                         "默认值与 .env.example 不同值＝两面各写一份，改一边就分叉")


class CallSitesReadSettings(unittest.TestCase):
    def test_briefing_reads_settings(self):
        src = _read(BRIEFING_PY)
        self.assertIn("weather_api_url", _attr_names(src),
                      "briefing.py 的天气请求没读 settings.weather_api_url")
        self.assertEqual(_lan_hits(src), [],
                         f"briefing.py 仍写死内网地址：{_lan_hits(src)}")

    def test_registry_reads_settings(self):
        src = _read(REGISTRY_PY)
        self.assertIn("weather_api_url", _attr_names(src),
                      "registry.py 的 get_weather 没读 settings.weather_api_url")
        self.assertEqual(_lan_hits(src), [],
                         f"registry.py 仍写死内网地址：{_lan_hits(src)}")


class RulerCalibration(unittest.TestCase):
    """⛔ 一把只会点头的尺：三条合成腿（咬／放行／放行）。"""

    def test_bites_on_hardcoded_literal(self):
        src = 'url = "http://192.168.2.200:3000/api/weather"\n'
        self.assertEqual(len(_lan_hits(src)), 1, "认不出写死的内网地址＝这把尺恒绿")

    def test_passes_on_settings_read(self):
        src = 'url = get_settings().weather_api_url\n'
        self.assertEqual(len(_lan_hits(src)), 0, "把 settings 读法算成命中＝这把尺恒红")

    def test_passes_on_comment_line(self):
        src = '# 曾经写死 http://192.168.2.200:3000/api/weather\n'
        self.assertEqual(len(_lan_hits(src)), 0, "注释行⛔ 算命中（文书里提一次就永久红）")

    def test_attr_chain_helper_sees_the_attribute(self):
        src = "s = get_settings()\nawait client.get(s.weather_api_url)\n"
        self.assertIn("s.weather_api_url", _attr_chains(src), "属性链探测失效＝上面两格是假绿")
        self.assertIn("weather_api_url", _attr_names(src), "属性名探测失效＝上面两格是假绿")

    def test_attr_helper_ignores_string_forms(self):
        """★ 应咬的那一侧：只有字面量／注释里出现该键名时，属性读法探测⛔ 点头。"""
        src = '# settings.weather_api_url 以前是写死的\nurl = "http://x/api/weather"\n'
        self.assertNotIn("weather_api_url", _attr_names(src),
                         "把注释里的字样读成「已走 settings」＝这一族永远绿")


if __name__ == "__main__":
    unittest.main()
