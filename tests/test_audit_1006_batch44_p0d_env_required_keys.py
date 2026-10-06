# -*- coding: utf-8 -*-
"""批44 组3 / P0-D（最终审计报告 P0-D＝第七轮 P0-11）。

`butler/config.py` 里 `get_settings()` 对四把键缺键当场 `raise RuntimeError`
（DOUBAO_API_KEY／DESKPILOT_API_TOKEN／TASK_REPORT_TOKEN／BUTLER_WEB_PASSWORD），
而 `.env.example` 只记了其中两把 ⇒ 全新部署照样例复制 `.env` 一定起不来，
且报错发生在第一次写库的惰性调用点，离根因隔两个文件。

本档⛔ import `butler`（只需 `ast`＋读文件），所以它是**文件面**验收：
主机（`/vol1/1000/docker/doubao-butler`）跑权威读数。

判据⛔ 写死四枚名册：必需键集合由 `config.py` 现推（绊线绑推导⛔绑常量），
推导尺自己配合成源码校准格（放行格＋必咬格都得出读数）。
"""

import ast
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / "butler" / "config.py"
ENV_EXAMPLE = ROOT / ".env.example"
COMPOSE = ROOT / "docker-compose.yml"

SRC = CONFIG.read_text(encoding="utf-8")
ENV_SRC = ENV_EXAMPLE.read_text(encoding="utf-8")
COMPOSE_SRC = COMPOSE.read_text(encoding="utf-8")

_REQUIRED_MARK = "is required but not set"
_ENV_FUNCS = {"_env", "_env_int", "_env_float", "_env_bool"}
_ENV_KEY_RE = re.compile(r"[A-Z][A-Z0-9_]{2,}\Z")
# 凭据形状键（值⛔ 落进样例文件）：键名以 _TOKEN / _KEY / _PASSWORD 结尾
_CRED_SUFFIX = ("_TOKEN", "_KEY", "_PASSWORD")
_PLACEHOLDER_RE = re.compile(r"(?:your|change|replace|sample)_[A-Za-z0-9_]+\Z")


def _env_read_calls(src: str) -> set:
    """模块里经 `_env(...)` 族读取的环境变量名全集（推导的取值域）。"""
    out = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
        if name not in _ENV_FUNCS or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            if _ENV_KEY_RE.match(first.value):
                out.add(first.value)
    return out


def _required_env_names(src: str) -> set:
    """从报错文案现推「缺键即拒绝启动」的环境变量名。

    两种在役形状：
      · 文案里直接写键名（DOUBAO_API_KEY／BUTLER_WEB_PASSWORD）；
      · 键名在 `for` 的元组名册里、文案用 f-string 变量（WO-BUT-018 那对）。
    后者按「所在 For 的可迭代对象里的键名字面量」整册收下：名册＝同一段护栏，
    宁可多要求一份文档记载，⛔ 漏一枚。
    """
    domain = _env_read_calls(src)
    tree = ast.parse(src)
    parent = {}
    for p in ast.walk(tree):
        for c in ast.iter_child_nodes(p):
            parent.setdefault(id(c), p)

    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise):
            continue
        seg = ast.get_source_segment(src, node) or ""
        if _REQUIRED_MARK not in seg:
            continue
        for m in re.finditer(r"\b[A-Z][A-Z0-9_]{2,}\b", seg):
            if m.group(0) in domain:
                out.add(m.group(0))
        cur, depth = node, 0
        while cur is not None and depth < 12:
            if isinstance(cur, ast.For):
                for sub in ast.walk(cur.iter):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and sub.value in domain:
                        out.add(sub.value)
                break
            cur = parent.get(id(cur))
            depth += 1
    return out


def _documented_keys(env_src: str) -> dict:
    """.env 样例里的键名→值（跳过注释与空行；CRLF 安全：按 splitlines 切）。"""
    kv = {}
    for line in env_src.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, val = stripped.partition("=")
        kv[key.strip()] = val.strip()
    return kv


class RulerCalibrationLeg(unittest.TestCase):
    """形状尺自校准：⛔ 只跑真文件。放行格与必咬格都要出读数。

    夹具里的 kwarg 一律写成 `k1=`/`k2=`：推导尺只读 `_env("NAME")` 的位置参数，
    而 `token=`/`api_key=` 这类真键名会被泄露闸按「键名＋= ＋≥6 非空」的长度口径咬成假阳性。
    """

    def test_t1_literal_form_is_caught(self):
        syn = (
            "def get_settings():\n"
            "    s = X(k1=_env(\"DOUBAO_API_KEY\", \"\"))\n"
            "    if not s.doubao_api_key:\n"
            "        raise RuntimeError(\"DOUBAO_API_KEY is required but not set in environment.\")\n"
            "    return s\n"
        )
        self.assertEqual(_required_env_names(syn), {"DOUBAO_API_KEY"})

    def test_t2_loop_roster_form_is_caught(self):
        syn = (
            "def get_settings():\n"
            "    s = X(k1=_env(\"A_TOKEN\"), k2=_env(\"B_TOKEN\"))\n"
            "    for _attr, _envvar in ((\"a_token\", \"A_TOKEN\"), (\"b_token\", \"B_TOKEN\")):\n"
            "        if not getattr(s, _attr):\n"
            "            raise RuntimeError(f\"{_envvar} is required but not set in environment. \")\n"
            "    return s\n"
        )
        self.assertEqual(_env_read_calls(syn), {"A_TOKEN", "B_TOKEN"})
        self.assertEqual(_required_env_names(syn), {"A_TOKEN", "B_TOKEN"})

    def test_t3_key_without_gate_is_not_claimed(self):
        syn = (
            "def get_settings():\n"
            "    return X(k1=_env(\"HA_TOKEN\"), k2=_env(\"BARK_URL\", \"http://x\"))\n"
        )
        self.assertEqual(_required_env_names(syn), set())

    def test_t4_raise_with_other_wording_is_not_claimed(self):
        """负控：文案没有绊线短语 ⇒ 不许把名册里的键当必需键（尺子不是「见 raise 就收」）。

        `A_TOKEN`/`B_TOKEN` 先经 `_env(...)` 读进取值域，保证这条 PASS 只因缺短语，
        ⛔ 只因名册为空。
        """
        syn = (
            "def get_settings():\n"
            "    s = X(k1=_env(\"A_TOKEN\"), k2=_env(\"B_TOKEN\"))\n"
            "    for _attr, _envvar in ((\"a_token\", \"A_TOKEN\"), (\"b_token\", \"B_TOKEN\")):\n"
            "        if bad:\n"
            "            raise RuntimeError(f\"{_envvar} looks stale, continuing anyway\")\n"
        )
        self.assertEqual(_env_read_calls(syn), {"A_TOKEN", "B_TOKEN"})
        self.assertEqual(_required_env_names(syn), set())

    def test_t5_domain_excludes_non_env_literals(self):
        """负控：报错文案里出现但不是 `_env(...)` 读的键（路径／属性名）不许进集合。"""
        syn = (
            "def get_settings():\n"
            "    s = X(other=_env(\"OTHER_KEY\"))\n"
            "    if not s.web_password:\n"
            "        raise RuntimeError(\"WEB_PASSWORD is required but not set. See /app/DATA_DIR.\")\n"
        )
        self.assertEqual(_env_read_calls(syn), {"OTHER_KEY"})
        self.assertEqual(_required_env_names(syn), set())


class RealDerivationLeg(unittest.TestCase):
    """对真 `config.py` 的推导：名册⛔ 手抄，但四枚已审事实必须都在册。"""

    def test_t6_derived_set_covers_audited_four(self):
        derived = _required_env_names(SRC)
        print("REQUIRED|count=%d|names=%s" % (len(derived), sorted(derived)))
        for name in ("DOUBAO_API_KEY", "DESKPILOT_API_TOKEN", "TASK_REPORT_TOKEN", "BUTLER_WEB_PASSWORD"):
            self.assertIn(name, derived, "config.py 现推的必需键名册缺 %s" % name)

    def test_t7_derived_names_are_all_read_via_env(self):
        domain = _env_read_calls(SRC)
        derived = _required_env_names(SRC)
        self.assertTrue(derived)
        self.assertEqual(derived - domain, set(), "推导越界：出现的名字不是 `_env(...)` 读的键")

    def test_t8_gate_sites_and_names_are_consistent(self):
        """护栏段数 ⊇ 名册来源：只要求「每一枚推导名都在某个绊线 raise 的可见范围内」。"""
        tree = ast.parse(SRC)
        marked = [n for n in ast.walk(tree)
                  if isinstance(n, ast.Raise) and _REQUIRED_MARK in (ast.get_source_segment(SRC, n) or "")]
        derived = _required_env_names(SRC)
        print("GATE|raises=%d|derived=%d" % (len(marked), len(derived)))
        self.assertGreaterEqual(len(marked), 3, "绊线 raise 少于三处＝WO-BUT-017/018/022 三段护栏被拆了")
        self.assertGreaterEqual(len(derived), len(marked), "名册比 raise 还少＝有键被推导漏掉")


class DocCoverageLeg(unittest.TestCase):
    """P0-D 本体：每一枚必需键都要在 `.env.example` 里有记载。"""

    def test_t9_every_required_key_is_documented(self):
        kv = _documented_keys(ENV_SRC)
        derived = _required_env_names(SRC)
        missing = sorted(derived - set(kv))
        print("DOCFACE|keys=%d|required=%d|missing=%s" % (len(kv), len(derived), missing))
        self.assertEqual(missing, [], ".env.example 未记载启动硬门槛键：%s" % ", ".join(missing))

    def test_t10_required_keys_have_comment(self):
        """光有一行 `KEY=` 不够：样例是给人抄的，缺键必崩的键旁边要有一行说明。"""
        comment_blob = "\n".join(line.strip() for line in ENV_SRC.splitlines()
                                 if line.strip().startswith("#"))
        kv = _documented_keys(ENV_SRC)
        quiet = []
        for name in sorted(_required_env_names(SRC)):
            if name not in kv:
                continue
            if name not in comment_blob:
                quiet.append(name)
        print("COMMENTFACE|quiet=%s" % quiet)
        self.assertEqual(quiet, [], "以下硬门槛键在样例里没有注释说明：%s" % ", ".join(quiet))


class ValueShapeLeg(unittest.TestCase):
    """.env.example 是进 git 的样例文件：凭据形状键的值只能是空或占位符。"""

    def test_t11_credential_keys_are_empty_or_placeholder(self):
        kv = _documented_keys(ENV_SRC)
        bad = []
        for key, val in sorted(kv.items()):
            if not key.endswith(_CRED_SUFFIX):
                continue
            if val == "" or _PLACEHOLDER_RE.match(val):
                continue
            bad.append("%s=%s" % (key, "<len %d>" % len(val)))
        print("VALUEFACE|cred_keys=%d|bad=%s" % (
            sum(1 for k in kv if k.endswith(_CRED_SUFFIX)), bad))
        self.assertEqual(bad, [], "样例文件里出现非占位符的凭据形状值：%s" % ", ".join(bad))


class ComposeWiringLeg(unittest.TestCase):
    """`.env.example` 是正确的记载面：管家容器的环境变量确实经 env_file 从 `.env` 注入。"""

    def test_t12_butler_service_uses_env_file(self):
        self.assertIn("env_file:", COMPOSE_SRC)
        self.assertIn(".env", COMPOSE_SRC)
        block = COMPOSE_SRC.split("env_file:", 1)[1].split("environment:", 1)[0]
        print("ENVFILE|%s" % " ".join(line.strip() for line in block.splitlines() if line.strip()))
        self.assertIn(".env", block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
