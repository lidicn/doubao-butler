"""一次性落码器：台账 §1 首表追加三行（批6 收册／回归门自己咬出的那条相反断言／批7 电视通知腿），
并在文末追加 §9。

锚点＝整行 startswith ＋ 唯一命中；§1 插入点＝**首表那一段连续的 `|` 行的末行**（从整行表头
（含「现象（我复核后的说法）」那枚只此一份的列名）往后走到断行为止），⛔ 手写行号（§8 是昨天刚追加的，
行号随每次追加漂移），⛔ 用「§2 之前最后一条 `| ` 行」（那会落进第二张三列表里，格数不对）。
写回走 bytes＋splitlines(True)，CR／末行换行符血统改前改后各验一次（本台账血统＝LF／CR 0）。
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

LED = pathlib.Path("doc/审计报告分诊台账-20261002.md")
BASE_BYTES = 49073
BASE_LINES = 347
BASE_H2 = 8
BASE_H3 = 2
CRB = b"\r"
LF = b"\n"

# 本台账有 4 张表的表头都以「| 表行 | 报告ID |」开头（`:24 :88 :98 :110`，`grep -n "^| 表行 | 报告ID |"` 现读），
# 所以锚点必须吃整行表头而不吃前缀——首版就是被这条守卫拦下的（命中 4 次 → raise，未落盘）。
TABLE1_HEADER = "| 表行 | 报告ID | 现象（我复核后的说法） | 修复提交 | 验收腿 |"

# 每行 5 格，与首表表头同形制；格内允许出现 `|`（沿用本台账既有写法，如 `PROBE|61`），
# 但第 0/1/3 格必须无 `|`，否则拼接出的格数就和表头不一致——这条由下面的断言把住。
NEW_ROWS = [
    (
        "§6 在册 30 枚",
        "批6 收册",
        "未 await 候选名单**逐枚点开完毕：0 枚真未 await**（`EXTERNAL_SYNC=10`＝外部同步 API、本就该同步调；"
        "`SYNC_OK=20`＝接收者那条腿是 sync 或已 await）。尺＝`scripts/audit_1002/classify_unawaited_1002.py`："
        "按**接收者**解析而非末段名（同名 `start/stop/notify/execute/record` 正是这 30 枚假候选的来路），"
        "解析不出的接收者一律记 `UNKNOWN`＝红，⛔「没 resolve 成」读成「没问题」。"
        "现读 `TOTAL|EXTERNAL_SYNC=10 SYNC_OK=20 sites=30 rows=30 py_files=220 red=0`＋`SELFTEST|PASS`。"
        "本节口径覆盖「同族新发现」表末行那条『未修·在册』与 §6『未 await 普查在册 30 枚』",
        "dccb97d",
        "`python3 -m unittest tests.test_audit_1002_batch6_gate` → 现读 `Ran 8 tests in 9.983s / OK`"
        "（3 类 8 例：夹具真值钉 `S1..S4`／UNKNOWN 判红／self-check＋名册三例（无 async-unawaited、"
        "分布等于现读、30 sites 全分类）＋锚点两例（12 枚 def 至今仍是 sync、合成变异腿证明 def／async def 分得出））。"
        "尺测不到什么＝§9-1 四条",
    ),
    (
        "—",
        "回归门自咬",
        "`tests/test_ma_client_args.py` 里有一条断言与契约表 §三"
        "（`E:/NAS/homesdk/doc/ADM联动主题注册表与消息契约.md:90`：`retrieve_agent_memories` 的 member_id 必填、fail-closed）"
        "**相反**：它要求缺主体时仍然外发。真因在我自己——`724b7ff`（卡6，10-02 04:14Z）把生产腿改成「连请求都不发」，"
        "却没同步这条测试，而当时只跑了契约门（`rc=0 / Ran=38`）、⛔ 跑全量 discover ⇒ 这条红在树上挂了约 6 小时没人看见"
        "（§9-3 的分母表里那两行 `Ran 567` 就是它的前后腿）。authority 腿本就在"
        "`tests/contract/test_db_ma_contract.py::test_02`（断 `calls == []`）",
        "74f56da",
        "同尺前后：`Ran 567 tests in 54.691s / FAILED (failures=1, errors=10, …)` → "
        "`Ran 567 tests in 52.585s / FAILED (errors=10, …)`（failures 归 0）。"
        "对齐后的断言挂长期差分腿 `test_fail_closed_guard_makes_the_difference`"
        "（同一 Spy、同一条空 member：带门 `calls == []`／拆门必外发且外发参数里没有 `member_id`），"
        "另用一次性进程内变异探针替换 `MemoryAgentClient.retrieve` 读出 `MUTANT|BIT`（脚本已删并 `ls` 反证）"
        "⇒ 那句 `assert calls == []` 不是恒真。落码器＝`scripts/audit_1002/patch_ma_client_stale_1002.py`"
        "（一次性：锚点已施加过，重跑必 raise）",
    ),
    (
        "—",
        "批7 电视通知腿",
        "电视通知**假绿通道**：`integrations/tv.py:54` 的 `TVClient.notify` 有两腿——mqtt 为 None 时 "
        "`logger.warning` 后直接 return（消息丢掉、不抛），正常腿才 `publish`，改前两腿都返回 None；"
        "`notify/router.py` 的 `_to_tv`（现读 `:303`）丢掉返回值、无条件 `return ChannelResult(CHANNEL_TV, True)"
        "` ⇒ `NotifyResult.ok`（`:142`，`all(r.ok)`）在「丢弃」那条腿上读成成功；"
        "三条在架调用方（`core/cron_task.py:269`／`bus/inbox.py:313`／`api/notify_routes.py:81`）都只看 `res.ok`。"
        "附带：`TVPopupPort`（`:74`）只声明 `popup`（`:84`），而全树 `def popup` 命中＝端口声明本身＋"
        "`tests/test_notify_router.py:74` 的假件，真身 `TVClient` 没有 popup ⇒ 「无 tv_payload」那条分支在生产对象上"
        "必然 AttributeError（被 `_deliver`（`:245`）吞成 ok=False；现网三条调用方都带 payload ⇒ 此刻零受害者，"
        "已另例钉住该形状，⛔ 悄悄翻绿）",
        "6b6a92b",
        "先红后绿：`tests/test_audit_1002_batch7_tvtruth.py` 改前 `Ran 6 / FAILED (failures=4)"
        "`（notify 返回 None×2／路由假绿×1／端口缺声明×1），改后 `Ran 6 tests in 0.197s / OK`。"
        "落码器＝`scripts/audit_1002/patch_tv_truth_1002.py`（4 处落点，整行唯一命中＋逐文件字节／行／CR／末行换行守卫）。"
        "**状态＝已落码-未生效**：`butler/` 是容器 rw 绑定挂载，进程内仍是旧模块，等授权那次合并 `docker restart`",
    ),
]

SECTION9 = '''
---

## §9 批6 收册 · 批7 电视通知腿 · 全量回归分母（2026-10-02）

本节只加读数与新形状，判定写在 §1 那三行里；⛔ 改动上面各节已定稿的字。

### §9-1 批6 那把尺的盲区（写清楚它测不到什么）

1. **源码级、非运行时**：尺吃 AST，⛔ 看见运行期动态派发（`getattr(rt, name)` 里 `name` 是变量、
   以 `**kwargs` 传函数名那类）。这类调用它报 `UNKNOWN`＝判红，⛔ 判绿。
2. **`getattr` 只吃字面串形式**（`getattr(x, "attr", d)`）。变量形式＝红，留给下一次逐枚点开。
3. **接收者解析靠赋值链**（`rt.tv = tv`、`bridge = getattr(rt, "ilink_bridge", None)`、
   `_, _, bridge = _get_ilink()` 这几型是本轮现读补出来的 R8/R9）。
   形状变了它会先判红而不是判绿——方向是安全的，代价是我得再来一轮。
4. **名册是时刻快照**：30 枚的**行号**会被任何上游改动推移。本轮自己就撞上一次——
   批7 给端口补 `notify` 声明把 `notify/router.py` 那枚调用点从 `:302` 推到 `:309`，锚腿当场报
   `UNKNOWN / DRIFT：这一行 AST 里没有该名字的 Call`（⇒ 这就是它该有的反应，⛔ 把锚腿调松）。
   重看点开结果仍是 sync 的 `TVClient.notify`，判定不变，名册改 `:309`
   （`classify_unawaited_1002.py` 里那条注释记着这次漂移的来路）。

### §9-2 批7 之外的一条同源观察

`bus/topics.py:77` 的 `PUB_TV_NOTIFY = f"{TV_PREFIX}/cmd/notify"` 行尾注释还写着**「预留」**（本轮 `grep -n` 现读），
而 `integrations/tv.py` 的 `notify`／`play_url` 早就在用它发真消息（`core/cron_task.py:239` 的注释也把它当现路写）。
＝同一族的「注释承诺落后于代码」，与本轮递给 DCD 的六议题（`push_guard.py` 的 L1/L4、`_OUTPUTS` 白名单）同形状，
只是这条方向相反：**码在跑，文书还说没跑**。⛔ 本批只登记、不动那行以外的语义。

### §9-3 本轮全量回归的分母（三个数，⛔ 用「全过」代替）

| 口径 | 数 | 怎么来的 |
|---|---|---|
| AST 里的 test 定义总数 | 777（类内 677＋模块级 100） | `ast.walk` 数 `FunctionDef/AsyncFunctionDef` 且名以 `test` 开头（⛔ `^def test` 那把粗尺会漏 `async def`，见 §5） |
| 本宿主机可收集 | 567（批7 前）→ 573（批7 后） | `python3 -m unittest discover -s tests -t .` 的 `Ran=` |
| 实跑 | 与可收集同数 | 同一次输出，无「收了没跑」的缺口 |
| 差额 210（567 口径） | 100 条模块级＋110 条宿主缺包不可导入 | 模块级 100 条**全在** `test_v25_pytest_shim.py` 名册那 8 档里（`neither=0`），由 shim 以类内方法身份重跑⇒已含在 567 内；110 条＝宿主无 starlette／pytest 的那些模块内的类 |
| 红点 | failures 1（旧测试在位时）→ 0／errors 10 | 那 10 枚 errors 全是宿主缺包（starlette 8＋pytest 2），⛔ 记成代码缺陷；容器侧 starlette 有、pytest 无 |
| 契约门 | rc=0、Ran=38 | `CONTRACT_IN_CONTAINER=1 bash scripts/contract_gate.sh`（宿主跑；容器内跑会因 docker CLI 403 判 2） |
| 仓根 `gates.sh` | **rc=2** | 宿主 `import homesdk.gates` 失败即退出（日志原文「homesdk 未安装」），⛔ 读成「门禁过」；装 wheel 需授权，本批未装 |

三次同尺全量（时间即区分腿）：`Ran 567 … 54.691s / FAILED (failures=1, errors=10, skipped=9, expected failures=1)`
→ `Ran 567 … 52.585s / FAILED (errors=10, …)`（§1 那行『回归门自咬』的前后腿）
→ `Ran 573 … 51.690s / FAILED (errors=10, …)`，同批 `grep -cE "^(FAIL):"` 读数 `0`／`10`。

### §9-4 复跑（命令原文，⛔ 凭手感重述）

```text
# 权威树＝/vol1/1000/docker/doubao-butler（NAS），⛔ E 盘镜像（已过期）
cd /vol1/1000/docker/doubao-butler

# 批6 尺（名册现读 + 夹具自证）
python3 scripts/audit_1002/classify_unawaited_1002.py --gate .
# 批6 验收 8 例 / 批7 验收 6 例
python3 -m unittest tests.test_audit_1002_batch6_gate
python3 -m unittest tests.test_audit_1002_batch7_tvtruth
# ma 那条对齐后的差分腿（模块级用例不经 discover，走 shim）
python3 -m unittest tests.test_v25_pytest_shim
# 契约四档（容器内跑，tests/ 送进 /tmp）
CONTRACT_IN_CONTAINER=1 bash scripts/contract_gate.sh
# 宿主全量（分母三数：AST 777 / 可收 573 / 实跑 573）
python3 -m unittest discover -s tests -t .
# 本台账落码器（一次性：锚点已施加过，重跑必 raise）
python3 scripts/audit_1002/patch_ledger_batch67_1002.py
```
'''


def _unique_index(prefix, lines, where):
    hits = [i for i, ln in enumerate(lines) if ln.startswith(prefix)]
    if len(hits) != 1:
        raise SystemExit(f"{where}：前缀 {prefix!r} 命中 {len(hits)} 次，期望 1")
    return hits[0]


def main(argv: list[str]) -> int:
    apply_flag = "--apply" in argv
    data = LED.read_bytes()
    if len(data) != BASE_BYTES or data.count(CRB):
        raise SystemExit(f"BASE-MISMATCH bytes={len(data)} cr={data.count(CRB)}")
    lines = data.decode("utf-8").splitlines(True)
    if len(lines) != BASE_LINES:
        raise SystemExit(f"BASE-LINES {len(lines)}，期望 {BASE_LINES}")
    h2 = sum(1 for ln in lines if ln.startswith("## "))
    h3 = sum(1 for ln in lines if ln.startswith("### "))
    if (h2, h3) != (BASE_H2, BASE_H3):
        raise SystemExit(f"BASE-HEADINGS h2={h2} h3={h3}，期望 {BASE_H2}/{BASE_H3}")
    fence0 = sum(1 for ln in lines if ln.startswith("```"))
    if fence0 % 2:
        raise SystemExit(f"改前围栏就不成对：{fence0}")

    head = _unique_index(TABLE1_HEADER, lines, "§1 首表表头")
    end = head
    while end + 1 < len(lines) and lines[end + 1].startswith("|"):
        end += 1
    if end - head < 3:
        raise SystemExit(f"首表行数异常：表头 {head + 1}、末行 {end + 1}")
    last = lines[end].rstrip("\n")
    if not last.endswith("|"):
        raise SystemExit(f"首表末行不以 | 收尾：{last[-40:]!r}")
    print(f"ANCHOR|首表 表头＝{head + 1} 末行＝{end + 1}（{last[:34]}…）")
    if not lines[-1].startswith("```"):
        raise SystemExit(f"文末不是围栏收尾：{lines[-1][:40]!r}")

    rows = []
    for cells in NEW_ROWS:
        if len(cells) != 5:
            raise SystemExit(f"新行应为 5 格：{cells[0]!r}")
        for i in (0, 1, 3):
            if "|" in cells[i]:
                raise SystemExit(f"第 {i} 格含 | ⇒ 格数会与表头不符：{cells[i][:30]!r}")
        rows.append("| " + " | ".join(cells) + " |\n")

    section = [ln + "\n" for ln in SECTION9.split("\n")]
    # SECTION9 以换行开头，首元素是空串＝与 §8 末行之间的分隔空行
    new = lines[:end + 1] + rows + lines[end + 1:] + section
    out = "".join(new).encode("utf-8")

    out_lines = out.decode("utf-8").splitlines(True)
    if out.count(CRB):
        raise SystemExit("CR 血统被引入")
    if out.endswith(LF) != data.endswith(LF):
        raise SystemExit("末行换行符血统被改")
    n_h2 = sum(1 for ln in out_lines if ln.startswith("## "))
    n_h3 = sum(1 for ln in out_lines if ln.startswith("### "))
    if n_h2 != BASE_H2 + 1:
        raise SystemExit(f"H2 期望 {BASE_H2 + 1} 实得 {n_h2}")
    if n_h3 != BASE_H3 + 4:
        raise SystemExit(f"H3 期望 {BASE_H3 + 4} 实得 {n_h3}")
    fences = sum(1 for ln in out_lines if ln.startswith("```"))
    if fences != fence0 + 2:
        raise SystemExit(f"围栏期望 {fence0 + 2} 实得 {fences}")

    text = out.decode("utf-8")
    for probe in ("## §1 已核实 + 已修", "## §2 已核实为真 · 待修", "## §8 DCD 决策申请第二件",
                  "| 61 | P1-6 (RO) |", "| 80 | S-02 (RB) |", "PROBE|61",
                  "| 表行 | 报告ID | 现象（我复核后的说法） | 修复提交 | 验收腿 |"):
        if text.count(probe) != 1:
            raise SystemExit(f"改前既有文本（应恰 1 次）异常：{probe!r} ×{text.count(probe)}")
    for probe in ("7a9a00be50f796ed07c03d0f89e29ffe",):
        # 红名册 md5 在本台账多次出现，只验「还在」
        if probe not in text:
            raise SystemExit(f"改前既有文本丢失：{probe!r}")
    for probe in ("## §9 批6 收册 · 批7 电视通知腿", "### §9-1", "### §9-2", "### §9-3", "### §9-4",
                  "TOTAL|EXTERNAL_SYNC=10 SYNC_OK=20 sites=30 rows=30 py_files=220 red=0",
                  "MUTANT|BIT", "UNKNOWN / DRIFT", "Ran 8 tests in 9.983s", "Ran 6 tests in 0.197s",
                  "Ran 567 tests in 54.691s", "Ran 567 tests in 52.585s", "Ran 573 … 51.690s",
                  "**rc=2**", "PUB_TV_NOTIFY"):
        if probe not in text:
            raise SystemExit(f"新文本探针丢失：{probe!r}")
    for cells in NEW_ROWS:
        marker = "| " + cells[0] + " | " + cells[1] + " |"
        if text.count(marker) != 1:
            raise SystemExit(f"新行形制异常：{marker!r} ×{text.count(marker)}")

    print(f"DRY|lines {BASE_LINES}->{len(out_lines)} bytes {BASE_BYTES}->{len(out)} "
          f"cr=0 h2={n_h2} h3={n_h3} rows+={len(rows)} fences={fences}")
    if apply_flag:
        LED.write_bytes(out)
        print("APPLIED|" + hashlib.md5(out).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
