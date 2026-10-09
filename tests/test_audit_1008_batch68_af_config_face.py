"""批68 #153 验收：af_bridge 文档面 / .env.example 配置面 与 config.py 真源对表。

三条生产腿现读应为 RED：
1. docstring 广告 `AUTOFORGE_POLL_INTERVAL`，而 config.py 从不读它（全仓只此一行命中＝死键）。
2. docstring 把 `data/config.json 或环境变量` 说成 af_bridge 自己的读取路径，并抄了一份会漂移的默认 URL。
3. config.py 实读的 `AUTOFORGE_BASE_URL` / `AUTOFORGE_API_TOKEN` 没登记进 `.env.example`。
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AF_BRIDGE = ROOT / "butler" / "af_bridge.py"
CONFIG = ROOT / "butler" / "config.py"
ENV_EXAMPLE = ROOT / ".env.example"

KEY_RE = re.compile(r"AUTOFORGE_[A-Z_]+")


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig")


def docstring_of(p: Path) -> str:
    return ast.get_docstring(ast.parse(read_text(p), filename=str(p))) or ""


def live_env_keys(config_text: str) -> set[str]:
    return set(re.findall(r'_env\("(AUTOFORGE_[A-Z_]+)"', config_text))


def example_keys(text: str) -> set[str]:
    keys = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name = line.split("=", 1)[0].strip()
        if KEY_RE.fullmatch(name):
            keys.add(name)
    return keys


def phantom_knobs(doc: str, live: set[str]) -> list[str]:
    """文档里承诺、config.py 里不读的键名。"""
    return sorted(set(KEY_RE.findall(doc)) - live)


def unregistered(live: set[str], example: set[str]) -> list[str]:
    """config.py 实读、`.env.example` 没登记的键名。"""
    return sorted(live - example)


def docstring_problems(doc: str) -> list[str]:
    problems = []
    if "http://" in doc or "https://" in doc:
        problems.append("COPIED_DEFAULT_URL")
    if "环境变量" in doc:
        problems.append("CLAIMS_DIRECT_ENV_READ")
    if "config.json" in doc:
        problems.append("CLAIMS_CONFIG_JSON_SOURCE")
    if "Settings" not in doc:
        problems.append("INJECTION_SOURCE_NOT_NAMED")
    return problems


class FileFaceTest(unittest.TestCase):
    def setUp(self):
        for p in (AF_BRIDGE, CONFIG, ENV_EXAMPLE):
            self.assertTrue(p.is_file(), "被侧面不存在，本腿等于没测: %s" % p)
        self.doc = docstring_of(AF_BRIDGE)
        self.live = live_env_keys(read_text(CONFIG))
        self.example = example_keys(read_text(ENV_EXAMPLE))

    def test_config_face_denominator_is_nonzero(self):
        self.assertGreaterEqual(
            len(self.live), 4,
            "config.py 只认出 %d 个 AUTOFORGE 键（现值 %s）⇒ 尺子失效，其余腿不可信"
            % (len(self.live), sorted(self.live)))

    def test_docstring_advertises_only_live_env_knobs(self):
        self.assertEqual(phantom_knobs(self.doc, self.live), [])

    def test_docstring_names_injection_source_without_copied_default(self):
        self.assertEqual(docstring_problems(self.doc), [])

    def test_every_live_knob_is_registered_in_env_example(self):
        self.assertEqual(unregistered(self.live, self.example), [])

    def test_env_example_advertises_no_key_config_does_not_read(self):
        self.assertEqual(sorted(self.example - self.live), [])


class RulerCalibrationTest(unittest.TestCase):
    """负控：合成夹具两侧都要过（应放行＝0，应咬＝恰好点名那枚）。"""

    def test_rulers_pass_on_synthetic_clean_inputs(self):
        clean_doc = "配置项（经 Settings 注入）：\n- AUTOFORGE_BASE_URL: 地址\n"
        self.assertEqual(phantom_knobs(clean_doc, {"AUTOFORGE_BASE_URL"}), [])
        self.assertEqual(docstring_problems(clean_doc), [])
        self.assertEqual(unregistered({"AUTOFORGE_BASE_URL"}, {"AUTOFORGE_BASE_URL"}), [])

    def test_rulers_bite_on_synthetic_violations(self):
        bad_doc = "- AUTOFORGE_GHOST: 默认 http://x.test:1\n（data/config.json 或环境变量）\n"
        self.assertEqual(phantom_knobs(bad_doc, {"AUTOFORGE_REAL"}), ["AUTOFORGE_GHOST"])
        self.assertEqual(docstring_problems(bad_doc), [
            "COPIED_DEFAULT_URL", "CLAIMS_DIRECT_ENV_READ",
            "CLAIMS_CONFIG_JSON_SOURCE", "INJECTION_SOURCE_NOT_NAMED"])
        self.assertEqual(unregistered({"AUTOFORGE_A", "AUTOFORGE_B"}, {"AUTOFORGE_A"}),
                         ["AUTOFORGE_B"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
