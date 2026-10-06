#!/usr/bin/env bash
# 批19 台账追加后的**独立第二把尺**（⛔ 与 patch_ledger_batch19_1002.py 共用代码路径：
# 这里全部走 grep/wc/tr，故意用不同算法，两把尺同号才算绿）。
# 用法：bash scripts/audit_1002/check_b19_section_numbers_1002.sh
set -u
cd "$(git rev-parse --show-toplevel 2>/dev/null || echo .)" || exit 3
L="doc/审计报告分诊台账-20261002.md"
S="scripts/audit_1002/ledger_1002_batch19_section.md"
WANT_H2=20 WANT_H3=111 WANT_LINES=2103 WANT_BYTES=235269
want_sub=12

echo "check_b19_section_numbers STAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ) epoch=$(date -u +%s)"
[ -f "$L" ] || { echo "MISSING|$L"; exit 4; }
[ -f "$S" ] || { echo "MISSING|$S"; exit 4; }

h2=$(grep -c '^## ' "$L"); h3=$(grep -c '^### ' "$L")
lines=$(wc -l < "$L" | tr -d ' '); bytes=$(wc -c < "$L" | tr -d ' ')
cr=$(tr -dc '\r' < "$L" | wc -c | tr -d ' ')
cr_s=$(tr -dc '\r' < "$S" | wc -c | tr -d ' ')
sec_head=$(grep -c '^## §20 批19 · ' "$L")
s19=$(grep -c '^## §19 批18 · ' "$L")
sub=$(grep -c '^### §20-' "$L")
sub_s=$(grep -c '^### §20-' "$S")
last=$(tail -n 1 "$L" | cut -c1-40)
# 小节号连号：§20-0…§20-11 各出现一次（缺号／重号都算红）
seq_ok=$(grep -o '^### §20-[0-9]\+' "$L" | sed 's/.*§20-//' | sort -n | uniq | tr '\n' ' ' | sed 's/ $//')

echo "h2=$h2/$WANT_H2 h3=$h3/$WANT_H3 lines=$lines/$WANT_LINES bytes=$bytes/$WANT_BYTES"
echo "cr_ledger=$cr cr_section=$cr_s  §20_head=$sec_head  §19_head=$s19  §20_sub_in_ledger=$sub/$want_sub  §20_sub_in_section=$sub_s/$want_sub"
echo "subhead_sequence=[$seq_ok]"
echo "last_line=[$last]"

rc=0
[ "$h2" = "$WANT_H2" ] || { echo "RED|h2"; rc=1; }
[ "$h3" = "$WANT_H3" ] || { echo "RED|h3"; rc=1; }
[ "$lines" = "$WANT_LINES" ] || { echo "RED|lines"; rc=1; }
[ "$bytes" = "$WANT_BYTES" ] || { echo "RED|bytes"; rc=1; }
[ "$cr" = "0" ] || { echo "RED|cr_ledger"; rc=1; }
[ "$cr_s" = "0" ] || { echo "RED|cr_section"; rc=1; }
[ "$sec_head" = "1" ] || { echo "RED|§20_head(必须恰好 1)"; rc=1; }
[ "$s19" = "1" ] || { echo "RED|§19_head(本节⛔ 吃掉上一节)"; rc=1; }
[ "$sub" = "$want_sub" ] || { echo "RED|§20_sub_in_ledger"; rc=1; }
[ "$sub_s" = "$want_sub" ] || { echo "RED|§20_sub_in_section"; rc=1; }
[ "$seq_ok" = "0 1 2 3 4 5 6 7 8 9 10 11" ] || { echo "RED|subhead_sequence"; rc=1; }
[ -n "$last" ] || { echo "RED|tail_empty"; rc=1; }

# 负控：这把尺必须咬得住。真删一行到 /tmp 再量，期望 RED|lines 与 RED|bytes 同时出现。
NEG="/tmp/b19_negctrl_$$.md"; head -n $((lines - 1)) "$L" > "$NEG"
n_lines=$(wc -l < "$NEG" | tr -d ' ')
n_bytes=$(wc -c < "$NEG" | tr -d ' ')
rm -f "$NEG"; ls "$NEG" >/dev/null 2>&1 && { echo "RED|负控临时文件未删净"; rc=1; }
if [ "$n_lines" = "$WANT_LINES" ] && [ "$n_bytes" = "$WANT_BYTES" ]; then
  echo "RED|NEG_CONTROL_DID_NOT_BITE(删一行后仍同号＝这把尺是死的)"; rc=1
else
  echo "NEG_BITE|lines=$n_lines bytes=$n_bytes(删一行后两数皆变＝尺会咬)"
fi
[ -e "$NEG" ] && echo "RED|LEFTOVER|$NEG"

[ "$rc" = 0 ] && echo "GREEN|batch19_ledger(两把尺同号＝追加器 python 计数＋本尺 grep/wc/tr 计数)" || echo "RED|见上行"
echo "rc=$rc"
exit "$rc"
