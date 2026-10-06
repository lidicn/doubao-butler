# 外部执行器可行性探针 · OpenCode / DSH Desktop（2026-09-21）

> 座位：Q-ext-1（可行性探针，只做可行性，不替 PM 选型）
> 成文时钟：`date -u` = **2026-09-21T06:49Z**（本地 14:49 +0800）
> 本单性质：**只读盘上探测**。除 `--version` / `--help` 类只读参数外，**未启动任一程序的新实例、未投递任何任务、未安装/卸载/改配置/改注册表、未杀进程**。
> 全篇无口令值。凭据只以「键名 + 存在性」提及。

---

## 〇、一句话结论

**两个都能从命令行丢任务跑（CLI 已装在 PATH 上，且有官方 headless 一次性入口）；读回输出上 OpenCode 现在就能读（SQLite 只读已实测 103 会话），DSH 差一步（会话文件是 zstd 压缩、本机无解码器，且它的读回 HTTP 口要 `dsh web` 打印的带令牌 URL）。**
起步推荐 **方案① 先接 OpenCode**；DSH 留作②「国际版闲置档」的备选。

---

## 一、Q1：能不能从命令行把任务丢给它跑？

### OpenCode — 能（档 a，命令面完整可复跑）
- CLI **已在 PATH**：`which opencode` → `C:\Users\lidicn\AppData\Roaming\npm\opencode`。
- `opencode --version` → **`1.18.12`**（实得 1 行）。
- `opencode --help` 列出的一等命令（择要）：
  - `opencode run [message..]` —— **一次性、非交互跑一个任务**（这就是投递入口）。
  - `opencode serve` —— 起无头 HTTP 服务器；`opencode attach <url>` —— 挂到已在跑的服务器。
  - `opencode session` / `opencode export [sessionID]` —— 会话管理与导出（读回用，见 Q2）。
  - `opencode acp`（Agent Client Protocol 服务器）、`opencode mcp`、`opencode providers`、`opencode models`。
  - 全局参数：`--port`、`--hostname`（默认 `127.0.0.1`）。
- `opencode run --help` 实测关键旗标：`--dir <路径>`（在指定仓库里跑）、`--format json`（原始 JSON 事件，机器可解析）、`-m provider/model`、`--agent`、`--session/--continue`、`--attach <url>`（配 `-u/-p` basic auth）、`--auto`（自动放行权限，标红 dangerous）。
- **可复跑投递命令（我给形，未开火）**：
  ```
  opencode run --format json --dir "E:\NAS\<repo>" "任务正文"
  ```
  > ⚠️ 我**没有实际执行**这条（跑它=启动新实例+吃配额，越本单红线）。判据来自「CLI 在 PATH + 版本可读 + 子命令/旗标文档完整」，属档 (a)。首次真投需 PM 点头。

### DSH Desktop — 能（档 a，命令面完整可复跑）
- 装的是 Electron 壳（`DSH Desktop.exe`，`main: ./out/main/index.js`，无 `bin` 字段），**壳本身不是 CLI**；但它的内核 `@deepseek-ai/dsh` 是 Node CLI，且 **CLI 已在 PATH**：`which dsh` → `C:\Users\lidicn\AppData\Roaming\npm\dsh`。
- `dsh --version` → **`0.1.5-rc.1`**（实得 1 行）。
- `dsh --help` 择要：`dsh --profile <name> ...` 启动 `$DSH_HOME/profiles` 下的配置档；文档原样给了
  ```
  dsh --profile headless "run the tests"   # answer one task, print the result, and exit
  ```
  —— 即 **headless 一次性投递**正是官方用法。另有 `dsh --profile web`（起 Web/HTTP 应用）、`--dump-config` / `--dump-default-config`（打印配置树并退出，纯只读，**我也没跑**，避免越「只 --help/--version」线）。
- 盘上佐证 profile 真实存在：`~/.dsh/profiles/headless/` 目录在（`package.json` name=`dsh-profile-headless`，含 `cordis.yml`/`cordis.patch.yml`）。除 `headless` 外还有 `acp / default / web / minimal-test` 等档。
- **可复跑投递命令（我给形，未开火）**：
  ```
  dsh --profile headless "任务正文"
  ```
  > 同上：**未实际执行**（红线：不启新实例）。档 (a) 依据 = CLI 在 PATH + help 文档 + headless profile 盘上存在。

---

## 二、Q2：已有会话在跑，PM 能不能读它的输出？

**当前进程实况**（`tasklist`，`date -u`=06:45Z 那一次采样）：OpenCode.exe 6 个进程、DSH Desktop.exe 5 个进程在跑；DSH harness 是**独立 `node.exe`（pid 30260）**。

### OpenCode — 能读，且我现在就证明了（档 a）
- 会话/消息落在 SQLite：`~/.local/share/opencode/opencode.db`（约 **416 MB**，mtime `2026-09-21T06:21:44Z` = 本地 14:21，**今日仍在写**）。
- 用 SDK 自带 `sqlite3`（`%LOCALAPPDATA%\Android\Sdk\platform-tools\sqlite3.exe`）**只读**开（`file:...?mode=ro`，不建表不写）实测：
  - `.tables` → `session message part project credential account ... `（含 `credential`/`account` 表 —— 我**未查其内容**）。
  - `SELECT count(*)`：**sessions = 103、messages = 7,889**。
  - 最新 3 条 session 标题（含 `New session - 2026-09-20T17:55:33.215Z`）。
  - 另有 `~/.local/share/opencode/{log,plans,tool-output}` 目录，`tool-output/` 最新文件 mtime 本地 09-20 19:38。
- 现成读回 CLI：`opencode export <sessionID>`（导出会话为 JSON）、`opencode session`（管理）。
- 旁证：OpenCode 桌面自带内部 HTTP（pid 30560 → `127.0.0.1:10573`），一条无害 `GET /` → **401**（需 `OPENCODE_SERVER_PASSWORD`）。**这条只是附证**，读回不必走它 —— 直接只读 db 或 `opencode export` 即可。

### DSH — 读回差一步（档 b / 一处「未验-无通道」）
- 会话落在：`~/.dsh/sessions/<工作区键>/<会话 UUID>/session.v3.jsonl.zstd`（**zstd 压缩的 JSONL**）。
  - 工作区键按路径命名，实测有 `--E-NAS-DesktopHub--`、`--E-NAS-Desktop-agent--`、`--D-Documents-WorkSpace-dog--`、`--D-Documents-WorkSpace-Test--`、`--C-Users-...-feishu-task-agent-bin--` 等。
  - 最新一条 mtime → epoch `1789893267` = **`2026-09-20T08:34:27Z`**（本地 09-20 16:34）。
- **未验-无通道**：本机 PATH 上**没有 `zstd`/`unzstd`**（`which` 双双未命中），我**无法**当场解出一条会话证明读回 —— 缺「zstd 解码器」这一件，且本单禁装。
- 替代读回 = 走 harness 的本地 HTTP（`node.exe` pid 30260 在 `127.0.0.1:43129` LISTENING）。一条无害 `GET /` → **401**，正文：`dsh web authentication required; reopen the URL printed by dsh web.`
  - 即 **档 (b)**：能读，但**差一个只能他做的动作** —— 需 PM 跑一次 `dsh --profile web`（或桌面里开 web 模式），拿到它**打印的带令牌 URL**；我按红线**不去取、不试**那个令牌。
  - `GET /health` → 404（该服务无此路由）。
- 其它盘上物（仅列，未读内容）：`~/.dsh/` 有 `server.js`、`start-dsh.cmd`、`start-dsh.ps1`、`settings.yaml`、`task-board/{ledger-v2.json,scheduler-v2.json}`、`storages/`；`%APPDATA%\dsh-desktop\{harness,desktop-service,logs}`。

---

## 三、Q3：要让他能用，差哪一步？（三档判定）

| 程序 | 投递任务（Q1） | 读回输出（Q2） | 综合档 |
|---|---|---|---|
| **OpenCode** | **(a)** `opencode run --format json --dir <repo> "任务"`，命令面完整、CLI 在 PATH；**我未开火**（红线），首次真投待 PM 点头 | **(a)** 现在即可：SQLite `opencode.db` 只读实测 103 会话/7,889 消息；或 `opencode export <id>` | **(a)** |
| **DSH Desktop** | **(a)** `dsh --profile headless "任务"`，headless profile 盘上真实存在；**我未开火** | **(b)+未验**：HTTP 读回要 PM 开 `dsh web` 交出带令牌 URL；盘上会话是 zstd，本机无解码器 → **未验-无通道（缺 zstd）** | **(b)**（读回这一步只有他能补） |

**都不落到档 (c)**：两者 CLI 均已安装、均有官方 headless 入口、DSH 桌面壳为 MIT 开源（`github.com/dataelement/dsh-desktop`），无需再装新软件即可投递。

**差的具体动作清单**：
1. OpenCode：**无阻塞动作**。唯二由 PM 裁的量：投递时用哪个 `-m provider/model`（其 credential 表已配置，程序在活跃使用）；是否允许 `--auto` 自动放行权限（默认关，dangerous）。
2. DSH：**要闭环读回，只有他能做的一个动作 = 跑一次 `dsh --profile web` 并把它打印的带令牌 URL 交给 PM**（或另装 zstd 解码 —— 本单禁装）。不做这步，DSH 只能「投得出去、收不回话」，或收话要另找解码路子。

---

## 四、结述 + 下一步建议（PM 要的一句话）

- **OpenCode：档 (a)。** 一条 `opencode run --format json --dir <repo> "任务"` 就能投；用只读 `opencode.db` 或 `opencode export <id>` 就能收 —— **投、收都无需令牌周转**，且我已实测它的会话库今天还在写、可读。
- **DSH Desktop：档 (b)。** 投递 OK（`dsh --profile headless "任务"`），但**读回差他一个动作**（开 `dsh web` 交带令牌 URL，否则 zstd 会话文件本机解不开 = 未验-无通道）。
- **建议（选方案①还是②，一句）**：**方案① 起步接 OpenCode** —— 它是这里唯一「我这一侧就能把投递+读回闭环跑通、不把你拉进令牌/解码周转」的执行器；DSH 归到②「国际版闲置档」里作备选，等它那条 `dsh web` 令牌 URL 动作有主再上。

> 免责/边界：上表「可复跑命令」都是我**给形未开火**（本单红线：不启新实例）。真投一次会消耗各自账号配额，且 OpenCode 的 `--auto`、DSH 的令牌交接属你/PM 的动作，不由我代做。
