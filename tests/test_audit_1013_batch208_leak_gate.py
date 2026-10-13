# -*- coding: utf-8 -*-
"""批208（2026-10-13）推送前泄露闸——出资人那句「不把这个完成，以后也会一样把 key 提交到 github」的机械腿。

病根现读（10-13，两条都是量出来的，⛔ 我嘴上归纳）：
· `.git/hooks/` 里除 `.sample` 外**一枚钩子都没有** ⇒ 提交那一刻没有任何东西在跑；
· `gates.sh` 里 `grep -nE "leak|secret|credential|凭据"` **0 命中** ⇒ 三道门（AST／冒烟／契约）
  没有一道看内容。
⇒ 所以「有没有泄露」到今天为止全靠**某个人记得去跑一把尺**。批206 那两枚活凭据就是这么进的文件面的
（我把真值手敲进负控夹具），而那条线我⛔ 撞见过第二次——同形病在 memory 条目七十三里写着。

本档审的是 `scripts/leak_gate.py`：一把**纯函数**尺＋一枚**会拦人的钩子**。
两臂：
  A 值比对：拿 `.env` 里的长值（≥10 且非占位）撞待提交文本，命中只出键名与长度，⛔ 出值；
  B 用户数据路径：`butler/static/avatars/`·`data/roles/_archive/` 这类**内容即个人信息**的路径
    一旦出现在暂存面就红——⛔ 靠 `.gitignore` 那一行，因为 `git add -f` 与「先 add 后改 ignore」
    两条路都能绕过它（B 臂跑的是**索引里实际有什么**，那才是会被推出去的东西）。

校准三格（⛔ 只会点头的尺）：SYNTH 真值插一行必须咬、CLEAN 占位符必须放行、
用户数据路径必须咬而源码路径必须放行。列表一致性一格：本档的 B 臂名册与批172 那把形状尺的
`USERDATA_DIRS_MUST_BE_IGNORED` 必须同源（两把尺⛔ 各写一份，一份改了另一份就是摆设）。
"""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import unittest

TREE = pathlib.Path(__file__).resolve().parents[1]
GATE = TREE / "scripts" / "leak_gate.py"
B172 = TREE / "tests" / "test_audit_1010_batch172_gitignore_db_shape.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("leak_gate", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class GateFileTest(unittest.TestCase):
    def test_gate_script_exists(self):
        self.assertTrue(GATE.is_file(), f"泄露闸不在位：{GATE} ⇒ 提交那一刻没有任何东西在跑")

    def test_precommit_hook_is_shipped_and_wired(self):
        hook = TREE / "scripts" / "git-hooks" / "pre-commit"
        self.assertTrue(hook.is_file(), "钩子模板不在位 ⇒ 每个 clone 都得靠人记得装")
        text = hook.read_text(encoding="utf-8")
        self.assertIn("leak_gate.py", text, "钩子没接泄露闸")
        self.assertIn("exit 1", text, "钩子拦不住人（⛔ 非零退出＝⛔ 拦）")
        # 本机现读：`python3` 不在 PATH（只有 `python`）——写死一枚＝每次提交都被 127 挡住，
        # 而"挡住的理由"和"发现泄露"在用户眼里长得一模一样。
        for cand in ("python3", "python"):
            self.assertIn(cand, text, f"钩子没给解释器留退路（缺 {cand}）")

    def test_gates_sh_has_a_leak_leg(self):
        text = (TREE / "gates.sh").read_text(encoding="utf-8")
        self.assertIn("leak_gate.py", text,
                      "gates.sh 三道门里没有内容腿（现读 10-13：grep 凭据类关键词 0 命中）")


class ValueArmTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not GATE.is_file():
            raise unittest.SkipTest(f"闸不在位：{GATE}")
        cls.gate = _load_gate()

    def test_synthetic_value_is_caught_and_never_echoed(self):
        values = {"BARK_KEY": "SYNTHbarkkey0123456789AB",
                  "WEB_PASSWORD": "SYNTHwebpass0123456789ab"}
        problems = self.gate.value_problems(
            "services:\n  x:\n    environment:\n      - BARK_KEY=SYNTHbarkkey0123456789AB\n",
            "docker-compose.yml", values)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("BARK_KEY", problems[0])
        self.assertNotIn("SYNTHbark", "\n".join(problems), "闸把值本体打印出来＝自己造一枚泄露面")

    def test_placeholder_and_short_values_pass(self):
        values = {"BARK_KEY": "SYNTHbarkkey0123456789AB"}
        for line in ("      - BARK_KEY=${BARK_KEY}\n",
                     "      - BARK_KEY=changeme\n",
                     "      - BARK_KEY=your_bark_key_here\n"):
            self.assertEqual(self.gate.value_problems(line, "compose.yml", values), [], line)

    def test_short_env_values_are_declared_a_blind_spot(self):
        """短值（如 `WEB_PASSWORD=abc`）做子串必假阳——⛔ 撞，但必须**登记**成盲区。

        地址/型号类键（`MQTT_USER`·`MQTT_HOST`）另走「登记不拦」桶：把它们算进比对集
        是一把恒红尺（`butler/config.py` 的默认值本身就是它）。
        """
        long_vals, short, nonsecret = self.gate.split_env_values(
            {"MQTT_USER": "butler", "WEB_PASSWORD": "abc",
             "BARK_KEY": "SYNTHbarkkey0123456789AB"}, min_len=10)
        self.assertEqual(sorted(long_vals), ["BARK_KEY"], long_vals)
        self.assertEqual([k for k, _ in short], ["WEB_PASSWORD"], short)
        self.assertEqual(nonsecret, ["MQTT_USER"], nonsecret)

    def test_env_parsing_skips_comments_quotes_and_blanks(self):
        raw = ('# BARK_KEY=SYNTHbarkkey0123456789AB\n'
               'BARK_KEY="SYNTHbarkkey0123456789AB"\n'
               "EMPTY_KEY=\n"
               "WEB_TOKEN=SYNTHwebpass0123456789ab\n")
        got = self.gate.parse_env(raw)
        self.assertEqual(sorted(got), ["BARK_KEY", "WEB_TOKEN"], got)
        self.assertEqual(got["BARK_KEY"], "SYNTHbarkkey0123456789AB")
        long_vals, short, _ = self.gate.split_env_values(
            {"SHORT_KEY": "no", "PLACE_KEY": "changeme_now", **got}, min_len=10)
        self.assertEqual(sorted(long_vals), ["BARK_KEY", "WEB_TOKEN"], long_vals)
        self.assertEqual([k for k, _ in short], ["SHORT_KEY"], short)


class UserdataArmTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not GATE.is_file():
            raise unittest.SkipTest(f"闸不在位：{GATE}")
        cls.gate = _load_gate()

    def test_userdata_paths_bite_and_source_paths_pass(self):
        staged = ["butler/static/avatars/kevin.png",
                  "data/roles/_archive/caesar.json",
                  "butler/static/js/app.js",
                  "butler/roles/store.py",
                  "tests/test_audit_1010_batch172_gitignore_db_shape.py"]
        got = self.gate.userdata_problems(staged)
        self.assertEqual([g.split()[1].split("（")[0] for g in got],
                         ["butler/static/avatars/kevin.png", "data/roles/_archive/caesar.json"], got)

    def test_roster_matches_the_gitignore_shape_ruler(self):
        """两把尺⛔ 各写一份名册：闸与批172 的形状尺必须同源，否则一边改了另一边恒绿。"""
        import ast
        tree = ast.parse(B172.read_text(encoding="utf-8"))
        theirs = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", "") == "USERDATA_DIRS_MUST_BE_IGNORED" for t in node.targets):
                theirs = list(ast.literal_eval(node.value))
        self.assertIsNotNone(theirs, "批172 里找不到那枚名册 ⇒ 一致性格失去对照")
        self.assertEqual(sorted(set(theirs)), sorted(set(self.gate.USERDATA_PATHS)),
                         "闸与形状尺的名册分叉了")


class StagedScanTest(unittest.TestCase):
    """真跑一遍 `--staged`：⛔ 碰本仓索引，拿临时 git 仓库喂它。"""

    @classmethod
    def setUpClass(cls):
        if not GATE.is_file():
            raise unittest.SkipTest(f"闸不在位：{GATE}")
        if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
            raise unittest.SkipTest("NOT_MEASURED：这一面没有 git，机制腿跑不了")

    def test_cli_on_a_temp_repo_reports_the_planted_leak(self):
        import shutil
        import tempfile
        td = pathlib.Path(tempfile.mkdtemp(prefix="b208_gate_"))
        try:
            subprocess.run(["git", "init", "-q"], cwd=td, capture_output=True, check=True)
            (td / ".env").write_text("BARK_KEY=SYNTHbarkkey0123456789AB\n", encoding="utf-8")
            (td / "compose.yml").write_text("BARK_KEY=SYNTHbarkkey0123456789AB\n", encoding="utf-8")
            (td / "clean.txt").write_text("BARK_KEY=${BARK_KEY}\n", encoding="utf-8")
            subprocess.run(["git", "add", "compose.yml", "clean.txt"], cwd=td,
                           capture_output=True, check=True)
            out = subprocess.run(["python", str(GATE), "--staged", "--repo", str(td)],
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 1, f"该拦没拦 rc={out.returncode}\n{out.stdout}{out.stderr}")
            self.assertIn("BARK_KEY", out.stdout)
            self.assertNotIn("SYNTHbark", out.stdout, "输出里带了值本体")
            self.assertNotIn("clean.txt", out.stdout, "占位符那枚被误报")
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_cli_on_a_temp_repo_passes_a_clean_tree(self):
        import shutil
        import tempfile
        td = pathlib.Path(tempfile.mkdtemp(prefix="b208_gate_ok_"))
        try:
            subprocess.run(["git", "init", "-q"], cwd=td, capture_output=True, check=True)
            (td / ".env").write_text("BARK_KEY=SYNTHbarkkey0123456789AB\n", encoding="utf-8")
            (td / "app.py").write_text("TOKEN_ENV = 'BARK_KEY'  # 只出键名\n", encoding="utf-8")
            subprocess.run(["git", "add", "app.py"], cwd=td, capture_output=True, check=True)
            out = subprocess.run(["python", str(GATE), "--staged", "--repo", str(td)],
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, f"干净件被误拦\n{out.stdout}{out.stderr}")
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_missing_env_is_not_measured_not_green(self):
        import shutil
        import tempfile
        td = pathlib.Path(tempfile.mkdtemp(prefix="b208_gate_noenv_"))
        try:
            subprocess.run(["git", "init", "-q"], cwd=td, capture_output=True, check=True)
            (td / "app.py").write_text("x = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "app.py"], cwd=td, capture_output=True, check=True)
            out = subprocess.run(["python", str(GATE), "--staged", "--repo", str(td)],
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 2,
                             f"取不到 .env 时⛔ 判绿（rc 应为 2＝本腿未量）\n{out.stdout}{out.stderr}")
            self.assertIn("未量", out.stdout)
        finally:
            shutil.rmtree(td, ignore_errors=True)


class RangeArmTest(unittest.TestCase):
    """C 臂：`--range A..B` 撞**待推的每一笔 blob**。

    为什么 HEAD 两臂不够：涂销只改最新那一枚，中间 commit 的 blob 一个字节没动，
    而 push 推的是**整条 range**——10-13 现读：本仓 HEAD 跑 --all＝0 命中，
    同一条尺跑 `origin/main..HEAD` 逐枚＝12 枚里 10 枚带真值。⛔ 这一臂的尺等于没有。
    """

    def test_range_helper_reports_which_commit_carries_the_value(self):
        import shutil
        import subprocess
        import tempfile
        td = pathlib.Path(tempfile.mkdtemp(prefix="b208_range_"))
        try:
            gate = _load_gate()
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=td, capture_output=True, check=True)
            subprocess.run(["git", "config", "user.email", "t@t"], cwd=td, capture_output=True, check=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=td, capture_output=True, check=True)
            (td / ".env").write_text("BARK_KEY=SYNTHbarkkey0123456789AB\n", encoding="utf-8")
            (td / "readme.md").write_text("起点\n", encoding="utf-8")
            subprocess.run(["git", "add", "readme.md"], cwd=td, capture_output=True, check=True)
            subprocess.run(["git", "commit", "-qm", "起点"], cwd=td, capture_output=True, check=True)
            base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=td,
                                  capture_output=True, text=True).stdout.strip()
            (td / "note.md").write_text("key: SYNTHbarkkey0123456789AB\n", encoding="utf-8")
            subprocess.run(["git", "add", "note.md"], cwd=td, capture_output=True, check=True)
            subprocess.run(["git", "commit", "-qm", "带真值那笔"], cwd=td, capture_output=True, check=True)
            (td / "note.md").write_text("key: 已涂销\n", encoding="utf-8")
            subprocess.run(["git", "commit", "-qam", "涂销那笔"], cwd=td, capture_output=True, check=True)
            out = subprocess.run([sys.executable, str(GATE), "--range", f"{base}..HEAD",
                                  "--repo", str(td)],
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 1,
                             f"HEAD 干净但待推面带真值 ⇒ 该拦没拦 rc={out.returncode}\n{out.stdout}")
            self.assertIn("BARK_KEY", out.stdout)
            self.assertNotIn("SYNTHbark", out.stdout)
        finally:
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
