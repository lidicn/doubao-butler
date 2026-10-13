# -*- coding: utf-8 -*-
"""批209（2026-10-13）泄露闸第三臂：按实例标识符（豆包会话号那类）＋带理由的豁免基线。

现读病根（⛔ 我归纳，全部有出处）：
· 值比对臂（批208 A 臂）的比对集只来自 `.env`。而本仓今天有一类**不在 `.env` 里**的用户数据
  ——豆包会话号（17 位）：`git grep -nE "\\b3[0-9]{16}\\b" HEAD` 现读 **15 枚唯一值／40 处命中**，
  其中 6 枚只存在于文档（`doc/02-接口文档/doubao2api_调用文档.md:126-131`、
  `doc/06-场景设计/顾安恒专属对话.md:16-20`），1 枚硬码在 `butler/api/doubao_webhook.py:525`；
· `data/roles/*.json` 里 `conversation_id` **0 命中**（现读）⇒ 没有任何运行时真值源可比对，
  所以 A 臂此刻对它**天生看不见**——不是尺写窄了，是语料里就没有那把对照。

 ⇒ 本批做两件事：
   ①`workorders/leak/instance_ids.txt`（⛔ 入库，与 `workorders/` 同一条 ignore）当**第二份比对集**，
     A 臂机械整条复用，只是键名换成 `INSTANCE#n`。册不在位时⛔ 静默当绿，必须打印「未量」。
   ②`.leak-baseline.txt`（入库）：豁免只认 `path:lineno ＋非空理由`，缺理由**判尺红**——
     与仓里既有 `.gates-baseline.txt` 同一口径（放行必须留字，⛔ 让闸门被空行动静默放宽）。

校准四格：SYNTH 会话号插一行必须咬／CLEAN 占位符必须放行／册不在位必须报未量且⛔ 不改退出码／
基线缺理由必须被尺子咬住。输出⛔ 回显值本体（闸自己不造泄露面）。
"""
from __future__ import annotations

import importlib.util
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

TREE = pathlib.Path(__file__).resolve().parents[1]
GATE = TREE / "scripts" / "leak_gate.py"
SYNTH_CID = "3" + "0" * 16           # 合成夹具：形似而⛔ 等于任何在册号（值由程序派生，⛔ 手敲真值——
                                     # 本档第一版就是手敲了一枚真会话号，被这把闸自己拦在提交门外）
SYNTH_BARK = "SYNTHbarkkey0123456789AB"


def _load_gate():
    spec = importlib.util.spec_from_file_location("leak_gate_b209", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _init_repo() -> pathlib.Path:
    td = pathlib.Path(tempfile.mkdtemp(prefix="b209_gate_"))
    subprocess.run(["git", "init", "-q"], cwd=td, capture_output=True, check=True)
    (td / ".env").write_text(f"BARK_KEY={SYNTH_BARK}\n", encoding="utf-8")
    return td


def _run(td: pathlib.Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GATE), "--staged", "--repo", str(td)] + list(extra),
                          capture_output=True, text=True)


class RegistryArmTest(unittest.TestCase):
    """①实例号册进比对集：咬得住、且不出口值。"""

    @classmethod
    def setUpClass(cls):
        if not GATE.is_file():
            raise unittest.SkipTest(f"闸不在位：{GATE}")
        if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
            raise unittest.SkipTest("NOT_MEASURED：这一面没有 git，机制腿跑不了")

    def test_planted_instance_id_is_caught_and_value_never_echoed(self):
        td = _init_repo()
        try:
            reg = td / "ids.txt"
            reg.write_text(SYNTH_CID + "\n", encoding="utf-8")
            (td / "doc.md").write_text(f"| 顾安恒 | `{SYNTH_CID}` |\n", encoding="utf-8")
            subprocess.run(["git", "add", "doc.md"], cwd=td, capture_output=True, check=True)
            out = _run(td, "--instance-ids", str(reg))
            self.assertEqual(out.returncode, 1,
                             f"会话号插进文档该拦没拦 rc={out.returncode}\n{out.stdout}{out.stderr}")
            self.assertIn("INSTANCE", out.stdout, "命中没标成实例号类键名")
            self.assertNotIn(SYNTH_CID, out.stdout, "输出里带了值本体")
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_placeholder_instance_id_passes(self):
        td = _init_repo()
        try:
            reg = td / "ids.txt"
            reg.write_text(SYNTH_CID + "\n", encoding="utf-8")
            (td / "doc.md").write_text("| 顾安恒 | «会话号·已移出公开仓» |\n", encoding="utf-8")
            subprocess.run(["git", "add", "doc.md"], cwd=td, capture_output=True, check=True)
            out = _run(td, "--instance-ids", str(reg))
            self.assertEqual(out.returncode, 0, f"占位符被误拦\n{out.stdout}{out.stderr}")
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_missing_registry_says_unmeasured_without_changing_verdict(self):
        """册不在位＝这一类没量到：必须打印「未量」，⛔ 静默当绿，也⛔ 因为缺册就谁都跑不动闸。"""
        td = _init_repo()
        try:
            (td / "doc.md").write_text("no secrets here\n", encoding="utf-8")
            subprocess.run(["git", "add", "doc.md"], cwd=td, capture_output=True, check=True)
            out = _run(td, "--instance-ids", str(td / "does-not-exist.txt"))
            self.assertIn("未量", out.stdout, "缺册却没说「未量」＝把看不见读成干净")
            self.assertEqual(out.returncode, 0, f"缺册不该自己判红（别的臂照判）\n{out.stdout}")
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_default_registry_path_is_gitignored(self):
        """册的默认落点⛔ 能被提交——它装的是真值名册。"""
        mod = _load_gate()
        rel = mod.INSTANCE_IDS_DEFAULT
        self.assertTrue(rel.startswith("workorders/"),
                        f"册默认落在可提交的位置：{rel}＝尺自己造泄露面")


class BaselineTest(unittest.TestCase):
    """②豁免基线：只认「位置＋非空理由」，缺理由判红。"""

    @classmethod
    def setUpClass(cls):
        if not GATE.is_file():
            raise unittest.SkipTest(f"闸不在位：{GATE}")
        if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
            raise unittest.SkipTest("NOT_MEASURED：这一面没有 git，机制腿跑不了")

    def test_baseline_line_with_reason_waives_only_that_location(self):
        td = _init_repo()
        try:
            reg = td / "ids.txt"
            reg.write_text(SYNTH_CID + "\n", encoding="utf-8")
            (td / "waived.py").write_text(f'CID = "{SYNTH_CID}"  # 兜底\n', encoding="utf-8")
            (td / "not_waived.py").write_text(f'CID = "{SYNTH_CID}"\n', encoding="utf-8")
            (td / ".leak-baseline.txt").write_text("waived.py:1 兜底待 P2 移出（路线图 §十）\n",
                                                  encoding="utf-8")
            subprocess.run(["git", "add", "waived.py", "not_waived.py"], cwd=td,
                           capture_output=True, check=True)
            out = _run(td, "--instance-ids", str(reg))
            self.assertEqual(out.returncode, 1, f"未豁免那枚必须还红\n{out.stdout}")
            body = out.stdout
            problems, waivers = [], []
            for line in body.splitlines():
                s = line.strip()
                if s.startswith("A-LEAK") or s.startswith("INSTANCE"):
                    problems.append(s)
                if "[豁免]" in s:
                    waivers.append(s)
            self.assertTrue(any("not_waived.py" in p for p in problems),
                            f"未登记的位置也被放过了＝基线成了万能钥匙\n{body}")
            self.assertFalse([p for p in problems if p.rsplit("file=", 1)[-1] == "waived.py:1"],
                             f"已豁免位置仍判红\n{body}")
            self.assertTrue(any("waived.py:1" in w for w in waivers),
                            f"豁免要看得见（⛔ 静默滤掉）\n{body}")
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_baseline_without_reason_is_a_ruler_problem(self):
        td = _init_repo()
        try:
            (td / ".env").write_text(f"BARK_KEY={SYNTH_BARK}\n", encoding="utf-8")
            (td / ".leak-baseline.txt").write_text("app.py:12\n", encoding="utf-8")
            (td / "app.py").write_text("print('clean')\n", encoding="utf-8")
            subprocess.run(["git", "add", "app.py"], cwd=td, capture_output=True, check=True)
            out = _run(td)
            self.assertEqual(out.returncode, 1, f"基线缺理由必须被咬 rc={out.returncode}\n{out.stdout}")
            self.assertIn("BASELINE", out.stdout)
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_shipped_baseline_entries_all_carry_a_reason(self):
        """本仓那份基线（入库件）逐行核：⛔ 空理由＝静默放宽闸门。"""
        bl = TREE / ".leak-baseline.txt"
        if not bl.is_file():
            self.skipTest("本仓基线件尚未落（本批后半段）")
        for raw in bl.read_text(encoding="utf-8").splitlines():
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            loc, _, reason = s.partition(" ")
            self.assertRegex(loc, r"^.{1,120}:\d+$", f"基线行缺 `path:lineno` 定位：{s[:60]}")
            self.assertTrue(reason.strip(), f"基线行⛔ 没写放行理由：{loc}")


if __name__ == "__main__":
    unittest.main()
