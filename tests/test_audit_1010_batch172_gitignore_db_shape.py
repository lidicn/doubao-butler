"""批172（D21 的 `*.db` 腿）：按报告的处方改**会更糟**——本批落的是「不动它」这把尺。

报告原文（第十九轮 §三／最终轮 §处置）的处方：`data/`、`*.db` 都照 D19 那样加前导斜杠。
`data/` 那半条 10-09 已落（现读 `.gitignore:2:/data/`）。`*.db` 这半条⛔ 照抄，理由是把两件事
分开了看：

  D19 的病＝**源代码被无声吞掉**（`tts/` 把 `butler/tts/` 整包 .py 吞了）⇒ 药是**收窄**模式。
  `*.db` 现在兜的是**运行期产物**：仓里现读 0 枚 `.db/.db-wal/.db-shm`（⛔ 已发生的损失＝没有），
  一旦收窄成 `/*.db`，下一个把 sqlite 落在 `butler/` 或 `tests/fixtures/` 的脚本就**不再被兜住**——
  而本仓还往公开快照那条线上走（10-02 单提交 `836df79`）。少一个 fixture 能重生成，
  多一枚含真实消息／口令的运行库进了 git 历史⛔ 撤不回来。两害相比⛔ 同权重。

机制腿⛔ 靠我嘴上说，是隔离仓库里 `git check-ignore` 量出来的（与 Arm E 同一跑法，本窗现读）：
  现况   `*.db`  → `butler/x.db` = `.gitignore:3:*.db`（兜住）／`butler/tts/a.py` = NOT-IGNORED（D19 已修）
  处方   `/*.db` → `butler/x.db` = **NOT-IGNORED**（漏）
  回退   `tts/`  → `butler/tts/a.py` = `.gitignore:2:tts/`（整包又没了）
⇒ 三格各是一张"这把尺⛔ 恒绿"的证据：处方与回退都会被咬住。

臂次：S＝纯文本形状（哪面都能跑）；E＝机制腿，需要 git 可执行＋能建临时仓库，⛔ git 的那面上
这一格是 NOT_MEASURED（会打 skip 原因）⇒ **S 腿照常跑，⛔ 整档静默全跳**。
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import tempfile
import unittest

TREE = pathlib.Path(__file__).resolve().parents[1]
GITIGNORE = TREE / ".gitignore"

# 必须**裸**（不限深度）的形态：兜运行期产物，落哪儿都算
BARE_REQUIRED = ("*.db", "*.db-wal", "*.db-shm")
# 必须**锚到根**的形态：D19/D21 收编过的目录类，收窄过＝回到原判例
ANCHORED_REQUIRED = ("/data/", "/tts/")


def gitignore_problems(text: str) -> list[str]:
    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]
    problems = []
    for pat in BARE_REQUIRED:
        if pat not in lines:
            problems.append(f"S1-MISSING-BARE {pat}（收窄了：子目录里的运行库不再被兜住）")
        if "/" + pat in lines:
            problems.append(f"S1-ANCHORED {pat} 被钉成了 /{pat}（同上一条的另一 half）")
    for pat in ANCHORED_REQUIRED:
        if pat not in lines:
            problems.append(f"S2-MISSING-ANCHOR {pat}")
        bare = pat.lstrip("/")
        if bare in lines:
            problems.append(f"S2-UNANCHORED {bare}（D19 原病：会把 butler/{bare} 这类源码目录吞掉）")
    return problems


CLEAN_TEXT = "\n".join(["# x", "/data/", "/tts/", "*.db", "*.db-wal", "*.db-shm", "tmp/"]) + "\n"
PRESCRIPTION_TEXT = CLEAN_TEXT.replace("*.db", "/*.db")
D19_REGRESS_TEXT = CLEAN_TEXT.replace("/tts/", "tts/")


def run_ignore_probe(gitignore_text: str, files: dict[str, str]) -> dict[str, str | None]:
    """在一次性空仓库里放一份 .gitignore 与若干探针文件，返回每枚探针**被哪条规则**吃掉。

    None ＝ NOT-IGNORED（git check-ignore rc=1 或该路径不在输出里）。
    """
    td = pathlib.Path(tempfile.mkdtemp(prefix="b172_probe_"))
    try:
        subprocess.run(["git", "init", "-q"], cwd=td, capture_output=True, check=True)
        (td / ".gitignore").write_text(gitignore_text, encoding="utf-8")
        for rel in files:
            p = td / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(files[rel], encoding="utf-8")
        out = subprocess.run(["git", "check-ignore", "--verbose"] + list(files),
                             cwd=td, capture_output=True, text=True)
        got: dict[str, str | None] = {}
        for line in out.stdout.splitlines():
            f = line.split("\t")
            if len(f) >= 2:
                got[f[1]] = f[0]
        return {rel: got.get(rel) for rel in files}
    finally:
        shutil.rmtree(td, ignore_errors=True)


class CalibrationTest(unittest.TestCase):
    """尺先证会咬：干净件放行，处方件与回退件各咬在自己那一臂。"""

    def test_clean_shape_passes(self):
        self.assertEqual(gitignore_problems(CLEAN_TEXT), [], "干净件被咬 ⇒ 尺恒红")

    def test_prescription_bits_the_bare_arm(self):
        got = gitignore_problems(PRESCRIPTION_TEXT)
        self.assertTrue(got, "报告的处方（`/*.db`）本该被咬，却判绿 ⇒ 这把尺是摆设")
        self.assertTrue(all(g.startswith("S1-") for g in got), got)
        self.assertEqual(len(got), len(BARE_REQUIRED) * 2, got)

    def test_d19_revert_bits_the_anchor_arm(self):
        got = gitignore_problems(D19_REGRESS_TEXT)
        self.assertEqual(got, [
            "S2-MISSING-ANCHOR /tts/",
            "S2-UNANCHORED tts/（D19 原病：会把 butler/tts/ 这类源码目录吞掉）",
        ], got)

    def test_mechanism_probe_distinguishes(self):
        """Arm E 的**尺的尺**：同一跑法必须能分出「被哪条吃掉」与「没被吃」。"""
        probe = run_ignore_probe("*.zzz\n/keep/\n", {
            "butler/deep/x.zzz": "", "keep/a.zzz": "", "root.zzz": "", "butler/plain.py": "",
        })
        self.assertIsNotNone(probe["butler/deep/x.zzz"], "裸模式没兜住子目录＝跑法本身坏了")
        self.assertIsNotNone(probe["root.zzz"])
        self.assertIsNone(probe["butler/plain.py"], "无关文件被误判成 ignored ⇒ 跑法⛔ 有分辨力")


class ProductionShapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not GITIGNORE.is_file():
            raise unittest.SkipTest(f".gitignore 不在位：{GITIGNORE}")
        cls.text = GITIGNORE.read_text(encoding="utf-8")

    def test_shipped_gitignore_shape_is_as_ruled(self):
        self.assertEqual(gitignore_problems(self.text), [],
                         "运行库兜底被收窄／目录锚定被放宽：\n" + "\n".join(gitignore_problems(self.text)))


class ProductionMechanismTest(unittest.TestCase):
    """Arm E：拿**真 .gitignore** 在隔离仓库里跑机制，⛔ 碰本仓、⛔ 装包。"""

    @classmethod
    def setUpClass(cls):
        if not GITIGNORE.is_file():
            raise unittest.SkipTest(f".gitignore 不在位：{GITIGNORE}")
        if shutil.which("git") is None:
            raise unittest.SkipTest("NOT_MEASURED：这一面没有 git 可执行，机制腿跑不了（形状腿 S 照常）")
        cls.text = GITIGNORE.read_text(encoding="utf-8")

    def test_runtime_db_anywhere_is_still_caught(self):
        probe = run_ignore_probe(self.text, {"butler/x.db": "", "butler/store/y.db-wal": ""})
        for rel, src in probe.items():
            self.assertIsNotNone(src, f"{rel} 没被兜住（真库含消息与口令，进 git 历史撤不回）")

    def test_source_package_under_tts_is_not_eaten(self):
        """D19 的**现网形状**判据：`butler/tts/` 下的 .py 必须⛔ 被 .gitignore 吃掉。"""
        probe = run_ignore_probe(self.text, {"butler/tts/engine.py": "", "butler/tts/sub/a.py": ""})
        for rel, src in probe.items():
            self.assertIsNone(src, f"{rel} 被 {src} 吞掉＝D19 复发")

    def test_data_dir_root_still_caught(self):
        probe = run_ignore_probe(self.text, {"data/live.db": "", "data/sub/live.db": ""})
        for rel, src in probe.items():
            self.assertIsNotNone(src, f"{rel} 未被 /data/ 兜住")


if __name__ == "__main__":
    unittest.main()
