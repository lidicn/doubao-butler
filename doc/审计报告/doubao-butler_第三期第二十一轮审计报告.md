# doubao-butler 第三期审计报告 · 第二十一轮

- **审计目标**：`lidicn/doubao-butler`
- **轮次**：round-021
- **专题**：批201 续收三枚——DB47（半死 HA WebSocket 吊死 ws_connect）/ DB53＋DB53b（技能隔离错杀合法技能 + 重启后不自愈）/ DB54（计数窗口被 NTP 阶跃清空 fail-open）
- **落盘状态**：本轮报告**已落盘**（前两轮第十二、第十六曾两次报告文件未落盘却声称有产出，本轮特意不重蹈）

---

## 一句话结论

**收编 4 项：DB47（high）半死 HA 把 WebSocket connect 自己吊死；DB53（high）check_security 整体序列化扫描误杀合法技能；DB53b（medium）隔离时间戳纯内存重启即失；DB54（medium）计数窗口用墙上时钟被 NTP 阶跃清空（fail-open）。**

DB53/DB53b/DB54 同落 `butler/skills/quarantine.py` 一文件，**叠加放大**：一个误判 + 一次重启 = 技能永久死亡，且没有自愈路径。三枚一起收，因为这层耦合不在单枚上看得到。

DB47 是上一轮（第十五轮）确证、补丁只在只读副本的 high，本轮把补丁从副本落到生产码，并**补了一个被自己漏掉的邻接回归**：batch48 握手超时门在 DB47 单点抽象后假红。

---

## 一、DB47 [high]：半死 HA 把 ws_connect 自己吊死

### 根因

```python
# core/event_stream.py / core/xiaomi_ear.py 两腿同形
async with aiohttp.ClientSession() as session:
    async with session.ws_connect(ws_url, heartbeat=30) as ws:
        msg = await asyncio.wait_for(ws.receive_json(), timeout=15)   # 只管握手后读帧
```

`heartbeat=30` 与 `wait_for(ws.receive_json(), 15)` 都只在**连接建立之后**起作用。而半死 HA（TCP 连得上、对方永不开成 WebSocket）是**握手阶段挂起**，不抛异常——上面那段 `wait_for` 根本走不到。重连循环 `while self._running: try: await self._connect() except Exception:` 只在抛异常时重试，挂起不抛 ⇒ 卡在第一轮，**无日志、无重连**。

`event_stream` 是 HA 状态订阅的主通道；这条腿哑了，整个 butler 对 HA 状态变化失聪。

### 本机实测（`workorders/readings/1011/b201_db47_ws_timeout_1011.txt`）

靶子＝accept 后一个字节都不发的半死服务器（HA 半死最真实形态）。本机 aiohttp 3.14.1，⛔ 抄成现网 3.9.5 读数：

| 形 | elapsed | 判 |
|---|---|---|
| [A] 裸 `session.ws_connect(...)` | 15.0s 撞我的闸 | 无界 |
| [D] `timeout=ClientWSTimeout(...)` | 15.0s | 无界（印证第十五轮疑点：只管收发，管不到握手） |
| [E] `asyncio.wait_for(ws_connect)` | 2.0s | **有界** |

选 [E] 而不是 aiohttp 的 `ClientTimeout`：

- `ClientTimeout(total=)` 是**整次操作**的上界，WebSocket 的"这次操作"就是整条连接 ⇒ 健康长连接会被它掐死；
- `sock_read` 对 ws 帧在现网 aiohttp==3.9.5 上的语义本机验不了（本机 3.14.1）⇒ 不押它；
- 只挑**不依赖 aiohttp 版本语义**的那一种（asyncio 自己的上界），且只套「连接＋握手」那一段，稳态收发一点没被管。

### 补丁

新增单点 `butler/core/ws_connect.py`：

```python
@asynccontextmanager
async def bounded_ws_connect(session, url, *, connect_timeout=20.0, heartbeat=30):
    ws = await asyncio.wait_for(session.ws_connect(url, heartbeat=heartbeat),
                                timeout=connect_timeout)
    try:
        yield ws
    finally:
        await ws.close()
```

两腿（`event_stream._connect` / `xiaomi_ear._connect`）改挂这个单点——⛔ 各写各的 try 是根因家族①（护栏装在入口，每加一个入口漏接一次）。

DB47 第三处无超时（`tts_baidu._get_token`）与 `requirements.txt` 声明 `aiohttp==3.9.5` 上一轮已在副本里收，本轮只动这两条 ws 腿。

### 镜像门（`tests/test_audit_1011_batch201_db47_half_dead_ws_leg.py`，6 例）

- `test_01` 半死靶子握手被上界咬住（elapsed < 4s）
- `test_02` **反向用例**：健康应答的握手照放行、拿到可用连接（⛔ 只测拦截会漏掉拦太宽）
- `test_03` 体内异常后 socket 仍关（连接不泄漏）
- `test_04` AST 尺：butler/ 下除单点模块外无裸 `session.ws_connect(`
- `test_05` 尺子咬得住合成裸调用（test_04 不是摆设）
- `test_06` 单点自己的 `ws_connect(` 不被判成病灶（放行侧校准）

### ⚠ 本轮抓到的一条过程缺陷（必须记）

DB47 落盘后跑邻接门，batch6 门报 4 枚锚点漂移：

```
MOVED core/event_stream.py EventStream.stop 104→105
MOVED core/xiaomi_ear.py XiaomiEar.note_speak 133→134
MOVED core/xiaomi_ear.py XiaomiEar.start 44→45
MOVED core/xiaomi_ear.py XiaomiEar.stop 51→52
```

原因：两条腿顶部各净插 1 行 `import bounded_ws_connect`，让后续锚点全 +1。我只重钉了 `EventStream.start` 一枚（96→97），漏了同文件的 `stop` 和 xiaomi_ear 三枚。

**这条的教训**：`git diff` 时只看「改了什么」会漏——必须跑一次门让它自己报。锚点尺子（`def_at` 现读报的行号）是这一枚的承重点，没人现读就漂着。已四枚按符号现读重钉。

### ⚠ batch48 邻接回归（DB47 单点抽象的副作用）

DB47 把两条 ws 腿收进单点后，batch48 握手超时门（`tests/test_audit_1007_batch48_sess_timeout.py`）假红：

- `_receiver_bound_to_wsconnect` 只认 `ws_connect(...) as ws` 绑出的 ws，单点 `bounded_ws_connect(...) as ws` 它不认 ⇒ `recv_total` 3→0（握手帧读点判 0）；
- `ws_sessions` 计数腿只认属性调用 `session.ws_connect`，单点裸名 `bounded_ws_connect(session, ...)` 它不认 ⇒ 1→0。

补丁（只动尺、⛔ 动判据/阈值）：

- 新增 `WS_ENTRY_POINTS = {"ws_connect", "bounded_ws_connect"}` + `_entry_point_name` helper（属性/裸名两形都认）；
- `_receiver_bound_to_wsconnect`：with 绑定源改用 `_entry_point_name` 判，两形都放行；
- `_scan` 的 ws 会话计数腿：`bounded_ws_connect(session, ...)` 裸名按第一实参＝会话变量识别为 WS 会话，heartbeat 标 True（单点模块硬编 `HEARTBEAT_SECONDS=30`，不在调用点）；
- 合成夹具（`FIXTURE_UNWRAPPED` 仍是 `session.ws_connect` 属性形）咬合负控不变——尺仍咬裸 `receive` 无 `wait_for`。

**这条值得记**：这是「补丁改了调用形状、邻接判据还认旧形状」的教科书例子。DB47 自己的门绿了，但 batch48 这枚**同源的**握手门假红了。对策：每落一枚改动调用形状的补丁，都要跑一遍「引用了被改符号」的邻接门。

---

## 二、DB53 [high]：check_security 把"格式"当成"格式化磁盘"

### 根因

```python
# skills/quarantine.py 原 :82-88
def check_security(self, skill: dict) -> tuple[bool, str]:
    skill_str = json.dumps(skill, ensure_ascii=False).lower()   # 整个技能，含自然语言
    for pattern in DANGEROUS_PATTERNS:                           # 黑名单里有裸词 "format"
        if pattern.lower() in skill_str:
            return False, f"安全违规：检测到危险模式 '{pattern}'"
```

15 条黑名单里有一条是**裸词 `"format"`**。而 `schema.py:96` 是 `brain = dict(brain_raw)`——brain 原样透传，`prompt` 是自由文本。

于是 `prompt: "Format the reply as one line"` ⇒ `json.dumps(...).lower()` 含 `"format"` ⇒ 判违规。

### 后果链

```
runner.py:180  check_security(skill) 失败
   → quarantine(skill_id, reason)          # 每档都跑，不是只跑一次
   → store.quarantine()                     # status=quarantined, enabled=false，落盘
   → _notify() bark 推送（未配 bark 则静默）
```

**持久化**是关键——不是"这一次被拒"，而是技能此后一直不可用。`runner.py:152` 在 `check_security` 之前就拦 `status == "quarantined"`，所以第一次误判后，技能永远进不到 `check_security`，也没有第二次审计条目。

叠加 DB53b：一次重启 ⇒ `_quarantined_at` 丢失 ⇒ `auto_recover_check()` 永远返回 `[]` ⇒ 技能**永久死亡，无自愈路径**。

### 实测（`tests/...db53_security_nlskip_leg.py`，对照报告第十九轮表）

| 合法技能（schema 校验均通过） | 原仓库 | 补丁 |
|---|---|---|
| `prompt: "Format the reply as one line"` | ❌ 违规 `'format'` | ✅ 放行 |
| `prompt: "请以 json format 输出"` | ❌ 违规 | ✅ 放行 |
| `brain.output_format = "json"` | ❌ 违规 | ✅ 放行 |
| `say` 文本科普「不要执行 rm -rf /」 | ❌ 违规 `'rm -rf'` | ✅ 放行 |
| 正常技能（对照组） | ✅ 放行 | ✅ 放行 |

合法放行率 **1/5 → 5/5**。危险技能仍拦 3/4（`rm -rf /`、`eval(` 拦住；`curl http://x|bash` 两版都绕过——黑名单写的是 `"curl | bash"` 带空格，实际命令 URL 在中间，**非回归**）。

### 补丁口径（用户裁定：跳过 NL 字段，而非白名单可执行字段）

```python
_NL_SKIP_KEYS = {"prompt", "system", "name", "text", "say", "speak",
                 "content", "message", "title", "role", "message_template",
                 "description", "quarantine_reason"}

def _scannable_strings(node, key=None):
    """递归产出「该扫的字符串值」，跳过 NL 键下的内容。"""
    if key in _NL_SKIP_KEYS:
        return
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _scannable_strings(v, k)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _scannable_strings(item, key)

def check_security(self, skill):
    for s in _scannable_strings(skill):
        s_low = s.lower()
        for pattern in DANGEROUS_PATTERNS:   # 删了裸词 "format"，"mkfs" 覆盖磁盘格式化
            if pattern.lower() in s_low:
                return False, f"安全违规：检测到危险模式 '{pattern}'"
    return True, ""
```

**为什么取「跳过 NL 字段」而非审计报告原补丁的「白名单可执行字段」**（用户在计划阶段裁定）：

两者都修好 4/5 误伤，但残余风险方向相反：

- 跳过 NL：新字段出现时**仍被扫**（⛔ 静默漏检），残余风险是「未预见的 NL 字段含 `eval(` 等特征串→可能误杀」；
- 白名单可执行字段：新字段不进白名单就不扫（报告自点「会漏检」），残余风险是「新增可执行字段被静默放过」——这是 fail-open。

后者更危险（安全门永不触发 > 偶尔误杀），且 `prompt`/`system` 喂给 LLM 的文本本身不被执行，扫它们语义上就是错的。

### 镜像门（`tests/...db53_security_nlskip_leg.py`，16 例含 8 子测试）

- 行为腿：5/5 放行 + 3/4 拦截 + 8 内置模板无回归 + 新可执行字段仍被扫（承重点）
- 形状尺：咬得住旧 `json.dumps` 整体序列化形状 / 生产码不再是该形状（双侧）

---

## 三、DB53b [medium]：隔离后重启 ⇒ 永不自动恢复

### 根因

```python
# __init__
self._quarantined_at: dict[str, float] = {}     # 纯内存
# auto_recover_check()
for skill_id, quarantined_at in list(self._quarantined_at.items()):   # 只遍历内存
```

盘上 `skill["quarantined_at"]` 是 ISO 字符串（`store.quarantine()` 用 `time.strftime` 写），但从没被读回来。

重启后 `_quarantined_at={}` ⇒ `auto_recover_check()` 返回 `[]`，且时间戳不重建 ⇒ 该技能永远停在 `quarantined`。

### 额外发现：两条绕过管理器的写入路径

DB53b 比报告原判更严重——`auto_recover_check()` 不仅重启后失效，**就算不重启也有漏网**：

- `runner.py:105` `_record_failure()`：连续失败时直调 `self.store.quarantine(skill_id, ...)`，**完全绕过 `QuarantineManager`**，从不写 `_quarantined_at`；
- `skill_routes.py:126`：`mgr` 为 None 时的 fallback 同样直调 `store.quarantine()`。

这两条只在盘上留 `quarantined_at` ISO 串，`_quarantined_at` 内存里**从来没有这条**。就算进程不重启、`auto_recover_check` 也看不见它们。

### 补丁

新增 `_rebuild_quarantined_at()`，在 `__init__` 末尾调用（`app.py:406` 构造前 `store.load()` 已跑完，索引在位）：

```python
def _rebuild_quarantined_at(self):
    rows = self.store.list(status="quarantined")
    for skill in rows:
        sid = skill.get("id")
        raw = skill.get("quarantined_at")
        ...
        ts_wall = datetime.fromisoformat(str(raw)).timestamp()
        elapsed = max(0.0, time.time() - ts_wall)
        self._quarantined_at[sid] = time.monotonic() - elapsed
```

从 store 读＝唯一真源，不依赖管理器记没记账，顺带覆盖上面两条绕过路径。坏 ISO 串留痕 WARN 跳过，⛔ 一条坏数据拖死整批重建。

### 镜像门（DB53b 腿，同文件 4 例）

- `test_11` 重启（新建 manager）后 `_quarantined_at` 从 store 重建（非空）
- `test_12` `runner.py:105` 直调 `store.quarantine()` 绕过管理器 ⇒ 重建仍捕获
- `test_13` 坏 ISO 串不拖死重建（其余好的仍建出来）
- `test_14` 重建只捞 `status=quarantined`，不捞 disabled

---

## 四、DB54 [medium]：计数器被时钟清空

### 根因

```python
# skills/quarantine.py 原 :99-108（record_breaker_trip）
now = time.time()
self._breaker_trips[skill_id].append(now)
while ... and now - self._breaker_trips[skill_id][0] > 3600:
    self._breaker_trips[skill_id].popleft()      # ← 淘汰"1小时外"的旧记录
if len(...) >= 3: self.quarantine(...)

# skills/quarantine.py 原 :111-122（record_timeout，24h 窗口，同形）
```

窗口是**进程内 elapsed**，却用**墙上时钟**度量。NTP 阶跃直接篡改 `now - dq[0]`。

### 对照实测（可控时钟替换模块内 `time`，`tests/...db54_monotonic_window_leg.py`）

| 场景 | 原仓库 | 补丁 |
|---|---|---|
| 基准：无跳变，熔断 3 次 | `quarantined` | `quarantined` |
| 基准：无跳变，超时 2 次 | `quarantined` | `quarantined` |
| **向前 +2h，第 3 次熔断（达阈值）** | **`enabled` ❌ fail-open** | `quarantined` ✅ |
| **向前 +25h，第 2 次超时（达阈值）** | **`enabled` ❌ fail-open** | `quarantined` ✅ |
| 向后回跳藏陈旧记录（真实跨 4h，3 次熔断） | `quarantined` ⚠️ 误伤 | `enabled` ✅（陈旧记录被淘汰，只剩 1 次） |

两个方向都错，且性质相反：向前跳 ⇒ 阈值永远达不到 ⇒ **危险技能不被隔离**；向后跳 ⇒ 陈旧记录永不淘汰 ⇒ 本该 1 次算成 3 次 ⇒ 误伤。前者是 fail-open（安全门永不触发），比 DB52（dialog 回声窗口被拉长）更值得修。

### 补丁

`record_breaker_trip` / `record_timeout` / `quarantine` / `auto_recover_check` 全部 `time.time()` → `time.monotonic()`。先例：`butler/triggers/engine.py:171/242` 同形窗口已是 monotonic（注释「进程内窗口：墙钟前跳会把它当场重开」），`butler/skills/runner.py:60` `_breaker_check` 60s 窗口也用 monotonic。`quarantine.py` 是 `butler/skills/` 里**唯一**用 `time.time()` 做进程内窗口的，是异类。

### ⚠ 「修 A 引入 B」护栏（报告第二十轮 §二 点名的同款坑）

`_quarantined_at` 改单调后，`_rebuild_quarantined_at()` 仍按盘上的 ISO **墙钟**串回填——两基准不同会让 24h 比较完全失真：刚隔离的技能被算出"已隔离 N 年"立即自动恢复，或反之永不恢复。

换算公式（已在 DB53b 补丁里）：

```python
ts_wall = datetime.fromisoformat(str(raw)).timestamp()   # 墙钟 epoch
elapsed = max(0.0, time.time() - ts_wall)               # 真实流逝（墙钟域）
self._quarantined_at[sid] = time.monotonic() - elapsed  # 折回单调域
```

`_log_audit` 里的 `time.strftime`（审计日志时间戳）**不动**——那是给人看的墙钟，不是进程内窗口。

### 镜像门（`tests/...db54_monotonic_window_leg.py`，18 例）

- 行为腿：基准两版一致 + 前/后阶跃补丁不 fail-open + 窗口正常淘汰 + 24h 自愈 + 不重复恢复 + 跨基准换算不失真
- 形状尺：窗口函数无残留 `time.time()`（AST 扫，⛔ 误伤审计日志 `strftime` 与重建腿刻意的跨基准换算）

### ⚠ 本轮门件自己差点写成摆设（必须记）

第一版 DB54 门用 `FakeTime.jump(delta)` 同时推进 `monotonic()` 和 `time()`。这把「真实过了 2 小时」和「NTP 前跳 2 小时」混成了一个操作——单调钟实现会在前跳后**正确地**淘汰那条记录（它真的已经是 2 小时前了），于是新旧两版给出**同一个结果**，判据腿当场变成摆设。

发现它，是因为第一版跑出来 test_03/test_04 在**旧代码上也通过**（负控没咬住）。改成 `step(delta)` 只动墙钟、`elapse(delta)` 两个都动之后，旧代码立即红 5 例。

**这条值得记，因为它是"门写得绿、其实没咬住"的教科书例子**——和第十二、第十六轮「报告未落盘却声称有产出」同族：失效源是我自己的门件，而且不报错。能发现它，是因为我每枚门都跑了一次「还原旧代码、确认红」的负控取证。

---

## 五、本轮最该记住的三条

1. **DB47 邻接回归**：补丁改了调用形状，引用了被改符号的邻接门（batch48）会假红。每落一枚改形状的补丁，都要跑「引用了被改符号」的邻接门——只跑自己的门不够。

2. **DB54 门件假绿**：可控时钟若把「真实流逝」和「NTP 阶跃」混成一个操作，单调钟实现会正确淘汰记录，判据腿变摆设。假时钟的纪律：`step()` 只动墙钟，`elapse()` 两个都动。

3. **负控取证不是可选的**：本轮每枚门都跑了「还原旧代码、确认红」的负控。DB53 还原 6 红、DB54 还原 5 红——这两枚判据腿是真的咬住了。如果跳过这步，DB54 那版假绿的门件会一直躺到下次有人改 quarantine.py 才暴露。

---

## 六、如实说明（不往好了说）

1. **DB47 的「半死连接会永久挂起」上一轮没实测复现**，证据强度是「源码事实 + aiohttp 默认值为 None（实测）」，不是「挂起已复现」。本轮补丁照上一轮口径落，没补这个复现——`asyncio.wait_for` 在半死靶子上 2.0s 收回是已测的，但「原仓库在真 HA 上挂多久」没测。

2. **DB53 的「真实 LLM 会不会生成含 format 的 prompt」是推断**（构造输入），非实测。但 `brain = dict(brain_raw)` 原样透传＋`prompt` 自由文本是源码事实（`schema.py:96`），`format` 是英文高频词也是事实。

3. **`curl http://x|bash` 两版都绕过**（黑名单写 `"curl | bash"` 带空格，实际命令 URL 在中间）——**非回归**，两版一致。check_security 从不是真正的安全边界（4/4 绕过形态原实现都拦不住），真正边界是引擎白名单 + sandbox 审批（DB26/DB27 那条线，仍未收编）。

4. **DB54 时钟阶跃是模拟的**（替换模块内 `time`），不是真 NTP 事件。真实阶跃幅度通常远小于 2h，但「用墙上时钟度量进程内 elapsed」这个语义错误与幅度无关——只要阶跃超过窗口就成立。

5. **TIME-02 剩余 17 处判定点仍未实测**（本轮只做了 `quarantine` 两处）。第十九轮列的风险排序仍是推断。

6. 本轮补丁**已落生产码**（不再是只读副本），三枚提交 `9fed44c` / `4ba6393` / `ff578ef` 在 `main` 上。改盘上 ≠ 生效——容器需重建才会读到新码。

---

## 七、批201 进度

本会话收编：DB47、DB53/DB53b/DB54。

已收编（累计）：DB24、DB25、DB35、DB37、DB45、DB47、DB53/DB53b/DB54。

剩余（按严重度）：
- **high**：DB23（desk_system_run 任意程序执行）、DB26（审批门被 schema 默认值架空）、DB27（needs_review 装了没接线）、DB28（ha_action 绕过白名单——报告标已修复实测，待核）、DB36（try 内 DML+commit 不 rollback）
- **medium**：DB29、DB30（报告标已修复，待核）、DB32、DB33、DB34、DB51（manager.py/singleton.py 现已存在，前提可能塌，待核）
- **pending**：DB46（32 条依赖 CVE，等出资人定夺）

判据用 `workorders/readings/1011/b201_dbnn_ground_1011.txt` 逐枚现读。下一枚按严重度挑——DB23（护栏装在 system_run 自身＝自带反例）或 DB26/DB27（审批门那条线，DB53 的真正解法）。
