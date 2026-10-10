"""批171（D20 收尾）：requirements.txt 的备注⛔ 讲一件盘上找不到的事。

开窗现读（本批先逐条点开）：备注那句「fastapi：唯一 import 者是
`butler/api/people_routes.py`」——**那个路径已不存在**。DCD 20261008「DB 孤儿资产处置」裁乙
把它挪进了 `scripts/contrib/`（执行 commit `cfa55a6`），那文件头注现在自己写着「未接线半成品／
⛔ 为其加依赖」。**备注没跟着改** ⇒ 谁照它去 `butler/api/` 找那枚路由，只会找到一堵墙；更糟的是
「处置归 DCD 裁定（见 doc/决策申请/）」读起来像**还挂着**，而裁早已下完并执行。

备注这类东西的特殊之处：它⛔ 被 import 检查、⛔ 被类型检查、⛔ 进不了任何门的依赖图，
**全部的产出就是"那一句关于树的话是真的"**。所以本批的尺⛔ 测形制，只测三件事：
  A 引用路径必须盘上存在（本批的 RED 就在这臂）；
  B 声称的「N 个 butler/ 模块直接 import（名单）」必须现读数得出来，且名单每个名字都能在一枚
    现读进口者路径里找到（名单⊆现读、数量==现读 ⇒ 两向都钉，⛔ 只数总数）；
  C 「⛔ 进镜像／裁定前不为其加依赖」点名的包，⛔ 同时出现在依赖行里（自己跟自己打架）。

负控六格打在**合成件**上：干净件应当放行 1 格；A 臂咬 1 格；B 臂各咬 2 格（总数漂移／名单里
那枚名字根本⛔ 是进口者）；C 臂各咬 2 格（两种措辞——真件用的是"仍⛔ 为其加依赖"那一种，
关键字表只认另一种的话，fastapi 这条等于没在眼下）。⛔ 为了咬去拗生产码。
运行时腿只读盘＋⛔ 装包⛔ 起容器⛔ 动 `.env`：A/B/C 全是树与文本的形状。
"""
from __future__ import annotations

import pathlib
import re
import unittest

TREE = pathlib.Path(__file__).resolve().parents[1]
REQ = TREE / "requirements.txt"

# 备注里引用的"仓库路径"：必须是这四段开头、且带 .py 的（目录形态如 doc/决策申请/ ⛔ 算引用文件）
CITED = re.compile(r"\b((?:butler|scripts|tests|doc|integrations)/[\w./\-]+\.py)\b")
# 「6 个 butler/ 模块直接 import」
COUNT_CLAIM = re.compile(r"(\d+)\s*个\s*butler/\s*模块直接\s*import（([^）]*)）")
# 备注行的包名段：「# - aiohttp==3.9.5 = …」／「# - requests：…」
DEP_IN_NOTE = re.compile(r"^#\s*-\s*([A-Za-z][A-Za-z0-9._\-]*)")
# 「不加依赖」的几种写法（"为其加依赖" 一条同时吃 "⛔ 为其加依赖" 与 "不为其加依赖"）
NO_ADD = ("进镜像", "为其加依赖", "不为其加依赖", "⛔ 加依赖")


def requirement_names(text: str) -> set[str]:
    """依赖行的包名（去 extras／版本／注释，大小写归一）。"""
    out = set()
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        s = s.split("#", 1)[0].strip()
        s = re.split(r"[<>=!;\[]", s, 1)[0].strip()
        if s:
            out.add(s.lower())
    return out


def live_butler_importers(tree: pathlib.Path, dep: str) -> list[str]:
    """butler/ 下**真的** import 这个包的模块路径（按相对路径给，⛔ 数名字）。"""
    hits = []
    root = tree / "butler"
    if not root.is_dir():
        return hits
    pat = re.compile(r"^\s*(?:import|from)\s+" + re.escape(dep) + r"\b", re.M)
    for p in sorted(root.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        try:
            if pat.search(p.read_text(encoding="utf-8", errors="replace")):
                hits.append(p.relative_to(root).as_posix())
        except OSError:
            continue
    return hits


def note_problems(req_text: str, tree: pathlib.Path) -> list[str]:
    """三臂共用一把尺：返回**逐条点名**的问题清单，空＝放行。"""
    problems: list[str] = []
    note_lines = [l for l in req_text.splitlines() if l.strip().startswith("#")]
    body = "\n".join(note_lines)

    # A：备注引用的路径必须盘上存在
    for path in sorted(set(CITED.findall(body))):
        if not (tree / path).is_file():
            problems.append(f"A-CITED-MISSING {path}")

    # B：声称的 butler/ 进口模块数与名单，必须现读数得出来
    for m in COUNT_CLAIM.finditer(body):
        claimed = int(m.group(1))
        listed = [x.strip() for x in m.group(2).split("/") if x.strip()]
        # 名单所属的包＝该备注行开头的包名
        line_start = body.rfind("\n#", 0, m.start())
        d = DEP_IN_NOTE.match(body[line_start + 1:]) if line_start != -1 else None
        dep = d.group(1) if d else ""
        if not dep:
            problems.append(f"B-NO-DEP-NAME 计数声明无所属包：{m.group(0)[:40]}")
            continue
        live = live_butler_importers(tree, dep)
        if claimed != len(live):
            problems.append(f"B-COUNT-DRIFT {dep} 备注={claimed} 现读={len(live)}")
        for name in listed:
            if not any(name in p for p in live):
                problems.append(f"B-LISTED-NOT-IMPORTER {dep} 名单里的 {name} 现读⊆进口者")

    # C：声明「⛔ 进镜像／不为其加依赖」的包，⛔ 同时是依赖行
    declared = requirement_names(req_text)
    for line in note_lines:
        if not any(k in line for k in NO_ADD):
            continue
        d = DEP_IN_NOTE.match(line)
        if d and d.group(1).lower() in declared:
            problems.append(f"C-SELF-CONTRADICTION {d.group(1)} 备注说不加，依赖行里却有")

    return problems


# ── 合成件：应当放行的干净件（证明这把尺⛔ 恒红）────────────────────────
CLEAN_FIXTURE = """\
httpx==0.27.2

# 备注：
# - aiohttp==3.9.5 = 现读值，1 个 butler/ 模块直接 import（m）
# - requests：仅 scripts/host_tool.py 使用 ⇒ ⛔ 进镜像
"""

# 三枚各咬一臂的脏件（⛔ 一件咬三处——那样分不清哪臂活着）
BAD_CITED = CLEAN_FIXTURE.replace("scripts/host_tool.py", "scripts/gone_tool.py")
BAD_COUNT = CLEAN_FIXTURE.replace("1 个 butler/ 模块直接 import（m）",
                                  "9 个 butler/ 模块直接 import（m）")
BAD_LISTED = CLEAN_FIXTURE.replace("import（m）", "import（ghost）")
BAD_CONTRA = "aiohttp==3.9.5\nrequests==2.31.0\n\n# - requests：仅宿主机脚本使用 ⇒ ⛔ 进镜像\n"
# 同一臂、换一种措辞（真件的 fastapi 那条用的是这一种）⇒ ⛔ 关键字表只吃一种写法
BAD_CONTRA_ALT = "aiohttp==3.9.5\nrequests==2.31.0\n\n# - requests：裁已下，仍⛔ 为其加依赖\n"


def synthetic_tree(root: pathlib.Path) -> pathlib.Path:
    (root / "butler").mkdir(parents=True, exist_ok=True)
    (root / "butler" / "m.py").write_text("import aiohttp\n", encoding="utf-8")
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "host_tool.py").write_text("import requests\n", encoding="utf-8")
    return root


class CalibrationTest(unittest.TestCase):
    """负控：尺必须先证明它会咬，而且各臂单独咬。"""

    def setUp(self):
        import tempfile
        self.td = pathlib.Path(tempfile.mkdtemp())
        self.tree = synthetic_tree(self.td)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def test_clean_fixture_passes(self):
        self.assertEqual(note_problems(CLEAN_FIXTURE, self.tree), [],
                         "干净合成件被咬 ⇒ 这把尺恒红，⛔ 当门用")

    def test_missing_cited_path_bits(self):
        got = note_problems(BAD_CITED, self.tree)
        self.assertEqual([g for g in got if g.startswith("A-")],
                         ["A-CITED-MISSING scripts/gone_tool.py"], got)
        self.assertEqual([g for g in got if not g.startswith("A-")], [], got)

    def test_count_drift_bits(self):
        got = note_problems(BAD_COUNT, self.tree)
        self.assertEqual([g for g in got if g.startswith("B-")],
                         ["B-COUNT-DRIFT aiohttp 备注=9 现读=1"], got)
        self.assertEqual([g for g in got if not g.startswith("B-")], [], got)

    def test_listed_name_not_an_importer_bits(self):
        got = note_problems(BAD_LISTED, self.tree)
        self.assertEqual([g for g in got if g.startswith("B-")],
                         ["B-LISTED-NOT-IMPORTER aiohttp 名单里的 ghost 现读⊆进口者"], got)

    def test_declared_but_refused_bits(self):
        got = note_problems(BAD_CONTRA, self.tree)
        self.assertEqual([g for g in got if g.startswith("C-")],
                         ["C-SELF-CONTRADICTION requests 备注说不加，依赖行里却有"], got)

    def test_refusal_wording_variant_also_bits(self):
        """真件用的是「⛔ 为其加依赖」这一种措辞 ⇒ 关键字表⛔ 只认一种（否则 fastapi 那条根本没在眼下）。"""
        got = note_problems(BAD_CONTRA_ALT, self.tree)
        self.assertEqual([g for g in got if g.startswith("C-")],
                         ["C-SELF-CONTRADICTION requests 备注说不加，依赖行里却有"], got)


class ProductionNoteTest(unittest.TestCase):
    """正网件：真 requirements.txt 对真树，三臂必须全绿。"""

    @classmethod
    def setUpClass(cls):
        if not REQ.is_file():
            raise unittest.SkipTest(f"requirements 不在位：{REQ}")
        cls.text = REQ.read_text(encoding="utf-8")
        cls.problems = note_problems(cls.text, TREE)

    def test_notes_have_no_untrue_claim(self):
        self.assertEqual(self.problems, [],
                         "备注里这些句子关于树的话已不成立：\n" + "\n".join(self.problems))

    def test_butler_has_no_fastapi_importer(self):
        """这格是「⛔ 为 fastapi 加依赖」这句话的**现读支点**：哪天 butler/ 里长出
        一枚 fastapi 路由（或那条 starlette 重写做完了），这句话就翻，这里当场红。"""
        live = live_butler_importers(TREE, "fastapi")
        self.assertEqual(live, [], f"butler/ 里出现了 fastapi 进口者：{live}")

    def test_aiohttp_version_line_is_present(self):
        """备注钉的是 `aiohttp==3.9.5`（活容器现读值）⇒ 依赖行必须仍是那一枚，
        ⛔ 备注与依赖行各说各话。"""
        self.assertIn("aiohttp==3.9.5", self.text)
        self.assertIn("aiohttp", requirement_names(self.text))


if __name__ == "__main__":
    unittest.main()
