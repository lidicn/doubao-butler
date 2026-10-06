r"""批16 变异探针：把三枚修法各自退回缺陷态，看 14 条验收腿是否**逐枚点名咬住**。

规矩（批15 的两条教训已经落在这儿）：
  1. 「等价变异」标 CONTROL_ 并且**必须存活**——它测的是腿的过度窄，不是腿的缺口；⛔ 计入 bites。
  2. 出口判据用**推导值**：real = len(MUTANTS) - control_total，bitten 必须等于 real，
     control_survived 必须等于 control_total 且 >0；⛔ 打印常量当读数（批15 我干过一次）。
  3. 注入按**整行 + 该文件自己的行尾**拼字节（bark/memory_agent 全 CRLF、aliases 全 LF），
     锚点命中数≠1 直接 rc=3 退出，不做半吊子替换。
  4. 每次篡改后**逐字节还原**并验 md5 回到 BASELINE；还原失败＝污染工作树，rc 非 0 点名。
     ⚠ 还原会把 mtime 刷到探针运行时刻（内容没变）⇒ mtime 此后只能证「盘比进程新」，⛔ 当落码时刻用。

它测不到什么：⛔ 现网（腿跑在仓源码上，容器里那份要等授权 restart）；⛔ 跨文件契约（bark 的修法
与 app.py:651-663 那个 5 分钟 job 的配合）；变异集⛔ 穷尽（只覆盖"退回缺陷态"这一族语义邻近的改动）。

用法：python3 -B scripts/audit_1002/mutate_b16_1002.py
"""
import hashlib
import re
import subprocess
import sys

SUITE = ["tests.test_audit_1002_batch16_order_and_clear"]
EXPECT_RAN = 14  # 批16 GREEN 那把尺的原样读数（Ran 14 tests / OK）

BASELINE = {
    "butler/integrations/bark.py": "33805cd879c124f904003638b13982d7",
    "butler/integrations/memory_agent.py": "22e90f1bd83ce6276085e5952ddc7a30",
    "butler/core/aliases.py": "ba541ed3d69aa7479629e07592d28009",
    "tests/test_audit_1002_batch16_order_and_clear.py": "6d17003edc022b5b2f6c9fbeaba17aa8",
}

BARK = "butler/integrations/bark.py"
MA = "butler/integrations/memory_agent.py"
ALI = "butler/core/aliases.py"

MUTANTS = [
    ("BARK_CLEAR_BACK", BARK,
     ["        if flushed_groups:",
      "            self._merge_cache = [i for i in self._merge_cache",
      "                                 if (i.get(\"group\") or \"default\") not in flushed_groups]"],
     ["        self._merge_cache.clear()"]),

    ("BARK_ENCRYPT_URL_BACK", BARK,
     ["                        r = await c.post(self._encrypt_url, data={"],
     ["                        r = await c.post(self._push_url, data={"]),

    ("BARK_OK_ALWAYS_TRUE", BARK,
     ["                if ok:", "                    sent_count += 1"],
     ["                if True:", "                    sent_count += 1"]),

    ("BARK_KEEP_NEVER_APPENDS", BARK,
     ["                    flushed_groups.append(group_name)"],
     ["                    pass"]),

    ("MA_SLICE_BACK", MA,
     ["        for m in alive[-limit:]:"],
     ["        for m in memories[-limit:]:"]),

    ("MA_NO_FILTER", MA,
     ["        alive = [m for m in memories",
      "                 if not (isinstance(m, dict) and m.get(\"state\") == \"revoked\")]"],
     ["        alive = list(memories)"]),

    ("ALIAS_WRITE_IN_PLACE_BACK", ALI,
     ["        tmp = ALIAS_FILE.with_name(ALIAS_FILE.name + \".tmp\")",
      "        tmp.write_text(json.dumps(self.aliases, ensure_ascii=False, indent=2),"
      " encoding=\"utf-8\")",
      "        os.replace(tmp, ALIAS_FILE)"],
     ["        ALIAS_FILE.write_text(json.dumps(self.aliases, ensure_ascii=False, indent=2),"
      " encoding=\"utf-8\")"]),

    ("ALIAS_REPLACE_BEFORE_WRITE", ALI,
     ["        tmp.write_text(json.dumps(self.aliases, ensure_ascii=False, indent=2),"
      " encoding=\"utf-8\")",
      "        os.replace(tmp, ALIAS_FILE)"],
     ["        os.replace(tmp, ALIAS_FILE)",
      "        tmp.write_text(json.dumps(self.aliases, ensure_ascii=False, indent=2),"
      " encoding=\"utf-8\")"]),

    # ---- 等价对照：语义不变，必须存活（＝腿没过度窄）----
    ("CONTROL_EQUIV_WARNING_TEXT", BARK,
     ["                logger.warning(\"bark merged flush failed: %s (group=%s, %d 条留待下轮)\","
      " e, group_name, count)"],
     ["                logger.warning(\"bark merged flush failed: %s\", e)"]),

    ("CONTROL_EQUIV_DROP_ANNOTATION", BARK,
     ["        flushed_groups: list[str] = []"],
     ["        flushed_groups = []"]),
]

FAIL_RE = re.compile(r"^(FAIL|ERROR): (\S+)", re.M)
RAN_RE = re.compile(r"^Ran (\d+) tests", re.M)


def md5_of(path):
    return hashlib.md5(open(path, "rb").read()).hexdigest()


def eol_of(raw):
    head = raw.split(b"\n", 1)[0]
    return b"\r\n" if head.endswith(b"\r") else b"\n"


def as_bytes(lines, eol):
    return eol.join(l.encode("utf-8") for l in lines)


def run_suite():
    p = subprocess.run([sys.executable, "-m", "unittest", *SUITE, "-v"],
                       capture_output=True, text=True)
    blob = p.stdout + p.stderr
    names = sorted({n for _kind, n in FAIL_RE.findall(blob)})
    ran = RAN_RE.search(blob)
    return p.returncode, names, int(ran.group(1)) if ran else -1


def main():
    for rel, want in BASELINE.items():
        got = md5_of(rel)
        if got != want:
            print("BASELINE_DRIFT|%s|got=%s|want=%s" % (rel, got, want))
            return 2
    control_total = sum(1 for m in MUTANTS if m[0].startswith("CONTROL"))
    real_total = len(MUTANTS) - control_total
    survivors, bitten_total = [], 0
    control_survived, bad_restore = 0, []
    for name, rel, old, new in MUTANTS:
        raw = open(rel, "rb").read()
        eol = eol_of(raw)
        old_b, new_b = as_bytes(old, eol), as_bytes(new, eol)
        hits = raw.count(old_b)
        if hits != 1:
            print("MUTANT_ANCHOR|%s|%s|hits=%d" % (name, rel, hits))
            return 3
        tampered = raw.replace(old_b, new_b, 1)
        if tampered == raw:
            print("MUTANT_NOOP|%s" % name)
            return 4
        open(rel, "wb").write(tampered)
        rc, names, ran = run_suite()
        open(rel, "wb").write(raw)
        if md5_of(rel) != BASELINE[rel]:
            bad_restore.append(rel)
        if name.startswith("CONTROL"):
            if rc == 0 and ran == EXPECT_RAN:
                control_survived += 1
                print("%s|CONTROL_SURVIVED(ok)|rc=%d|ran=%d" % (name, rc, ran))
            else:
                print("%s|CONTROL_BITTEN(红)|rc=%d|ran=%d|legs=%s" % (name, rc, ran, names or "无"))
        else:
            if rc != 0 and names:
                bitten_total += 1
                print("%s|BITTEN|legs=%d|%s" % (name, len(names), ",".join(names)))
            else:
                survivors.append(name)
                print("%s|SURVIVED(红)|rc=%d|ran=%d|want_ran=%d" % (name, rc, ran, EXPECT_RAN))
    print("CENSUS|mutants=%d bitten=%d controls=%d survived=%s|restore_bad=%s"
          % (real_total, bitten_total, control_total, survivors or "无", bad_restore or "无"))
    ok = (not survivors and not bad_restore and bitten_total == real_total
          and control_survived == control_total > 0)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
