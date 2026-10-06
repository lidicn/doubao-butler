"""一次性追加器：给台账写 §11（DCD 裁定②／批10 三入口装门）。

护栏（与 §9／§10 同口径，一条不减）：
 1. 施加前 md5 必须＝ BASE_MD5，任何一格行数／字节／CR／末行换行／标题数不符 ⇒ raise，⛔ 落盘；
 2. 施加＝**append-only**，assert「旧文本是新文本的前缀」；
 3. 写回走 bytes＋逐字节，CR 施加前后都数一次（本台账 CR＝0 是血统，⛔ 抹成 CRLF）；
 4. 本节自量的四个数（行数／字节数／`^## `／`^### `）由**定点收敛**算出：
    占位符替成候选值→重算最终字节→再替，直到不变；不收敛就 raise（写进本文件的数一改就作废，正是这条要防的）；
 5. 最终字节的 md5 ⛔ 写进本节（写进去＝本行之后还有内容，「戳早于落盘」那条尺会判红），
    它只出现在本笔 commit 正文；落盘戳同理。本仓无戳尺（`receipt_stamp_check.py` 不在 DB 树）⇒ 本格判「人工-无尺」。

默认 DRY（只打印将要追加什么），`--apply` 才写。锚点已施加过 ⇒ 重跑必 raise（raise＝它对，不是坏）。
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

LED = pathlib.Path("doc/审计报告分诊台账-20261002.md")
BASE_MD5 = "7066f7d84756d442c49414d7aa2fbb28"
BASE = {"newlines": 552, "bytes": 69761, "cr": 0, "crlf": 0, "endnl": True, "h2": 10, "h3": 12}
CR = bytes([13])
CRLF = bytes([13, 10])
LF = bytes([10])

SECTION = """
---

## §11 DCD 裁定② 落地 · 批10 三入口装门（2026-10-02）

本节口径**覆盖** §10-1 表里 ② 那行的「⛔ 未开工」——该行自本节起作废；⛔ 改动上面各节已定稿的字
（追加器＝`scripts/audit_1002/patch_ledger_batch10_1002.py`，最强护栏＝「旧文本是新文本的前缀」）。
裁定＝`E:/NAS/关键决策部/decisions/20261002-DB六件影子代码-裁定.md:32-42`，提交＝`a0495c8`。

### §11-1 五条腿的指纹（改前 → 改后，全 LF／CR 0；`notify/router.py` 末行无换行＝保持原样）

| 文件 | 改前 | 改后 | 装了什么 |
|---|---|---|---|
| `butler/modes/engine.py` | `425726cc7bf8` 302 行／10,655 B | `3ac6f4e45f30` 354 行／13,112 B | `can_run_skill` 折入此前零调用的 `is_care_skill_disabled`（`care_skills_disabled` 从此有读者）；新增模块级 `get_mode_engine`／`_gate`／`gate_tts`／`gate_bark`／`gate_skill`。**fail-open＝一份定义**（抄三遍必漂成三种行为）；`rt.mode_engine` 每次现取（`app.py:281` 建 tts、`:316` 建 runner、`:427` 才装 mode_engine ⇒ 构造期注入拿到的必然是 None） |
| `butler/tts/manager.py` | `978d2cf274e3` 259 行／10,690 B | `7e5881537134` 272 行／11,427 B | `speak()` 增 keyword-only `priority`；门在 `TTS_SPEAK` 日志之前，拦下记 `TTS_SUPPRESSED mode=… via=… priority=…` 并返回 None |
| `butler/tts/adapter.py` | `7e3046f6d033` 83 行／3,415 B | `6bb53b1b09b9` 85 行／3,582 B | 把 `item.priority` 传进 `manager.speak`＝ALERT 豁免的唯一来路（`core/dialog.py:667` 按 `source=="alert"` 定 priority → helper → queue → adapter） |
| `butler/notify/router.py` | `8d05c5960312` 326 行／13,647 B | `e2e5dea2a79d` 336 行／14,509 B | `_to_bark` 加 `gate_bark`，`silent` 口径＝`bark_kwargs.level=="passive"`；拦下记 `NOTIFY_BARK_BLOCKED`，回 `ChannelResult(..., error="mode_blocked:<mode>")` |
| `butler/skills/runner.py` | `a48fade514e0` 460 行／20,864 B | `8636d89a5d41` 473 行／21,894 B | `run()` 在 `skill disabled` 之后加 `gate_skill`；`force`／`dry_run` 两条人工腿不拦；拦下回 `status="mode_blocked"` |

### §11-2 红绿两半（TDD：⛔ 先看它红，⛔ 落码）

- 验收件＝`tests/test_audit_1002_batch10_dcd2.py` `3d9096b34fac` 336 行／15,850 B，22 条腿 6 档
  （GateApi／ZeroCallerGuard／TTSMouth／AdapterPriority／BarkRoute／SkillRun）。裁定② 的硬格
  「睡眠模式下 ALERT 级仍能播」＝`TTSMouthGateTest.test_sleep_mode_still_plays_alert_priority`。
- **红半**（落码前跑真旧码）＝`Ran 22 / FAILED (failures=8, errors=5)`，红因逐类点名：
  2 枚 ImportError（`gate_*` 这几个名字还不存在）、3 枚 TypeError（`speak()` 收到未知关键字 `priority`）、
  8 枚行为／源码断言（零调用的判定函数仍零调用、三入口一处门都没有）。
- **绿半**＝`Ran 22 tests in 0.532s / OK`。
- **变异 7 枚全被咬住**＝`scripts/audit_1002/mutate_dcd2_gates_1002.py` → `SUM|mutated=7|not_bitten=0`
  （跑完 `RESTORED|rc=0`，五枚生产文件 md5 回到改后值，⛔ 留 `.bak`）。M4（把 `_gate` 改成 fail-closed）
  `reds=4`。⚠ 第一次跑时 M4 报的是 `NOT_BITTEN`，**那是我这把尺的错不是代码的错**：`reds` 用的是
  裸方法名的 `set()`，把 `BarkRouteGateTest`／`TTSMouthGateTest`／`SkillRunGateTest` 里同名的三条 fail-open
  腿去重成一条 ⇒ 尺已改成按原始 `FAIL:`／`ERROR:` 行计数（类名前缀进名字），三条腿各自改名。

### §11-3 装门之前先量的半径（只读尺 `scripts/audit_1002/census_mode_gate_radius_1002.py`，真读 `MODE_RULES` 与 `ModeEngine.can_run_skill`）

`SCANNED|files=24|unparseable=0`（三档目录 `builtin`／`user`／`agent`）｜`ENABLED|n=13`（半径只算真会跑的）｜
各模式被拦：`daily 0`／`movie 13`／`sleep 13`／`guest 0`／`away 13`｜**13 只逐枚后缀都是 `#normal`**｜
`OUTPUT_TYPES|{"tv_notify": 11, "xiaomi_speak": 2}`｜`BARK_SKILLS|n=0`｜`TTS_SKILLS|n=13`｜
`SILENT_MODES|bark=['movie','sleep']|tts=['movie','sleep','away']`。

### §11-4 顺带：我加行把批6 的在册锚点推移了两枚（⛔ 改数过关）

批6 门现读两枚 `UNKNOWN`＝`DRIFT：这一行 AST 里没有该名字的 Call`，分布从 `SYNC_OK 20` 掉到 18。
重钉尺 `scripts/audit_1002/repin_batch6_sites_1002.py` 把两枚逐条点开：`notify/router.py` 名为 `notify` 的
Call **只有 1 枚**＝`:320 recv=self.tv.notify in async def _to_tv:314`（delta 恰＝+11＝我在该文件加的行数）；
`skills/runner.py` 同样只 1 枚＝`:424 recv=rt.tv.notify in async def _push_tv:399`（delta 恰＝+13）。
⇒ 同一枚调用、判定不变（sync 的 `TVClient.notify`），`classify_unawaited_1002.py` 里 309→320、411→424，
并在表旁留了「302→309→320」这条两次推移的注释血统。重钉后合跑批6 8 例＋批10 22 例＝`Ran 30 tests / OK`
（`SYNC_OK` 回 20、`UNKNOWN` 归 0）。批6 的 12 枚 `ANCHORS` 落点⛔ 触及本轮改过的文件＝没漂。

### §11-5 本轮分母（三个数，⛔ 用「全过」代替）

| 口径 | 数 | 来源 |
|---|---|---|
| AST 里的 test 定义总数 | 821＝类内 721＋模块级 100（＝§10-5 那次的 799＋本批 22） | `scripts/audit_1002/ast_test_census_1002.py`，`FILES_SCANNED` 63 档 |
| 本宿主机可收集＝实跑 | 611（＝589＋22） | `python3 -m unittest discover -s tests -t .` 的 `Ran=` |
| 类内 − 实跑 | 721−611＝**110** | 与 §10-5 那格「110」同号＝本批没新增「宿主导不进来的档」 |
| 红 | `FAILED (errors=10, skipped=9, expected failures=1)`；`^FAIL:` 计数＝0 | 那 10 枚 errors 同一次跑里 `ModuleNotFoundError` 恰 10 行＝starlette 8＋pytest 2（环境因，⛔ 记成代码缺陷） |
| 契约门／仓根 gates | 本轮未跑 | 本批 5 枚都在 `butler/` 自家、⛔ 触及跨仓面＝没验，下轮补 |

### §11-6 装完门量出的四问已递（裁② 的语义缺口＋跟办 1）

件＝`doc/决策申请/20261002-DB-模式闸门落地后四处规则语义待裁-决策申请.md` `c37d87f7bf67` 100 行／13,299 B
（同批投 `E:/NAS/关键决策部/inbox/`，两份 md5 同号＝`c37d87f7bf67`，现读 12:36:52Z 那一次）。四问一句话版：

1. **技能类别认不出**：`SKILL_CATEGORIES`（`modes/engine.py:88-92`）11 枚关键字 vs 在册 13 只 ID ⇒ `emergency_only`／
   `anomaly_only` 两档不可达，睡眠／观影效果等同「全拦」（含 `monitor-camera`）；
2. **Bark 静默无紧急例外**：`bark_silent` 只在 movie/sleep 为真，而生产侧 `level=passive` **零调用点**
   ⇒ 这两档把推送全拦、告警也推不到手机；
3. **9 条嘴不完全过门**：4 条直连 `rt.tts.speak(...)`（`proactive/engine.py:321`、`timeseries/anomaly.py:371`、
   `morning/routine.py:207`、`notifier/router.py:82`）过 `manager.speak` 但永远 `priority=None`；
   5 条完全不过门（`notify/router.py:294`、`core/dialog.py:708`、`tools/schedule.py:106`、
   `decision/action_router.py:80`、`tts/manager.py:178-180` 合成失败时自弹 Bark 绕开 bark 闸）；
4. **`security_level` 零读者**：`grep -rn security_level butler --include=*.py` ＝只有 5 行赋值
   （`monitor_only`×3／`enhanced`／`full_arm`），⛔ 任何读者，三值语义没文书 ⇒ 跟办 1 指定随 ② 一并递。

四问我一问都没自主下手（改的都是主人能感知的产品语义）。**顺带登记（⛔ 占裁定名额）**：
`tts/helper.py:20` 的 `override_quiet` 默认 `True` ⇒ 队列自带的「夜间静默」对经 helper 的调用方是空转，
与本次的模式静默是两套机制；裁⑤ 的「一套队列归一」若落地，问 3 与它同批最省。

### §11-7 复跑（命令原文，⛔ 凭手感重述）

```text
# 权威树＝/vol1/1000/docker/doubao-butler（NAS），⛔ E 盘镜像（生产码面已过期）
cd /vol1/1000/docker/doubao-butler

python3 -m unittest tests.test_audit_1002_batch10_dcd2        # 批10 验收 22 例
python3 -m unittest tests.test_audit_1002_batch6_gate         # 批6 门 8 例（含重钉后的两枚在册锚点）
python3 scripts/audit_1002/mutate_dcd2_gates_1002.py           # 变异 7 枚，跑完自动还原＋md5 复验
python3 scripts/audit_1002/census_mode_gate_radius_1002.py     # 半径普查（只读）
python3 scripts/audit_1002/repin_batch6_sites_1002.py .        # 重钉尺（只读，点名每枚同名 Call）
python3 scripts/audit_1002/ast_test_census_1002.py             # 分母那把尺
python3 -m unittest discover -s tests -t .                     # 宿主全量，三个数
# 一次性落码器：锚点已施加过，重跑必 raise（raise＝它对，不是坏）
python3 scripts/audit_1002/patch_dcd2_1002.py
python3 scripts/audit_1002/patch_ledger_batch10_1002.py
```

（本节⛔ 测到的：五枚文件的源码形状＋22 条门腿＋7 枚变异腿＋分母三数。本节⛔ 测不到的，三格一律标「未量」，
⛔ 写成绿：① **现网未生效**＝五枚 .py 在容器挂载面上还没跑过一条真实消息，生效要一次授权 `docker restart`，
那扇窗我没开（决策申请里写明「四问裁完之前别开窗」正是为这一格）；② bark 通道此刻能否真把消息推到手机
（`notify/router.py` 的 `if self.bark is not None:` 那侧装配未量）；③ 宿主那 10 枚 errors 在容器侧的表现
（容器有 starlette、无 pytest）。
落盘自量：施加＝append-only，脚本 assert「旧文本是新文本的前缀」；施加前 md5＝脚本 `BASE_MD5`
`7066f7d84756d442c49414d7aa2fbb28`（552 行／69,761 B／CR 0／`^## `10＋`^### `12）；施加后 @@NL@@ 个换行／
@@BYTES@@ 字节／CR @@CR@@／末行换行 @@ENDNL@@／`^## `@@H2@@ 段＋`^### `@@H3@@ 段——这四个数是脚本在写盘那一刻
按最终字节算出来的（定点收敛），⛔ 事后手改本行，一改这几个数就作废；最终字节的 md5 与落盘戳都只出现在
本笔 commit 正文的 `date -u` 原样行（取于本文件最后一次写入之后）。本仓⛔ 戳尺 ⇒ 这两腿判「人工-无尺」，
与 §8／§9／§10 同口径。）

"""


def stats(b: bytes) -> dict:
    text = b.decode("utf-8")
    lines = text.splitlines()
    return {
        "newlines": b.count(LF),
        "bytes": len(b),
        "cr": b.count(CR),
        "crlf": b.count(CRLF),
        "endnl": b.endswith(LF),
        "h2": sum(1 for l in lines if l.startswith("## ")),
        "h3": sum(1 for l in lines if l.startswith("### ")),
    }


def main() -> int:
    apply_flag = "--apply" in sys.argv
    raw = LED.read_bytes()
    cur = stats(raw)
    if hashlib.md5(raw).hexdigest() != BASE_MD5:
        raise SystemExit(f"ABORT md5 不符：现={hashlib.md5(raw).hexdigest()} 期={BASE_MD5}")
    for k, v in BASE.items():
        if cur[k] != v:
            raise SystemExit(f"ABORT 基底 {k} 不符：现={cur[k]} 期={v}")
    if raw.count(CR):
        raise SystemExit("ABORT 基底有 CR，本节按 LF 血统写的")

    # 定点收敛：占位符替成候选值→按最终字节重算→再替，直到不变
    body = SECTION
    for _ in range(12):
        cand = (raw + body.encode("utf-8"))
        s = stats(cand)
        nxt = (body.replace("@@NL@@", str(s["newlines"]))
                   .replace("@@BYTES@@", f'{s["bytes"]:,}')
                   .replace("@@CR@@", str(s["cr"]))
                   .replace("@@ENDNL@@", "末行换行符不变" if s["endnl"] else "⛔ 末行没了")
                   .replace("@@H2@@", str(s["h2"]))
                   .replace("@@H3@@", str(s["h3"])))
        if nxt == body:
            break
        body = nxt
    else:
        raise SystemExit("ABORT 自量数字不收敛")

    if "@@" in body:
        raise SystemExit("ABORT 还有未收敛的占位符")
    final = raw + body.encode("utf-8")
    fs = stats(final)
    if not final.startswith(raw):
        raise SystemExit("ABORT 旧文本不是新文本的前缀（＝这不是追加，是改写）")
    if fs["cr"] or fs["crlf"]:
        raise SystemExit(f"ABORT 写回后有 CR：cr={fs['cr']} crlf={fs['crlf']}")
    if not fs["endnl"]:
        raise SystemExit("ABORT 写回后末行换行丢了")
    if fs["h2"] != BASE["h2"] + 1:
        raise SystemExit(f"ABORT `^## ` 段数不是 +1：现={fs['h2']}")
    want_h3 = 7  # 本节自己的 `### §11-x` 小标题数（§11-1…§11-7），与本文件历史数无关，写死便于发现漏段
    got_h3 = sum(1 for l in body.splitlines() if l.startswith("### "))
    if got_h3 != want_h3:
        raise SystemExit(f"ABORT 本节 ### 段数={got_h3}，期={want_h3}")
    if fs["h3"] != BASE["h3"] + want_h3:
        raise SystemExit(f"ABORT `^### ` 段数={fs['h3']}，期={BASE['h3'] + want_h3}")

    print(f"DRY 追加字节={len(body.encode('utf-8'))} 追加行={body.count(chr(10))} 最终={fs}")
    nonempty = [l for l in body.splitlines() if l.strip()]
    print("首行|", nonempty[0])
    print("标题|", [l for l in nonempty if l.startswith("## ")])
    print("末行|", nonempty[-1][:60])
    if not apply_flag:
        print("（未写盘。要写：python3 scripts/audit_1002/patch_ledger_batch10_1002.py --apply）")
        return 0
    LED.write_bytes(final)
    back = stats(LED.read_bytes())
    if back != fs:
        raise SystemExit(f"ABORT 回读与预期不符：{back} != {fs}")
    print("APPLIED|", f"md5={hashlib.md5(LED.read_bytes()).hexdigest()}", back)
    return 0


if __name__ == "__main__":
    sys.exit(main())
