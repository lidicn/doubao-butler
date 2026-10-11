"""批198（WebUI 自更新·宿主腿）：`scripts/host_check_update.sh` + `scripts/host_update.sh` 的活体门。

为什么要活体跑（⛔ 只读形状）：这两枚脚本有 `git reset --hard` 和 `docker restart` 两条不可逆腿，
写在纸面上「看着像」与「真会拒」是两回事。本档在临时目录里造两枚合成 git 仓（上游＝发布源、
部署＝REPO_DIR），真跑脚本，真读它落的预览件与结果件。

owner 2026-10-11 的三条裁定在这里各有一格：
  · 发布源＝现在的公开 GitHub 仓 ⇒ 脚本按 `UPDATE_REPO_URL` 取码（⛔ 只认 origin；合成仓用本地路径当 URL 验这一条）
  · 只有版本 tag 才自动重启 ⇒ `main` 落码⛔ 重启、`v1.2.3` 落码并重启（假 docker 记录器数次数）
  · 脏树拒执并回报「有未收编的本地改动」⇒ 结果件里必须是这串字面（与 butler/update_gate.py 同一把尺）

两侧校准（形状尺⛔ 只跑真文件）：合成「脏树闸条件被改成永假」的那份必须让码真的动（证明拦住它的是那枚闸，
不是别的巧合）；形状尺对 CLEAN 合成件放行、对三枚 SYNTH 合成件各咬一格。

盲点（自报）：
 1. 本档需要 `bash` + `git`（宿主面）。容器内⛔ git ⇒ 这档在容器里会**红**，⛔ 把它当成「代码有 bug」；
    现读拓扑见 `workorders/readings/1011/b197_topology_nas.txt`。
 2. `docker` 是假记录器（PATH 前置一个假 `docker`），验的是「脚本会不会去敲这条命令」，
    ⛔ 现网真的重启容器；`docker restart` 之后容器的行为⛔ 本档覆盖。
 3. git 行为与宿主版本相关（现读 2.39.5），本机 git 版本可能不同；`fetch/merge-base/describe` 三条是稳定接口。
 4. 合成仓⛔ 现网仓：预览件/结果件/备份 tar 全落临时目录，⛔ 碰 `/vol1`。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from butler import update_gate  # noqa: E402  纯判定档，⛔ 依赖 butler.app

CHECK_SH = REPO_ROOT / "scripts" / "host_check_update.sh"
UPDATE_SH = REPO_ROOT / "scripts" / "host_update.sh"

DIRTY_LITERAL = "有未收编的本地改动"
NON_FF_LITERAL = "目标不是当前码的后代"


def _force_rmtree(root):
    """Windows：`git` 把 pack 文件写成只读⇒ `rmtree(ignore_errors=True)` 静默留下合成仓（本盘垃圾）。
    先剥只读位再删；删不净就打出残留路径（⛔ 让「没清干净」和「清干净了」长得一样）。"""
    base = pathlib.Path(root)
    for p in (list(base.rglob("*")) + [base]):
        try:
            os.chmod(p, stat.S_IWRITE)
        except OSError:
            pass
    shutil.rmtree(root, ignore_errors=True)
    if base.exists():
        print("WARN 合成仓残留: %s" % root)

BASH = shutil.which("bash")
GIT = shutil.which("git")
CYGPATH = shutil.which("cygpath")

GITCONFIG = """[user]
\tname = b198 harness
\temail = b198@example.invalid
[core]
\tautocrlf = false
\tfilemode = false
\tsafecrlf = false
"""


def _msys(win_path: str) -> str:
    """Windows 路径 → bash 认的路径（Git Bash 下用 cygpath，其余原样返回）。"""
    if CYGPATH:
        out = subprocess.run([CYGPATH, "-u", str(win_path)], capture_output=True, text=True)
        if out.returncode == 0:
            return out.stdout.strip()
    return pathlib.PurePath(str(win_path)).as_posix()


class _Harness(unittest.TestCase):
    """合成双仓夹具：upstream＝发布源，deploy＝部署树（含 data/ 与 .env）。"""

    def setUp(self):
        missing = [n for n, v in (("bash", BASH), ("git", GIT)) if not v]
        if missing:
            self.fail("批198 需要 %s（本档活体跑宿主腿，⛔ 无依赖时静默放过）。现缺：%s"
                      % ("/".join(["bash", "git"]), ",".join(missing)))
        self.root = tempfile.mkdtemp(prefix="dbt_b198_")
        self.gitconfig = pathlib.Path(self.root) / "gitconfig"
        self.gitconfig.write_text(GITCONFIG, encoding="utf-8")
        self.upstream = os.path.join(self.root, "upstream")
        os.makedirs(self.upstream)
        self._git("init", "-b", "main", self.upstream)
        self._commit_file(self.upstream, "butler/stub.py", "print('v1')\n", "seed")
        self._commit_file(self.upstream, "docker-compose.yml", "services: {}\n", "compose")
        self._commit_file(self.upstream, "scripts/noop.py", "x = 1\n", "scripts dir")
        self._git("clone", self.upstream, os.path.join(self.root, "deploy"))
        self.deploy = os.path.join(self.root, "deploy")
        self.data = os.path.join(self.deploy, "data")
        os.makedirs(self.data, exist_ok=True)
        self.env_file = pathlib.Path(self.deploy) / ".env"
        self._write_env(True, "main")
        self.fakebin = os.path.join(self.root, "fakebin")
        os.makedirs(self.fakebin, exist_ok=True)
        self.docker_log = os.path.join(self.root, "docker_calls.txt")
        self._make_fake_docker()

    def tearDown(self):
        _force_rmtree(self.root)

    # ---- 夹具工具 ----
    def _git(self, *args):
        env = dict(os.environ)
        env["GIT_CONFIG_GLOBAL"] = str(self.gitconfig)
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        proc = subprocess.run([GIT, *args], capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, " ".join(args) + "\n" + proc.stderr)
        return proc.stdout.strip()

    def _commit_file(self, repo, rel, text, msg):
        path = pathlib.Path(repo) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self._git("-C", repo, "add", "-A")
        self._git("-C", repo, "commit", "-q", "-m", msg)
        return self._git("-C", repo, "rev-parse", "HEAD")

    def _write_env(self, enabled: bool, branch: str):
        self.env_file.write_text(
            "# 合成件：⛔ 任何真凭据\n"
            "BUTLER_WEB_PASSWORD=dummy4\n"
            "SELF_UPDATE_ENABLED=%s\n"
            "UPDATE_REPO_URL=%s\n"
            "UPDATE_BRANCH=%s\n"
            % ("true" if enabled else "false", _msys(self.upstream), branch),
            encoding="utf-8")

    def _make_fake_docker(self):
        fake = pathlib.Path(self.fakebin) / "docker"
        fake.write_text('#!/bin/sh\necho "$@" >> "%s"\n' % _msys(self.docker_log), encoding="utf-8")
        os.chmod(str(fake), 0o755)
        pathlib.Path(self.docker_log).write_text("", encoding="utf-8")

    def _docker_calls(self):
        return [l for l in pathlib.Path(self.docker_log).read_text(encoding="utf-8").splitlines() if l.strip()]

    def _run(self, script: pathlib.Path, mutate=None):
        """真跑一枚宿主脚本。mutate 用来造「闸被删掉」的负控副本。"""
        target = script
        if mutate is not None:
            target = pathlib.Path(self.root) / (script.name + ".mutated")
            target.write_text(mutate(script.read_text(encoding="utf-8")), encoding="utf-8")
        env = dict(os.environ)
        env.update({
            "DB_UPDATE_REPO_DIR": _msys(self.deploy),
            "DB_UPDATE_DATA_DIR": _msys(self.data),
            "DB_UPDATE_PY": _msys(sys.executable),
            "DB_UPDATE_CONTAINER": "dbt-fake-container",
            "GIT_CONFIG_GLOBAL": _msys(str(self.gitconfig)),
            "GIT_CONFIG_NOSYSTEM": "1",
            "PATH": os.pathsep.join([self.fakebin, env.get("PATH", "")]),
        })
        proc = subprocess.run([BASH, _msys(str(target))], capture_output=True, text=True, env=env,
                              cwd=self.root)
        return proc

    def _read_json(self, name):
        path = pathlib.Path(self.data) / name
        self.assertTrue(path.exists(), "%s 没落盘（rc/输出见调用处）" % name)
        return json.loads(path.read_text(encoding="utf-8"))

    def _head(self):
        return self._git("-C", self.deploy, "rev-parse", "HEAD")

    def _marker(self, ref):
        path = pathlib.Path(self.data) / ".update_request.json"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"ref": ref, "confirm_version": "1.0.0",
                                   "requested_at": "2026-10-11T00:00:00Z"}), encoding="utf-8")
        os.replace(tmp, path)


class CheckLegTests(_Harness):
    def test_01_clean_fast_forward_writes_preview(self):
        tip = self._commit_file(self.upstream, "butler/stub.py", "print('v2')\n", "advance")
        proc = self._run(CHECK_SH)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        prev = self._read_json(".update_available.json")
        self.assertTrue(prev["has_update"], prev)
        self.assertEqual(prev["remote_commit"], tip[:12])
        self.assertEqual(prev["behind"], 1, prev)
        self.assertEqual(prev["dirty"], 0, prev)
        self.assertTrue(prev["fast_forward"], prev)
        self.assertEqual(prev["ref"], "main", prev)
        self.assertEqual(len(prev["changelog"]), 1, prev)

    def test_02_dirty_tree_is_counted_not_hidden(self):
        self._commit_file(self.upstream, "butler/stub.py", "print('v2')\n", "advance")
        (pathlib.Path(self.deploy) / "butler" / "stub.py").write_text("print('本地热修')\n",
                                                                     encoding="utf-8")
        self._run(CHECK_SH)
        prev = self._read_json(".update_available.json")
        self.assertGreaterEqual(prev["dirty"], 1, prev)
        # 负控：未跟踪件（data/ 与 workorders 一族）⛔ 算进「未收编」
        (pathlib.Path(self.deploy) / "untracked_note.md").write_text("x\n", encoding="utf-8")
        self._run(CHECK_SH)
        self.assertEqual(self._read_json(".update_available.json")["dirty"], prev["dirty"],
                         "未跟踪件被数进 dirty⇒部署树会永远拒执")

    def test_03_non_fast_forward_marked_false(self):
        self._commit_file(self.deploy, "butler/local.py", "print('本地一笔未收编的历史')\n", "local")
        self._commit_file(self.upstream, "butler/stub.py", "print('v2')\n", "upstream diverges")
        self._run(CHECK_SH)
        prev = self._read_json(".update_available.json")
        self.assertFalse(prev["fast_forward"], prev)
        self.assertFalse(prev["has_update"], prev)

    def test_04_fetch_failure_leaves_readable_preview(self):
        self.env_file.write_text("UPDATE_REPO_URL=/nonexistent/nowhere.git\n"
                                 "UPDATE_BRANCH=main\nSELF_UPDATE_ENABLED=true\n", encoding="utf-8")
        proc = self._run(CHECK_SH)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        prev = self._read_json(".update_available.json")
        self.assertFalse(prev["has_update"], prev)
        self.assertIn("git fetch", prev["fetch_error"], prev)


class UpdateLegTests(_Harness):
    def test_05_no_marker_is_a_no_op(self):
        proc = self._run(UPDATE_SH)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse((pathlib.Path(self.data) / ".update_result.json").exists(),
                         "没有请求却写了结果＝状态件会被读成刚跑过一轮")
        self.assertEqual(self._docker_calls(), [])

    def test_06_dirty_tree_refuses_with_the_ruling_literal(self):
        self._commit_file(self.upstream, "butler/stub.py", "print('v2')\n", "advance")
        before = self._head()
        (pathlib.Path(self.deploy) / "butler" / "stub.py").write_text("print('热修')\n", encoding="utf-8")
        self._marker("main")
        proc = self._run(UPDATE_SH)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        res = self._read_json(".update_result.json")
        self.assertFalse(res["ok"], res)
        self.assertEqual(res["action"], "refuse", res)
        self.assertIn(DIRTY_LITERAL, res["error"], res)
        self.assertEqual(self._head(), before, "拒执却动了码＝拒执是假的")
        self.assertFalse((pathlib.Path(self.data) / ".update_request.json").exists())

    def test_07_dirty_gate_off_moves_the_code_negative_control(self):
        """负控：把那枚闸的条件改成永假⇒码真的动了。证明拦住 test_06 的是那枚闸，⛔ 别的巧合。"""
        self._commit_file(self.upstream, "butler/stub.py", "print('v2')\n", "advance")
        tip = self._git("-C", self.upstream, "rev-parse", "HEAD")
        before = self._head()
        (pathlib.Path(self.deploy) / "butler" / "stub.py").write_text("print('热修')\n", encoding="utf-8")
        self._marker("main")

        def disable_dirty_gate(src: str) -> str:
            """只把守卫的条件改成永假（⛔ 删整段：`then` 空体会让 bash 语法报错，那咬的是形制不是闸）。"""
            old = 'if [ "${DIRTY:-0}" -gt 0 ]; then'
            self.assertEqual(src.count(old), 1, "合成件里找不到那枚脏树闸⇒负控无效")
            return src.replace(old, "if [ 0 -gt 0 ]; then")

        proc = self._run(UPDATE_SH, mutate=disable_dirty_gate)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self._head(), tip, "删了闸还是不落码⇒test_06 的红另有原因")

    def test_08_non_fast_forward_refuses(self):
        self._commit_file(self.deploy, "butler/local.py", "print('本地历史')\n", "local")
        self._commit_file(self.upstream, "butler/stub.py", "print('v2')\n", "diverge")
        before = self._head()
        self._marker("main")
        proc = self._run(UPDATE_SH)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        res = self._read_json(".update_result.json")
        self.assertIn(NON_FF_LITERAL, res["error"], res)
        self.assertEqual(self._head(), before, "非快进被覆盖了")
        self.assertEqual(self._docker_calls(), [])

    def test_09_disabled_flag_refuses(self):
        self._commit_file(self.upstream, "butler/stub.py", "print('v2')\n", "advance")
        self._write_env(False, "main")
        before = self._head()
        self._marker("main")
        proc = self._run(UPDATE_SH)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        res = self._read_json(".update_result.json")
        self.assertIn("自更新未启用", res["error"], res)
        self.assertEqual(self._head(), before)

    def test_10_branch_update_lands_code_without_restart(self):
        tip = self._commit_file(self.upstream, "butler/stub.py", "print('v2')\n", "advance")
        self._marker("main")
        proc = self._run(UPDATE_SH)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        res = self._read_json(".update_result.json")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["action"], "apply_only", res)
        self.assertEqual(self._head(), tip, "只落码这条腿没落码")
        self.assertEqual(self._docker_calls(), [], "日常 main 更新⛔ 自动重启（owner 2026-10-11 裁定）")
        backups = list(pathlib.Path(self.data).glob("update-backup-*.tar.gz"))
        self.assertEqual(len(backups), 1, backups)

    def test_11_tag_update_lands_code_and_restarts_once(self):
        tip = self._commit_file(self.upstream, "butler/stub.py", "print('v2')\n", "tag me")
        self._git("-C", self.upstream, "tag", "v1.2.0")
        self._marker("v1.2.0")
        proc = self._run(UPDATE_SH)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        res = self._read_json(".update_result.json")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["action"], "apply_and_restart", res)
        self.assertEqual(self._head(), tip)
        calls = self._docker_calls()
        self.assertEqual(calls, ["restart dbt-fake-container"],
                         "版本 tag 应当恰好敲一次重启；空列表＝假 docker 没被 bash 认（见盲点 2）")

    def test_12_compile_failure_rolls_back(self):
        self._commit_file(self.upstream, "butler/broken.py", "def (:\n", "syntax bomb")
        before = self._head()
        self._marker("main")
        proc = self._run(UPDATE_SH)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        res = self._read_json(".update_result.json")
        self.assertFalse(res["ok"], res)
        self.assertIn("语法检查失败，已自动回滚", res["error"], res)
        self.assertEqual(self._head(), before, "语法没过却没回滚")
        self.assertEqual(self._docker_calls(), [])
        self.assertFalse((pathlib.Path(self.deploy) / "butler" / "broken.py").exists(),
                         "回滚只回了指针、坏文件还在树里")

    def test_13_already_latest_refuses(self):
        self._marker("main")
        proc = self._run(UPDATE_SH)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        res = self._read_json(".update_result.json")
        self.assertIn("已是最新", res["error"], res)


class ShapeAndParityTests(unittest.TestCase):
    """形状尺（两侧校准在前）＋容器/宿主同一把尺的字面核对。"""

    CLEAN = """#!/bin/bash
set -uo pipefail
if [ "${DIRTY:-0}" -gt 0 ]; then refuse_and_exit "有未收编的本地改动"; fi
git merge-base --is-ancestor A B || refuse_and_exit "目标不是当前码的后代"
git reset --hard B
if printf '%s' "$REF" | grep -qE '^v[0-9]+\\.[0-9]'; then docker restart c; fi
"""
    SYNTH_NO_DIRTY_GATE = """#!/bin/bash
set -uo pipefail
git merge-base --is-ancestor A B || refuse_and_exit "目标不是当前码的后代"
git reset --hard B
"""
    SYNTH_PRINTS_ENV = """#!/bin/bash
cat "$REPO_DIR/.env" >> "$LOG"
git status --porcelain
"""
    # 闸门齐全、只多一枚 push：这样「pushes-to-release-source」是该格的唯一新命中（⛔ 让一格校两条腿）
    SYNTH_PUSHES = """#!/bin/bash
set -uo pipefail
if [ "${DIRTY:-0}" -gt 0 ]; then refuse_and_exit "有未收编的本地改动"; fi
git merge-base --is-ancestor A B || refuse_and_exit "目标不是当前码的后代"
git reset --hard B
git push origin main
"""

    def _hits(self, src: str):
        bad = []
        reset_at = src.find("git reset --hard")
        if reset_at >= 0:
            dirty_at = src.find(DIRTY_LITERAL)
            ff_at = src.find("merge-base --is-ancestor")
            if dirty_at < 0 or dirty_at > reset_at:
                bad.append("dirty-gate-before-reset")
            if ff_at < 0 or ff_at > reset_at:
                bad.append("ff-gate-before-reset")
        if "docker restart" in src:
            test_at = src.find("^v[0-9]")
            if test_at < 0 or test_at > src.find("docker restart"):
                bad.append("tag-test-before-restart")
        if "cat \"$REPO_DIR/.env\"" in src or "grep .env" in src:
            bad.append("dumps-env")
        if "git push" in src:
            bad.append("pushes-to-release-source")
        return bad

    def test_14_ruler_calibrated_both_ways(self):
        self.assertEqual(self._hits(self.CLEAN), [], "CLEAN 合成件被咬⇒尺子过宽：%s" % self._hits(self.CLEAN))
        self.assertEqual(self._hits(self.SYNTH_NO_DIRTY_GATE), ["dirty-gate-before-reset"],
                         "SYNTH（没有脏树闸的 reset）没咬⇒尺子恒绿")
        self.assertEqual(sorted(self._hits(self.SYNTH_PRINTS_ENV)), ["dumps-env"], self._hits(self.SYNTH_PRINTS_ENV))
        self.assertEqual(sorted(self._hits(self.SYNTH_PUSHES)), ["pushes-to-release-source"],
                         self._hits(self.SYNTH_PUSHES))

    def test_15_real_scripts_pass_the_shape_ruler(self):
        for path in (CHECK_SH, UPDATE_SH):
            self.assertTrue(path.exists(), path)
            text = path.read_text(encoding="utf-8")
            self.assertEqual(self._hits(text), [], "%s：%s" % (path.name, self._hits(text)))
            self.assertIn("set -u", text, path.name)
            self.assertNotIn("\r\n", text, "%s 必须是 LF（宿主 bash 遇 CRLF 会把 shebang 读坏）" % path.name)

    def test_16_refusal_literals_are_one_ruler_two_faces(self):
        """容器预判（update_gate）与宿主执行（host_update.sh）⛔ 两套口径：同一串字面两边都得有。"""
        gate_src = (REPO_ROOT / "butler" / "update_gate.py").read_text(encoding="utf-8")
        host_src = UPDATE_SH.read_text(encoding="utf-8")
        for literal in (DIRTY_LITERAL, NON_FF_LITERAL, "自更新未启用"):
            self.assertIn(literal, gate_src, literal)
            self.assertIn(literal, host_src, "%s 只在容器侧⇒宿主拒执时状态件报的是另一套话" % literal)

    def test_17_tag_shape_parity_container_vs_host(self):
        """`v1.2` 算 tag、`main`/`ver1.2`/`2.1.0` 不算——容器正则与宿主 grep 对同一批样本必须同判。"""
        samples = ["main", "latest", "v1.2.0", "v2.1", "ver1.2", "2.1.0", ""]
        host_lines = "; ".join('printf "%%s\\n" "$(printf %%s \'%s\' | grep -qE \'^v[0-9]+\\.[0-9]\' '
                               '&& echo tag || echo nontag)"' % s.replace("'", "") for s in samples)
        proc = subprocess.run([BASH, "-c", host_lines], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = [l.strip() for l in proc.stdout.splitlines()]
        want = ["tag" if update_gate.is_release_ref(s) else "nontag" for s in samples]
        self.assertEqual(got, want, "样本=%s 宿主=%s 容器=%s" % (samples, got, want))


if __name__ == "__main__":
    unittest.main()
