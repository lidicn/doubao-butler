#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""推送前泄露闸（批208，2026-10-13）。

出资人那一问：「为什么总会有 key 暴露到 github 上呢？」——现读的两个病根：
`.git/hooks/` 里除 `.sample` 外一枚钩子都没有；`gates.sh` 三道门（AST／冒烟／契约）里
`grep -nE "leak|secret|credential|凭据"` 0 命中。⇒ 有没有泄露，全靠某个人记得去跑一把尺。

两臂：
  A 值比对：拿 `.env` 的长值撞待提交文本。⛔ 形状猜（形状尺在本仓历史上假阳 1845 次），
    ⛔ 打印值本体（闸自己造泄露面＝条目九十三那条老病）。
  B 用户数据路径：头像与归档名册这类**内容即个人信息**的路径出现在索引里就红。
    这一臂⛔ 依赖 `.gitignore`——`git add -f` 与「先 add 再改 ignore」两条路都绕得过它，
    要看的是**索引里实际有什么**，那才是会被推出去的东西。

退出码：0 干净 / 1 命中（拦） / 2 本腿未量（取不到 .env 或取不到 git ⇒ ⛔ 当绿）。
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

# 与 tests/test_audit_1010_batch172_gitignore_db_shape.py 的 USERDATA_DIRS_MUST_BE_IGNORED 同源，
# 一致性格由那把尺盯住（两把尺各写一份＝改一边另一边恒绿）。
USERDATA_PATHS = (
    "butler/static/avatars/",
    "data/roles/_archive/",
)
# 短值做子串匹配必假阳（`MQTT_USER=butler` 一次撞出 1845 处，把真信号埋掉）：
# 长值精确撞，短值只登记成盲区表（键名＋长度，⛔ 值）。
MIN_VALUE_LEN = 10
PLACEHOLDER = ("changeme", "change_me", "your-", "your_", "placeholder", "<", "${",
               "replace", "example", "none", "false", "true")
# 只拦**键名带凭据语义**的那批。地址/型号键（`MQTT_HOST`·`HA_URL`·`DOUBAO_MODEL`）的默认值
# 就是它自己，`butler/config.py` 里现读几十处——把它们算进比对集是一把恒红尺＝没有闸
# （判例：恒红与恒绿同病，都不改变真文件读数）。它们单列成「登记不拦」桶，读数里看得见。
SECRET_KEY_RE = re.compile(r"(?i)(token|password|passwd|secret|api_?key|credential|signature"
                           r"|\biv\b|\bsig\b|_key$|^key_|access_|auth|cert|private)")


def parse_env(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        v = v.strip().strip('"').strip("'")
        if v:
            out[k.strip()] = v
    return out


def split_env_values(values: dict[str, str], min_len: int = MIN_VALUE_LEN):
    """三分：`long`（撞）／`short`（凭据语义但值太短，撞必假阳 ⇒ 盲区）／`nonsecret`（登记不拦）。"""
    long_vals: dict[str, str] = {}
    short: list[tuple[str, int]] = []
    nonsecret: list[str] = []
    for k, v in values.items():
        if not SECRET_KEY_RE.search(k):
            nonsecret.append(k)
            continue
        if any(p in v.lower() for p in PLACEHOLDER):
            continue
        if len(v) < min_len:
            short.append((k, len(v)))
            continue
        long_vals[k] = v
    return long_vals, short, nonsecret


def value_problems(text: str, source: str, values: dict[str, str], label: str | None = None) -> list[str]:
    """`label` 给单行调用方用（CLI 从 git grep 拿的是**文件内真实行号**，⛔ 再挂一个 1）。"""
    problems = []
    for no, line in enumerate(text.splitlines(), 1):
        for k, v in values.items():
            if v in line:
                problems.append(f"A-LEAK key={k} len={len(v)} file={label or f'{source}:{no}'}")
    return problems


def userdata_problems(paths: list[str]) -> list[str]:
    return [f"B-USERDATA {p}（用户数据路径进了索引＝会被推出去）"
            for p in paths if p.startswith(USERDATA_PATHS)]


def _git(repo: pathlib.Path, args: list[str]) -> str:
    return subprocess.run(["git", "-c", "core.quotePath=false"] + args,
                          cwd=repo, capture_output=True, text=True,
                          errors="replace").stdout


def _candidate_lines(repo: pathlib.Path, source: str,
                     values: dict[str, str]) -> list[tuple[str, str, str]]:
    """`git grep -n -F -f <值表>` 一次取回**可能命中**的行，⛔ 逐文件起子进程。

    输出形如 `[rev:]path:lineno:content`——先剥已知的 rev 前缀，再按前两个冒号切
    （content 里当然有冒号，⛔ 用 partition 全切）。值表只活在临时文件里（0600，用完即删），
    ⛔ 落进仓库目录。
    """
    import os
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix="leakgate_", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(sorted(set(values.values()))))
        out = subprocess.run(["git", "-c", "core.quotePath=false", "grep", "-n", "-I", "-F",
                              "-f", tmp, source],
                             cwd=repo, capture_output=True, text=True, errors="replace").stdout
    finally:
        os.unlink(tmp)
    prefix = source + ":"
    hits = []
    for line in out.splitlines():
        if line.startswith(prefix):
            line = line[len(prefix):]
        path, _, rest = line.partition(":")
        lineno, _, content = rest.partition(":")
        hits.append((path, lineno, content))
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="推送前泄露闸（值比对＋用户数据路径两臂）")
    ap.add_argument("--staged", action="store_true", help="扫索引里的暂存件（钩子用这条）")
    ap.add_argument("--all", action="store_true", help="扫 HEAD 的整份名册（gates.sh 用这条）")
    ap.add_argument("--repo", default=".", help="仓库根（默认当前目录）")
    ap.add_argument("--min-len", type=int, default=MIN_VALUE_LEN)
    args = ap.parse_args(argv)

    repo = pathlib.Path(args.repo).resolve()
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        print("[GATE-UNTRUSTWORTHY] 取不到 git ⇒ 本腿未量，⛔ 记成绿")
        return 2
    env_path = repo / ".env"
    if not env_path.exists():
        print("[GATE-UNTRUSTWORTHY] 取不到 .env ⇒ 值比对腿未量，⛔ 记成绿")
        return 2
    if not (args.staged or args.all):
        print("[USAGE] 要选一条腿：--staged（索引）或 --all（HEAD 全量）")
        return 2

    values, short, nonsecret = split_env_values(
        parse_env(env_path.read_text(encoding="utf-8", errors="ignore")), min_len=args.min_len)
    print(f"[denominator] .env 入比对集 {len(values)} 枚（凭据语义键名＋长度≥{args.min_len} 且非占位）")
    print(f"[blind-spot] 凭据语义但值短（子串必假阳）{len(short)} 枚：{sorted(k for k, _ in short)}")
    print(f"[登记不拦] 地址/型号类键 {len(nonsecret)} 枚：{sorted(nonsecret)}")

    listing = ("diff", "--cached", "--name-only") if args.staged else ("ls-tree", "-r", "--name-only", "HEAD")
    paths = [p for p in _git(repo, list(listing)).splitlines() if p.strip()]
    problems = userdata_problems(paths)
    print(f"[scope] 待检文件 {len(paths)} 枚（{'暂存索引' if args.staged else 'HEAD 全量'}）")

    source = "--cached" if args.staged else "HEAD"
    for path, lineno, line in _candidate_lines(repo, source, values):
        problems += value_problems(line, path, values, label=f"{path}:{lineno}")

    for line in problems:
        print("  " + line)
    print("[verdict] " + (f"⛔ 命中 {len(problems)} 处" if problems
                          else "两臂 0 命中（值比对＋用户数据路径）"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
