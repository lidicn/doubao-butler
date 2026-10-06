#!/usr/bin/env bash
# 批17 §18 落盘自量：追加前/后各取一次四把独立尺（wc -c / wc -l / grep -c 标题 / 二进制 CR），
# 并把「重跑必 raise」的腿放在同一次调用里（先落盘→再复采→最后重跑闸）。
set -u
cd /vol1/1000/docker/doubao-butler
OUT=workorders/readings/1003b17
echo "stamp_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "cmd: python3 -B scripts/audit_1002/patch_ledger_batch17_1002.py --apply"
python3 -B scripts/audit_1002/patch_ledger_batch17_1002.py --apply
echo "apply_rc=$?"
T=doc/审计报告分诊台账-20261002.md
echo "--- 四把独立尺（追加后） ---"
echo "BYTES|$(wc -c < "$T")"
echo "LINES|$(wc -l < "$T")"
echo "H2|$(grep -c '^## ' "$T")"
echo "H3|$(grep -c '^### ' "$T")"
echo "CR_BINARY|$(tr -cd '\r' < "$T" | wc -c)"
echo "MD5|$(md5sum "$T" | cut -d" " -f1)"
echo "TAIL_HEAD|$(grep '^## ' "$T" | tail -1)"
echo "--- 节文件形状 ---"
S=scripts/audit_1002/ledger_1002_batch17_section.md
echo "SECTION|md5=$(md5sum "$S" | cut -d' ' -f1)|bytes=$(wc -c < "$S")|lines=$(wc -l < "$S")|CR=$(tr -cd '\r' < "$S" | wc -c)"
echo "APPLIER|md5=$(md5sum scripts/audit_1002/patch_ledger_batch17_1002.py | cut -d' ' -f1)|bytes=$(wc -c < scripts/audit_1002/patch_ledger_batch17_1002.py)"
echo "--- 重跑必 raise ---"
python3 -B scripts/audit_1002/patch_ledger_batch17_1002.py
echo "rerun_rc=$?"
echo "--- 本节 §18 标题点名（追加自证：旧段⛔ 被吃） ---"
echo "SEC17_LINES|$(grep -c '^### §17-' "$T")"
echo "SEC18_LINES|$(grep -c '^### §18-' "$T")"
echo "G179_STILL_THERE|$(grep -c '12 行＝就地截断写' "$T")"
