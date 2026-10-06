# C 桶 29 行语义对账 · 2026-10-03

范围：`roster_reconcile_1002.tsv` 中 `bucket==C` 的全部 29 行（以 TSV 为准）。
台账＝`ledger_copy_1002.md`（1,235 行版）。所有行号来自 `grep -n` 或带行号打印的真实读数。

作废区核对：本台账唯一的置顶撤回＝§15-0（`:1094`，撤回 §14-6 的「面板在撒谎」说法，但 §14-6 的键形制 bug 本体未被否定、且已由批14 修复）；§10-1 表里 ②③⑤ 的「⛔ 未开工」被 §11/§12/§13 覆盖（`:558`、`:667`）。本文件 29 条判定均未引用任何作废段落。

汇总：COVERED 1 条（行 176）· PARTIAL 5 条（行 47、151、159、172、181）· NOT_IN_LEDGER 23 条。

---

### C|6|P0-2
- 机制（照抄 TSV 的 mechanism，可截断）：`_env_bool` 三元两分支均 False → 布尔配置永不为 True
- 我在台账里找过的关键词（机制组：`_env_bool`、`布尔配置`、`永不为 True`、`三元`、`布尔`；文件组：`config.py`；符号组：`env_bool`）：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。全部关键词零命中；最近邻是 `:27`「`api/config_routes.py` 用了 `logger` 却没定义…config.json 一损坏，兜底分支自己抛 NameError」——同是 config 面但机制完全不同（logger 未定义 ≠ 布尔解析恒 False）。

### C|37|NEW-3,P2-2
- 机制（照抄 TSV 的 mechanism，可截断）：`time.sleep(2) if False else None` 恒假死代码且 time 未导入 → restart() 不等待，读到过渡态误判
- 我在台账里找过的关键词（机制组：`恒假`、`过渡态`、`不等待`；文件组：`docker_tools`；符号组：`time.sleep`、`sleep(2)`）：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。三组关键词全零命中；台账里的「restart」全部是运维 `docker restart` 窗口语境（如 `:14`），与本缺陷的 restart() 等待逻辑无关。

### C|46|P1-15
- 机制（照抄 TSV 的 mechanism，可截断）：_save 非原子全量覆盖，损坏即别名归零、无备份无告警
- 我在台账里找过的关键词（机制组：`别名`、`原子`、`非原子`、`备份`；文件组：`aliases`；符号组：`_save`）：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。`_save` 唯一命中是 `:991`「`_save_cooldowns()` 改写库」＝触发冷却迁 SQLite（表行 62/跟办 2 那条线），与别名文件非原子覆写不是同一缺陷；「备份」零命中。

### C|47|B-19,P2-11,P2-35
- 机制（照抄 TSV 的 mechanism，可截断）：路径与 Settings.data_dir 脱钩 → 改 DATA_DIR 即数据分裂；self_evolve 另缺 try/finally
- 我在台账里找过的关键词（机制组：`/app/data`、`DATA_DIR`、`data_dir`、`硬编码`、`数据分裂`；文件组：`self_evolve`、`aliases`、`deps`；符号组：`try/finally`）：
- 判定：PARTIAL(主机制已按「表行 47」点名登记为待办，但同行附带的主张「self_evolve 另缺 try/finally」台账无任何记录——`self_evolve` 字样零命中，`:26` 的 61 行 P1-6 修的是 analyze_llm_traces 返回形状，另一码事)
- 证据（台账原文引一句 + 行号）：`:242`「P2-15、M-01、表行 47 的 `/app/data` 硬编码族（24 个文件）」（§6 剩余面·待办簇 ⑥）。

### C|68|M-08,P1-21
- 机制（照抄 TSV 的 mechanism，可截断）：无 raise_for_status → 401/500 错误体被当成功解析，熔断失效、故障伪装成「没查到」
- 我在台账里找过的关键词（机制组：`错误体`、`熔断失效`、`伪装`、`没查到`；文件组：`memory_agent`；符号组：`raise_for_status`、`401`）：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。MA 族在册条目全是别的机制：`:93` MA-02＝extractor 排序截旧、`:42`＝retrieve member_id fail-closed 测试对齐、`:107` MA-08 已证否。「401」命中均为字节数巧合（`:700`「401 行」）。

### C|90|B-03
- 机制（照抄 TSV 的 mechanism，可截断）：无 sleep/movie 自动退出规则 → 模式卡死，连锁禁用当日晨起简报与整层主动问询
- 我在台账里找过的关键词（机制组：`自动退出`、`退出规则`、`卡死`、`晨起`、`简报`、`问询`；文件组：`auto_switch`；符号组：同文件面零命中）：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。最近邻 `:117`「**整个模式行为规则层未接线**（睡眠模式照旧开口）」＝表行 91 B-04 的门未接问题，与 B-03 的「模式进了不退」方向相反、机制不同；§11 批10 装门也未触及退出规则。

### C|94|T-04
- 机制（照抄 TSV 的 mechanism，可截断）：fast_routes.json 解析失败回退空列表且不回填内置规则 → 快速路由永久静默失效
- 我在台账里找过的关键词（机制组：`解析失败`、`回退`、`回填`、`内置规则`、`静默失效`；文件组：`fast_routes`；符号组：`fast_routes.json`）：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。fast_routes 三兄弟（本行与行 95、127）在台账零提及；RB 的 T 族在册只有 `:96`「| 131 | T-13 (RB) | `record_fallback` 同上（零非定义引用）」＝另一缺陷。

### C|95|T-05
- 机制（照抄 TSV 的 mechanism，可截断）：路径硬编码 /app/data 且构造期 mkdir/open 无保护 → 非默认环境 Agent 构造即崩
- 我在台账里找过的关键词（机制组：`构造即崩`、`构造期`、`mkdir`、`open 无保护`；文件组：`fast_routes`、`agent.py`；符号组：`/app/data`＋族核对）：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。`:566`/`:701` 的「构造期注入拿到的必然是 None」是 mode_engine/push_guard 晚绑说明，非本缺陷；`/app/data` 硬编码族登记（`:242`）点名的是「表行 47（24 个文件）」，24 文件未列名册、其中是否含 fast_routes.py 无从指认，且「构造即崩」半条机制任何族都不覆盖。

### C|109|M-09
- 机制（照抄 TSV 的 mechanism，可截断）：role_state.json 非原子覆写 + 只捕 FileNotFoundError → 写坏后投喂接口全线 500 且不自愈
- 我在台账里找过的关键词（机制组：`投喂`、`自愈`、`非原子`、`500`（命中仅 `:240` 路由族，机制不符）；文件组：`feeder`、`role_state`；符号组：`FileNotFoundError`）：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。三组关键词全零命中（`FileNotFoundError`、`role_state`、`feeder` 均为 0）。

### C|110|M-10
- 机制（照抄 TSV 的 mechanism，可截断）：投喂丢弃 `new_cid`，成败只看 reply 前缀 → 会话轮换后仍被标 fed
- 我在台账里找过的关键词（机制组：`投喂`、`会话轮换`、`旧事实`、`标 fed`；文件组：`feeder`；符号组：`new_cid`、`reply 前缀`、「前缀」——命中仅 `:1133`/`:1152` 冷却键前缀匹配，另一码事）：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。三组关键词全零命中。

### C|113|D-06
- 机制（照抄 TSV 的 mechanism，可截断）：成员 ID 两套大小写、精确匹配且丢弃无日志 → Kevin/Emily 的 ArcFace 信号被静默吞掉
- 我在台账里找过的关键词（机制组：`大小写`、`精确匹配`、`静默吞掉`、`成员 ID`；文件组：`sources.py`、`fusion`；符号组：`Kevin`、`Emily`、`ArcFace`）：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。presence 面在册的只有两条证否：`:105`「「presence `get_snapshot` 不存在」：定义在 `butler/presence/api.py:27`」与 `:107` MA-08 证否，均与本机制无关。

### C|119|B-10
- 机制（照抄 TSV 的 mechanism，可截断）：加密模式 flush_merged 打到缺 `/推送加密` 的错误 URL，且无论成败无条件清空合并缓存 → 消息丢失
- 我在台账里找过的关键词（机制组：`推送加密`、`合并缓存`、`无条件清空`；文件组：`bark.py`；符号组：`flush_merged`）：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。bark 面在册命中全是别家机制：`:51` 签名外入参 `reason=`、`:621` bark_silent 语义、`:118` ttl_s 别混号提醒，无一触及 flush_merged/URL 缺段/缓存无条件清空。

### C|127|T-06
- 机制（照抄 TSV 的 mechanism，可截断）：快速路由每次命中都在 async 热路径同步截断写整个规则文件
- 我在台账里找过的关键词（机制组：`热路径`、`截断`、`同步写`；文件组：`fast_routes`、`agent.py`；符号组：整文件 grep「截断」命中仅 `:174` 讲管道 head 截断读数，非本缺陷）：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。三组关键词均无同机制命中。

### C|143|M-19
- 机制（照抄 TSV 的 mechanism，可截断）：recall 先切片 `[-limit:]` 后过滤 revoked → 取回条数不足/可能取最旧
- 我在台账里找过的关键词（机制组：`先切片`、`取回条数`、`过滤`、`召回`；文件组：`memory_agent`；符号组：`revoked`、`recall`、`[-limit:]`）：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。`revoked` 零命中；`:93`「`memory/extractor.py:110` `ORDER BY ts ASC` 配 LIMIT ⇒ 截的是最旧 N 条」是**同族不同缺陷**（extractor 的 SQL 排序 vs memory_agent recall 的先切片后过滤 revoked，文件与形状都不同，批8 的 `ORDER BY ts DESC` 修复（`:437`）不落在本机制上）。

### C|144|M-20
- 机制（照抄 TSV 的 mechanism，可截断）：feed(500)/overview(1000) limit 不一致 + 先截断后过滤 → 旧事实永不投喂
- 我在台账里找过的关键词（机制组：`投喂`、`旧事实`、`先截断后过滤`、`limit 不一致`；文件组：`feeder`；符号组：`feed(`、`overview(`、「limit」——命中仅记忆条数参数说明（`:93`、`:116`），非本缺陷）：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。feeder 两兄弟（行 109/110/144）在台账零提及；`:116`「`:111 LIMIT ?`（`:112` 传 `max_turns * 3`…）」讲 extractor 溢出需 ~129 条/天，另一缺陷。

### C|147|D-14
- 机制（照抄 TSV 的 mechanism，可截断）：场景 morning 的 description 声称「切 CCTV1」，steps 里没有任何换台动作
- 我在台账里找过的关键词（机制组：`CCTV`、`换台`、`场景`、`文案与执行不符`；文件组：`scene_routes`；符号组：`description`/`steps`）：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。全零命中。台账里「文书承诺 vs 代码没做」的形状很多（§8 六议题），但没有一条是场景 steps 缺换台动作。

### C|150|B-23
- 机制（照抄 TSV 的 mechanism，可截断）：文档声明 3 项检查实际只实现 2 项，「门窗」只匹配 door
- 我在台账里找过的关键词（机制组：`门窗`、`3 项检查`、`只实现`；文件组：`security_monitor`；符号组：`window`（无）——security 面命中仅 `:117`/`:627` 的 `security_level` 模式语义，另一议题）：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。三组关键词无同机制命中。

### C|151|B-24
- 机制（照抄 TSV 的 mechanism，可截断）：已读路由 `{id}` 未限定整型 → 非数字路径 500
- 我在台账里找过的关键词（机制组：`未限定`、`整型`、`非数字`、`路由未校验`（命中 `:240` 族登记）；文件组：`notification_routes`（零命中）；符号组：`{id}`）：
- 判定：PARTIAL(§6 待办簇④按机制登记了「路由未校验入参 → 500」这一族，但族的花名册只写「P2-1~P2-31 里约 15 行」，本行报告 ID 是 B-24（RB），不在其点名范围内，也没被单独登记——差在「本行确属该族」这层认领无法从台账文字指认)
- 证据（台账原文引一句 + 行号）：`:240`「④ 路由未校验入参 → 500 族（P2-1~P2-31 里约 15 行）⑤ 保留策略缺失族（B-25 / P2-12）」；`:151` 的 `notification_routes` 字样全台账 0 命中。

### C|159|P2-7
- 机制（照抄 TSV 的 mechanism，可截断）：会话落盘同步 I/O 在请求热路径；import 期读硬编码 /app/data/sessions.json，非容器失效
- 我在台账里找过的关键词（机制组：`热路径`、`同步 I/O`、`会话落盘`；文件组：`sessions.json`（0 命中）、`deps.py`（0 命中，但 `:242` 族＝表行 47 的文件名册在汇总表侧含 deps.py:34）；符号组：`import 期读`）：
- 判定：PARTIAL(/app/data 硬编码半条落在表行 47 族的登记里（deps.py 明列该族文件名册），「会话落盘同步 I/O 在请求热路径＋import 期读」半条台账无任何记录；§6-②「同步 DB 阻塞族」说的是 DB，sessions.json 是文件 I/O，指不上)
- 证据（台账原文引一句 + 行号）：`:242`「表行 47 的 `/app/data` 硬编码族（24 个文件）」；`:239`「② 同步 DB 阻塞族（6 ID）」（范围不含本行的文件 I/O 说法，未认领）。

### C|163|P2-11
- 机制（照抄 TSV 的 mechanism，可截断）：用户与内置同名引擎时 user 静默覆盖 builtin 无告警
- 我在台账里找过的关键词（机制组：`静默覆盖`、`内置同名`、`同名引擎`；文件组：`plugins`；符号组：`builtin`——命中仅 `:467` 模板测试名与 `:589` 技能目录普查，非本缺陷）：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。注意撞号陷阱：台账 `:242` 登记的「表行 47」其 ID 串里也有 P2-11（R3 侧），与本行 P2-11（RO，plugins 覆盖）同号不同缺陷，按 §5-2 撞号规矩不能拿来判 COVERED。

### C|169|P2-17
- 机制（照抄 TSV 的 mechanism，可截断）：user_device_map 漏配 device_tracker → 该成员恒 unknown、永久隐身
- 我在台账里找过的关键词（机制组：`隐身`、`恒 unknown`、`漏配`；文件组：`presence_config`（命中 `:107` 为 MA-08 证否引的 presence_config.json:51，另一缺陷）、`user_device_map`（0）；符号组：`device_tracker`）：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。`:107`「真源是 `data/presence_config.json:51` 的实体名字符串 ⇒ 「代码里没这个函数」不是缺陷」只处理 MA-08 的存在性主张，不覆盖 device_tracker 漏配。

### C|171|P2-21
- 机制（照抄 TSV 的 mechanism，可截断）：`guard(request)` 调用两次（重复鉴权 + 重复 sessions.json I/O）
- 我在台账里找过的关键词（机制组：`重复鉴权`、`两次`（命中 `:109`/`:601`/`:704`/`:1020` 均别事）、`双调用点`；文件组：`stream_routes`（0）；符号组：`guard(request)`）：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。三组关键词无同机制命中。

### C|172|P2-23
- 机制（照抄 TSV 的 mechanism，可截断）：recent/state 缺 guard；`int(limit)` 未包 try
- 我在台账里找过的关键词（机制组：`缺 guard`、`鉴权缺失`、`int(limit)`（0）、`未包 try`；文件组：`dialog_routes`（0）；符号组：`guard`——命中皆 push_guard/闸门语境，另族核对 `:240`）：
- 判定：PARTIAL(`int(limit)` 未包 try → 500 这半条正中 §6-④「路由未校验入参 → 500 族」，且 P2-23 落在族点名的 P2-1~P2-31 范围内；「recent/state 缺 guard」那半条是鉴权缺口不是 500，任何族都不覆盖，台账亦无单点记录)
- 证据（台账原文引一句 + 行号）：`:240`「④ 路由未校验入参 → 500 族（P2-1~P2-31 里约 15 行）」。

### C|173|P2-24
- 机制（照抄 TSV 的 mechanism，可截断）：except 后以 HTTP 200 返回 `{"pushed": False}` → 只看状态码的调用方误判成功
- 我在台账里找过的关键词（机制组：`状态码`、`误判成功`、`假成功`；文件组：`bark_routes`（0）；符号组：`pushed`——「假绿」命中（`:43` 电视通知、`:1039` §14-6/§15-0）都是别家机制）：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。本行的 except→200+pushed:False 形状与 `:43` 的「notify 返回 None 被 `_to_tv` 丢成无条件 True」是两条不同代码的缺陷，不能互认。

### C|176|P2-28
- 机制（照抄 TSV 的 mechanism，可截断）：messages 元素假定 dict → 任一非 dict 即 500
- 我在台账里找过的关键词（机制组：`路由未校验`（命中 `:240`）、`入参校验`、`500`；文件组：`openai_routes`（0）；符号组：`messages`、`非 dict`）：
- 判定：COVERED(§6 待办簇④, 台账行 240)——处置形态是「登记为在册待办」，非已修；机制正中「路由未校验入参 → 500 族」，报告 ID P2-28 落在族点名的 P2-1~P2-31 范围内
- 证据（台账原文引一句 + 行号）：`:240`「④ 路由未校验入参 → 500 族（P2-1~P2-31 里约 15 行）」。

### C|177|P2-29
- 机制（照抄 TSV 的 mechanism，可截断）：`all(r["ok"] …)` 依赖返回契约，某分支漏 "ok" 即 KeyError
- 我在台账里找过的关键词（机制组：`返回契约`、`漏 "ok"`、`KeyError`；文件组：`scene_routes`（0）；符号组：`all(r`——命中 `:43`「`NotifyResult.ok`（`:142`，`all(r.ok)`）在「丢弃」那条腿上读成成功」）：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。`:43` 是同形制词面（all(...ok)）下的**另一枚缺陷**：notify/router 的 tv 腿假绿（已修 6b6a92b），与本行 scene_routes 的 `r["ok"]` KeyError 风险不同文件不同病因，不能拿来判 COVERED。

### C|178|P2-30
- 机制（照抄 TSV 的 mechanism，可截断）：schedule 失败被 except 静默吞成 `{}`，不可观测
- 我在台账里找过的关键词（机制组：`静默吞`、`不可观测`、`咽掉`（命中 `:26`/`:36`/`:51` 全是别的 except 咽案例）；文件组：`profile_routes`（0）；符号组：`schedule`（命中均 cron_task/media_stop 语境））：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。台账有「except 咽掉」这一族的通用敏感（§5），但没有一条落在 profile_routes schedule→{} 这枚具体缺陷上。

### C|181|P2-4
- 机制（照抄 TSV 的 mechanism，可截断）：adm_heartbeat 任务无取消路径；固定 client_id 使新旧容器互相踢
- 我在台账里找过的关键词（机制组：`取消路径`、`不收尾`（族核对 `:239`）、`互踢`、`踢`；文件组：`mqtt_client`（0）；符号组：`adm_heartbeat`、`clean_session`、`client_id`——mqtt 命中 `:43`/`:119` 均别事）：
- 判定：PARTIAL(§6-① 登记了「关停不收尾族（6 个 ID 并 1 行）」，adm_heartbeat 无取消路径正中这一机制，但 6 枚 ID 未列名册、无法指认本行在不在册；「固定 client_id 新旧容器互踢」半条任何族都不覆盖，台账零记录)
- 证据（台账原文引一句 + 行号）：`:239`「① 关停不收尾族（6 个 ID 并 1 行）② 同步 DB 阻塞族（6 ID）③ SQLite 连接族（4 ID）」。

### C|184|P2-10
- 机制（照抄 TSV 的 mechanism，可截断）：回执回调在 paho 网络线程内做阻塞 IO → 阻塞 MQTT 心跳/收包，触发假掉线重连
- 我在台账里找过的关键词（机制组：`假掉线`、`回执`、`心跳`（命中 `:1033` 是 decision_heartbeat 触发器名，别事）、`阻塞 IO`；文件组：`mqtt_client`（0）；符号组：`zap`、`on_result`、`paho`）：
- 判定：NOT_IN_LEDGER
- 证据（台账原文引一句 + 行号）：无证据行。最近邻是 `:239`「② 同步 DB 阻塞族」，但本行机制是 paho 网络线程被回调 IO 卡住（心跳/收包面），台账文字没有任何一处能指认它包含本行，三组关键词也全无命中，按规矩判找不到。
