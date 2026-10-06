r"""独立复核两份子座位交付：引用锚点逐条点开 + 零命中声称复跑 + 集合相等核对。

只用打印行号的尺；所有 PASS/RED 都附正则原文。
"""
import re
import sys
import collections

LEDGER = sys.argv[1] if len(sys.argv) > 1 else "workorders/readings/1003/ledger_head_1003.md"
C_FILE = "scripts/audit_1002/_reconcile_c_1002.md"
X_FILE = "scripts/audit_1002/_roster_extra_1002.md"
X_TSV = "scripts/audit_1002/_roster_extra_1002.tsv"
TSV = "scripts/audit_1002/roster_reconcile_1002.tsv"
MERGED = "doc/审计报告/_缺陷汇总_供审阅.md"
REPORTS = {
    "D3": "doc/审计报告/doubao-butler-第三轮审计-动态实验与链路追踪.md",
    "RT2": "doc/审计报告/doubao-butler-第二轮审计-运行时验证与测试实证.md",
    "R5": "doc/审计报告/doubao-butler-第五轮审计报告.md",
    "R4": "doc/审计报告/doubao-butler-第四轮审计报告.md",
}

QUOTE_RE = re.compile(r"`?:(\d+)`?\s*「([^」]+)」")
HEAD_RE = re.compile(r"^### C\|(\d+)\|(.+)$", re.M)
VERD_RE = re.compile(r"^- 判定：(COVERED|PARTIAL|NOT_IN_LEDGER)", re.M)


def load(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


def main():
    ledger = load(LEDGER)
    ctext = open(C_FILE, encoding="utf-8").read()
    out = []

    # ---- 1. ledger 基底对账
    out.append("LEDGER_LINES|%d" % len(ledger))

    # ---- 2. C 文件头部行号集合 == TSV bucket C 集合
    tsv_rows = [ln.split("\t") for ln in load(TSV)]
    hdr = tsv_rows[0]
    ib = hdr.index("bucket")
    iu = hdr.index("num")
    tsv_c = sorted(int(r[iu]) for r in tsv_rows[1:] if len(r) > ib and r[ib] == "C")
    c_heads = sorted(int(m[0]) for m in HEAD_RE.findall(ctext))
    out.append("TSV_C|%d|%s" % (len(tsv_c), "uniq=%s" % (len(set(tsv_c)) == len(tsv_c))))
    out.append("FILE_C|%d|%s" % (len(c_heads), "uniq=%s" % (len(set(c_heads)) == len(c_heads))))
    only_tsv = sorted(set(tsv_c) - set(c_heads))
    only_file = sorted(set(c_heads) - set(tsv_c))
    out.append("SET_EQ|%s|only_tsv=%s|only_file=%s" % (set(tsv_c) == set(c_heads), only_tsv, only_file))

    # ---- 3. 判定计数
    vc = collections.Counter(VERD_RE.findall(ctext))
    out.append("VERDICTS|%s|sum=%d" % (dict(vc), sum(vc.values())))

    # ---- 4. 引用锚点逐条点开：`:N「片段」` 必须是台账第 N 行的子串
    ok = bad = 0
    misses = []
    for n, frag in QUOTE_RE.findall(ctext):
        n = int(n)
        probe = frag[:18]
        line = ledger[n - 1] if 0 < n <= len(ledger) else ""
        if probe in line:
            ok += 1
        else:
            bad += 1
            misses.append((n, probe, line[:110]))
    out.append("ANCHOR|ok=%d|bad=%d|re=%s" % (ok, bad, QUOTE_RE.pattern))
    for m in misses:
        out.append("ANCHOR_MISS|%d|%r|actual=%r" % m)

    # ---- 4b. 负控：把同一批片段挪到「行号 +7」上，必须几乎全部咬不住（否则这把尺恒真）
    pairs = [(int(n), f[:18]) for n, f in QUOTE_RE.findall(ctext)][:12]
    loose = sum(1 for n, f in pairs if 0 < n + 7 <= len(ledger) and f in ledger[n + 6])
    out.append("ANCHOR_SELFTEST|pairs=%d|shift7_still_hit=%d" % (len(pairs), loose))

    # ---- 5. 零命中声称复跑
    ZEROS = ["_env_bool", "布尔配置", "docker_tools", "self_evolve", "raise_for_status",
             "fast_routes", "role_state", "feeder", "new_cid", "device_tracker",
             "user_device_map", "flush_merged", "推送加密", "revoked", "scene_routes",
             "security_monitor", "notification_routes", "sessions.json", "plugins",
             "stream_routes", "dialog_routes", "openai_routes", "profile_routes",
             "adm_heartbeat", "clean_session", "paho", "int(limit)"]
    for kw in ZEROS:
        n = sum(1 for ln in ledger if kw in ln)
        out.append("ZERO|%s|%d" % (kw, n))

    # ---- 6. roster_extra：16 枚新条目的合并表覆盖情况
    # 6a. ID+简称整串命中（D3/RT2/R5 简称根本不在合并表来源清单里，这一腿对它们必然 0 命中＝无效探针，
    #     只对 R4 有判别力，故单列）
    # 6b. 文件名候选行：把该行「文件:行号」单元里的文件名拿去 grep 合并表，逐行点开由我判机制是否同名。
    merged = load(MERGED)
    merged_src = merged[2] if len(merged) > 2 else ""
    out.append("MERGED_SRC_CLAIM|%s" % merged_src[:120])
    known_tags = set(re.findall(r"[A-Z]{1,3}\d?", "R1 R2 R3 R4 RS RO RB"))
    xtext = open(X_FILE, encoding="utf-8").read()
    xrows = re.findall(r"^\| (\d+) \| (.+?) \| (.+?) \| (.+?) \|", xtext, re.M)
    out.append("X_ROWS|%d|%s" % (len(xrows), [r[0] for r in xrows]))
    nums = [int(r[0]) for r in xrows]
    out.append("X_NUMS_CONT|201..216=%s" % (nums == list(range(201, 217))))
    ID_CELL = re.compile(r"([A-Z]+-\d+)\s*\((\w+)\)")
    FILE_CELL = re.compile(r"([A-Za-z0-9_]+\.py)")
    for num, cell, _sev, loc in xrows:
        idhits = []
        tag_known = False
        for abbr, tag in ID_CELL.findall(cell):
            if tag in known_tags:
                tag_known = True
                pat = re.compile(r"\b%s\b.*\(%s\)|\(%s\).*\b%s\b" % (abbr, tag, tag, abbr))
                for idx, ln in enumerate(merged, 1):
                    if pat.search(ln):
                        idhits.append("%s:%d" % (tag, idx))
        files = sorted(set(FILE_CELL.findall(loc)))
        cand = []
        for f in files:
            for idx, ln in enumerate(merged, 1):
                if f in ln and "| 表行" not in ln and ln.startswith("| "):
                    cand.append("%s@%d" % (f, idx))
        out.append("XID|%s|%s|tag_in_merged_src=%s|id_hits=%s|file_cands=%s"
                   % (num, cell.replace(" (", "("), tag_known, idhits[:6], cand[:8]))

    # ---- 7. 四份报告 EOF 行数声称
    EOF = {"D3": 307, "RT2": 258, "R5": 255, "R4": 224}
    for k, p in REPORTS.items():
        ln = load(p)
        out.append("EOF|%s|%d|claim=%d|%s" % (k, len(ln), EOF[k], "OK" if len(ln) == EOF[k] else "MISMATCH"))

    # ---- 8. 关键引用行原文（自声明条数 / 撤回冲突）
    SHOW = [("RT2", 14), ("RT2", 212), ("RT2", 218), ("RT2", 220), ("D3", 12), ("D3", 282),
            ("R5", 6), ("R4", 8), ("MERGED", 3), ("MERGED", 17), ("MERGED", 41)]
    for src, n in SHOW:
        rows = load(REPORTS[src]) if src in REPORTS else merged
        txt = rows[n - 1] if 0 < n <= len(rows) else "<OUT OF RANGE>"
        out.append("LINE|%s:%d|%s" % (src, n, txt[:160]))

    # ---- 9. 第二把尺：机制关键词（我手工点出来的探针）在合并表里是否已有同机制行
    MECH = {
        "201": ["提前 return", "speak 路由", "落库"],
        "202": ["非原子", "原子写", "冷却"],
        "203": ["绕过", "旁路", "TTSQueue"],
        "204": ["override_quiet", "静默"],
        "205": ["TTS_DIR", "导入", "ModuleNotFound", "import"],
        "206": ["raise_for_status", "状态码", "误报健康", "不校验"],
        "207": ["硬编码", "内网 URL", "门禁名单"],
        "208": ["pytest.ini", "conftest", "async 测试"],
        "209": ["MaskingFilter", "脱敏", "_SENSITIVE_KEYS"],
        "210": ["test_mcp_server_contract"],
        "211": ["test_v25_pytest_shim", "shim"],
        "212": ["房间", "room", "设备映射", "臆造键"],
        "213": ["入队成功", "spoken", "on_done"],
        "214": ["/api/skill/runs", "skill/runs", "不存在的路由"],
        "215": ["room_entities", "doubao_tts", "兜底"],
        "216": ["返回值形状", "形状不一致", "bool"],
    }
    for num in sorted(MECH):
        hits = []
        for kw in MECH[num]:
            for idx, ln in enumerate(merged, 1):
                if kw in ln and ln.startswith("| "):
                    hits.append("%s@%d" % (kw, idx))
        out.append("MECH|%s|%s" % (num, hits[:10] if hits else "NO_MECH_HIT"))

    # ---- 10. 子座位声称「合并表已有」的那 11 条：把被指名的合并表行原文打出来
    CLAIMED = ["33", "37", "55", "28", "53", "69", "71", "31", "72", "73", "74", "39", "202"]
    for n in CLAIMED:
        idx = int(n)
        out.append("MERGED_LINE|%d|%s" % (idx, merged[idx - 1][:170] if 0 < idx <= len(merged) else "OOR"))

    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
