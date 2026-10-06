"""一次性补读数：把**改前那份真旧码**从 `git show aee7a2e:butler/memory/extractor.py` 取出来，
喂与验收同一份内存夹具，读出旧行为的真实返回值。

用途＝DCD 裁定① 那两枚 recency 用例「改前为什么红」的 FAIL 半证据。
纪律：⛔ 手写复刻旧码（复刻只算我自己的猜想）、⛔ checkout 工作树（会动现网挂载），
只走 `git show` → `/tmp` → importlib 装载，跑完同一次调用内删除并 `ls` 反证。
旧提交号是不可变 ref：本脚本在①合入后仍可复跑，读数值不随时间变。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sqlite3
import subprocess
import sys
import time

OLD_REF = "aee7a2e:butler/memory/extractor.py"
TMP = pathlib.Path("/tmp/old_extract_b8_probe_mod.py")


def _load_old() -> object:
    proc = subprocess.run(["git", "show", OLD_REF], capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"git show 失败 rc={proc.returncode}: {proc.stderr[:120]}")
    src = proc.stdout
    if "ORDER BY ts ASC" not in src:
        raise SystemExit("取到的不是旧码（没有 ORDER BY ts ASC）")
    if "result.reverse()" in src:
        raise SystemExit("取到的已是新码（含 result.reverse）")
    TMP.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("old_extract_b8_probe", str(TMP))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if "old_extract_b8_probe" not in mod.__file__:
        raise SystemExit(f"探针没吃到 /tmp 那份旧码：__file__={mod.__file__}")
    print(f"LOADED|__file__={mod.__file__} bytes={len(src)}")
    return mod


def _conn(rows) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE chat_logs (ts REAL, user_msg TEXT, assistant_reply TEXT,"
        " role TEXT, source TEXT, room TEXT)"
    )
    conn.executemany("INSERT INTO chat_logs VALUES (?,?,?,?,?,?)", rows)
    return conn


def main() -> int:
    sys.path.insert(0, ".")
    mod = _load_old()
    import butler.store.db as dbmod

    now = time.time()
    base = [(i, f"心情序号 {i:02d} 号") for i in range(30)]

    for tag, inject_noise_at in (("PLAIN", None), ("NOISE_IS_NEWEST", 29)):
        rows = []
        for i, msg in base:
            text = f"【ℹ️】请原样输出" if inject_noise_at == i else msg
            rows.append((now - (29 - i) * 10, text, "", "lidicn", "chat", "living"))
        dbmod.get_conn = lambda *a, **k: _conn(rows)
        got = mod.MemoryExtractor._fetch_recent_dialogs(object(), days=3650, max_turns=5)
        seqs = [g["user_msg"].split()[1] if " " in g["user_msg"] else g["user_msg"][:6] for g in got]
        print(f"OLD|{tag}|n={len(got)}|seqs={seqs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
