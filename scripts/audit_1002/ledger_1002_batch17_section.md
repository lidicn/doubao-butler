## §18 批17 · 「整份 JSON 就地截断写」一族：新 helper `write_json_atomic` + 五处落点 + 读侧语义（表行 45／109／94／127，2026-10-03）

### §18-0 本节怎么读（先给三句话）

一句活：本批把**五处**「把整份 JSON 直接盖在真文件上」的写手改成**先序列化、再写兄弟临时文件、再 `os.replace` 顶替**，
并把**两处读侧**（`feeder` 读 `role_state.json`、`fast_routes` 读 `fast_routes.json`）从「崩／静默回退空表」改成
「记一条 error 并回退内置值、且⛔ 构造期把坏文件覆写掉」。
状态＝**已落码-未生效**（与 §17-8 同一张窗单，唯一路径仍是那一次经授权的 `docker restart`）。
本节的每个数都另有一条我已跑命令的输出行，出处集中在 `workorders/readings/1003b17/`（该目录 `??` 未跟踪＝⛔ 无 git 托底）。

### §18-1 报告坐标（E 盘原文现读；表行号 = 该文件行号 − 20）

`doc/审计报告/_缺陷汇总_供审阅.md` 只在 E 盘工作副本，**权威树没有这个目录** ⇒ 原文从 E 盘读、**代码一律从权威树读**（口径沿用 §16-10／§17-11）。
本批四枚，本次现读：

| 表行 | 该行在文件中的行号 | 原文报告号 | 原文坐标 | 原文一句话 |
|---|---|---|---|---|
| 45 | :65 | P1-13 (R3) | `butler/roles/store.py:175-180`、`butler/devices.py:171-178` | 无锁 + 裸 `write_text` 非原子 → 崩溃留截断 JSON，用户角色／设备配置静默重置为出厂 |
| 109 | :129 | M-09 (RB) | `memory/feeder.py:43-54` | `role_state.json` 非原子覆写 + 只捕 `FileNotFoundError` → 写坏后投喂接口全线 500 且不自愈 |
| 94 | :114 | T-04 (RB) | `core/fast_routes.py:30,34,36` | `fast_routes.json` 解析失败回退空列表且不回填内置规则 → 快速路由永久静默失效 |
| 127 | :147 | T-06 (RB) | `core/fast_routes.py:118,127`、`core/agent.py:53` | 快速路由每次命中都在 async 热路径同步截断写整个规则文件 |

原文行号与权威树现读行号**不等**（报告写 `devices.py:171-178`，落码前那枚 `save()` 在 `:169-178` 一带；批16 §17-2 已记过这类漂移）
⇒ 定位一律按「符号 + 上下文」，行号只作参考；本批改动落点的**落码后**行号见 §18-4 表。

### §18-2 为什么这四枚同批（以及为什么⛔ 给两处补「序列化失败」腿）

四枚是**同一个失效形状**：`write_text(json.dumps(...))` / `open(w)+json.dump` 先把真文件截断成 0 字节，再往里写；
进程在写完之前死掉，盘上就留一份截断 JSON。下一次读到的行为分三种，全都是坏消息：
`devices`／`roles/store`／`roles/state` 读到损坏即**回退出厂默认**（用户配置归零，表行 45 后半句），
`feeder` 只捕 `FileNotFoundError` ⇒ `JSONDecodeError` 直接冒到接口层（表行 109 的「全线 500」），
`fast_routes` 捕 `Exception` 后回退**空列表**并且从不回填内置规则 ⇒ 快速路由永久失效且零日志（表行 94）。
同一形状同批改，能一次把「临时文件命名」「`.tmp` 不被 glob 吃到」「读侧异常语义」三件事只写一遍。

⛔ 给五处落点逐一补「序列化失败」腿的理由（写在这里免得下批当成缺口）：序列化在**目标文件之外**做，
失败时既没碰真文件也没碰临时文件 ⇒ 这条路径在五处是**同一份 helper 的同一条腿**，由 §18-3 的两条 helper 腿覆盖；
在五处各写一遍只会得到五次同一个断言。真站得住的缺口是「helper 之外还有⛔ 走这条路径」，那由两把尺的对账负责（§18-8）。

### §18-3 验收：20 条腿 = 13 先红 + 7 笼头对照（⛔ 把「全绿」当分母）

件＝`tests/test_audit_1002_batch17_atomic_json_writes.py`，先红读数 `red_batch17.txt`（stamp `2026-10-02T18:27:18Z`，
`Ran 20 tests` / `FAILED (failures=9, errors=4)` / `unittest_rc=1`，`...` 行 20 条＝分母自证）。

13 条先红（名字⛔ 缩略，逐条来自那次输出）：

```
FAIL: test_devices_save_promotion_failure_keeps_previous_content
FAIL: test_role_write_promotion_failure_keeps_previous_content
FAIL: test_role_state_save_promotion_failure_keeps_previous_content
FAIL: test_feeder_role_state_promotion_failure_keeps_previous_content
FAIL: test_feeder_role_state_partial_write_keeps_previous_content
FAIL: test_fast_routes_promotion_failure_keeps_previous_content
FAIL: test_fast_routes_partial_write_keeps_previous_content
FAIL: test_fast_routes_corrupt_file_falls_back_to_builtins
FAIL: test_fast_routes_non_list_json_falls_back_to_builtins
ERROR: test_feeder_read_role_state_survives_corrupt_json
ERROR: test_feeder_get_conversation_id_returns_none_on_corrupt_state
ERROR: test_atomic_helper_writes_file_and_removes_temp_sibling
ERROR: test_atomic_helper_keeps_target_when_serialization_fails
```

前 9 条红在**旧码**（就地截断写／静默回退），后 4 条红在 `ModuleNotFoundError`（helper 当时不存在）＝两种失败原因都对应本批要落的东西，
没有一条是打错字造成的红。7 条笼头对照（先红时就该绿，它们防的是「我把修复写歪成另一种坏」）：

```
test_devices_save_roundtrips_and_leaves_no_temp_sibling
test_role_write_roundtrips_and_leaves_no_temp_sibling
test_role_state_save_roundtrips_and_leaves_no_temp_sibling
test_feeder_role_state_roundtrips_and_leaves_no_temp_sibling
test_fast_routes_first_run_seeds_builtins
test_fast_routes_corrupt_file_not_overwritten_at_construction
test_role_registry_glob_ignores_temp_sibling
```

四条 `roundtrips_and_leaves_no_temp_sibling` 钉的是**格式血统**（落盘文本必须等于 `json.dumps(obj, ensure_ascii=False, indent=2)`）
＋**零临时残留**；`role_registry_glob_ignores_temp_sibling` 钉 `store.py` 的 `*.json` glob 会把 `.json.tmp` 当成一枚角色读进来；
两条 `fast_routes` 对照钉「第一次运行要有内置规则、坏文件不许在构造期被覆写」。

### §18-4 落码：一次性补丁 + 六件产码

补丁＝`scripts/audit_1002/patch_b17_atomic_json_1002.py`（DRY→`--apply`，同一把闸）。施加读数 `apply_and_green.txt`
（stamp `2026-10-02T18:31:56Z`）逐行原样：

```
PLAN|butler/devices.py|lines 218->220|bytes 10865->10995|CR 218->220|md5->15c2516f5a548240623da051fb64c5f7
PLAN|butler/roles/store.py|lines 218->219|bytes 10451->10559|CR 0->0|md5->9e9670b37cbe30b6c30c211805f4cd24
PLAN|butler/roles/state.py|lines 48->50|bytes 1760->1909|CR 48->50|md5->3a08a3bc372eeb3cec832dba35061968
PLAN|butler/memory/feeder.py|lines 297->301|bytes 12549->12881|CR 0->0|md5->dc3bd6cbb8265018817580a08e120d5d
PLAN|butler/core/fast_routes.py|lines 151->161|bytes 5329->6014|CR 0->0|md5->2fb75829c02fe7021bbc04600e40d589
PLAN|NEW|butler/core/atomic_json.py|lines=32|bytes=1776|CR=0|md5=8c4744dbcf3b91e2702ff71073e4effa
APPLIED|6 files|stamp=a4c99fb737f81623abeb1a0d5ceac33a
```

`CR 218->220`、`48->50`＝**换行血统保住**（`devices.py`／`roles/state.py` 整文件 CRLF，插进去的块被补丁脚本按目标文件自己的终止符转换过；
`roles/store.py`／`feeder.py`／`fast_routes.py` 是 LF，CR 恒 0）。计数只用二进制 `count(b"\r")`，⛔ `grep -c $'\r'`（§15 记过它被 Git Bash 吞成空模式）。
五枚既有 atomic 实现（`aliases.py`／`config_routes.py`／`deps.py`／`triggers/store.py`／`skills/store.py`）由 `UNTOUCHED_BEFORE` → 写后再取，
五条 `UNTOUCHED|...|同号` 全在 `apply_and_green.txt` 里 ⇒ 本批⛔ 碰它们（它们已是正解，只是彼此五套写法）。
重跑必 raise（`rerun_must_raise.txt`，stamp `2026-10-02T18:33:45Z`）：`ALREADY_SCAN|...|hit=yes` ×6，
一条 `ALREADY_APPLIED|` 把六件**全点名**（⛔ 只报第一件），`rerun_rc=1`。
落码后单跑验收：`Ran 20 tests in 0.059s` / `OK` / `unittest_rc=0`。

### §18-5 裁定记录一：`roles/state.py` 算不算本批范围

**算，按「同源扩展」并入。** 表行 45 原文只点名 `roles/store.py:175-180` 与 `devices.py:171-178`，没点 `roles/state.py`；
但 `roles/state.py::_save()` 与 `memory/feeder.py::_write_role_state()` 写的是**同一个 `role_state.json`**，
形状与表行 45 前半句完全一致（`write_text(json.dumps(...))`，无锁）。
只改报告点名的两处，第三写手仍会把那枚文件截断 ⇒ 表行 109 的「写坏后 500」照旧能由一个我没修的落点触发。
并入的代价＝多两件文件、多四条腿；收益＝那枚文件的所有写手一次对齐。
⚠ 这条并⛔ 治好「双写手互相覆盖」（lost update）：两处各自读全量再写全量，谁后写谁赢，本批⛔ 碰该语义（登记见 §18-9）。

### §18-6 裁定记录二：`fast_routes` 读侧语义 = 报错 + 回退内置 + ⛔ 覆写坏文件

表行 94 要的是「解析失败别静默、并把内置规则回填」。本批落成形：
`_load()` 先 `isinstance(data, list)` 验形状（非 list 当场 `ValueError`，防「文件是合法 JSON 但不是路由表」这一类），
任何异常一律 `logger.error(...)` 后 `self._builtin()` 回退；**构造期⛔ 调 `_save()`** ⇒ 坏文件留在盘上＝现场保留，
运维还能捞出那份截断 JSON 看它坏在哪，而不是被一次启动悄悄洗干净。
后果要写明白：`fast_routes.json` 坏掉时，用户在面板上改过的自定义路由**不会恢复**（回退的是内置表），
且下一次任何合法写会覆盖坏文件——这是「永久静默失效」换「有声失效 + 自定义丢失」，不是无损修复。
`_save()` 侧本批只把它换成原子写；表行 127 的**性能半**（每次命中同步写整个文件，async 热路径）⛔ 本批做，理由见 §18-9。

### §18-7 变异探针：9 枚真变异全被咬，2 枚等价对照存活

`mutate_b17_1002.py` 读数 `mutation_probe_b17.txt`，出口行原样：
`CENSUS|real=9 bitten=9 controls=2 misbite=0 skipped=无 restore_bad=无`（`rc=0`）。
逐枚（`expected`＝这条变异**应当**咬住的腿数，`bitten`＝实咬）：

```
M1 devices.save 退回就地 write_text                     expected=1 bitten=1
M2 roles/store._write 退回就地 write_text                expected=1 bitten=1
M3 roles/state._save 退回就地 write_text                 expected=1 bitten=1
M4 feeder 写侧退回 open(w)+json.dump 增量截断            expected=2 bitten=2
M5 feeder 读侧退回只捕 FileNotFoundError                 expected=2 bitten=2
M6 fast_routes._save 退回 open(w)+json.dump              expected=2 bitten=2
M7 fast_routes._load 退回静默回退空列表                  expected=2 bitten=2
M8 helper 失败时不清理临时兄弟文件                        expected=4 bitten=3
M9 helper 只写临时文件、从不 rename 顶替                  expected=4 bitten=4
C1 等价对照：tmp 变量改名 + with_name 改写成 parent/()     red_legs=0 SURVIVED
C2 等价对照：只改注释文字                                 red_legs=0 SURVIVED
```

**M8 少咬一条是我的预期错，⛔ 记成缺口**：那 4 条里有一条是「序列化失败」腿，它在 helper 里根本到不了临时文件那一步
（先序列化、后写盘），所以「不清理」对它天然无作用 ⇒ 该腿的预期应为 3。
CENSUS 行仍判绿（`bitten==real` 且对照全存活且无 skip/还原失败），这条错⛔ 会由出口行暴露，故在此点名。
每枚变异跑完都按 GREEN md5 逐字节还原（`RESTORE|...|OK` ×9+2），`restore_bad=无` 是「探针⛔ 把现网改脏」的物理证据。

### §18-8 两把尺对账：把 §17-9 那句「另 12 行＝就地截断写」更正为 21 枚（⛔ 改 §17，追加更正）

§17-9 当时只用了**单行正则** `.write_text\(json\.`（17 行 − 5 行正解 = 12）。本批配了一把 AST 尺并做差集，两个方向都量了：
落码前 `b17_two_rulers_reconcile.txt`（stamp `2026-10-02T18:15:59Z`）：
`SCAN_ROOT|butler|py_files=219`、`AST_RULER|sites=23`、`GREP_RULER|lines=17`、`ONLY_IN_AST=6`、`ONLY_IN_GREP=0`、
`ALREADY_ATOMIC_in_same_func=5`、`IN_PLACE_NONATOMIC=18`、`OTHER_FAMILY|json.dump(_to_handle|count=3`。
落码后同尺复采 `b17_two_rulers_after.txt`（stamp `2026-10-02T18:42:40Z`）：
`sites=20`、`lines=15`、`ONLY_IN_AST=5`、`IN_PLACE_NONATOMIC=15`、`OTHER_FAMILY count=1`、`ALREADY_ATOMIC=5`（未变）。

差集解释（⛔ 一句话糊过去）：`20 = 15 + 5` 自洽；AST 少 3 ＝ 本批改的 `devices.py`、`roles/store.py`、`roles/state.py`；
`feeder.py:54`、`fast_routes.py:44` 那两枚旧写法是 `open(w)+json.dump`，**从来就⛔ 在 write_text 两把尺的分母里**，
它们只出现在 `OTHER_FAMILY` 那一族（3→1）。⇒ 落码前「整份截断写」的真数＝ **18（write_text 族非原子）+ 3（句柄族）= 21**，
§17-9 的 12 是**单行正则的下界**，两次都偏窄（漏 6 处换行写法 + 3 处句柄写法）；本批改 5 枚 ⇒ 余 **15 + 1 = 16**，
其中句柄族剩的那枚是 `butler/api/push_routes.py:152`（⛔ 在任何报告里，我这侧新发现，登记 §18-9）。
`ONLY_IN_GREP=0` 两侧都成立 ⇒ AST 尺⛔ 漏写法。**扫描根＝`butler/`，⛔ 全集**（`scripts/`、`tests/` 未扫）。

### §18-9 ⛔ 本批做的（登记，⛔ 混进本批提交）＋ 我这把尺测不到什么

- **表行 45 的「无锁」半**：五处写手现在原子了，但**并发两个写手**仍会互踩——临时文件名是定值（`target.name + ".tmp"`），
  同文件并发写会写到同一个 tmp。§17-9 给批17 的那条风险原样继承，⛔ 由本批解除；要真解得先测出本仓有没有多进程同写路径（现在没量过）。
- **`role_state.json` 双写手 lost update**（`roles/state.py` 与 `memory/feeder.py` 各自读全量→写全量）：我这侧新发现，⛔ 在报告里，⛔ 动。
- **`butler/api/push_routes.py:152`** 就地 `json.dump` 写技能文件：⛔ 在报告里，我这侧新发现 ⇒ 只登记，开单要另立一批。
- **表行 127 的性能半**（T-06：每次快速路由命中在 async 热路径同步写整个文件）：改法是「去抖／后台写／只在增删时写」，属设计变更＋性能主张，⛔ 与原子写混一批。
- **表行 95 的 T-05**（`/app/data` 硬编码，§17-9 现读 28 次／23 文件）：范围未裁（一次全改＝23 文件大改，只改 `fast_routes`＝又留分裂），⛔ 自主扩。
- **`devices.py::load()` 的种子回滚**：读到损坏文件时它仍会写回出厂默认（表行 146 D-12「复活」问题），本批⛔ 碰——它与「现场保留」是两条裁定，那条已在窗单外挂着。
- **`triggers/audit.py` 一类「写成功才报成功」的收尾**：批16 的口径，本批⛔ 顺手扩大。
- **测不到什么**：
  1. 真断电／`kill -9` 落在 `tmp.write_text` 与 `os.replace` 之间——我只证了「同进程抛异常」和「⛔ 就地截断」两种；
  2. **fsync 级持久性⛔ 主张**（helper 刻意⛔ `fsync`：断电窗口内 tmp 可能已成、rename 未成，此时数据仍是旧的完整文件＝安全方向，但⛔ 由我证明）；
  3. 「截断 JSON 在现网真的发生过」——⛔ 有现网证据，本批按代码语义修，不按事故计数修；
  4. 并发（见上）；5. 面板侧读到 `.json.tmp` 的展示后果——只证了 `store.py` 的 glob 不吃它，⛔ 扫过其它 glob；
  6. `.tmp` 会不会被 NAS 的备份／清理脚本当垃圾删——未查。

### §18-10 本批我自己抓到的错（四条，按发现顺序）

1. **闸门顺序第二次踩同一条坑**（§17-3 记过一次）：重跑补丁先吐 `BASELINE_DRIFT|butler/devices.py|md5`，
   把「已经改过」误报成「基底漂移」——报错方向是错的。修成**逐文件预扫描 `ALREADY_SCAN`**（六件全打印 `hit=yes/no` 再一次失败列全名）。
2. **叠了第二个漏，而且更隐蔽**：`already` 标记字面量结尾带 `\n`，在整文件 CRLF 的 `devices.py`／`roles/state.py` 里**恒不命中**
   ⇒ 那道闸在这两个文件上形同虚设（第 1 条之所以只报第一个文件，正是因为它在此处压根没咬）。修法＝比较前双边归一成 LF 视图。
   ⚠ 教训通用化：**标记类判据的字面量必须匹配目标文件自己的换行血统**，否则闸门是装饰。两处都落进补丁脚本 docstring。
3. **首跑读数被同名重跑覆盖**：验收首跑 `Ran 20 / FAILED (failures=8, errors=7)` 里三条是**我的测试自身缺陷**
   （`DeviceRegistry` 入参传成 `SimpleNamespace`；`role_registry_glob` 那条把 `load()` 的种子行为当断言对象；`non_list` 腿死在我自己的推导式里）。
   修正后重跑复用了同一个文件名 ⇒ **首跑原始读数只存在于会话里，盘上只剩修正版**。已把「读数件一次一名」记为工序要求（下批起）。
4. **变异探针 M8 预期腿数写错**（4 而非 3），出口行照样判绿 ⇒ 见 §18-7；同时探针初稿的 CENSUS 行里有一条恒假表达式，
   改成由 `controls - controls_ok` 推 `misbite` 并单列 `skipped`，退出码收紧。

### §18-11 收口与复跑（命令原文，⛔ 凭手感重述）

```
cd /vol1/1000/docker/doubao-butler
python3 -B -m unittest tests.test_audit_1002_batch17_atomic_json_writes        # 落码前 Ran 20 / FAILED(9,4)；落码后 OK / rc=0
python3 -B scripts/audit_1002/patch_b17_atomic_json_1002.py --apply             # 期望 raise ALREADY_APPLIED|六件全点名 rc=1
python3 -B scripts/audit_1002/mutate_b17_1002.py                               # 期望 CENSUS|real=9 bitten=9 controls=2 misbite=0 skipped=无 restore_bad=无，rc=0
python3 -B scripts/audit_1002/probe_b17_reconcile_1002.py                      # 两把尺对账：AST/GREP 差集 + OTHER_FAMILY
bash scripts/audit_1002/run_b17_regression_1002.sh                             # 六件 GREEN 基线 + 全量回归 + 红名单身份 + AST 三数
```

全量回归读数 `regression_and_census.txt`（stamp `2026-10-02T18:36:18Z`，原件 `discover_full.txt` 18,759 B／`census.txt` 601 B）：
`Ran 729 tests in 60.929s`、`FAILED (errors=10, skipped=9, expected failures=1)`、`count^FAIL: = 0`、`count^ERROR: = 10`、
`redlist_md5=9490b2e3085174ec213d6b34d0e0d01d want=9490b2e3085174ec213d6b34d0e0d01d` → `REDLIST_SAME`
⇒ 十枚红与批14／15／16 **同一批身份**（比的是排序清单的 md5，⛔ 条数），本批⛔ 新增行为回归；
`TMP_REMOVED|/tmp/b17_redlist_9679.txt` 与取数在同一次调用内（「临时文件已删」的盘上反证）；
AST 三数 `FILES_SCANNED|70`、`AST_TOTAL=939` = `IN_CLASS=839` + `MODULE_LEVEL=100`，`census_rc=0`。
⚠ 三数是**本机**口径，⛔ 现网（现网 `tests/` 不在镜像，§15 记过）。

落盘自量：施加＝append-only，脚本 assert「旧文本是新文本的前缀」；追加前基底＝
`02b384650c8d62cff3dab466975561b0`／1,587 行／176,446 B／CR 0／`^## ` 17 段＋`^### ` 75 段（追加前的定值，写死安全）；
追加后的字节数／段数／最终 md5 与 `date -u` 原样戳⛔ 写进本节，只指两处＝本笔 commit 正文的 `APPLIED|` 行与
`workorders/readings/1003b17/ledger_after.txt`（`wc -c`／`grep -c "^## "`／`grep -c "^### "`／二进制 CR 四把独立尺同批打）。
本仓⛔ 戳尺 ⇒ 这两腿判「人工-无尺」，与 §8–§17 同口径。
