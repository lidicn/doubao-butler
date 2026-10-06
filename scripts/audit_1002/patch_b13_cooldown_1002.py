"""批13 一次性落码器：触发冷却持久化从 JSON 收口进 SQLite（DCD 20260928 第 7 条 / 20261002 跟办 2）。

⛔ 把新代码写在 python 字面量里再拼接——本仓的老坑是字面量会静默吃掉 `\r`/`\f`；
所以本器**按行号切片**施加（每片先 `startswith` 断言锚点行原文），行号来自
`awk NR` 打印行号的尺，⛔ 用 sed 偏移推。任何一格不符 ⇒ raise，⛔ 把树留在半改状态。

五枚文件（基线现读于 2026-10-02 15:19:25Z 的权威树，全 LF／CR 0／末行有换行）：
  butler/store/db.py             3aa5bd86dfa767ccab6738407f71c5c3  564 行／16,228 B
  butler/store/repo.py           4d48ab45529a888f049bae305b134802  640 行／23,427 B
  butler/triggers/engine.py      8ad01292a6e5f026a366dc3611bdee6b  493 行／24,466 B
  tests/test_dry_run_gate.py     b86f727ee85fbf3f18514456a8966026  280 行／14,211 B
  tests/test_audit_1002_batch4.py 6b091550ef77b9ee06bd4d7deb7b7c5d 396 行／20,270 B

用法：
    python3 scripts/audit_1002/patch_b13_cooldown_1002.py          # DRY
    python3 scripts/audit_1002/patch_b13_cooldown_1002.py --apply  # 写盘（重跑必 raise）
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]

BASE = {
    "butler/store/db.py": ("3aa5bd86dfa767ccab6738407f71c5c3", 564),
    "butler/store/repo.py": ("4d48ab45529a888f049bae305b134802", 640),
    "butler/triggers/engine.py": ("8ad01292a6e5f026a366dc3611bdee6b", 493),
    "tests/test_dry_run_gate.py": ("b86f727ee85fbf3f18514456a8966026", 280),
    "tests/test_audit_1002_batch4.py": ("6b091550ef77b9ee06bd4d7deb7b7c5d", 396),
}

# ---------------------------------------------------------------- butler/store/db.py
DB_ENSURE = '''_TRIGGER_COOLDOWNS_DDL = (
    "CREATE TABLE IF NOT EXISTS trigger_cooldowns ("
    " cd_key     TEXT PRIMARY KEY,"
    " last_fired REAL NOT NULL)"
)
_TRIGGER_COOLDOWNS_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_trigger_cooldowns_last ON trigger_cooldowns(last_fired)",
)


def _ensure_trigger_cooldowns(c: sqlite3.Connection) -> None:
    """跟办 2（DCD 20261002:107＝20260928 第 7 条「JSON 收口进 SQLite」的欠执行）：建冷却表。

    形制照 `_ensure_trigger_evaluations`：独立受防的 DDL，⛔ 挤进 `_init` 那条 executescript
    （老库上那条脚本全是 no-op 不吃写锁，而新表的 CREATE 在现网库上是真写）。
    与评估账的差别在**后果**：这张表读不到＝重启后所有触发器冷却清零（会提前再响一次），
    所以读侧⛔ 把失败折成空 dict，`repo.load_cooldowns` 直接抛，engine 接住并进台账。
    """
    try:
        c.execute(_TRIGGER_COOLDOWNS_DDL)
        for stmt in _TRIGGER_COOLDOWNS_IDX:
            c.execute(stmt)
        c.commit()
    except Exception as e:
        logger.warning("trigger_cooldowns init failed: %s: %s"
                       " (cooldowns will NOT survive a restart until this is fixed): %s",
                       type(e).__name__, str(e)[:200], e)


'''

# ---------------------------------------------------------------- butler/store/repo.py
REPO_BLOCK = '''# ---- 触发冷却（trigger_cooldowns，DCD 20260928 第 7 条 / 20261002 跟办 2） ----

COOLDOWN_RETENTION_S = 86400 * 7   # 旧 JSON 时代就是 `86400*7`，原样搬，⛔ 悄悄改宽改窄


def load_cooldowns(retention_s: float = COOLDOWN_RETENTION_S,
                   now: float | None = None) -> dict[str, float]:
    """读未过窗的冷却键。读失败**直接抛**：空 dict 和"没读到"是两件事。

    折成空 dict 会被上游读成「所有触发器都没冷却」⇒ 全员提前再响一次；
    抛出去则由 `TriggerEngine._load_cooldowns` 接住、进 `db_write_failures` 台账并 loud。
    """
    c = get_conn()
    cutoff = (time.time() if now is None else now) - retention_s
    return {r["cd_key"]: float(r["last_fired"])
            for r in c.execute("SELECT cd_key, last_fired FROM trigger_cooldowns"
                               " WHERE last_fired>=?", (cutoff,))}


def save_cooldowns(values: dict[str, float], retention_s: float = COOLDOWN_RETENTION_S,
                   now: float | None = None) -> int:
    """整批 upsert ＋顺手清掉过窗的行。

    旧码每轮把整个 dict 重写进 JSON，天然不会长歪；收进库里若只 INSERT 就变成**只增账**，
    所以过期清理必须跟写同批（⛔ 另起一个"以后再来扫"的腿）。
    INSERT OR REPLACE⛔ 换成 ON CONFLICT DO UPDATE：后者要 SQLite>=3.24，现网镜像的库版本没现读过。
    """
    c = get_conn()
    cutoff = (time.time() if now is None else now) - retention_s
    c.execute("DELETE FROM trigger_cooldowns WHERE last_fired<?", (cutoff,))
    c.executemany("INSERT OR REPLACE INTO trigger_cooldowns (cd_key, last_fired) VALUES (?,?)",
                  [(str(k), float(v)) for k, v in values.items()])
    c.commit()
    return len(values)


'''

# ---------------------------------------------------------------- butler/triggers/engine.py
ENGINE_BLOCK = '''    def _load_cooldowns(self, snapshot: str = ""):
        """从 SQLite 读回未过窗的冷却；`snapshot` 非空时先把旧 JSON 搬进库（只搬一次）。

        读库失败⛔ 折成"没有冷却"——那种读法把「查不到」变成「全员没冷却」。
        接住异常、进台账、保住进程内状态，响亮但不打断启动。
        """
        try:
            self._last_fired.update(repo.load_cooldowns())
        except Exception as e:
            logger.warning("load cooldowns from sqlite failed: %s: %s"
                           " (this process starts with an empty cooldown map)",
                           type(e).__name__, str(e)[:200])
            write_failures.record("triggers/engine.load_cooldowns",
                                  "%s: %s" % (type(e).__name__, str(e)[:200]),
                                  table="trigger_cooldowns")
        if snapshot:
            self._import_legacy_snapshot(snapshot)

    def _import_legacy_snapshot(self, path: str) -> None:
        """迁移腿：旧 `trigger_cooldowns.json` 只读这一次，**验过才退役**。

        两笔来源都可能是最新的 ⇒ 同键取较新那个（更长的冷却＝⛔ 提前放行）。
        删文件是不可逆动作，挂在「写进去并且读回来逐键对得上」之后：验证不过就把快照留着，
        下次启动再搬一遍（整段幂等），⛔ 把用户的活数据删在半路。
        """
        if not os.path.exists(path):
            return
        try:
            data = json.load(open(path, encoding='utf-8'))
        except Exception as e:
            logger.warning("cooldown snapshot unreadable, kept: %s (%s)", path, e)
            return
        now = time.time()
        fresh = {k: float(v) for k, v in data.items()
                 if isinstance(v, (int, float)) and now - v < repo.COOLDOWN_RETENTION_S}
        if not fresh:
            logger.info("cooldown snapshot %s carries nothing fresh", path)
            return
        for k, v in fresh.items():
            if v > self._last_fired.get(k, 0):
                self._last_fired[k] = v
        try:
            repo.save_cooldowns(self._last_fired)
            back = repo.load_cooldowns()
        except Exception as e:
            logger.warning("cooldown snapshot not persisted, kept: %s (%s)", path, e)
            write_failures.record("triggers/engine.import_legacy_snapshot",
                                  "%s: %s" % (type(e).__name__, str(e)[:200]),
                                  table="trigger_cooldowns")
            return
        missing = [k for k, v in fresh.items() if back.get(k) != v]
        if missing:
            logger.warning("cooldown snapshot kept: %d/%d key(s) unverified in sqlite: %s",
                           len(missing), len(fresh), missing[:5])
            return
        try:
            os.remove(path)
        except Exception as e:
            logger.warning("cooldown snapshot imported but unlink failed: %s (%s)", path, e)
            return
        logger.info("cooldown snapshot imported and retired: %s (%d entries)", path, len(fresh))

    def _save_cooldowns(self):
        """整张冷却表写进 SQLite；写失败＝loud＋进台账，但进程内状态⛔ 跟着丢。"""
        try:
            repo.save_cooldowns(self._last_fired)
        except Exception as e:
            logger.warning("save cooldowns failed: %s: %s", type(e).__name__, str(e)[:200])
            write_failures.record("triggers/engine.save_cooldowns",
                                  "%s: %s (cooldowns are in-memory only until this write works)"
                                  % (type(e).__name__, str(e)[:200]),
                                  table="trigger_cooldowns")

    def set_runtime(self, rt) -> None:
        self.rt = rt
        # 旧 JSON 只在这一步被读一次（迁移）；此后 engine ⛔ 再碰这枚文件
        data_dir = getattr(rt, "data_dir", "") or getattr(getattr(rt, "settings", None), "data_dir", "")
        self._load_cooldowns(os.path.join(data_dir, "trigger_cooldowns.json") if data_dir else "")'''

# ---------------------------------------------------------------- tests/test_dry_run_gate.py
DRY_RECORDERS = '''    def _record_cooldowns(self, values, *a, **k):
        self.cooldown_saves.append(dict(values))
        self.cooldown_store.update(values)
        return len(values)

    def _read_cooldowns(self, *a, **k):
        return dict(self.cooldown_store)

'''

# ---------------------------------------------------------------- tests/test_audit_1002_batch4.py
B4_METHODS = '''    def _record_cooldowns(self, values, *a, **k):
        self.cooldown_saves.append(dict(values))
        self.cooldown_store.update(values)
        return len(values)

    def _read_cooldowns(self, *a, **k):
        return dict(self.cooldown_store)

'''

OPS = {
    "butler/store/db.py": [
        (104, 103, "def _init(c: sqlite3.Connection) -> None:", None, DB_ENSURE),
        (459, 459, "    _ensure_trigger_evaluations(c)", "    _ensure_trigger_evaluations(c)",
         '    _ensure_trigger_evaluations(c)\n    _ensure_trigger_cooldowns(c)\n'),
    ],
    "butler/store/repo.py": [
        (251, 250, "EVALUATION_CALIBER_NOTE = (", None, REPO_BLOCK),
    ],
    "butler/triggers/engine.py": [
        (59, 94, "    def _load_cooldowns(self):", "            self._load_cooldowns()",
         ENGINE_BLOCK + "\n"),
        (44, 44, '        self._cooldown_file: str = ""  # 由 set_runtime 设置', None, ""),
        (19, 19, "from butler.store import write_failures", None,
         "from butler.store import repo, write_failures\n"),
    ],
    "tests/test_dry_run_gate.py": [
        (210, 210, '        self.assertTrue(o["cooldown_file_written"]', None,
         '        self.assertGreaterEqual(o["cooldown_saves"], 1,\n'
         '                           "变异体没写冷却＝它根本没走到执行路径")\n'),
        (154, 154, '        self.assertTrue(o["cooldown_file_written"]', None,
         '        self.assertEqual(o["cooldown_saves"], 1, "冷却没写库＝这条腿根本没跑")\n'),
        (129, 129, '        self.assertFalse(o["cooldown_file_written"]', None,
         '        self.assertEqual(o["cooldown_saves"], 0, "dry_run 却写了冷却库")\n'),
        (113, 113, "        eng._cooldown_file = self.cooldown_file", None, ""),
        (109, 108, "    def build(self, trig=None, engine_cls=TriggerEngine):", None,
         DRY_RECORDERS),
        (107, 107, '        self.cooldown_file = os.path.join(self.tmp, "trigger_cooldowns.json")',
         None,
         '        # 批13：冷却真源换成 SQLite，本档只钉闸门形状——落盘那一格由记录器收下，\n'
         '        # 跨重启的真库腿在 `tests/test_audit_1002_batch13_cooldown.py`。\n'
         '        self.cooldown_saves = []\n'
         '        self.cooldown_store = {}\n'
         '        for name, fn in (("save_cooldowns", self._record_cooldowns),\n'
         '                         ("load_cooldowns", self._read_cooldowns)):\n'
         '            old = getattr(repo, name)\n'
         '            setattr(repo, name, fn)\n'
         '            self.addCleanup(setattr, repo, name, old)\n'),
        (84, 84, '        "cooldown_file_written": os.path.exists(cooldown_file),', None,
         '        "cooldown_saves": len(saves),\n'),
        (73, 73, "def observe(eng, runner, cooldown_file, dry_run", None,
         'def observe(eng, runner, saves, dry_run, event="person_enter"):\n'),
        (17, 17, "import os", None, ""),
    ],
    "tests/test_audit_1002_batch4.py": [
        (318, 318, "        self.assertTrue(self.cooldown_file.exists()", None,
         '        self.assertTrue(self.cooldown_saves,\n'
         '                        "冷却没写库＝重启即失忆（跨重启的真库腿在 batch13 验收件）")\n'),
        (201, 201, "        eng._cooldown_file = str(self.cooldown_file)", None, ""),
        (197, 196, "    def build(self, trig=None, runner=None, store=None):", None, B4_METHODS),
        (187, 188, "        saved = [(\"add_trigger_run\"",
         '                 ("add_trigger_evaluation", lambda *a, **k: self.eval_rows.append(a))]',
         '        saved = [("add_trigger_run", lambda *a, **k: self.run_rows.append(a)),\n'
         '                 ("add_trigger_evaluation", lambda *a, **k: self.eval_rows.append(a)),\n'
         '                 ("save_cooldowns", self._record_cooldowns),\n'
         '                 ("load_cooldowns", self._read_cooldowns)]\n'),
        (183, 183, '        self.cooldown_file = pathlib.Path(self.tmp) / "trigger_cooldowns.json"',
         None,
         '        # 批13 改口：JSON 不再是真源，本档把 repo 的读写腿接管成记录器。\n'
         '        self.cooldown_saves = []\n'
         '        self.cooldown_store = {}\n'),
    ],
}

# 只有 test_dry_run_gate 用整串重命名调用点（7 处，现读于 `grep -n observe(`）
GLOBAL_REPL = {
    "tests/test_dry_run_gate.py": [("self.runner, self.cooldown_file,",
                                    "self.runner, self.cooldown_saves,", 7)],
}


def stats(raw: bytes) -> dict:
    text = raw.decode("utf-8")
    lines = text.splitlines(True)
    return dict(md5=hashlib.md5(raw).hexdigest(), lines=len(lines), bytes=len(raw),
                crlf=raw.count(b"\r\n"), endnl=raw.endswith(b"\n"))


def apply_ops(rel: str, text: str) -> str:
    lines = text.splitlines(True)
    for start, end, first, last, new in sorted(OPS[rel], key=lambda o: -o[0]):
        body = lines[start - 1:end] if end >= start else []
        anchor = lines[start - 1] if start - 1 < len(lines) else ""
        if first and not anchor.startswith(first):
            raise SystemExit(f"ABORT {rel}:{start} 锚点首行不符：{anchor!r} 期以 {first!r} 起头")
        if last and not body[-1].startswith(last):
            raise SystemExit(f"ABORT {rel}:{end} 锚点末行不符：{body[-1]!r} 期以 {last!r} 起头")
        repl = [l + "\n" for l in new.split("\n") if l != ""] if new else []
        if new and not new.endswith("\n"):          # 末行不带换行＝接回原文件的下一行
            repl[-1] = repl[-1][:-1]
        lines[start - 1:start - 1 + len(body)] = repl
    out = "".join(lines)
    for old, new, want in GLOBAL_REPL.get(rel, []):
        got = out.count(old)
        if got != want:
            raise SystemExit(f"ABORT {rel} 全局替换命中 {got} 次，期望 {want} 次：{old!r}")
        out = out.replace(old, new)
    return out


def main() -> None:
    apply = "--apply" in sys.argv
    originals = {}
    for rel, (md5, nlines) in BASE.items():
        p = REPO / rel
        raw = p.read_bytes()
        s = stats(raw)
        if s["md5"] != md5:
            raise SystemExit(f"ABORT {rel} 基线 md5 不符：现={s['md5']} 期={md5}（重跑必红＝已施加过）")
        if s["lines"] != nlines or s["crlf"] != 0 or not s["endnl"]:
            raise SystemExit(f"ABORT {rel} 基线形状不符：{s}")
        originals[rel] = raw

    results = {}
    for rel in BASE:
        text = originals[rel].decode("utf-8")
        new = apply_ops(rel, text)
        try:
            ast.parse(new)
        except SyntaxError as e:
            raise SystemExit(f"ABORT {rel} 施加后语法不过：{e}")
        if new.count("\r") != 0:
            raise SystemExit(f"ABORT {rel} 施加产物含 CR")
        if new.endswith("\n") != text.endswith("\n"):
            raise SystemExit(f"ABORT {rel} 末行换行被改：{text[-1]!r} -> {new[-1]!r}")
        results[rel] = new
        print("DRY|%-32s 行 %4d->%4d 字节 %6d->%6d" % (
            rel, len(text.splitlines(True)), len(new.splitlines(True)),
            len(text.encode("utf-8")), len(new.encode("utf-8"))))

    if not apply:
        print("（未写盘。要写：python3 scripts/audit_1002/patch_b13_cooldown_1002.py --apply）")
        return
    for rel, new in results.items():
        (REPO / rel).write_text(new, encoding="utf-8", newline="")
    for rel, new in results.items():
        back = (REPO / rel).read_bytes()
        if back != new.encode("utf-8"):
            raise SystemExit(f"ABORT {rel} 回读与内存不一致")
        s = stats(back)
        print("APPLIED|%s|md5=%s|行=%d|字节=%d|CR=%d|末行换行=%s" % (
            rel, s["md5"], s["lines"], s["bytes"], s["crlf"], s["endnl"]))


if __name__ == "__main__":
    main()
