r"""批17 变异探针：逐枚把「绿码」改回缺陷形态，验每条腿真的咬得住；等价对照必须存活。

为什么要有它：批17 的 20 条腿全绿只证明「代码满足这些断言」，不证明「这些断言认得缺陷」。
本脚本对每枚真缺陷形态（M1-M9）注入到**断言真正读到的那一层**，跑一遍验收，记下变红的腿名；
再对两枚**语义等价**改写（C1/C2）做同样的事——它们必须⛔ 让任何腿变红。
等价对照存活＝腿没有过拟合到字面写法；真缺陷全被咬＝腿不是恒真断言。
把等价对照计入「缺口」是错的：⛔ 它测的不是行为。

用法（权威树根）：
    python3 -B scripts/audit_1002/mutate_b17_1002.py
每枚变异后**立刻按字节还原**并比对 GREEN md5；任何一次还原失败都会在 restore_bad 里点名并退出非 0
（还原失败＝我把工作树弄坏了，比探针本身重要）。
它测不到什么：⛔ 注入我没想到的缺陷形态（比如把 tmp 换成随机后缀——那会堆 .tmp，本脚本的
residue 腿能咬，但「固定 tmp 名两个写手互踩」这条腿⛔ 在本批的断言里，见台账 §18 ⛔清单）；
⛔ 跨文件组合缺陷（每枚只改一处）。
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEST = "tests.test_audit_1002_batch17_atomic_json_writes"

GREEN = {
    "butler/devices.py": "15c2516f5a548240623da051fb64c5f7",
    "butler/roles/store.py": "9e9670b37cbe30b6c30c211805f4cd24",
    "butler/roles/state.py": "3a08a3bc372eeb3cec832dba35061968",
    "butler/memory/feeder.py": "dc3bd6cbb8265018817580a08e120d5d",
    "butler/core/fast_routes.py": "2fb75829c02fe7021bbc04600e40d589",
    "butler/core/atomic_json.py": "8c4744dbcf3b91e2702ff71073e4effa",
}

HELPER_BODY_NEW = '''def write_json_atomic(path, data) -> None:
    """把 data 序列化成 JSON 并原子替换 path。序列化失败即抛出，目标与临时文件都不碰。"""
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
'''

# (编号, 说明, 文件, 旧字面量, 新字面量, 期望变红的腿, 是不是等价对照)
CASES = [
    ("M1", "devices.save 退回就地 write_text", "butler/devices.py",
     "            write_json_atomic(self.path, data)\n",
     '            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")\n',
     ["test_devices_save_promotion_failure_keeps_previous_content"], False),
    ("M2", "roles/store._write 退回就地 write_text", "butler/roles/store.py",
     '            write_json_atomic(self.dir / f"{role.id}.json", role.to_dict())\n',
     '            (self.dir / f"{role.id}.json").write_text(\n'
     '                json.dumps(role.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")\n',
     ["test_role_write_promotion_failure_keeps_previous_content"], False),
    ("M3", "roles/state._save 退回就地 write_text", "butler/roles/state.py",
     "            write_json_atomic(self.path, self._m)\n",
     '            self.path.write_text(json.dumps(self._m, ensure_ascii=False, indent=2), encoding="utf-8")\n',
     ["test_role_state_save_promotion_failure_keeps_previous_content"], False),
    ("M4", "feeder 写侧退回 open(w)+json.dump 增量截断", "butler/memory/feeder.py",
     "        write_json_atomic(self._role_state_path(), state)\n",
     '        with open(self._role_state_path(), "w", encoding="utf-8") as f:\n'
     "            json.dump(state, f, ensure_ascii=False, indent=2)\n",
     ["test_feeder_role_state_partial_write_keeps_previous_content",
      "test_feeder_role_state_promotion_failure_keeps_previous_content"], False),
    ("M5", "feeder 读侧退回只捕 FileNotFoundError", "butler/memory/feeder.py",
     "        except Exception as e:\n"
     '            # 表行 109 M-09：只捕 FileNotFoundError，半截 JSON 会把异常抛穿成投喂接口全线 500\n'
     '            logger.error("role_state.json unreadable, treat as empty: %s", e)\n'
     "            return {}\n",
     "",
     ["test_feeder_read_role_state_survives_corrupt_json",
      "test_feeder_get_conversation_id_returns_none_on_corrupt_state"], False),
    ("M6", "fast_routes._save 退回 open(w)+json.dump", "butler/core/fast_routes.py",
     "        write_json_atomic(ROUTES_FILE, self.routes)\n",
     '        with open(ROUTES_FILE, "w", encoding="utf-8") as f:\n'
     "            json.dump(self.routes, f, ensure_ascii=False, indent=2)\n",
     ["test_fast_routes_partial_write_keeps_previous_content",
      "test_fast_routes_promotion_failure_keeps_previous_content"], False),
    ("M7", "fast_routes._load 退回静默回退空列表", "butler/core/fast_routes.py",
     "            except Exception as e:\n"
     "                # 表行 94 T-04：回退空列表＝快速路由永久静默失效 ⇒ 兜底成内置规则。\n"
     "                # ⛔ 在这里 _save()：盖回去＝抹掉现场，人工无从判断丢了哪些用户自加规则。\n"
     '                logger.error("fast_routes.json unreadable, fallback to builtin rules: %s", e)\n'
     "                self.routes = self._builtin()\n",
     "            except Exception:\n"
     "                self.routes = []\n",
     ["test_fast_routes_corrupt_file_falls_back_to_builtins",
      "test_fast_routes_non_list_json_falls_back_to_builtins"], False),
    ("M8", "helper 失败时不清理临时兄弟文件", "butler/core/atomic_json.py",
     "        tmp.unlink(missing_ok=True)\n",
     "",
     ["test_devices_save_promotion_failure_keeps_previous_content",
      "test_feeder_role_state_promotion_failure_keeps_previous_content",
      "test_fast_routes_promotion_failure_keeps_previous_content",
      "test_atomic_helper_keeps_target_when_serialization_fails"], False),
    ("M9", "helper 只写临时文件、从不 rename 顶替", "butler/core/atomic_json.py",
     "        os.replace(tmp, target)\n",
     "        pass\n",
     ["test_devices_save_roundtrips_and_leaves_no_temp_sibling",
      "test_role_state_save_roundtrips_and_leaves_no_temp_sibling",
      "test_feeder_role_state_roundtrips_and_leaves_no_temp_sibling",
      "test_atomic_helper_writes_file_and_removes_temp_sibling"], False),
    ("C1", "等价对照：tmp 变量改名 + with_name 改写成 parent/()", "butler/core/atomic_json.py",
     HELPER_BODY_NEW,
     HELPER_BODY_NEW.replace("tmp = target.with_name(target.name + \".tmp\")",
                             "tmp_path = target.parent / (target.name + \".tmp\")")
                     .replace("tmp.write_text", "tmp_path.write_text")
                     .replace("os.replace(tmp, target)", "os.replace(tmp_path, target)")
                     .replace("tmp.unlink", "tmp_path.unlink"),
     [], True),
    ("C2", "等价对照：只改注释文字", "butler/devices.py",
     "            # 表行 45 P1-13：就地 write_text 崩在半路＝半截 devices.json，下次 load() 静默回退出厂种子\n",
     "            # 原子写：先写同目录临时兄弟，再一次 rename 顶上去\n",
     [], True),
]

RED_RE = re.compile(r"^(?:FAIL|ERROR): (test_\w+)", re.M)


def crlf_of(rel: str) -> bool:
    return (REPO / rel).read_bytes().count(b"\r") > 0


def run_suite():
    p = subprocess.run([sys.executable, "-B", "-m", "unittest", TEST],
                       cwd=str(REPO), capture_output=True, text=True)
    return sorted(set(RED_RE.findall(p.stdout + p.stderr))), p.returncode


def main():
    print("PRECHECK|验收基线")
    reds, rc = run_suite()
    if rc == 0 and not reds:
        print("BASELINE_GREEN|rc=0|red_legs=0")
    else:
        print("BASELINE_NOT_GREEN|rc=%d|red=%s" % (rc, ",".join(reds)))
        return 2

    bitten = 0
    controls_ok = 0
    skipped = []
    restore_bad = []
    real = sum(1 for c in CASES if not c[6])
    ctrl = sum(1 for c in CASES if c[6])
    for cid, note, rel, old, new, expect, equiv in CASES:
        p = REPO / rel
        raw = p.read_bytes().decode("utf-8")
        needle, repl = (old, new)
        if crlf_of(rel):
            needle = needle.replace("\n", "\r\n")
            repl = repl.replace("\n", "\r\n")
        if raw.count(needle) != 1:
            print("CASE_SKIP|%s|anchor_count=%d|%s" % (cid, raw.count(needle), rel))
            skipped.append(cid)
            continue
        p.write_bytes(raw.replace(needle, repl, 1).encode("utf-8"))
        reds, rc = run_suite()
        p.write_bytes(raw.encode("utf-8"))
        back = hashlib.md5(p.read_bytes()).hexdigest()
        ok_restore = back == GREEN[rel]
        if not ok_restore:
            restore_bad.append("%s|%s|%s" % (cid, rel, back))
        hit = [l for l in expect if l in reds]
        if equiv:
            good = (not reds) and ok_restore
            if good:
                controls_ok += 1
            print("CONTROL|%s|%s|red_legs=%d|expected=0|%s"
                  % (cid, note, len(reds), "SURVIVED" if good else "BROKEN_EQUIVALENCE"))
            if reds:
                print("        误红腿：%s" % ",".join(reds))
        else:
            good = bool(hit) and ok_restore
            if hit:
                bitten += 1
            print("MUTANT|%s|%s|expected=%d|bitten=%d|%s|red_total=%d"
                  % (cid, note, len(expect), len(hit), "BITTEN" if hit else "SURVIVED(!)", len(reds)))
            print("        变红：%s" % (",".join(reds) if reds else "无"))
        print("        RESTORE|%s|%s|md5=%s|%s" % (cid, rel, back[:12], "OK" if ok_restore else "BAD"))

    print("CENSUS|real=%d bitten=%d controls=%d misbite=%d skipped=%s restore_bad=%s"
          % (real, bitten, ctrl, ctrl - controls_ok,
             "无" if not skipped else ",".join(skipped),
             "无" if not restore_bad else ",".join(restore_bad)))
    return 0 if (bitten == real and controls_ok == ctrl and not skipped and not restore_bad) else 1


if __name__ == "__main__":
    sys.exit(main())
