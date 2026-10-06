#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批 13（触发冷却 JSON→SQLite，DCD 20261002 跟办 2）变异探针。

干什么：把护栏逐条拆掉，看验收是不是真的会红。活下来的变异体＝缺腿，
必须补一条**先红后绿**的用例，⛔ 把「没被测到」记成「测过了」。

安全：
- 原件只在内存里，⛔ .bak / ⛔ 临时副本文件（NAS 规范禁 .bak 堆积）。
- 每个变异体跑完立刻按字节还原并比对 md5；还原不上就 SCREAM 并非零退出。
- 起跑前先验三枚文件的基线 md5，对不上就直接拒跑（树不是我以为的那棵树）。
- 测试子进程带 `-B`，⛔ 把变异体版本的 .pyc 写进 __pycache__。

它测不到什么：只测「这批验收咬不咬得住这条改动」。它⛔ 证明现网真库上
建表/迁移会成功（那只有一条授权的 `docker restart` 能证），也⛔ 覆盖
「新增一条 JSON 写入」这类需要造码的相反方向（下面 JSON_ONLY 一条除外，
它就是把旧写盘复活）。
"""
import argparse
import hashlib
import re
import subprocess
import sys

DB = "butler/store/db.py"
REPO = "butler/store/repo.py"
ENG = "butler/triggers/engine.py"

BASELINE = {
    DB: "5754194f3548a795d04fdb89c04e042d",
    REPO: "84013fcc7013c7c09258fff6f4943537",
    ENG: "307424a3555ecffb49379dfd0973b9b7",
}

SUITE = [
    "tests.test_audit_1002_batch13_cooldown",
    "tests.test_write_failures",
    "tests.test_trigger_evaluation_ledger",
]

LOAD_BLOCK = (
    '    c = get_conn()\n'
    '    cutoff = (time.time() if now is None else now) - retention_s\n'
    '    return {r["cd_key"]: float(r["last_fired"])\n'
    '            for r in c.execute("SELECT cd_key, last_fired FROM trigger_cooldowns"\n'
    '                               " WHERE last_fired>=?", (cutoff,))}\n'
)
LOAD_BLOCK_CUT_OFF = (
    '    c = get_conn()\n'
    '    cutoff = -1.0  # MUTANT: retention window ignored\n'
    '    return {r["cd_key"]: float(r["last_fired"])\n'
    '            for r in c.execute("SELECT cd_key, last_fired FROM trigger_cooldowns"\n'
    '                               " WHERE last_fired>=?", (cutoff,))}\n'
)
LOAD_BLOCK_SWALLOW = (
    '    try:\n'
    '        c = get_conn()\n'
    '        cutoff = (time.time() if now is None else now) - retention_s\n'
    '        return {r["cd_key"]: float(r["last_fired"])\n'
    '                for r in c.execute("SELECT cd_key, last_fired FROM trigger_cooldowns"\n'
    '                                   " WHERE last_fired>=?", (cutoff,))}\n'
    '    except Exception:\n'
    '        return {}  # MUTANT: read failure folded into "nobody is cooling down"\n'
)

MUTANTS = [
    {
        "name": "NO_TABLE_CREATED",
        "file": DB,
        "why": "拆掉启动路径上的建表调用 ⇒ 冷却表根本不存在",
        "old": "    _ensure_trigger_evaluations(c)\n    _ensure_trigger_cooldowns(c)\n",
        "new": "    _ensure_trigger_evaluations(c)\n",
    },
    {
        "name": "GUARD_IS_SILENT",
        "file": DB,
        "why": "把守护的 logger.warning 降成 debug ⇒ 建表失败没人知道（旧假绿形状）",
        "old": '        logger.warning("trigger_cooldowns init failed: %s: %s"',
        "new": '        logger.debug("trigger_cooldowns init failed: %s: %s"',
    },
    {
        "name": "EXTRA_COLUMN",
        "file": DB,
        "why": "DDL 多塞一列 ⇒ 裁定过的列形状（两列）必须被钉住",
        "old": '    " last_fired REAL NOT NULL)"',
        "new": '    " last_fired REAL NOT NULL, junk TEXT)"',
    },
    {
        "name": "LOAD_SWALLOWS_TO_EMPTY",
        "file": REPO,
        "why": "读失败折成空 dict ⇒ 上游读成「全员没冷却」，且台账不会出现",
        "old": LOAD_BLOCK,
        "new": LOAD_BLOCK_SWALLOW,
    },
    {
        "name": "LOAD_IGNORES_WINDOW",
        "file": REPO,
        "why": "过窗条目照样返回 ⇒ 退役窗口形同不存在",
        "old": LOAD_BLOCK,
        "new": LOAD_BLOCK_CUT_OFF,
    },
    {
        "name": "SAVE_NO_PRUNE",
        "file": REPO,
        "why": "去掉写同批的过期清理 ⇒ 变成只增账",
        "old": '    c.execute("DELETE FROM trigger_cooldowns WHERE last_fired<?", (cutoff,))\n',
        "new": '    # MUTANT: prune dropped\n',
    },
    {
        "name": "SNAPSHOT_NOT_MIGRATED",
        "file": ENG,
        "why": "整条迁移腿不调 ⇒ 旧 JSON 里的冷却直接蒸发",
        "old": "        if snapshot:\n            self._import_legacy_snapshot(snapshot)\n",
        "new": "        if snapshot:\n            pass  # MUTANT: migration skipped\n",
    },
    {
        "name": "DELETE_WITHOUT_VERIFY",
        "file": ENG,
        "why": "跳过逐键回读校验 ⇒ 验证不过也敢删用户的活数据文件",
        "old": "        missing = [k for k, v in fresh.items() if back.get(k) != v]\n        if missing:\n",
        "new": "        missing = []  # MUTANT: verification bypassed\n        if missing:\n",
    },
    {
        "name": "STALE_SNAPSHOT_WINS",
        "file": ENG,
        "why": "同键⛔ 取较新 ⇒ 旧快照值覆盖库里更新的冷却＝提前放行",
        "old": "        for k, v in fresh.items():\n            if v > self._last_fired.get(k, 0):\n                self._last_fired[k] = v\n",
        "new": "        for k, v in fresh.items():\n            self._last_fired[k] = v  # MUTANT: overwrite unconditionally\n",
    },
    {
        "name": "SNAPSHOT_HANDLE_LEAK",
        "file": ENG,
        "why": "退回批 13 修掉之前的裸 open ⇒ 句柄泄漏腿必须咬（这条有已知红→绿的来历）",
        "old": '            with open(path, encoding="utf-8") as fh:\n                data = json.load(fh)\n',
        "new": '            fh = open(path, encoding="utf-8")\n            data = json.load(fh)\n',
    },
    {
        "name": "SAVE_NEVER_PERSISTS",
        "file": ENG,
        "why": "退化成旧「只在内存」形状 ⇒ 触发后库里没有行",
        "old": '        try:\n            repo.save_cooldowns(self._last_fired)\n'
               '        except Exception as e:\n'
               '            logger.warning("save cooldowns failed: %s: %s", type(e).__name__, str(e)[:200])\n',
        "new": '        try:\n            pass  # MUTANT: in-memory only\n'
               '        except Exception as e:\n'
               '            logger.warning("save cooldowns failed: %s: %s", type(e).__name__, str(e)[:200])\n',
    },
    {
        "name": "LOAD_FAIL_UNLEDGED",
        "file": ENG,
        "why": "读失败不进 db_write_failures ⇒ 自愈台账看不见这条",
        "old": '            write_failures.record("triggers/engine.load_cooldowns",\n'
               '                                  "%s: %s" % (type(e).__name__, str(e)[:200]),\n'
               '                                  table="trigger_cooldowns")\n',
        "new": "            pass  # MUTANT: not ledged\n",
    },
    {
        "name": "SAVE_FAIL_UNLEDGED",
        "file": ENG,
        "why": "写失败不进台账 ⇒ 冷却丢史无声",
        "old": '            write_failures.record("triggers/engine.save_cooldowns",\n'
               '                                  "%s: %s (cooldowns are in-memory only until this write works)"\n'
               '                                  % (type(e).__name__, str(e)[:200]),\n'
               '                                  table="trigger_cooldowns")\n',
        "new": "            pass  # MUTANT: not ledged\n",
    },
    {
        "name": "IMPORT_FAIL_UNLEDGED",
        "file": ENG,
        "why": "快照搬不进库不进台账 ⇒ 退役失败没人追",
        "old": '            write_failures.record("triggers/engine.import_legacy_snapshot",\n'
               '                                  "%s: %s" % (type(e).__name__, str(e)[:200]),\n'
               '                                  table="trigger_cooldowns")\n',
        "new": "            pass  # MUTANT: not ledged\n",
    },
]

FAIL_RE = re.compile(r"^(FAIL|ERROR): (\S+).*", re.M)


def md5(path):
    with open(path, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


def run_suite(timeout):
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "-f"] + SUITE,
        capture_output=True, text=True, timeout=timeout,
    )
    names = sorted({m.group(2) for m in FAIL_RE.finditer(proc.stdout + proc.stderr)})
    return proc.returncode, names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="只跑某个变异体（名字见 --list）")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    if args.list:
        for m in MUTANTS:
            print("%-26s %s" % (m["name"], m["why"]))
        return 0

    for path, want in BASELINE.items():
        got = md5(path)
        if got != want:
            print("BASELINE_MISMATCH|%s|want=%s|got=%s" % (path, want, got))
            print("⇒ 这棵树不是我落码时那棵树，拒跑。先重新对基线再谈变异。")
            return 2

    todo = [m for m in MUTANTS if not args.only or m["name"] == args.only]
    if args.only and not todo:
        print("UNKNOWN_MUTANT|%s" % args.only)
        return 2

    print("SUITE|" + ",".join(SUITE))
    print("MUTANTS|" + str(len(todo)))
    print("BASELINE|" + ";".join("%s=%s" % (p, BASELINE[p]) for p in sorted(BASELINE)))

    survivors, accidents = [], []
    for m in todo:
        with open(m["file"], encoding="utf-8") as fh:
            text = fh.read()
        n = text.count(m["old"])
        if n != 1:
            print("ANCHOR_%s|%s|count=%d" % ("AMBIGUOUS" if n > 1 else "MISS", m["name"], n))
            accidents.append(m["name"])
            continue
        try:
            with open(m["file"], "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text.replace(m["old"], m["new"]))
            rc, names = run_suite(args.timeout)
        except subprocess.TimeoutExpired:
            print("TIMEOUT|%s" % m["name"])
            accidents.append(m["name"])
            rc, names = -1, ["<timeout>"]
        finally:
            with open(m["file"], "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        back = md5(m["file"])
        if back != BASELINE[m["file"]]:
            print("RESTORE_FAILED|%s|%s|%s" % (m["name"], m["file"], back))
            return 3
        verdict = "BITTEN" if rc != 0 else "SURVIVED"
        print("%s|%s|rc=%d|legs=%s" % (verdict, m["name"], rc, ",".join(names) or "-"))
        if rc == 0:
            survivors.append(m["name"])

    print("SUMMARY|run=%d|bitten=%d|survived=%d|anchor_or_timeout=%d"
          % (len(todo), len(todo) - len(survivors) - len(accidents), len(survivors), len(accidents)))
    print("RESTORED_OK|" + ";".join("%s=%s" % (p, BASELINE[p]) for p in sorted(BASELINE)))
    if survivors:
        print("⇒ 存活变异体＝缺验收腿，逐条补「先红后绿」再谈收口：" + ",".join(survivors))
    return 1 if (survivors or accidents) else 0


if __name__ == "__main__":
    sys.exit(main())
