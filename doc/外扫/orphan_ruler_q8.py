#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
orphan_ruler_q8.py — Q8 报告《doc/外扫/孤儿函数-R19口径-20260921.md》§7 脚本体完整本体落盘。

来源与口径说明（无省略号、无"同原尺"式引用，全部展开为字面代码）：
- 本文件是 Q8 当轮以 stdin heredoc 内联跑过的那把 R19 尺子的完整落盘版，只读、无网络。
- 扫描面 = 树根下 butler/ + vendor/ + tts/（报告§一四件套）；tests/、tmp/、archive/、
  vibe_deploy/、_wt2/、workorders/、doc/、scripts/、根散文件均不在面内（报告§一"0 命中自报分母"段 + R6 固定语句）。
- 定义面/过滤/扣减规则逐条同源 workorders/tools/orphan_symbol_scan.py（Q8 判其引用面不符 R19 而弃用其引用面，
  定义面行内同源移植）：NOISE 名单、len<4、装饰器定义剔除、test_* 文件定义剔除、own 按 def 行扣减。
- 唯一改面处（R19）：判据只用 .py 引用面 `py_text.count(name) - own == 0`；
  .md/.json/.yaml/.yml/.toml/.sh/.txt/.env 一律不计入引用，仅按词边界记 md_hits 供对账（报告§一"引用面口径"）。
- 并列跑旧尺面（total(py+other) - own == 0），两集差 = 被 .md/配置救回数（报告§§4/7）。
树根默认 E:\NAS\doubao-butler（非权威树·E 盘镜像），可经 argv[1] 传入其它树根。
输出全部打到 stdout。
"""
import os
import re
import sys

# 报告§7：BASE = "E:/NAS/doubao-butler"，正斜杠书写避开 Windows 转义坑；argv[1] 可覆盖
BASE = (sys.argv[1] if len(sys.argv) > 1 else "E:/NAS/doubao-butler").replace("\\", "/")

# ———— 收集扫描面（报告§7 字面展开） ————
# 扫描面=应用码三目录；tests/tmp/archive/vibe_deploy/_wt2/workorders/doc 不在面内
py_files, other_files = [], []
for root in ["butler", "vendor", "tts"]:
    for dirpath, dirnames, filenames in os.walk(os.path.join(BASE, root)):
        # 目录剪枝同原尺 orphan_symbol_scan.py:29
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git", "node_modules")]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            if fn.endswith(".py"):
                py_files.append(p)
            elif fn.endswith((".json", ".yaml", ".yml", ".toml", ".md", ".sh", ".txt", ".env")):
                other_files.append(p)
            # 其余扩展名的非码文件不入 other_files —— 非码清单字面量同原尺 :37（报告§7"非码文件只记 md_hits"所指的 other_files 面）

# ———— read() 缓存（报告§7 注释"read() 缓存同原尺"→ 按原尺 :50-62 字面展开） ————
sources, unreadable = {}, 0


def read(path):
    global unreadable
    if path not in sources:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                sources[path] = fh.read()
        except OSError:
            unreadable += 1
            sources[path] = ""
    return sources[path]


# ———— 正则与名单（报告§7 注释"DEF_RE/NOISE/DECOR_RE 同原尺"→ 字面量取自原尺，逐条展开） ————
# NOISE 名单：原尺 orphan_symbol_scan.py:16-20
NOISE = re.compile(
    r"^(main|run|start|stop|setup|teardown|setUp|tearDown|setUpClass|tearDownClass|"
    r"__.*__|model_config|class_config|dict|json|schema|get|set|items|keys|values|"
    r"load|save|to_dict|from_dict|__str__)$"
)
# DEF_RE：原尺 :65；DECOR_RE：原尺 :66
DEF_RE = re.compile(r"^(\s*)(async\s+)?def\s+([A-Za-z_]\w*)\s*\(", re.M)
DECOR_RE = re.compile(r"^\s*@")

# ———— 定义面（逐条同原尺 :68-83；含 test_ 定义剔除与装饰器定义剔除，报告§一"定义面已按原尺剔"段） ————
candidates = {}
for path in py_files:
    text = read(path)
    # 排除规则字面同原尺 :71（Q8 前置：扫描面本身不含 tests/，此判据原样保留以复算）
    if "/tests/" in path or os.path.basename(path).startswith("test_"):
        continue
    lines = text.split("\n")
    for m in DEF_RE.finditer(text):
        indent, _, name = m.group(1), m.group(2), m.group(3)
        if NOISE.match(name) or len(name) < 4:
            continue
        lineno = text.count("\n", 0, m.start())
        ctx = lines[lineno - 1] if lineno else ""
        if DECOR_RE.match(ctx):
            continue
        kind = "module" if indent == "" else "method"
        candidates.setdefault(name, []).append((path, kind))

# ———— 引用面（R19 改面处，报告§7） ————
# py_text = 仅 .py 拼接；corpus_text = py+other（仅供旧尺面并列对账，不参与 R19 判据）
py_text = "\n".join(read(p) for p in py_files)
corpus_text = py_text + "\n" + "\n".join(read(p) for p in other_files)

r19_rows = []   # R19 面候选：只数 .py
old_names = []  # 旧尺面候选：total(py+other)，严格 ==0（报告§4"严格等式扣减"）
for name, defs in sorted(candidates.items()):
    # own 扣减（报告§7 注释"own=Σdef行count 同原尺"→ 按原尺 :92-95 字面展开，含同名多处 def 落同文件双倍扣减的已知假阳性行为）
    own = 0
    for path, _kind in defs:
        for line in read(path).split("\n"):
            if re.search(r"\bdef\s+%s\s*\(" % re.escape(name), line):
                own += line.count(name)
    # 判据（R19）：py_text.count(name) - own == 0 ⇒ 候选
    is_r19 = (py_text.count(name) - own == 0)
    # 并列跑：total(py+other)-own == 0 的旧尺名单
    is_old = (corpus_text.count(name) - own == 0)
    if is_r19:
        # md_hits（word 边界计非码面）仅报告不判（原尺 :99-100 同款词边界计数）
        word = re.compile(r"\b%s\b" % re.escape(name))
        md_hits = sum(len(word.findall(read(p))) for p in other_files)
        r19_rows.append((name, defs, md_hits))
    if is_old:
        old_names.append(name)

module_rows = [r for r in r19_rows if any(k == "module" for _, k in r[1])]
method_rows = [r for r in r19_rows if all(k == "method" for _, k in r[1])]
old_set = set(old_names)
rescued = sorted(r[0] for r in r19_rows if r[0] not in old_set)

# ———— 输出（stdout） ————
print("tree=%s (非权威树 E 盘镜像口径由使用者自标)" % BASE)
print("scope: roots=butler,vendor,tts  py_files=%d other_files=%d unreadable=%d"
      % (len(py_files), len(other_files), unreadable))
print("distinct_def_names=%d" % len(candidates))
print("R19 面（只数 .py）候选: total=%d module_level=%d method_level=%d"
      % (len(r19_rows), len(module_rows), len(method_rows)))
for name, defs, md_hits in r19_rows:
    paths = ";".join(p.replace(BASE + "/", "").replace(BASE + "\\", "") for p, _ in defs)
    print("  %-30s defs=%d  md_hits=%d  %s" % (name, len(defs), md_hits, paths))
print("旧尺面（py+other 严格 ==0）候选=%d: %s" % (len(old_names), ", ".join(old_names)))
print("被 .md/非码面救回数（R19 候选 - 旧尺候选, DELTA）=%d" % len(rescued))
print("救回名单: %s" % ", ".join(rescued))
print("NOTE: 输出为 CANDIDATE 名单，不是孤儿判定；每个名字进 backlog 前仍须人工复核动态派发/框架回调（原尺 NOTE 同此告诫）。")
sys.exit(0)

# ———— 落盘自验（本座位 S2 当场复跑，只读，非权威树 E 盘镜像） ————
# 首条 date -u 原样：Mon Sep 21 10:55:46 UTC 2026
# 末条 date -u 原样：Mon Sep 21 10:57:17 UTC 2026
# 复算三数 vs 报告三数（全部一致）：
#   定义面总数 distinct_def_names：复算 1126 / 报告 1126
#   R19 面候选数：复算 63（module 0 + method 63）/ 报告 63
#   被 .md 与非码面救回数：复算 55 / 报告 55
# 旁证：py_files=195、other_files=215、unreadable=0、旧尺面严格 ==0 名单 8 个，均与报告§一/§4 逐字一致。
# 裁定：复算一致，本脚本为 Q8 §7 那次内联跑的可复算本体。
