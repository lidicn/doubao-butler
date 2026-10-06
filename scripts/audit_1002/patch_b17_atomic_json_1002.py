r"""批17 一次性落码器：整份 JSON 的就地截断写 → 写 tmp 再一次 rename；读侧解析失败语义。

用法（在权威树根跑）：
    python3 -B scripts/audit_1002/patch_b17_atomic_json_1002.py          # 干跑，只出 PLAN
    python3 -B scripts/audit_1002/patch_b17_atomic_json_1002.py --apply  # 落盘一次，重跑必 raise

为什么写成脚本（⛔ 手敲 sed）：五处改动各有自己的换行血统（devices.py / roles/state.py 整文件 CRLF，
其余 LF），手敲命令会把 CRLF 文件整文件抹成 LF（台账条目「改文件逐文件保换行符血统」）。
本脚本对每个文件**按其自身血统**写回：CRLF 文件的插入块也带 \r\n，LF 文件一个 \r 都不许出现。

六道闸（顺序即优先级，⛔ 把 ALREADY_APPLIED 排在基线校验之后——批16 §17-3 记过「先判 MD5 后判 ALREADY
会把已经改过误报成基底漂移」，本批第一次重跑又踩了同一条，且叠了第二个漏：标记字面量带 \n，
在整文件 CRLF 的 devices.py / roles/state.py 里恒不命中＝那道闸在两个文件上形同虚设。两处都记在台账 §18-10）：
  1 ALREADY_APPLIED  —— 新标记已在 ⇒ 这是重复投递，raise（一次性）
  2 BASELINE_*       —— md5/行数/字节/CR 必须等于下面钉死的现读基线，否则基底不是我量过的那一份
  3 ANCHOR_*         —— 旧块必须**恰好出现一次**（0 次＝码已漂；多次＝会改错地方）
  4 MUST_ABSENT/PRESENT —— 旧写法必须消失、新写法必须存在（按调用点字面量数，不散文匹配）
  5 EOL_LOST / CR_INTRODUCED / ENDNL_LOST / FUNC_COUNT —— 换行血统与函数个数守恒
  6 WRITE_VERIFY     —— 写回后重读字节必须与内存里算出的那份逐字节相同

它测不到什么：⛔ 判这段代码是不是**对的**（对错由 tests/test_audit_1002_batch17_atomic_json_writes.py 判）；
⛔ 保证除这 5 个文件外无人改动（越界写由 scripts/audit_1002/probe_b17_landed_1002.py 用 git status 判）。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

NEW_MODULE_PATH = "butler/core/atomic_json.py"
NEW_MODULE_SRC = '''"""整份 JSON 的原子写：先写同目录的临时兄弟文件，再一次 rename 顶上去。

为什么要有它：仓里多处「就地截断写」（`open(path,"w")` 或 `Path.write_text` 直接盖目标文件）崩在写一半，
盘上就留下半截 JSON——下次读侧要么静默归零（别名/角色/设备被当成出厂状态），要么把异常抛穿成接口 500
（`doc/审计报告/_缺陷汇总_供审阅.md` 表行 45 :65、94 :114、109 :129、127 :147）。
仓内本已有 5 份各写各的原子实现，临时文件命名两种写法并存（`x.json.tmp` 与 `x.tmp`）⇒ 收敛到这一份。

临时文件用**固定名**（⛔ 随机后缀/uuid）：崩窗留下的固定名会被下一轮整文件覆盖，
随机后缀则每崩一次多一个孤儿，违反 `开发规范.md` 的 ⛔ .bak 堆积。

它测不到什么：⛔ fsync——只保证「rename 之前目标文件一个字节都不动」，⛔ 保证掉电后 tmp 里的字节已落物理盘
（这条取舍记在台账 §18-4：热路径每次命中都同步写盘，加 fsync 是把成本乘到每一次命中上）。
父目录由调用方负责（各站点本就各有自己的 mkdir 口径，归一化是另一件事）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def write_json_atomic(path, data) -> None:
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

IMPORT_ANCHOR = 'from butler.logging_setup import get_logger\n'
IMPORT_NEW = 'from butler.core.atomic_json import write_json_atomic\n' + IMPORT_ANCHOR

FILES = [
    {
        "path": "butler/devices.py",
        "md5": "3a44b4cf757d1079d87f7f8fa340d8cc", "lines": 218, "bytes": 10865, "cr": 218,
        "already": "            write_json_atomic(self.path, data)\n",
        "edits": [
            (IMPORT_ANCHOR, IMPORT_NEW),
            ('''    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {d.id: d.to_dict() for d in self.devices.values()}
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
''',
             '''    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {d.id: d.to_dict() for d in self.devices.values()}
            # 表行 45 P1-13：就地 write_text 崩在半路＝半截 devices.json，下次 load() 静默回退出厂种子
            write_json_atomic(self.path, data)
'''),
        ],
        "must_absent": ["self.path.write_text("],
        "must_present": ["from butler.core.atomic_json import write_json_atomic",
                         "write_json_atomic(self.path, data)"],
    },
    {
        "path": "butler/roles/store.py",
        "md5": "140750506a8471f26a6993bdc17f9a71", "lines": 218, "bytes": 10451, "cr": 0,
        "already": "            write_json_atomic(self.dir / f\"{role.id}.json\", role.to_dict())\n",
        "edits": [
            (IMPORT_ANCHOR, IMPORT_NEW),
            ('''    def _write(self, role: Role) -> None:
        try:
            (self.dir / f"{role.id}.json").write_text(
                json.dumps(role.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
''',
             '''    def _write(self, role: Role) -> None:
        try:
            # 表行 45 P1-13：半截 {id}.json 会让 load() 跳过该角色，ensure_defaults 再把出厂默认塞回来
            write_json_atomic(self.dir / f"{role.id}.json", role.to_dict())
'''),
        ],
        "must_absent": [").write_text("],
        "must_present": ["write_json_atomic(self.dir / f\"{role.id}.json\", role.to_dict())"],
    },
    {
        "path": "butler/roles/state.py",
        "md5": "267648a52b220bd4b70beca63c9f70cc", "lines": 48, "bytes": 1760, "cr": 48,
        "already": "            write_json_atomic(self.path, self._m)\n",
        "edits": [
            (IMPORT_ANCHOR, IMPORT_NEW),
            ('''    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._m, ensure_ascii=False, indent=2), encoding="utf-8")
''',
             '''    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # 同一份 role_state.json 的第二个写手（第一个是 memory/feeder.py）：就地截断写会盖坏对方刚绑好的会话
            write_json_atomic(self.path, self._m)
'''),
        ],
        "must_absent": ["self.path.write_text("],
        "must_present": ["write_json_atomic(self.path, self._m)"],
    },
    {
        "path": "butler/memory/feeder.py",
        "md5": "9c19089189583384056c99eff80e7d2f", "lines": 297, "bytes": 12549, "cr": 0,
        "already": "        write_json_atomic(self._role_state_path(), state)\n",
        "edits": [
            (IMPORT_ANCHOR, IMPORT_NEW),
            ('''    def _write_role_state(self, state: dict) -> None:
        path = self._role_state_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
''',
             '''    def _write_role_state(self, state: dict) -> None:
        # 表行 109 M-09：open(path,"w") + json.dump 先把文件截空再逐块写，崩在中途＝半截 JSON 落在盘上
        write_json_atomic(self._role_state_path(), state)
'''),
            ('''        except FileNotFoundError:
            return {}
''',
             '''        except FileNotFoundError:
            return {}
        except Exception as e:
            # 表行 109 M-09：只捕 FileNotFoundError，半截 JSON 会把异常抛穿成投喂接口全线 500
            logger.error("role_state.json unreadable, treat as empty: %s", e)
            return {}
'''),
        ],
        "must_absent": ['with open(path, "w", encoding="utf-8") as f:'],
        "must_present": ['write_json_atomic(self._role_state_path(), state)',
                         'role_state.json unreadable, treat as empty'],
    },
    {
        "path": "butler/core/fast_routes.py",
        "md5": "ef21bb6428050efe05145d001089aaf2", "lines": 151, "bytes": 5329, "cr": 0,
        "already": "        write_json_atomic(ROUTES_FILE, self.routes)\n",
        "edits": [
            ('''import json
import time
from pathlib import Path

ROUTES_FILE = Path("/app/data/fast_routes.json")
''',
             '''import json
import time
from pathlib import Path

from butler.core.atomic_json import write_json_atomic
from butler.logging_setup import get_logger

logger = get_logger("butler.core.fast_routes")

ROUTES_FILE = Path("/app/data/fast_routes.json")
'''),
            ('''    def _load(self):
        if ROUTES_FILE.exists():
            try:
                with open(ROUTES_FILE, encoding="utf-8") as f:
                    self.routes = json.load(f)
            except Exception:
                self.routes = []
        else:
            # 内置默认规则
            self.routes = self._builtin()
            self._save()

    def _save(self):
        ROUTES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ROUTES_FILE, "w", encoding="utf-8") as f:
            json.dump(self.routes, f, ensure_ascii=False, indent=2)
''',
             '''    def _load(self):
        if ROUTES_FILE.exists():
            try:
                with open(ROUTES_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    raise ValueError("fast_routes.json 顶层不是数组")
                self.routes = data
            except Exception as e:
                # 表行 94 T-04：回退空列表＝快速路由永久静默失效 ⇒ 兜底成内置规则。
                # ⛔ 在这里 _save()：盖回去＝抹掉现场，人工无从判断丢了哪些用户自加规则。
                logger.error("fast_routes.json unreadable, fallback to builtin rules: %s", e)
                self.routes = self._builtin()
        else:
            self.routes = self._builtin()
            self._save()

    def _save(self):
        ROUTES_FILE.parent.mkdir(parents=True, exist_ok=True)
        # 表行 127 T-06 的损坏半：就地截断写崩在中途＝整个规则文件只剩半截（性能半另议，见台账 §18 ⛔清单）
        write_json_atomic(ROUTES_FILE, self.routes)
'''),
        ],
        "must_absent": ['self.routes = []', 'with open(ROUTES_FILE, "w", encoding="utf-8") as f:'],
        "must_present": ["write_json_atomic(ROUTES_FILE, self.routes)",
                         "fallback to builtin rules",
                         'if not isinstance(data, list):'],
    },
]

# 本批⛔ 归队的既有原子实现：跑前跑后 md5 同号才算我没越界写（值由脚本现读，⛔ 手填）
UNTOUCHED = [
    "butler/core/aliases.py",
    "butler/api/config_routes.py",
    "butler/api/deps.py",
    "butler/triggers/store.py",
    "butler/skills/store.py",
]


def fail(tag, detail=""):
    raise SystemExit("%s|%s" % (tag, detail))


def shape(raw: bytes):
    text = raw.decode("utf-8")
    return (text, hashlib.md5(raw).hexdigest(), text.count("\n"), len(raw), raw.count(b"\r"))


def main():
    apply = "--apply" in sys.argv[1:]
    untouched_before = {rel: hashlib.md5((REPO / rel).read_bytes()).hexdigest() for rel in UNTOUCHED}
    for rel, got in untouched_before.items():
        print("UNTOUCHED_BEFORE|%s|md5=%s" % (rel, got))
    plan = []
    # 逐文件点名「是否已应用」：fail-fast 只会报第一个文件，看不出其余五处的闸门有没有咬合
    # （`already` 标记在 CRLF 文件里恒找不到就是靠这道预扫描暴露的，见 §18-10）。
    hits = []
    for spec in FILES:
        raw = (REPO / spec["path"]).read_bytes().decode("utf-8")
        hit = spec["already"].replace("\r\n", "\n") in raw.replace("\r\n", "\n")
        print("ALREADY_SCAN|%s|hit=%s" % (spec["path"], "yes" if hit else "no"))
        if hit:
            hits.append(spec["path"])
    if (REPO / NEW_MODULE_PATH).exists():
        hits.append(NEW_MODULE_PATH)
        print("ALREADY_SCAN|%s|hit=yes" % NEW_MODULE_PATH)
    else:
        print("ALREADY_SCAN|%s|hit=no" % NEW_MODULE_PATH)
    if hits:
        fail("ALREADY_APPLIED", ",".join(hits))
    for spec in FILES:
        p = REPO / spec["path"]
        if not p.exists():
            fail("FILE_ABSENT", spec["path"])
        text, md5, lines, nbytes, cr = shape(p.read_bytes())
        print("BASE_READ|%s|md5=%s|lines=%d|bytes=%d|CR=%d" % (spec["path"], md5, lines, nbytes, cr))
        for k, want in (("md5", spec["md5"]), ("lines", spec["lines"]),
                        ("bytes", spec["bytes"]), ("CR", spec["cr"])):
            got = {"md5": md5, "lines": lines, "bytes": nbytes, "CR": cr}[k]
            if got != want:
                fail("BASELINE_DRIFT", "%s|%s|got=%s|want=%s" % (spec["path"], k, got, want))

        crlf_file = cr > 0
        new_text = text
        for old, want_new in spec["edits"]:
            if "$(" in want_new or "`" in want_new:
                fail("INSERTED_PROSE_MANGLED", "%s|%r" % (spec["path"], want_new[:60]))
            if crlf_file:
                old = old.replace("\n", "\r\n")
                want_new = want_new.replace("\n", "\r\n")
            n = new_text.count(old)
            if n != 1:
                fail("ANCHOR_COUNT", "%s|got=%d|want=1|anchor=%r" % (spec["path"], n, old[:60]))
            new_text = new_text.replace(old, want_new, 1)

        lf_view = new_text.replace("\r\n", "\n")
        for needle in spec["must_absent"]:
            if needle in lf_view:
                fail("MUST_ABSENT_BROKEN", "%s|%r" % (spec["path"], needle))
        for needle in spec["must_present"]:
            if needle not in lf_view:
                fail("MUST_PRESENT_MISSING", "%s|%r" % (spec["path"], needle))

        before_fns = text.count("\n    def ") + text.count("\ndef ")
        after_fns = new_text.count("\n    def ") + new_text.count("\ndef ")
        if before_fns != after_fns:
            fail("FUNC_COUNT", "%s|before=%d|after=%d" % (spec["path"], before_fns, after_fns))
        new_cr = new_text.count("\r")
        new_lines = new_text.count("\n")
        if crlf_file:
            if new_cr != new_lines:
                fail("EOL_MIXED", "%s|CR=%d|LINES=%d" % (spec["path"], new_cr, new_lines))
        elif new_cr != 0:
            fail("CR_INTRODUCED", "%s|CR=%d" % (spec["path"], new_cr))
        if not new_text.endswith("\n") and not new_text.endswith("\r\n"):
            fail("ENDNL_LOST", spec["path"])

        out = new_text.encode("utf-8")
        print("PLAN|%s|lines %d->%d|bytes %d->%d|CR %d->%d|md5->%s"
              % (spec["path"], lines, new_lines, nbytes, len(out), cr, new_cr,
                 hashlib.md5(out).hexdigest()))
        plan.append((p, out, hashlib.md5(out).hexdigest(), spec["path"]))

    np = REPO / NEW_MODULE_PATH
    src = NEW_MODULE_SRC.encode("utf-8")
    if b"\r" in src or not src.endswith(b"\n") or "$(" in NEW_MODULE_SRC:
        fail("NEW_MODULE_SHAPE", "CR=%d|endnl=%s" % (src.count(b"\r"), src.endswith(b"\n")))
    print("PLAN|NEW|%s|lines=%d|bytes=%d|CR=0|md5=%s"
          % (NEW_MODULE_PATH, NEW_MODULE_SRC.count("\n"), len(src), hashlib.md5(src).hexdigest()))

    if not apply:
        print("DRY_RUN|未落盘|加 --apply 执行一次")
        return 0

    writes = plan + [(np, src, hashlib.md5(src).hexdigest(), NEW_MODULE_PATH)]
    for p, out, want_md5, rel in writes:
        p.write_bytes(out)
    for p, out, want_md5, rel in writes:
        raw = p.read_bytes()
        if hashlib.md5(raw).hexdigest() != want_md5:
            fail("WRITE_VERIFY", "%s|got=%s|want=%s" % (rel, hashlib.md5(raw).hexdigest(), want_md5))
        print("WRITTEN|%s|md5=%s|bytes=%d" % (rel, want_md5, len(raw)))

    for rel in UNTOUCHED:
        raw = (REPO / rel).read_bytes()
        got = hashlib.md5(raw).hexdigest()
        want = untouched_before[rel]
        if got != want:
            fail("COLLATERAL_WRITE", "%s|before=%s|after=%s" % (rel, want, got))
        print("UNTOUCHED|%s|md5=%s|同号" % (rel, got))
    print("APPLIED|%d files|stamp=%s" % (len(writes), hashlib.md5(b"".join(w[1] for w in writes)).hexdigest()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
