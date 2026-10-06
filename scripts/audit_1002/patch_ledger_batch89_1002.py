"""一次性追加器：把 DCD 六件裁定的收册段（§10）追加到 `doc/审计报告分诊台账-20261002.md` 末尾。

裁定文书＝`E:/NAS/关键决策部/decisions/20261002-DB六件影子代码-裁定.md`。
追加型改动＝最强护栏是「旧文本必须是新文本的前缀」，加上改前改后各验一次 CR／末行换行符／
行数／`^## `＋`^### ` 标题计数，以及本节读数的 must_have 探针。施加过再跑必 raise（BASE_MD5 对不上）。
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

TGT = pathlib.Path("doc/审计报告分诊台账-20261002.md")
BASE_MD5 = "eb4e39784cee7bf012ffd33c3e1a94c4"
BASE_BYTES = 58758
BASE_LINES = 424
BASE_H2 = 9
BASE_H3 = 6
CRB = b"\r"
LF = b"\n"

SECTION = """---

## §10 DCD 六件裁定收册 · 批8（⑥①）＋批9（④ 零受害者半）（2026-10-02）

本节只加读数与新形状，⛔ 改动上面各节已定稿的字（追加器＝`scripts/audit_1002/patch_ledger_batch89_1002.py`，
其最强护栏是「旧文本是新文本的前缀」）。裁定＝`E:/NAS/关键决策部/decisions/20261002-DB六件影子代码-裁定.md`。

### §10-1 六件各自的落点（哪件落了哪笔／哪件只落了一半）

| 件 | 裁 | 代码落点 | 提交 | 状态 |
|---|---|---|---|---|
| ⑥ `butler/engine.py` | A（删） | `git rm` 该文件（−249 行，零 importer 先自验） | `3514c14` | 已落码；容器挂载面当场就没有了（删是即时生效的那类） |
| ① 记忆取最新 | A | `butler/memory/extractor.py` `:114` `ORDER BY ts DESC` ＋ `:142` `result.reverse()`（取最新一截、喂模型仍升序，不动调用方） | `3514c14` | 已落码-未生效（.py 要 restart） |
| ④ 输出白名单 | B | 四枚：`schema.py`（名单外类型＝当场报错）、`templates.py`（删造哑技能的模板项）、`sandbox.py`（审批门改按 `brain.engine`，恒空的危险输出常量删掉）、`conflict.py`（实体改从 `brain.entity` 取） | `8c9f631` | **半件**：已落＝"类型非法"这条；未落＝"整段形状非法"（＝§10-4，新开了一件决策申请） |
| ② 模式行为规则 | A（接上三入口） | 待做（`tts/`、`notify/router.py`、`skills/runner.py` 三处各查一次；ALERT 必须豁免） | — | ⛔ 未开工 |
| ③ 优先级 TTL | A（落地） | 待做（进风控那一刻起算＋过期分支＋审计腿） | — | ⛔ 未开工 |
| ⑤ TTS 过载语义 | A（一套队列归一） | 待做（`tts/queue.py` 归一；过载丢最低优先级、保 ALERT／critical、⛔ 吞当前条） | — | ⛔ 未开工 |

跟办三条（`...裁定.md:104`）：跟办 1（`security_level` 登记为独立议题）＝本节 §10-5 末已登记；
跟办 2（`trigger_cooldowns.json` 未按 20260928 裁定进 SQLite＝欠执行非待裁）＝§4 那行已在册，排下一批；
跟办 3（④⑤ 验收必须运行时腿，⛔ 只 grep 文本）＝§10-3 按这条把 12 条腿分成运行时腿与源码腿。

### §10-2 批8（⑥①）的读数

- ①：`extractor.py` md5 `93353406af1459aa86e3993ca62dd39c` → `19c44dd6abbf36920a6cb24b9f22da33`，328→334 行，13,461→13,803 B，CR 0，定义数 7 不变。
  验收＝`tests/test_audit_1002_batch8_dcd6.py` 4 条腿，红半读数 `Ran 4 / failures=3`。
  旧行为取证＝`scripts/audit_1002/probe_old_extract_1002.py`（真旧码经 `git show aee7a2e:butler/memory/extractor.py` 落 `/tmp` 再加载，⛔ 手写复刻），
  两枚读数 `OLD|PLAIN|n=5|seqs=['00','01','02','03','04']`／`OLD|NOISE_IS_NEWEST|...`（＝改前 LIMIT 切走的是最旧一截，噪声排在最新侧时照样学旧事）。
- ⑥：删前零 importer 自验；删后 `docker exec ls /app/butler/engine.py` 无此文件、`/api/health`=200、容器 `Up 10 hours (healthy)`；
  10 分钟日志窗分母 `TOTAL_LINES=93`，其中 ModuleNotFound／ImportError／Traceback／`butler.engine` 四种命中各 0。

### §10-3 批9（④ 已落那半）的红绿两半

四枚生产文件指纹（改前 → 改后，全部 LF／CR 0）：
`schema.py 3888cd453566 → 7cc1a5f35c71`（212→220 行）、`templates.py 36d0b6395b32 → 403c3c57607c`（320→321）、
`sandbox.py 924b2d9d8170 → 049a506a61e5`（197→185）、`conflict.py 7e71603bcf9d → 5030ce774c2a`（356→361）。

- **红半跑的是真旧码**（⛔ 工作树 checkout、⛔ 手写复刻）：`cp -r butler tests /tmp/dcd4_red` 后用
  `git show HEAD:butler/skills/{schema,templates,sandbox,conflict}.py` 把四枚覆回 HEAD 版；
  复本四枚 md5 与上面「改前」列逐字节同号＝它确实是那一份。读数 `Ran 12 tests in 0.071s / FAILED (failures=6)`，
  六条红腿＝`SchemaWhitelistTest.test_unknown_output_type_is_an_error_not_silently_stripped`、
  `SchemaWhitelistTest.test_unknown_type_beside_valid_one_is_still_an_error`、
  `TemplateGeneratorTest.test_no_builtin_template_emits_unadmissible_output`、
  `SandboxGateTest.test_needs_review_bites_for_ha_action_engine`、
  `SandboxGateTest.test_dead_risky_outputs_constant_is_gone`、
  `ConflictGateTest.test_action_conflict_bites_for_same_entity_via_brain_engine`。
- **绿半**＝`Ran 12 tests in 0.041s / OK`，同一次命令的 `date -u` 原样行 `2026-10-02T11:45:07Z epoch=1790941507`。
- 跟办 3 的分法：这 12 条腿里 11 条是直接调用 `validate_skill`／`needs_review`／`assess_risk`／冲突检测器的**运行时腿**；
  `test_dead_risky_outputs_constant_is_gone` 是**源码级**腿，只证那枚恒空常量没留下，测不到行为。

### §10-4 未落的另一半＝本台账新开的一件待裁（⛔ 开发位自落）

裁定 B 的第 2 条说的是「名单外的**类型**」，我已落。盘上另有一类**整段形状**非法（`output` 是对象不是列表），
严格化后 `store.py:55-58` 那条 `if err: ... continue` 会**跳过整只技能**＝两枚主人自己的启用中技能从面板消失；
不严格化则它们照旧落回默认输出（该闭嘴的开口、该发手机的没发）。两个方向都有主，故递裁定。

现读（尺子＝`scripts/audit_1002/census_output_types_1002.py`，按 JSON 结构取，⛔ grep）：

```text
LOADED_FILES=24
SHAPE_NON_LIST=2
TRIG_SHAPE_VICTIMS=2
UNKNOWN_OUTPUT_ITEMS=0
SUM|kinds=3|total_output_items=22|unparseable=0
KINDS|{"bark": 1, "tv_notify": 18, "xiaomi_speak": 3}
```

两枚＝`data/skills/user/anti_addiction_alert.json`（`bark_level=critical`、`bark_sound=alarm`、`tts=true`）与
`data/skills/user/device_inspection.json`（`bark_level=active`、**`tts=false`**），都 `status=enabled`、`source=user`。
`TRIG_SHAPE_VICTIMS` 是同两枚的旧复数字段 `triggers`，但其值是**空列表**＝触发侧零意图丢失，⛔ 算进半径。
DCD 文里「在架 22 条 output 全在白名单内」与我这把尺的 `total_output_items=22` 同号。

决策申请＝`doc/决策申请/20261002-DB-两枚在册技能旧output形状待裁-决策申请.md`（四选项 B／A／C／D，推荐 B＝先迁数据再落闸，顺序钉死）。
落码器 `patch_dcd4_1002.py` 的 docstring 与 `must_have` 探针把这未落的两条分支按在原地（`if not isinstance(o, dict): continue`／`if not outputs:` 回落），
⛔ 后来人顺手一删＝在裁定之前单方面造出「技能消失」的后果。

### §10-5 本轮分母与登记（三个数，⛔ 用「全过」代替）

| 口径 | 数 | 来源 |
|---|---|---|
| AST 里的 test 定义总数（本尺含 `tests/contract/`） | 799＝类内 699＋模块级 100 | 新尺＝`scripts/audit_1002/ast_test_census_1002.py`（rglob，`FILES_SCANNED` 读数 62 档） |
| 与 §9-3 那把 777 的对账 | 同号 | §9 那把只扫顶档。顶档类内 661＋模块级 100＝761，加批8 的 4 条与批9 的 12 条＝**777**；`tests/contract/` 四档另计 38 条（＝契约门那个 `Ran=38`，两把尺在此互相咬住） |
| 本宿主机可收集＝实跑 | 577（批8 后）→ 589（批9 后） | `python3 -m unittest discover -s tests -t .` 的 `Ran=`；589−12＝577 这步只做减法，577 那一次我没留戳 |
| 类内 − 实跑 | 699−589＝**110** | 与 §9-3 那格「110 条＝宿主无 starlette／pytest 的那些模块内的类」同号＝本轮没新增不可导入的档 |
| 红 | `FAILED (errors=10, skipped=9, expected failures=1)`；`grep -cE "^(FAIL):"`＝0 | 那 10 枚 errors 同一次跑里 `ModuleNotFoundError` 出现 10 行＝starlette 8＋pytest 2，⛔ 记成代码缺陷 |
| 契约门／仓根 gates | 本轮未跑 | 批9 只动 `butler/skills/` 四枚，⛔ 触及跨仓契约面＝本轮没验，下轮补 |

跟办 1 登记：`security_level` 的安防语义（② 的一半）＝**独立议题**，等 ② 开工时一并递，⛔ 现在单方面定语义。

### §10-6 复跑（命令原文，⛔ 凭手感重述）

```text
# 权威树＝/vol1/1000/docker/doubao-butler（NAS），⛔ E 盘镜像（生产码面已过期）
cd /vol1/1000/docker/doubao-butler

python3 scripts/audit_1002/ast_test_census_1002.py --show-all            # 分母那把新尺（含 contract 档）
python3 scripts/audit_1002/census_output_types_1002.py            # 半径普查（只读）
python3 -m unittest tests.test_audit_1002_batch8_dcd6             # 批8 验收 4 例
python3 -m unittest tests.test_audit_1002_batch9_dcd4              # 批9 验收 12 例
python3 -m unittest discover -s tests -t .                         # 宿主全量，分母三数
python3 scripts/audit_1002/probe_old_extract_1002.py               # 批8 红半的旧码取证
# 两枚一次性落码器：锚点已施加过，重跑必 raise（raise＝它对，不是坏）
python3 scripts/audit_1002/patch_dcd61_1002.py
python3 scripts/audit_1002/patch_dcd4_1002.py
python3 scripts/audit_1002/patch_ledger_batch89_1002.py            # 本追加器，同理
```

红半复现（一次性，只在 `/tmp` 里造旧码副本，⛔ 动工作树）：

```text
rm -rf /tmp/dcd4_red && mkdir -p /tmp/dcd4_red
cd /vol1/1000/docker/doubao-butler && cp -r butler tests /tmp/dcd4_red/
for f in schema templates sandbox conflict; do
  git show HEAD~1:butler/skills/$f.py > /tmp/dcd4_red/butler/skills/$f.py   # HEAD~1＝8c9f631 之前那一笔
done
cd /tmp/dcd4_red && python3 -m unittest -v tests.test_audit_1002_batch9_dcd4
```

（本节⛔ 测到的：文件级行为断言与盘上形状计数。本节⛔ 测不到的：① 四枚 .py 在活容器里还没生效（生效要一次授权 restart）；
② bark 通道此刻能否真把消息推到手机（`notify/router.py` 的 `if self.bark is not None:` 那侧装配未量）；
③ 宿主全量那 10 枚 errors 在容器侧的表现（容器有 starlette、无 pytest）。三格一律标「未量」，⛔ 写成绿。
落盘自量（行数／字节／标题数／前缀断言）与戳的口径见本节末施加腿那一行。）
"""


def _heads(lines, prefix: str) -> int:
    return sum(1 for ln in lines if ln.startswith(prefix))


FOOTER = """（§10 施加腿自量：施加＝append-only，脚本里 assert「旧文本是新文本的前缀」；施加前 md5＝脚本 BASE_MD5
`eb4e39784cee7bf012ffd33c3e1a94c4`；施加后 <L> 行／<B> 字节／CR 0／末行换行符不变／`^## `<H2> 段＋`^### `<H3> 段——
这四个数是脚本在写盘那一刻按最终字节算出来的（占位符定点收敛），⛔ 事后手改本行，一改这四个数就作废；
最终字节的 md5 只出现在本笔 commit 正文（写进本文件＝本行之后还有内容，「戳／指纹早于落盘」那条尺会判红）。
落盘戳同理⛔ 写进本节：它只出现在本笔 commit 正文的 `date -u` 原样行，取于本文件最后一次写入之后。
`receipt_stamp_check.py` 不在本仓（DB 树无 `workorders/tools/`）⇒ 本格的戳与指纹两腿都判「人工-无尺」，与 §8／§9 同口径。）
"""


def render_footer(old_text: str) -> str:
    cand = FOOTER
    for _ in range(8):
        total = (old_text + SECTION + cand).splitlines(True)
        nxt = (FOOTER.replace("<L>", str(len(total)))
                      .replace("<B>", str(len("".join(total).encode("utf-8"))))
                      .replace("<H2>", str(_heads(total, "## ")))
                      .replace("<H3>", str(_heads(total, "### "))))
        if nxt == cand:
            return cand
        cand = nxt
    raise SystemExit("footer 定点不收敛＝数字位数会反复改变字节数，别写这四个数")


def main(argv: list[str]) -> int:
    do_apply = "--apply" in argv
    data = TGT.read_bytes()
    md5 = hashlib.md5(data).hexdigest()
    if md5 != BASE_MD5 or len(data) != BASE_BYTES or data.count(CRB):
        raise SystemExit(f"BASE-MISMATCH md5={md5} bytes={len(data)} cr={data.count(CRB)}")
    old_text = data.decode("utf-8")
    old_lines = old_text.splitlines(True)
    if len(old_lines) != BASE_LINES:
        raise SystemExit(f"BASE-LINES {len(old_lines)} 期望 {BASE_LINES}")
    h2 = _heads(old_lines, "## ")
    h3 = _heads(old_lines, "### ")
    if (h2, h3) != (BASE_H2, BASE_H3):
        raise SystemExit(f"BASE-HEADS h2={h2} h3={h3} 期望 {BASE_H2}/{BASE_H3}")
    if "## §10 " in old_text:
        raise SystemExit("§10 已存在＝本追加器施加过，⛔ 再追加一遍")

    new_text = old_text + SECTION + render_footer(old_text)
    if not new_text.startswith(old_text):
        raise SystemExit("不是 append-only（旧文本不再是前缀）＝上面某节的字被动过")
    new_lines = new_text.splitlines(True)
    out = new_text.encode("utf-8")
    if out.count(CRB) or out.endswith(LF) != data.endswith(LF):
        raise SystemExit(f"血统被改 cr={out.count(CRB)} endsLF={out.endswith(LF)}")
    if _heads(new_lines, "## ") != BASE_H2 + 1 or _heads(new_lines, "### ") != BASE_H3 + SECTION.count("\n### "):
        raise SystemExit(f"标题计数不对 h2={_heads(new_lines, '## ')} h3={_heads(new_lines, '### ')}")

    for probe in ("LOADED_FILES=24", "SHAPE_NON_LIST=2", "total_output_items=22",
                  "8c9f631", "3514c14", "Ran 12 tests in 0.041s", "epoch=1790941507",
                  "两枚在册技能旧output形状待裁", "人工-无尺",
                  "799＝类内 699＋模块级 100", "699−589＝**110**", "ast_test_census_1002.py"):
        if probe not in new_text:
            raise SystemExit(f"新文本探针丢失：{probe!r}")
    for keep in ("§9 批6 收册", "## §1 已核实 + 已修", "## §4 需裁定", "### §9-3 本轮全量回归的分母"):
        if keep not in old_text:
            raise SystemExit(f"旧节锚点本就不在（基底不对，别写）：{keep!r}")

    print(f"DRY|lines {BASE_LINES}->{len(new_lines)} bytes {BASE_BYTES}->{len(out)} "
          f"h2 {BASE_H2}->{_heads(new_lines, '## ')} h3 {BASE_H3}->{_heads(new_lines, '### ')} cr=0")
    if do_apply:
        TGT.write_bytes(out)
        print("APPLIED|" + hashlib.md5(out).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
