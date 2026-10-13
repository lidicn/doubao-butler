"""批172（D21 的 `*.db` 腿）＋批208（10-13 出资人改口：用户数据⛔ 入库）。

报告原文（第十九轮 §三／最终轮 §处置）的处方：`data/`、`*.db` 都照 D19 那样加前导斜杠。
`data/` 那半条 10-09 已落、现读形制＝`.gitignore:/data/*` ＋ `/data/roles/*`（**⛔ 归档区豁免行**，
见下文 S3 臂：10-13 出资人判「那五枚是用户数据，不是程序的一部分」，豁免作废）。
`*.db` 这半条⛔ 照抄，理由是把两件事分开了看：

  D19 的病＝**源代码被无声吞掉**（`tts/` 把 `butler/tts/` 整包 .py 吞了）⇒ 药是**按路径收窄**。
  `*.db` 现在兜的是**运行期产物**：仓里现读 0 枚 `.db/.db-wal/.db-shm`（⛔ 已发生的损失＝没有），
  一旦收窄成 `/*.db`，下一个把 sqlite 落在 `butler/` 或 `tests/fixtures/` 的脚本就**不再被兜住**——
  而本仓 remote 是**公开仓**（10-13 现读 `api.github.com/repos/lidicn/doubao-butler` ⇒ `private=False`）。
  少一个 fixture 能重生成，多一枚含真实消息／口令的运行库进了 git 历史⛔ 撤不回来。两害不同权重。

臂次：S＝纯文本形状（哪面都能跑）；E＝机制腿，需要 git 可执行＋能建临时仓库，⛔ git 的那面上
这一格是 NOT_MEASURED（会打 skip 原因）⇒ **S 腿照常跑，⛔ 整档静默全跳**。

⚠ 本档口径覆盖 10-13 早先写下的「归档件是行为件、要入库」那格（丙案）——那条已被出资人推翻，
S3 与机制腿一起倒向。划界按**内容**而不是按文件类型：头像（真人照片生成、文件名即人名）与
归档名册（家人英文名＋人设文本）＝用户数据⇒ S5 必须逐条忽略；PWA 图标与 `images/doubao.png`
被 `manifest.webmanifest`／`index.html`／`sw.js` 直接引用＝程序件⇒ S6 反过来禁止把它们忽略
（忽略了＝新鲜 clone 的图标全 404，且这正是要避开 D19 那种「按类型一网打」的写法）。
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import tempfile
import unittest

TREE = pathlib.Path(__file__).resolve().parents[1]
GITIGNORE = TREE / ".gitignore"

# 必须**裸**（不限深度）的形态：兜运行期产物，落哪儿都算
BARE_REQUIRED = ("*.db", "*.db-wal", "*.db-shm")
# 必须**锚到根**的形态：D19/D21 收编过的目录类，放宽过＝回到原判例
ANCHORED_FORMS = {"/data/": ("/data/", "/data/*"), "/tts/": ("/tts/",)}
# 10-13 出资人改口：`data/roles/_archive/*.json`（归档角色名册）是**用户数据**，⛔ 入库。
# 早先那条例外豁免（`!/data/roles/_archive/`）出现在 .gitignore 里＝把用户数据往公开仓放回去。
USERDATA_EXEMPTION_FORBIDDEN = ("!/data/roles/_archive/", "!/data/roles/", "!data/")
# 用户数据目录：必须**逐条显式忽略**（⛔ 靠裸图型一网打——那是 D19 的同病，只是方向相反）
USERDATA_DIRS_MUST_BE_IGNORED = (
    "butler/static/avatars/",   # 按真人照片生成的头像，文件名即人名
    "data/roles/_archive/",     # 归档角色名册（含家人英文名与人设文本）
)
# 程序件：被 manifest / index.html / sw.js 逐条引用，忽略掉＝新鲜 clone 图标 404。
# 这一臂的方向与 S5 相反：它们出现在忽略行里才算病。
PROGRAM_ASSET_DIRS_FORBIDDEN = (
    "butler/static/icons/",
    "butler/dashboard_pwa/icons/",
    "butler/static/images/",
)
# 兜调试产物⛔ 用裸图型：它按**类型**而非**路径**划界，将来要入库的合法资产会被它无声吞掉
BLANKET_IMAGE_FORBIDDEN = ("*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp")


def gitignore_problems(text: str) -> list[str]:
    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]
    problems = []
    for pat in BARE_REQUIRED:
        if pat not in lines:
            problems.append(f"S1-MISSING-BARE {pat}（收窄了：子目录里的运行库不再被兜住）")
        if "/" + pat in lines:
            problems.append(f"S1-ANCHORED {pat} 被钉成了 /{pat}（同上一条的另一 half）")
    for pat, forms in ANCHORED_FORMS.items():
        if not any(f in lines for f in forms):
            problems.append(f"S2-MISSING-ANCHOR {pat}")
        bare = pat.lstrip("/")
        if bare in lines:
            problems.append(f"S2-UNANCHORED {bare}（D19 原病：会把 butler/{bare} 这类源码目录吞掉）")
    for pat in USERDATA_EXEMPTION_FORBIDDEN:
        if pat in lines:
            problems.append(f"S3-USERDATA-EXEMPTION {pat}（10-13 出资人：归档名册是用户数据，⛔ 入库）")
    for pat in BLANKET_IMAGE_FORBIDDEN:
        if pat in lines:
            problems.append(f"S4-BLANKET-IMAGE {pat}（裸图型按类型划界＝D19 同病的反向形状）")
    for d in USERDATA_DIRS_MUST_BE_IGNORED:
        if d not in lines:
            problems.append(f"S5-USERDATA-NOT-IGNORED {d}")
    for d in PROGRAM_ASSET_DIRS_FORBIDDEN:
        if d in lines:
            problems.append(f"S6-PROGRAM-ASSET-IGNORED {d}（图标/logo 是程序件，被 manifest·sw.js 引用）")
    return problems


CLEAN_TEXT = "\n".join(["# x", "/data/*", "/data/roles/*", "/tts/",
                        "screenshot*.png", "/*.png",
                        "butler/static/avatars/", "data/roles/_archive/",
                        "*.db", "*.db-wal", "*.db-shm", "tmp/"]) + "\n"
PRESCRIPTION_TEXT = CLEAN_TEXT.replace("*.db", "/*.db")
D19_REGRESS_TEXT = CLEAN_TEXT.replace("/tts/", "tts/")
EXEMPTION_BACK_TEXT = CLEAN_TEXT.replace("data/roles/_archive/",
                                         "!/data/roles/_archive/\ndata/roles/_archive/")
# 我这侧 10-13 落地前的真实形制（`*.png` 裸着、根锚定那半条也没有）
IMAGE_BLANKET_TEXT = CLEAN_TEXT.replace("/*.png", "*.png")
# 用户数据目录的忽略行被抹掉＝头像/名册重新变成「未跟踪但可 `git add -A` 一把带走」
USERDATA_ARM_LOSS_TEXT = CLEAN_TEXT.replace("butler/static/avatars/\n", "")
# 反过来：把程序件（图标/logo）也一并忽略＝D19 的「按类型一网打」换了个方向复发
PROGRAM_BLANKET_TEXT = CLEAN_TEXT + "\n".join(PROGRAM_ASSET_DIRS_FORBIDDEN) + "\n"


def run_ignore_probe(gitignore_text: str, files: dict[str, str]) -> dict[str, str | None]:
    """在一次性空仓库里放一份 .gitignore 与若干探针文件，返回每枚探针**被哪条规则**吃掉。

    None ＝ NOT-IGNORED（git check-ignore rc=1 或该路径不在输出里）。
    """
    td = pathlib.Path(tempfile.mkdtemp(prefix="b172_probe_"))
    try:
        subprocess.run(["git", "init", "-q"], cwd=td, capture_output=True, check=True)
        (td / ".gitignore").write_text(gitignore_text, encoding="utf-8")
        for rel in files:
            p = td / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(files[rel], encoding="utf-8")
        out = subprocess.run(["git", "check-ignore", "--verbose"] + list(files),
                             cwd=td, capture_output=True, text=True)
        got: dict[str, str | None] = {}
        for line in out.stdout.splitlines():
            f = line.split("\t")
            if len(f) >= 2:
                got[f[1]] = f[0]
        return {rel: got.get(rel) for rel in files}
    finally:
        shutil.rmtree(td, ignore_errors=True)


class CalibrationTest(unittest.TestCase):
    """尺先证会咬：干净件放行，处方件与三种回退件各咬在自己那一臂。"""

    def test_clean_shape_passes(self):
        self.assertEqual(gitignore_problems(CLEAN_TEXT), [], "干净件被咬 ⇒ 尺恒红")

    def test_prescription_bits_the_bare_arm(self):
        got = gitignore_problems(PRESCRIPTION_TEXT)
        self.assertTrue(got, "报告的处方（`/*.db`）本该被咬，却判绿 ⇒ 这把尺是摆设")
        self.assertTrue(all(g.startswith("S1-") for g in got), got)
        self.assertEqual(len(got), len(BARE_REQUIRED) * 2, got)

    def test_d19_revert_bits_the_anchor_arm(self):
        got = gitignore_problems(D19_REGRESS_TEXT)
        self.assertEqual(got, [
            "S2-MISSING-ANCHOR /tts/",
            "S2-UNANCHORED tts/（D19 原病：会把 butler/tts/ 这类源码目录吞掉）",
        ], got)

    def test_exemption_coming_back_bits_the_userdata_arm(self):
        """负控：有人把「归档件是行为件」那条例外豁免重新加回来＝用户数据回公开仓 ⇒ 只咬 S3。"""
        got = gitignore_problems(EXEMPTION_BACK_TEXT)
        self.assertEqual([g for g in got if g.startswith("S3-")],
                         [f"S3-USERDATA-EXEMPTION !/data/roles/_archive/"
                          f"（10-13 出资人：归档名册是用户数据，⛔ 入库）"], got)
        self.assertEqual([g for g in got if not g.startswith("S3-")], [],
                         f"其余臂不该连带误报：{got}")

    def test_image_blanket_bits_the_shape_arm(self):
        """负控：把 `/*.png` 换回裸 `*.png`＝我这侧 10-13 前的真形制 ⇒ 只咬 S4 一条。"""
        got = gitignore_problems(IMAGE_BLANKET_TEXT)
        self.assertEqual(got, [
            "S4-BLANKET-IMAGE *.png（裸图型按类型划界＝D19 同病的反向形状）",
        ], got)

    def test_userdata_ignore_loss_bits(self):
        """负控：抹掉一枚用户数据目录的忽略行 ⇒ 只咬 S5 一条，⛔ 连带报别的臂。"""
        got = gitignore_problems(USERDATA_ARM_LOSS_TEXT)
        self.assertEqual(got, [f"S5-USERDATA-NOT-IGNORED butler/static/avatars/"], got)

    def test_program_asset_ignore_bits_the_other_direction(self):
        """负控（与 S5 反方向）：把图标/logo 也忽略掉＝按类型划界的病换边复发 ⇒ 只咬 S6。"""
        got = gitignore_problems(PROGRAM_BLANKET_TEXT)
        self.assertEqual([g.split("（")[0] for g in got],
                         [f"S6-PROGRAM-ASSET-IGNORED {d}" for d in PROGRAM_ASSET_DIRS_FORBIDDEN],
                         got)

    def test_mechanism_probe_distinguishes(self):
        """Arm E 的**尺的尺**：同一跑法必须能分出「被哪条吃掉」与「没被吃」。"""
        probe = run_ignore_probe("*.zzz\n/keep/\n", {
            "butler/deep/x.zzz": "", "keep/a.zzz": "", "root.zzz": "", "butler/plain.py": "",
        })
        self.assertIsNotNone(probe["butler/deep/x.zzz"], "裸模式没兜住子目录＝跑法本身坏了")
        self.assertIsNotNone(probe["root.zzz"])
        self.assertIsNone(probe["butler/plain.py"], "无关文件被误判成 ignored ⇒ 跑法⛔ 有分辨力")


class ProductionShapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not GITIGNORE.is_file():
            raise unittest.SkipTest(f".gitignore 不在位：{GITIGNORE}")
        cls.text = GITIGNORE.read_text(encoding="utf-8")

    def test_shipped_gitignore_shape_is_as_ruled(self):
        self.assertEqual(gitignore_problems(self.text), [],
                         "运行库兜底被收窄／目录锚定被放宽／用户数据豁免回潮：\n"
                         + "\n".join(gitignore_problems(self.text)))


class ProductionMechanismTest(unittest.TestCase):
    """Arm E：拿**真 .gitignore** 在隔离仓库里跑机制，⛔ 碰本仓、⛔ 装包。"""

    @classmethod
    def setUpClass(cls):
        if not GITIGNORE.is_file():
            raise unittest.SkipTest(f".gitignore 不在位：{GITIGNORE}")
        if shutil.which("git") is None:
            raise unittest.SkipTest("NOT_MEASURED：这一面没有 git 可执行，机制腿跑不了（形状腿 S 照常）")
        cls.text = GITIGNORE.read_text(encoding="utf-8")

    def test_runtime_db_anywhere_is_still_caught(self):
        probe = run_ignore_probe(self.text, {"butler/x.db": "", "butler/store/y.db-wal": ""})
        for rel, src in probe.items():
            self.assertIsNotNone(src, f"{rel} 没被兜住（真库含消息与口令，进 git 历史撤不回）")

    def test_source_package_under_tts_is_not_eaten(self):
        """D19 的**现网形状**判据：`butler/tts/` 下的 .py 必须⛔ 被 .gitignore 吃掉。"""
        probe = run_ignore_probe(self.text, {"butler/tts/engine.py": "", "butler/tts/sub/a.py": ""})
        for rel, src in probe.items():
            self.assertIsNone(src, f"{rel} 被 {src} 吞掉＝D19 复发")

    def test_data_dir_root_still_caught(self):
        probe = run_ignore_probe(self.text, {"data/live.db": "", "data/sub/live.db": ""})
        for rel, src in probe.items():
            self.assertIsNotNone(src, f"{rel} 未被 /data/ 兜住")

    def test_userdata_paths_are_caught_and_source_paths_are_not(self):
        """机制腿（真 .gitignore）：用户数据目录必须被兜住，⛔ 顺手把源码吞掉。

        头像是按真人照片生成的（文件名即人名）、归档名册含家人英文名——这两类进公开仓
        撤不回来；而 `butler/static/js/app.js` 这类**程序资产**⛔ 被同一条规则带走。
        """
        userdata = {
            "butler/static/avatars/kevin.png": "",
            "butler/static/avatars/lidicn.png": "",
            "data/roles/_archive/caesar.json": "",
            "data/roles/_archive/sub/luna.json": "",
        }
        source = {"butler/static/js/app.js": "", "butler/static/index.html": "",
                  "butler/static/icons/icon-192.png": "",
                  "butler/static/images/doubao.png": "",
                  "butler/dashboard_pwa/icons/icon-512.png": "",
                  "butler/dashboard_pwa/manifest.webmanifest": ""}
        probe = run_ignore_probe(self.text, dict(userdata, **source))
        for rel in userdata:
            self.assertIsNotNone(probe[rel], f"{rel} 没被兜住＝用户数据可以被 add 进公开仓")
        for rel in source:
            self.assertIsNone(probe[rel], f"{rel} 被 {probe[rel]} 吞掉＝程序资产进不了库")

    def test_debug_pngs_still_caught_after_narrowing(self):
        """收窄⛔ 等于放开：根下散落的调试截图与 `screenshot*` 仍要兜住。"""
        probe = run_ignore_probe(self.text, {"shot.png": "", "screenshot-1013.png": "",
                                             "butler/static/js/keepme.png": ""})
        self.assertIsNotNone(probe["shot.png"], "根下散落图没兜住")
        self.assertIsNotNone(probe["screenshot-1013.png"], "screenshot 前缀腿断了")
        self.assertIsNone(probe["butler/static/js/keepme.png"], "深路径的合法资源被裸图型吞了＝D19 同病")


if __name__ == "__main__":
    unittest.main()
