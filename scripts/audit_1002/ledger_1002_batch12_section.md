## §13 DCD 裁定⑤-2 落地 · 过载「丢最低优先＋保告警＋当前这条不吞」（2026-10-02）

本节口径**覆盖** §12-7-1 那格的「⇒ 批12＝⑤」（那句只承诺了要做，本节起它做了半条）；⛔ 改动上面各节已定稿的字。
追加器＝`scripts/audit_1002/patch_ledger_batch12_1002.py`，本节正文本体＝`scripts/audit_1002/ledger_1002_batch12_section.md`
（正文单独一个文件＝沿用 §12 那两处理由：python 字面量会静默吃掉 `\r`/`\f`，shell 双引号又把反引号当命令替换）。
裁定＝`E:/NAS/关键决策部/decisions/20261002-DB六件影子代码-裁定.md:70-83`；提交＝`7112987`（六枚文件一笔）。

裁定⑤ 一次给了三条（`:77-79`）：**⑤-1 一套队列归一／⑤-2 过载语义改／⑤-3 两处阈值常量并一处**。
本节**只做 ⑤-2**（`:78` 那句，字面清楚到不需要问我）；⑤-1／⑤-3 有产品语义岔路，已递
`E:/NAS/关键决策部/inbox/20261002-DB-TTS队列归一的落点与HTTP腿语义-决策申请.md`（下称「b12 决策申请」，状态＝**已落盘-未送**，
bark 归 PM 按）。**⑤-3 在裁定里排第 3，实际是 ⑤-1 的推论**：1 那家拆掉后 `TTS_QUEUE_MAX`/`TTS_OVERLOAD_PAUSE`
（`push_guard.py:58-59`）随之消失，「两处常量并一处」＝删掉一处，无需单独裁。

### §13-1 为什么本节只动一家（三条现读，⛔ 靠读码印象）

| 尺（命令原文） | 读数 | 含义 |
|---|---|---|
| `grep -n "self.tts_queue.enqueue" butler/notify/router.py` | `:281` 一条 | 真队列在现网**只有通知路由**这一个入队点 |
| `grep -n 'tts_decision.action == "drop"' butler/api/tts_routes.py` | `:271` | HTTP 腿**只判 drop**：`DECISION_QUEUE`（＝`"queue"`）从来不改行为，那条「入队」是假动作 |
| `grep -rn "tts_queue_length" butler tests --include=*.py` | 1 命中＝产生处本身（`push_guard.py:388`，在 `get_status()` `:371` 里） | guard 那口「队列」没有任何读者 |

⇒ ⑤-2 的语义落点＝`butler/tts/queue.py` 一家，这与 §12-1 那次落点偏移同一个方向（批11 的 TTL 也落在这家），
两条腿站在同一家里，⑤-1 真裁下来时⛔ 需要再搬一次。

第二处歧义一并递了（本节⛔ 擅自定）：`butler/tts/playback_queue.py` 是 139 行的**真队列**
（每台设备一条 `asyncio.Queue` ＋ worker，`manager.py:69` 建、`app.py:289` 接线，`grep -c "ttl\|TTL\|expires"`＝0）。
我的读法：「一家」指**决策排队**一家（谁先响），它干的是「同一台设备别叠着播」＝执行层串行锁，⛔ 第二家决策队列。
这句是**解释**不是事实，所以放进 b12 决策申请第一节让 DCD 认；他不认就是一个独立议题（给 `PlaybackQueue` 上优先级）。

### §13-2 三枚生产/测试文件的指纹（基线＝HEAD `2eed533` → 工作树；全 LF／CR 0；`queue.py` 与 `test_tts_queue.py` 末行无换行＝保持原样）

| 文件 | 改前 | 改后 | 装了什么 |
|---|---|---|---|
| `butler/tts/queue.py` | `af4b1d0676e5` 635 行／26,958 B | `3349e9bf6816` 656 行／28,598 B | 过载段重写（`:369-385`）：`_shed_lowest_for_room()`（`:607-623`）从最低优先级档丢起、同带丢最旧、**P1 ⛔ 丢**、当前这条照收；暂停窗口只在 `not already_paused` 时 arm（`:375-377`）⇒ 后续过载⛔ 续期（洪水下非告警负载不被永久饿死）；`_dropped["overload"] += shed`（`:373`）＝只记真被丢的，旧码在这里记 `len+1`（把当前这条也算成丢弃）；出队侧 `overload_pause`（`:407-409`）只挡非 P1，**告警在过载暂停期间当场能播**，手动暂停语义原样保留；**拆**「把告警 TTL 顺延到暂停结束后」那条分支（旧 `:346-349`，前提没了）与 `REASON_OVERLOAD_RESET` 这枚 reset 语义的原因码（旧 `:83`）；文件头 `:9-11` 与 `overload_text`（`:163`）两处承诺按新行为改口（⛔ 说了不做） |
| `tests/test_tts_queue.py` | `8a5367daf146` 455 行／16,232 B | `33c07d95688b` 457 行／16,743 B | 三枚过载腿改口（`test_overload_sheds_lowest_and_keeps_current_and_alert` 等）：`accepted is True`、`size==4`、`dropped==1`、告警暂停期间当场出队、601s 后队列照常走；**模块级函数数⛔ 变**（shim 闸基线仍 31，本节跑过） |
| `tests/test_audit_1002_batch11_dcd3.py` | `cf602dbc94ad` 322 行／15,861 B | `468cc15189d3` 325 行／15,978 B | 一条腿的 docstring 改口：旧那句引用了本节拆掉的「TTL 顺延」（且行号 `queue.py:302` 本就错），现改成「告警档 TTL＝0 不被扫掉」＋指到批12 的 `AlertExemptionTest` |

（`wc -l` 对 `queue.py`/`test_tts_queue.py` 各差 1＝末行无换行；本台账与落码器一律按 `splitlines(True)` 计数，两个口径都写在 `scripts/audit_1002/patch_dcd5_1002.py` 文档串里。）

### §13-3 红绿两半（TDD：⛔ 先看它红，⛔ 落码）

- 验收件＝`tests/test_audit_1002_batch12_dcd5.py` `4968e8c0b542` 253 行／12,912 B，16 条腿 3 档
  （OverloadShedTest 让位与计数／AlertExemptionTest 暂停期待遇／BatchTwelveHygieneTest ⛔ 空号回身咬自己）。
  运行时腿按跟办 3（`:108`）＝假时钟＋直接操作队列，⛔ 播声、⛔ 碰活库。
- **红半（对 HEAD 真旧码跑终版验收件，⛔ 改工作树）**＝`git archive HEAD butler tests scripts | tar -x -C /tmp/b12red`
  ＋把终版验收件复制进去 → `Ran 16 tests / FAILED (failures=13)`；读数件＝`workorders/readings/1002b12/red_vs_head.txt`
  （旧码 `queue.py` md5 同号 `af4b1d0676e5`＝红半跑的是真基线）。
  **三条腿在旧码上就是绿的**（如实登记，⛔ 「全红」才好看）：`test_manual_pause_still_blocks_alerts`、
  `test_new_non_alert_load_is_still_rejected_during_pause`、`test_non_alert_still_waits_during_overload_pause`
  ＝三条都是**钉「本节⛔ 改的那半边」**的表征腿（手动暂停语义、暂停期新非告警负载仍拒收、非告警继续等），
  它们该绿；新行为由另外 13 条红腿证明。
- **落码前重跑了一次红半**（原因见 §13-4 的第 1、2 条：我第一版验收件里两把尺本身写坏，改尺后⛔ 沿旧读数）：
  首版红＝`Ran 15 / FAILED (failures=11)`（同一份旧码），终版红＝`Ran 16 / FAILED (failures=13)`。**只认终版那次。**
- **绿半**＝活树 `python3 -m unittest tests.test_audit_1002_batch12_dcd5` → `Ran 16 tests / OK`（用时那格是每次跑的浮动值，
  ⛔ 抄进文书当身份；只认 `Ran=` 与判定行，读数件 `green_final.txt` 里有）；
  同批回归＝批11 `Ran 20 / OK`、批10 `Ran 22 / OK`、模块级 shim 闸 `Ran 20 / OK`（读数件 `green_final.txt`）。
- **变异 14 枚全被咬住**＝`scripts/audit_1002/mutate_dcd5_overload_1002.py` → `BASELINE|rc=0|reds=0`、
  `SUM|mutated=14|not_bitten=0`、`RESTORED|rc=0`（跑完 `queue.py` md5 回到 `3349e9bf6816`，⛔ 留 `.bak`；
  两档 suite 的 reds 相加才算覆盖面）。读数件＝`workorders/readings/1002b12/mutation.txt`。
  十四枚＝M1 不让位（连带把本批助手变成没人走的路）／M2 丢弃计数把当前这条也算进去／M3 每次过载都续期暂停／
  M4 告警档也进丢弃名单／M5 同带丢最新而非最旧／M6 出队侧暂停全挡／M7 把「只豁免过载暂停」放宽成「任何暂停都豁免」／
  M8 过载暂停期间非告警不再等待／M9 把 TTL 顺延那截接回去／M10 当前这条又被吞（旧行为整段复活）／
  M11 播报文案退回「已清空」／M12 文件头退回「清空」／M13 旧原因码复活／M14 暂停期间连告警一起挡在入队口。
  ⚠ M14 咬住的腿名是 `test_alert_paused_ttl_is_no_longer_deferred`（那条腿第一句 `assertTrue(res.accepted)` 顺带覆盖了
  「暂停期告警照收」）——**腿名⛔ 等于它承担的判据**，登记在此防下次按名找腿。

### §13-4 判例回身咬自己：两把尺在落码前被我自己判坏（⛔ 等代码打完再修尺）

本节按批11 §12-4 的判例提前自扫，抓到两处判坏（都在**落码之前**改掉，代价＝红半重跑一次）；第三条⛔ 同类缺陷，
是我为了「判坏时别静默放行」而补的自证腿：

1. **自指命中**：判「`REASON_OVERLOAD_RESET` 已拆干净」那把尺的扫描面含 `tests/**/*.py`，
   而**本文件正文就写着那个名字**（`hasattr(q_mod, "REASON_OVERLOAD_RESET")` 与那条正则的字面量）⇒ 判据永红，
   且红的原因不是残留。旧码那次它恰好先在前一条断言上红，所以这条坑没暴露。
   处置＝扫描面按路径剔掉本文件（`if p.resolve() == own: continue`），并在 msg 里说明剔的是判据自身。
2. **空号尺数错了单位**：第一版数「命中该符号的**文件数** ≥2」，把「只在自家文件内被调用的私有助手」误判成空号。
   批11 §12-4 那条判例的字面是「全树引用只有定义行本身」＝数的是**调用行**，⛔ 文件数。
   处置＝拆成两条腿：`test_batch12_helper_exists_and_is_actually_called`（`def` 行恰 1 ＋ 非 `def` 的引用行 ≥1）
   ＋`test_batch12_ships_no_other_new_zero_caller_symbol`（本批第二枚符号：生产码命中清单必须为空）。
   这条属 MEMORY 里「坏判据不许抽作者」那一族——判据在**合法实现**下也要成立，否则它测的是我的手感。
3. 分母自证两条腿都带：`assertGreater(len(src), 50)`＝扫描面塌了这把尺要红，⛔ 把「没测到」读成「通过」。
   这把尺实际扫到 **219 枚** `butler/**/*.py`（现读＝`python3 -c "import pathlib;print(len(list(pathlib.Path('butler').rglob('*.py'))))"`；
   下限只写 50＝留余量）。⛔ 与 §13-6 那格 `FILES_SCANNED|65` 混用——那 65 是**测试文件数**（`tests` 下 rglob 现读同值）。

本节造的新符号只有 `_shed_lowest_for_room` 一枚，被 `:371` 调用（M1 那枚变异＝专测「只剩 def 没人走」要能咬）。

### §13-5 顺带登记两处「装好但从未接线」（⛔ 占裁定名额，定性归 DCD）

1. **`on_overload` 生产侧从未接**：尺＝`grep -rn "on_overload" butler --include=*.py` → 命中全在 `butler/tts/queue.py`
   自己文件内（形参 `:228`、赋值 `:235`、`_fire_overload` 内部 `:626/630/…`）；`init_queue` 的签名
   （`butler/tts/singleton.py:43`）只接 `on_expired`，构造 `TTSQueue` 时⛔ 传 `on_overload=`。
   ⇒ 「过载时向客厅播报一句通知」这条能力**从没生效过**，本节改完 `_fire_overload()` 在生产路径上依然空转
   （测试里接了假回调，所以腿照跑）。补法＝`singleton.py` 一行；但它是**新能力上线**还是**修 bug**，要定性才动。
2. **`TTSQueue.pause()/resume()` 零生产调用者**：尺＝`grep -rn "\.pause(\|\.resume(" butler --include=*.py`
   → 五处命中全是别家同名方法（`xiaomi_ear.py:147/149`、`triggers/health_api.py:66/92`、`audiobook_routes.py:77/85`）。
   本节给 manual 暂停保留了原语义并单独立一条腿（`test_manual_pause_still_blocks_alerts`，旧码上也绿＝表征腿），
   ⛔ 顺手删这个 API——「只有测试在引用我」这件事另开一张单判。

两条边界（⛔ 夸口）：以上只是**代码侧 grep 证据**，我没做活体探针（容器内 `sys.modules`／真实调用计数那层没查）。

### §13-6 本轮分母（三个数，⛔ 用「全过」代替）

| 口径 | 数 | 来源 |
|---|---|---|
| AST 里的 test 定义总数 | 857＝类内 757＋模块级 100（＝§12-6 的 841＋本批 16） | `python3 scripts/audit_1002/ast_test_census_1002.py`（`workorders/readings/1002b12/census.txt`，`FILES_SCANNED|65`） |
| 本宿主机可收集＝实跑 | 647（＝631＋16） | `python3 -m unittest discover -s tests -t .` 的 `Ran=`（读数件 `green_final.txt` 末段） |
| 类内 − 实跑 | 757−647＝**110** | 与 §11-5／§12-6 那格**同号**＝本批没新增「宿主导不进来的档」；模块级那 100 条另由 `tests/test_v25_pytest_shim.py`（`Ran 20 / OK`）跑 |
| 红 | `FAILED (errors=10, skipped=9, expected failures=1)`；`^FAIL:`＝0 | `ModuleNotFoundError` 计数＝starlette 8＋pytest 2（环境因，⛔ 记成代码缺陷），本批跑完复采同数 |
| 契约门／仓根 gates | `gates_rc=2`＝homesdk 未装（`workorders/readings/1002b12/gates.txt`） | 环境缺口，⛔ 记成本批缺陷；本批四枚生产/测试文件全在 `butler/` 与 `tests/` 自家，⛔ 触及跨仓面 |

### §13-7 本节⛔ 含的（留给 ⑤-1／⑤-3／跟办／独立议题）

1. **⑤-1 一套队列归一**＝等 b12 决策申请回单（甲＝只拆 guard 那口假队列，HTTP 腿当场播不变，我推荐；
   乙＝甲＋把 HTTP `/api/tts/speak` 接进 `tts/queue.py`，代价＝所有调用方响应语义变更）。
   **丙（guard 读真队列深度）我已驳回并登记在单里**：HTTP 腿从不进 `tts/queue.py` ⇒ 风控读到的高度恒为 0＝假绿通道。
2. **⑤-3 两处常量并一处**＝⑤-1 的推论（选甲则 `:58-59` 那两枚随拆消失），⛔ 单独裁。
3. `on_overload` 接线定性（§13-5-1）与 `TTSQueue.pause()/resume()` 去留（§13-5-2）。
4. **`tts/helper.py:20` 的 `override_quiet=True` 是默认值**＝§12-7-3 已登记，与⑤ 同域，⛔ 二次投递。
5. **跟办 2**（`trigger_cooldowns.json` 进 SQLite）＝`:107` 明写「确认是欠执行⛔ 待裁，排进下一批」⇒ 本节没做，账还挂着；
   **跟办 1**（`security_level`）＝`:106` 独立议题，已随 §11-6 问 4 递出。

### §13-8 复跑（命令原文，⛔ 凭手感重述）

```text
cd /vol1/1000/docker/doubao-butler        # 权威树＝NAS，⛔ E 盘镜像（生产码面已过期）
python3 -m unittest tests.test_audit_1002_batch12_dcd5   # 批12 验收 16 例
python3 -m unittest tests.test_audit_1002_batch11_dcd3   # 批11 回归 20 例
python3 -m unittest tests.test_audit_1002_batch10_dcd2   # 批10 回归 22 例
python3 -m unittest tests.test_v25_pytest_shim           # 模块级 shim 闸 20 例（含 test_tts_queue 31 条）
python3 scripts/audit_1002/mutate_dcd5_overload_1002.py  # 变异 14 枚，自动还原＋md5 复验
python3 scripts/audit_1002/ast_test_census_1002.py       # 分母那把尺
python3 -m unittest discover -s tests -t .               # 宿主全量，三个数
bash gates.sh                                            # 仓根门：宿主 rc=2＝homesdk 未装
# 一次性落码器：锚点已施加过，重跑必 raise（raise＝它对，不是坏）
python3 scripts/audit_1002/patch_dcd5_1002.py
# 红半复现＝对 HEAD 真旧码跑终版验收件（⛔ checkout 工作树）
rm -rf /tmp/b12red /tmp/b12red_data && mkdir -p /tmp/b12red
git archive HEAD butler tests scripts | tar -x -C /tmp/b12red
cp tests/test_audit_1002_batch12_dcd5.py /tmp/b12red/tests/
cd /tmp/b12red && DATA_DIR=/tmp/b12red_data python3 -m unittest tests.test_audit_1002_batch12_dcd5
```

（本节⛔ 测到的：`queue.py` 的源码形状＋16 条腿＋14 枚变异腿＋分母三数。本节⛔ 测不到的，三格一律标「未量」，⛔ 写成绿：
① **现网未生效**＝`butler -> /app/butler` 是 bind mount，`.py` 落盘＝已落码，生效要一次授权 `docker restart`
（`up -d` 会丢可写层热修，⛔）。旧行为今天还在现网跑着：过载那一刻整队清空＋吞当前这条；
② **真实播报节律下会不会有该播的被过期掉**——本批只改过载分支，TTL 数值仍照抄裁定表（§12-7-2 那格同病），未量；
③ **过载暂停期告警当场播**在真机上响没响过＝未量（假时钟证据⛔ 等于客厅证据）。
读数件＝`workorders/readings/1002b12/`（`red_vs_head.txt`／`green_final.txt`／`mutation.txt`／`census.txt`／`gates.txt`／
`ledger_after.txt`），名单以该目录 `ls` 现读为准、**本节点名非全集**，最后一段最新；该目录 `??` 未跟踪＝⛔ 无 git 托底，
所以本节正文每个数都另有一条我已跑命令的输出行，⛔ 依赖这些文件存活。
落盘自量：施加＝append-only，脚本 assert「旧文本是新文本的前缀」；施加前＝
`0c6f6cc5c6cb9436afabd1125ec4aa0a`／813 行／97,371 B／CR 0／`^## ` 12 段＋`^### ` 27 段（这几格数⛔ 会随本节追加而变，
是**追加前**的定值，写死安全）；追加后的字节数／段数⛔ 写进本节（§12-5-2 那条 7 字节教训），只指两处＝
本笔 commit 正文的 `APPLIED|` 行，与 `workorders/readings/1002b12/ledger_after.txt`（`wc -c`／`grep -c "^## "`／
`grep -c "^### "`／二进制 CR 四把独立尺同批打）。最终字节的 md5 与落盘戳只出现在本笔 commit 正文的 `date -u` 原样行
（取于本文件最后一次写入之后）。本仓⛔ 戳尺 ⇒ 这两腿判「人工-无尺」，与 §8／§9／§10／§11／§12 同口径。）
