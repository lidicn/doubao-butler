# homesdk http/auth 接入：开工前的新旧并行对比（2026-10-01）

## 0. 口径

- 上位指令：`decisions/20261001-DB目标模式与AF三题-裁定.md` :68（议题 4 的 O-4 提前项）。原文关键三句：
  **「`homesdk http/auth 接入`现在就做」**、**「DB 是 homesdk 首个真实消费方」**、**「http/auth 是现成能力，接入成本低」**。
- 本窗边界：⛔ 动容器、⛔ `up -d`、⛔ 装包、⛔ 改 `vendor/`、⛔ 改 homesdk 仓本体。全部动作＝只读现量 + 把尺子与闸落进本仓。
- 读数件：`/tmp/homesdk_peer_probe.json`（14,485 B，md5 `de91fcc51a829f87b74a128921656f6c`），由
  `scripts/homesdk_peer_probe.py`（md5 `1aa8150fe1a2a28a6ff807d012e6ce9f`）当场产出；
  这把尺改过两版（v2 的表读数作废，见 §6），v3→v4 之间只把裸 `open()` 换成 `with`，
  **重跑产出的 JSON md5 同号 `de91fcc5…`** ⇒ 那批读数不是手抄的、可复跑。
  SDK 侧用的是投递件 wheel `homesdk-0.3.0`（sha256 `8863c641…324c`，解包在 `/tmp/hs030`，只读、未安装）。
- 本文里每个数字都来自一次已跑命令的原样输出；行号来自 `grep -n`。

## 1. 一句话结论：那句前提被切成两半

**「http/auth 是现成能力」对投递到 NAS 的 0.3.0 wheel 成立；对本仓镜像实际安装的那份不成立。**

- `Dockerfile:24-25`：`COPY vendor/homesdk /tmp/homesdk` + `RUN pip install --no-cache-dir /tmp/homesdk` ⇒ **镜像里的 homesdk 来自 `vendor/homesdk/`**；
- `vendor/homesdk/src/homesdk/` 现量只有 `consent.py`、`__init__.py`、`gates/`（`pyproject.toml:7` 版本 `0.1.1`）——
  **`http.py` 与 `auth.py` 一字没有**（`find` 现读，不是我推的）；
- 现网容器里跑的也是 0.1.1，`import homesdk` 后可用子模块只有 `['consent', 'gates']`。

⇒ 「接入」的第一步不是写调用点，而是**换构建输入**（vendor 升 0.3.0，或改成 vendored wheel + 一行 Dockerfile）。
按红线区口径（构建输入／供应链形态）这一件必须过 DCD，卡片已投：`关键决策部/inbox/` 的
`20261001-DB-homesdk-vendor形态-决策申请.md`（5,731 B，md5 `03cdaf1b06a89904eb04809623835123`）。本文负责把**裁完之后要动什么**先量清楚，让那一窗只剩执行。

## 2. 键名分区：DB 这批键，SDK 那条链认不认

DB 侧键名来自 `butler/config.py` 的 AST 扫描（65 枚 `_env("KEY", …)`）∪ 盘上 `.env` 的键名集合（37 枚），
并集 67 枚；SDK 侧命中由**哨兵探链**跑出来（一次一枚键，看 `peer_url`/`token_for`/`_basic_credentials` 取不取）。
下表 `sdk 链上的 DB 键` ＝交集，⛔ 我挑的清单。

### 2.1 URL 链（`PEER_<N>_URL` → `<N>_URL` → `<N>_BASE_URL`，各 ± `HOMESDK_`）

| peer | DB 在用的 URL 键 | `.env` 覆盖 | sdk 链上的 DB 键 | 接入结论 |
|------|-----------------|------------|-----------------|---------|
| butler | `BUTLER_BASE_URL`(`:211`)、`TASK_API_BASE_URL`(`:228`) | 否 / 否 | `BUTLER_BASE_URL` | DB 自指有两个键，链只认其一 |
| tv | `TV_HTTP_URL`(`:222`) | 否 | **无** | 要加别名 |
| tvpilot | `TVPILOT_HTTP_URL`(`:224`) | 否 | **无** | 要加别名 |
| deskpilot | `DESKPILOT_HTTP_URL`(`:225`) | 否 | **无** | 要加别名 |
| face | 无独立键（借 `TV_HTTP_URL`） | — | **无** | DB 侧根本没有这一格 |
| go2rtc | `GO2RTC_BASE_URL`(`:256`) | 是 | `GO2RTC_BASE_URL` | 零改动 |
| memory-agent | `MEMORY_AGENT_URL`(`:245`)、`MEMORY_AGENT_MCP_URL`(`:246`) | 是 / 是 | `MEMORY_AGENT_URL` | 零改动（MCP 那枚不在链形制里） |
| ha | `HA_URL`(`:259`) | 是 | `HA_URL` | 零改动 |

另：`PEER_*` 这种 SDK 规范形在 DB 侧**一枚都没有**（现量 0）⇒ 接入必然要动 compose，⛔ 白嫖现有键名。

### 2.2 token 链（`TOKEN_<N>` + `LEGACY_TOKEN_KEYS` 表）

| peer | sdk 链上的 DB 键 | DB 有、链不认的键 |
|------|-----------------|------------------|
| butler | **`BUTLER_WEB_PASSWORD`** | `TASK_REPORT_TOKEN` |
| memory-agent | `MEMORY_AGENT_TOKEN`、`MEMORY_AGENT_APP_TOKEN` | `MEMORY_AGENT_BUTLER_TOKEN` |
| tv / tvpilot / face | 无 | DB 侧无这些 peer 的 token 键 |
| deskpilot | 无（链认 `DESKPILOT_TOKEN`） | **`DESKPILOT_API_TOKEN`** |
| go2rtc | 无（链认 `GO2RTC_TOKEN`） | — |
| ha | 无（链认 `TOKEN_HA`） | **`HA_TOKEN`** |

链形制之外，SDK 的 `LEGACY_TOKEN_KEYS` 现量（探链跑完后再抽一次逐字节对账，同号）：
`butler: BUTLER_TOKEN, BUTLER_WEB_PASSWORD`／`memory-agent: MEMORY_AGENT_TOKEN, MEMORY_AGENT_APP_TOKEN`／
`tvpilot: TVPILOT_ADMIN_TOKEN, TVPILOT_DEVICE_TOKEN`／`autoflow: AUTOFLOW_TOKEN`／`deskpilot: DESKPILOT_TOKEN`／`go2rtc: GO2RTC_TOKEN`。

⇒ 那句「旧仓不改 compose 就能接上」（`auth.py:33-34` 的注释）对**本仓不成立**：
DB 的四枚凭据键（`DESKPILOT_API_TOKEN`/`HA_TOKEN`/`MEMORY_AGENT_BUTLER_TOKEN`/`TASK_REPORT_TOKEN`）链上一枚都不认，
接入时要么起别名、要么改调用点。`MEMORY_AGENT_BUTLER_TOKEN` 那枚正是 O-3（任务 #22）四路 token 里的第三路，两件事在同一个接缝上。

### 2.3 basic 链（`USER_<N>` / `PASS_<N>` / `PASSWORD_<N>` + 两张 legacy 表）

| peer | 链上的 DB 键 |
|------|-------------|
| go2rtc | `GO2RTC_USER`、`GO2RTC_PASS` |
| memory-agent | `MEMORY_AGENT_USER`、`MEMORY_AGENT_PASS` |
| 其余 6 个 peer | 无 |

`_basic_credentials` 缺一侧即返回 `None` ⇒ 这一格必须**两枚一起探**（v2 只放一枚，结果恒 `None`，
把「链上不认」读成了假读数，v3 改成 user×pass 两枚一组）。

## 3. P1 危险项（接入窗不许绕过）

`sdk 链上的 DB 键` 那一栏里，butler 命中的是 `BUTLER_WEB_PASSWORD`——**网页登录口令**。
`auth.py` 的宽松入口 `get_auth_headers("butler")` 会把它拼成 `Authorization: Bearer <登录口令>` 发出去
（探链现量：命中的三枚键 `BUTLER_TOKEN`/`BUTLER_WEB_PASSWORD`/`TOKEN_BUTLER` 全部走 `Authorization` + `Bearer ` 形状，无一例外）。

- DB 这一侧 `.env` 里 `BUTLER_WEB_PASSWORD` **有值**（`both_present=True`）；
- 成对键同值检测（只出布尔与长度，⛔ 值）：`BUTLER_WEB_USER|BUTLER_WEB_PASSWORD` `same_value=False` len=12/20；
  `GO2RTC_USER|GO2RTC_PASS` `same_value=False` len=6/11 ⇒ 不是「口令==用户名」那一型，但**口令会当 token 上 wire** 是另一型，同样不能要。

⇒ 接入时 butler 这一格只能走 `BUTLER_TOKEN`/`TOKEN_BUTLER`，且必须先把 DB 自指调用的真凭据（`TASK_REPORT_TOKEN`）
在 SDK 链上落一个键名；`require_auth_headers` 比 `get_auth_headers` 合适（缺凭据即抛，⛔ 空 dict 往下走）。
这条已写成测试闸：`tests/test_homesdk_vendor_gate.py::test_butler_web_password_sits_on_the_bearer_chain`——
它同时要求本文存在，散文中说过不算落盘。

## 4. DB 的内网地图 vs 库的「库内零私网地址」

`http.py` 的设计不变式是**库不持有任何 host/端口/IP 字面量**，解析不到就抛 `PeerUnknown`。
DB 现在**反过来**：`butler/config.py` 里 15 枚 `*_URL` / `*_BASE_URL` 键**全部带写死的默认值**，
其中 **7 枚 `.env` 没覆盖** ⇒ 现网这 7 条边是靠代码里那张内网地图在跑：

`AUTOFORGE_BASE_URL`(`:252`)、`BUTLER_BASE_URL`(`:211`)、`DESKPILOT_HTTP_URL`(`:225`)、`TASK_API_BASE_URL`(`:228`)、
`TTS_KOKORO_URL`(`:272`)、`TVPILOT_HTTP_URL`(`:224`)、`TV_HTTP_URL`(`:222`)。

⇒ 接进 SDK 之后，这批键的解析结果会从「静默用默认值」变成**抛 `PeerUnknown`**。这是行为变化，不是修 bug 的副作用：
compose/.env 必须同窗补齐这 7 枚（或补别名），且⛔ 用「try 住继续跑」把 fail-closed 又拆回 fail-open。
名单已钉进测试（`test_hardcoded_default_url_keys_without_env_override`），改动会翻红，不许无声继承。

## 5. 省下来的一条（我原本担心的接入成本，现量不成立）

`auth.py` 的注释说 TVPilot 认 `X-Admin-Token`、DeskPilot 认 `X-API-Token`、desktop_agent 认 `X-Agent-Token`，
所以我担心接 `get_auth_headers()` 会把 Bearer 发到只认自定义头的端点。**对本仓现量不成立**：
`grep -rn "Authorization" butler/ --include=*.py` = **28 处 / 20 个文件**，
自定义头 `X-*-Token` = **0 处**（`grep -o "X-[A-Za-z-]*"` 现量空）。
⇒ DB 的鉴权形状与 SDK 默认（`Authorization: Bearer`）同形，头名这一格不构成接入成本。
第三方文档里的断言先量再采信，这次量的是我自己这一侧。

## 6. fail-closed 口径实测 + 我这把尺的两次自纠

探链跑完，每个 peer 都留了三格自证（现量，全部符合）：

- 空环境下 `peer_url(peer)` ⇒ `PeerUnknown`（8/8）；
- 空环境下 `require_auth_headers(peer)` ⇒ `AuthMissing`（8/8）；
- 空环境下 `get_auth_headers(peer)` ⇒ `{}`（8/8）；
- 正对照：`PEER_<N>_URL` 每个 peer 都被取到（8/8）——**这一条是防止我把「我形制猜错」报成「SDK 不认」**；
- 表自证：legacy 三张表在探链跑完后**再抽一次**，逐字节对账同号。

两把刀都落在我自己身上，记进台账：

1. **`+=` 把源表改大了**。v2 从 AST 抽表时返回 list，`legacy += tables[key]` 对 butler/memory-agent 两个 peer
   恰好命中同一个键（`.replace("_","-")` 把 `MEMORY_AGENT` 变回 `memory-agent`）⇒ `x += x` **原地**把那枚 list 翻倍，
   每探一次翻一倍：printed 出来 `butler` 8 条、`memory-agent` 16 条，而源码是 2 条。
   那一版的表读数作废，v3 一律 `tuple` + 跑完再抽一次对账。SDK 自己没有这个坑（它写的是 `tuple(...)`）。
2. **手挑属性名会落成 None**。v2 的 token 段用我猜的属性名（`butler_web_password` 等）去 `config.py` 找键，
   两枚猜错落成 `db_key=None`，看起来像「DB 没有这枚键」。v3 改成**从 AST 全量取键名再算交集**，⛔ 手挑清单——
   与「安全覆盖面判据从权威源枚举」同族，这次是我自己犯。
3. 顺带：`ResourceWarning: unclosed file` 来自我这把尺（`open()` 没用 `with`）。同一形状我在 `engine.py:81`
   登记过没动，自己的新脚本里就不留了。

## 7. 接入清单（裁完之后要动的，逐条可执行）

1. **vendor 形态**（DCD 卡）：A 源树升 0.3.0（NAS 上只有 wheel，源码树不在 ⇒ 我要重建 `src/` 布局，等于我产库内容，
   且 3 枚 `gates/` 文件与 0.1.1 不同）／B vendored wheel + `Dockerfile` 一行改（原件 sha256 已登记，provenance 干净，
   代价＝二进制不可读、不能热修）／C 本窗不接、等 homesdk 仓投源码树。
2. **compose/.env 补 7 枚**（§4 名单）+ **别名 4 枚**（`TV_HTTP_URL`/`TVPILOT_HTTP_URL`/`DESKPILOT_HTTP_URL`/`TASK_API_BASE_URL`
   到 SDK 形制的映射）＋凭据 4 枚（§2.2 右列）。
3. **先解 P1**（§3）再谈 butler 这一格上链。
4. **调用点**：`butler/integrations/{deskpilot,ha,memory_agent}.py`、`butler/tools/desk_pilot.py`、
   `butler/api/task_routes.py` 等 20 个文件里选真的出站边；`core/dialog.py` 的 consent 用法不动。
5. **验收**：`scripts/homesdk_peer_probe.py` 重跑（分区必须只朝「更多键上链」方向变）、
   `tests/test_homesdk_vendor_gate.py` 全绿（A 组半接线闸届时应转成「vendor 已含 http/auth 且 DB 已接线」）、
   现网 4 连采每条边（点刻值 15 分钟作废）。

## 8. 状态

- 只读现量 + 尺子 + 开工前闸：✅ 落盘（本文、`scripts/homesdk_peer_probe.py`、`tests/test_homesdk_vendor_gate.py` 9 例）
- vendor 决策：⛔ 等 DCD 裁（卡片已投 inbox，`已落盘-未送`，bark 归 PM 座位）
- 运行时腿（`import homesdk.http` 之后真发一条出站请求）：⏳ **未生效-待窗**——本窗没动容器，且现在装的是 0.1.1，
  那条 import 在现网镜像里必然失败（这正是 §1 要裁的东西）
- 下一张：裁完之后按 §7 顺序走；在那之前 O-4 只剩「等」。
