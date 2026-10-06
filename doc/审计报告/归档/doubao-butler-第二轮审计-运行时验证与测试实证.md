# doubao-butler 第二轮审计：运行时验证与测试实证

> 审计对象：`lidicn/doubao-butler`（main, `836df79`）
> 本轮定位：**把第一轮 24 条静态结论拿到运行时验证**，并用项目自带的 45 个测试文件做实证交叉检验
> 核心手段：真实安装依赖 → 完整跑通测试套件 → 真实启动应用 → 动态复现缺陷
> 与第一轮的关系：第一轮"读代码猜"，第二轮"跑起来验"。**凡两轮结论冲突，以本轮运行时证据为准**

---

## 0. 本轮最重要的三句话

1. **第一轮 24 条结论中，22 条方向正确，1 条判断错误（P1-9 登录限流，实际已实现），1 条需降级（P0-3 从"必然 500"细化为"仅配置损坏时触发"）。**
2. **测试套件真实状态是 17 failed / 576 passed** —— 其中只有 2 个是真实产品缺陷，其余 15 个是测试基础设施与环境问题。这个比例说明**测试资产本身在腐烂**，比产品代码更值得优先修复。
3. **本轮新发现 4 条第一轮完全没看到的缺陷**，其中 1 条属 P1：应用在 `TTS_DIR` 不存在时**导入即崩溃**，且崩溃信息不指向"缺目录"。

---

## 1. Skills 寻找、安装与使用

按要求在 GitHub 检索并安装了 2 个适合本项目（Python 3.11 + Starlette + asyncio + MQTT + 家庭 IoT）的审计 skill：

| Skill | 来源 | 安装位置 | 本轮实际用途 |
|---|---|---|---|
| **python-doctor** | GitHub 社区 Python 诊断 skill | `/data/workspace/skills/python-doctor/` | 提供 100 分起扣的健康分模型（Critical −10 / High −7 / Medium −5 / Low −3）、分级阈值 A–F、secret 只报元数据原则 |
| **grade-python-project** | GitHub 社区 Python 项目评分 skill | `/data/workspace/skills/grade-python-project/` | 提供结构化评分维度与 evidence-based review 方法论（附 `/data/workspace/skills/evidence-based-review.md`） |

**使用方式与偏差说明（如实记录）**：

- 两个 skill 的健康分/评分模型被用于**第 6 节的健康分计算**与**缺陷定级标准**；
- `python-doctor` 的 `rules/` 规则目录在上游仓库**实际不存在**（SKILL.md 引用了但未提供），故按其内嵌规则 ID 目录自行构造了检测模式；**该项列为 Not Evaluated**，未假装执行；
- 二者的静态规则与第一轮的 ruff 扫描高度重叠，因此本轮的价值**不在于再跑一遍静态规则，而在于运行时验证**——这正是它们做不到的部分。

---

## 2. 测试套件实证：17 failed / 576 passed

完整跑通后的真实结果：

```
17 failed, 576 passed, 10 skipped, 1 xfailed, 7 subtests passed
```

**逐条归因（这是本轮最有价值的产出）**：

| 类别 | 数量 | 性质 |
|---|---|---|
| 真实产品缺陷 | **2** | deskpilot 不校验 HTTP 状态码 |
| 架构/安全缺陷 | **1** | 硬编码内网 URL 名单过期 |
| 测试基础设施腐烂 | **2** | v25_pytest_shim 阻断名单不咬合 |
| 测试自身脆弱 | **1** | mcp_server_contract 路径断言误报 |
| 环境/配置缺失 | **11** | 无 pytest 配置（10）+ homesdk 未安装（1） |

### 2.1 真实缺陷 A（P1）：DeskPilot / TVPilot 完全不校验 HTTP 状态码

**位置**：`butler/integrations/deskpilot.py`（`_get`/`_post`/`health`）、`butler/integrations/tvpilot.py:38/43/49/55/75`（**同构同缺陷**）

运行时实测：当远端返回 HTTP 403（错误体 JSON）时——

- `health()` 返回 `{"ok": True, "result": {403 错误体}}` → **网关返回错误，管家却判定对端健康**；
- `system_status()` 返回一个**没有 `ok` 键**的字典 → 直接违反项目自定的 `{ok, tool, result, cost_ms, error}` 响应包络契约。

根因是一行式的 `return r.json()`，把"HTTP 层失败"当成了"业务层成功"：

```python
# 现状
async def _get(self, path):
    r = await self.client.get(...)
    return r.json()          # ← 403/500 的 body 也被当成正常结果
```

**修复**（两处同改，并统一包络）：

```python
async def _get(self, path):
    r = await self.client.get(...)
    if r.status_code >= 400:
        raise UpstreamHTTPError(f"{path} -> HTTP {r.status_code}: {r.text[:200]}")
    return r.json()

async def health(self):
    try:
        data = await self._get("/health")
    except Exception as e:
        return {"ok": False, "tool": "deskpilot_health", "error": str(e), "cost_ms": 0}
    return {"ok": True, "tool": "deskpilot_health", "result": data, "cost_ms": 0}
```

**影响面**：全仓 `return r.json()` 共 **12 处**，均需按同一标准过一遍；全仓已有 `status_code` 检查 38 处，说明团队知道该做，只是这 12 处漏了。

### 2.2 真实缺陷 B（ARCH/SEC P1）：硬编码内网地址 + 门禁名单过期

`config.py` 硬编码了 **15 个 `192.168.2.x` 内网 URL 默认值**，全仓内网地址引用共 **43 处**。公开仓库等同于公开家庭内网拓扑（HA、go2rtc 摄像头流、Bark、memory-agent、doubao2api 的端口与位置一应俱全）。

`test_homesdk_vendor_gate` 失败正是在报这件事——固定名单 `PINNED_DEFAULT_ONLY_URL_KEYS` 只登记了 6 个，实际有 15 个，**漏登 8 个**：`BARK_URL`、`DOUBAO_API_URL`、`DOUBAO_BASE_URL`、`GO2RTC_BASE_URL`、`HA_URL`、`MEMORY_AGENT_MCP_URL`、`MEMORY_AGENT_URL`、`NEW_API_URL`。

**这里有值得肯定的地方**：`.env.example` 已把所有真实凭证替换为 `REDACTED-FOR-PUBLIC-SNAPSHOT`，`HA_TOKEN=`、`BARK_KEY=` 置空——**凭据层面脱敏是干净彻底的**。泄的是拓扑，不是密钥。

**修复**：15 个 URL 默认值全部改为空串，改为启动期必填校验（沿用现有 `config.py` 的必填校验机制即可，无需新写代码）；同步更新门禁名单。

### 2.3 环境缺陷：项目没有任何 pytest 配置（10 个失败的根因）

`memory_recall` 8 个 + `proactive_budget` 2 个全部报 `async def functions are not natively supported`。

根因：项目**没有** `pytest.ini` / `pyproject.toml` / `setup.cfg` / `conftest.py`，async 测试在全新 checkout 下无 `asyncio_mode` 配置。

**验证**：加 `--asyncio-mode=auto` 后 **26 passed, 1 failed**（唯一失败是 homesdk 未安装）。

**结论：这 10 个失败 100% 是配置缺失，产品代码无问题。**

**修复**（零风险，5 行）：

```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

### 2.4 其余：homesdk 未安装（1）、测试脆弱（1）、基础设施腐烂（2）

- **homesdk**：vendored 在 `vendor/homesdk/src`，`pip install ./vendor/homesdk` 未生效，需 `PYTHONPATH=vendor/homesdk/src`。**生产 Dockerfile 已 `pip install /tmp/homesdk`，无碍**，纯本地环境缺陷。
- **`test_mcp_server_contract`**：断言模块路径不含 `"data/"`，在 `/data/workspace/...` 下必然误报。属测试自身脆弱，应改断言逻辑（用相对路径或仓库根判定）。
- **`test_v25_pytest_shim` 2 个**：环境阻断名单与在册名单不咬合，过期基础设施，建议直接删除该 shim。

---

## 3. 运行时验证：第一轮结论的复核

### 3.1 ✅ P0-3 已动态复现（从静态推测升级为实证）

构造损坏的 `config.json` 后实际执行：

```
RAISED -> NameError name 'logger' is not defined
```

**确认**：`butler/api/config_routes.py:23` 的 `except` 分支引用了从未定义的 `logger`。原设计意图是"记日志 + 返回空配置降级"，实际是**在降级路径里再抛一个异常**，把可恢复故障放大为全站 500。

**细化**：触发条件是"config.json 存在但解析失败"（断电写半、手工编辑错、磁盘满），不是任何配置读取都会触发。这一点比第一轮表述更精确。

### 3.2 ✅ 应用可正常启动（第一轮无此结论）

补齐必填环境变量后：

```
IMPORT OK
/api/health    -> 200 {"ok":true,"data":{"online":true}}
/api/login/status -> 200 {"ok":true,"data":{"auth_required":true,"authed":false}}
```

**这是重要的正面结论**：应用在有完整配置时能正常装配、health 返回 200、鉴权正确启用。第一轮的担忧（"核心链路是否根本跑不起来"）**被证伪**。

### 3.3 🆕 新发现（P1）：`TTS_DIR` 不存在时应用导入即崩溃

首次尝试启动时实际报错：

```
RuntimeError: Directory '/app/tts' does not exist
    at butler/app.py:242  Mount("/tts", app=StaticFiles(directory=s.tts_dir, ...))
```

**问题**：`_build_app()` 在**模块导入期**（`app.py:929` 的 `app = create_app()`）无条件挂 `StaticFiles`。若 `tts_dir` 指向不存在的目录 → `RuntimeError` → **整个进程起不来**，且报错信息是 `Directory does not exist`，不提示"请创建目录"。

对比：代码里对 `data_dir` 到处写着 `mkdir(parents=True, exist_ok=True)`，**唯独 `tts_dir` 没有**。不一致。

**触发场景**（都不是臆想）：改了 `TTS_DIR` 环境变量指到新挂载点、从备份恢复时目录结构不全、本地开发/CI 环境、外部挂载卷晚于容器启动就绪。

**修复**（防御性，2 行）：

```python
Path(s.tts_dir).mkdir(parents=True, exist_ok=True)   # ★ 与 data_dir 一致
Mount("/tts", app=StaticFiles(directory=s.tts_dir, html=False), name="tts"),
```

### 3.4 ⚠️ 更正：第一轮 P1-9"登录无速率限制"**判断错误**

第一轮我写"`_login` 无失败计数/退避/锁定"。**运行时核查证明这是错的**：

```python
# butler/api/deps.py:140-141
_LOGIN_MAX_FAIL = 5
_LOGIN_LOCK_SECONDS = 15 * 60      # 15 分钟
```

`is_login_locked()` 与 `login()` 均已实现 5 次失败锁定 15 分钟，且登录比较用 `hmac.compare_digest`（常数时间，防时序侧信道）。**此项予以撤回**，第一轮的建议作废。这正是运行时验证的价值——静态阅读时我漏看了这段。

---

## 4. 安全审查（用户指定）

| 检查项 | 结论 | 证据 |
|---|---|---|
| **密钥管理** | ✅ 良好 | 缺 `DOUBAO_API_KEY`/`DESKPILOT_API_TOKEN`/`TASK_REPORT_TOKEN`/`BUTLER_WEB_PASSWORD` **启动即抛 RuntimeError**（fail-fast），不静默降级 |
| **空口令防护** | ✅ 良好 | `web_password` 为空串时**拒绝启动**（`config.py:317-318`），非 fail-open |
| **默认鉴权** | ✅ 良好 | `auth_enabled()` 要求 `web_user` 非空；`allow_no_auth` 默认 `False`，未配置用户时**默认拒绝**而非放行 |
| **口令 Bearer 兼容** | ✅ 可控 | `ALLOW_PASSWORD_AS_BEARER` 默认 **false**（旧客户端兼容旁路默认关闭），且每次命中都记账 |
| **登录限流** | ✅ 已实现 | 5 次失败锁定 15 分钟 + `hmac.compare_digest` |
| **命令注入** | ✅ 无风险 | 全仓唯一 `shell=True` 出现在 `quarantine.py:43` 的**隔离黑名单字符串里**，非真实执行；`docker_tools` 全程用**列表参数** + 容器白名单 `_ALLOWED_CONTAINERS` |
| **Docker 权限** | ✅ 收敛 | compose 走 `tecnativa/docker-socket-proxy`，只放行容器列表/日志/重启，禁止 create/build/exec/delete |
| **路径穿越** | ✅ 无风险 | `_avatar` 用 `^[A-Za-z0-9_\-]+$` 白名单校验 |
| **日志脱敏** | ⚠️ 有瑕疵 | `MaskingFilter`/`MaskingFormatter` 覆盖 password/token/secret/cookie/authorization 等 17 类键；但 `_SENSITIVE_KEYS` 含**裸 `key`**，会把 `bark_key`、`api_key` 之外的合法键（如 `sort_key`、`dedupe_key`）一并脱成 `***`，**过度脱敏会损害排障能力** |
| **内网拓扑泄露** | ❌ 需修 | 15 个硬编码 `192.168.2.x` 默认值（见 2.2） |

**安全面总体评价：显著好于同类家用项目。** fail-fast 密钥校验、拒绝空口令、默认拒绝鉴权、docker socket 代理收敛——这四点是很多自部署项目做不到的。主要缺口是拓扑泄露与脱敏过宽。

---

## 5. 本轮新增/修订缺陷清单

| ID | 等级 | 缺陷 | 状态 |
|---|---|---|---|
| N-1 | **P1** | `TTS_DIR` 不存在 → 导入期崩溃 | 🆕 本轮新发现 |
| N-2 | **P1** | DeskPilot/TVPilot 不校验 HTTP 状态码，403 误报健康 | 🆕 本轮新发现 |
| N-3 | **P1** | 15 个硬编码内网 URL + 门禁名单过期 8 个 | 🆕 本轮新发现 |
| N-4 | P2 | 无 pytest 配置 → 10 个 async 测试在全新 checkout 下失败 | 🆕 本轮新发现 |
| N-5 | P2 | 日志脱敏含裸 `key` → 过度脱敏损害排障 | 🆕 本轮新发现 |
| N-6 | P2 | `test_mcp_server_contract` 路径断言在 `/data/` 下误报 | 🆕 本轮新发现 |
| N-7 | P2 | `test_v25_pytest_shim` 基础设施过期（2 个失败） | 🆕 本轮新发现 |
| R-3 | **P0** | config 损坏 → `NameError` → 全站 500 | ✅ 动态复现，结论成立 |
| R-9 | ~~P1~~ | 登录无限流 | ❌ **撤回**，实际已实现 |

---

## 6. 健康分（按 python-doctor 模型）

起始 100 分，按 Critical −10 / High −7 / Medium −5 / Low −3 扣，每规则扣分上限为 `severity_points × min(count, 3)`：

| 维度 | 扣分 | 说明 |
|---|---|---|
| 导入期崩溃（N-1） | −7 | High，单点但影响启动 |
| 状态码未校验（N-2） | −7 | High，跨 2 个客户端 |
| 拓扑泄露（N-3） | −7 | High |
| config NameError（R-3） | −10 | Critical，放大故障且阻断自愈 |
| 第一轮存量 P0/P1（除已撤回项） | −21 | 按上限收敛后计入 |
| 测试基础设施腐烂（N-4/N-6/N-7） | −5 | Medium |
| 脱敏过宽（N-5） | −3 | Low |

**Health Score：100 − 60 = 40 / 100 → 等级 F（<60）**

**读这个分数的正确方式**：F 不代表"项目很差"。本模型对**故障放大类缺陷**（降级路径里再抛异常、健康检查误报健康、静默吞异常）惩罚极重，而这恰是本项目的系统性特征——**它的功能覆盖面异常完整**（技能引擎、多角色、定位融合、时序异常、自进化、iLink、 audiobook 一应俱全），**但每一层的兜底都在掩盖下一层的故障**。

**Top 3 改进建议**（投入产出比排序）：

1. **先修测试基础设施（约 1 小时）** —— 加 5 行 pytest 配置即可让 10 个"假失败"转绿，从此测试套件才具备回归守护能力。**在这之前修产品代码，你无法验证修复是否生效。**
2. **再修两条 P1 启动/探活缺陷（约 30 分钟）** —— `TTS_DIR` 补 `mkdir`、DeskPilot 补状态码校验。两者都是个位数行改动，直接消除"起不来"和"假健康"两类最恶劣的故障形态。
3. **最后做第一轮 P0-1/P0-3（约 20 分钟）** —— 技能确认 `role` 未绑定（1 行）、config `logger` 未定义（5 行）。**这两个是"核心功能静默失效"，用户每天都在遇到但永远不会报障。**

---

## 7. 诚实的边界声明

本轮未能完成或未能验证的部分，如实列出：

- **`python-doctor` 的 `rules/` 目录在上游不存在**，其规则集未真正执行，健康分的静态维度可能低估；
- **沙箱出站 HTTP 被代理策略全面拦截**（`policy_default_denied`），所有对外部服务的真实调用无法触达。这反而**意外暴露了 N-2**（403 被当健康），但同时也意味着**依赖真实 HA / MQTT / 小爱链路的功能未经验证**；
- **完整 lifespan 启动未执行**（真实 MQTT 连接、16 个定时任务、presence 轮询等）。`/api/health` 的 200 是在**未触发 lifespan** 的路径下取得的，故"应用能启动"这一结论**仅覆盖装配阶段，不覆盖运行期**；
- 沙箱 Python 为 **3.10**，项目要求 **3.11**，`match` 语句与部分 3.11 特性下的行为差异未覆盖；
- `api/skill_routes.py`(1061 行)、`api/doubao_webhook.py`(849)、`core/cron_task.py`(727) 的业务语义正确性**仍未审计**，本轮只覆盖了其结构性风险。
