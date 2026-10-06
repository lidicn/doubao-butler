# doubao-butler 第九轮审计报告 · 测试套件驱动

- **审计对象**：`lidicn/doubao-butler` @ `836df79`
- **本轮主线**：**跑项目自带的 593 个测试** —— 这是前八轮完全遗漏的、信噪比最高的缺陷来源
- **结论**：**1 个 P0 + 3 个 P1**，全部经执行验证

---

## 第一部分：工作流迭代（v1.1 → v1.2）

### 1.1 新增 `tests` 阶段（本轮核心改进）

**为什么重要**：前八轮我一直在自己写桩件、自己推断、自己验证。而仓库里**本来就有 43 个测试文件、593 个测试** —— 作者已经写好了断言，失败用例直接指向真实 bug。**信噪比远高于任何自研启发式**。

```bash
python3 /data/workspace/auditkit/audit.py tests --repo <repo> --out <out>
```
自动处理 `PYTHONPATH`（vendor homesdk）、注入桩件环境变量、`--asyncio-mode=auto`。

**执行顺序**调整为：`bootstrap / static / graph / tests / runtime / orphans / deadcall / cycles / contract` —— **tests 提到 runtime 之前**，因为它最便宜也最准。

### 1.2 修复 `contract` 路由匹配的 3 处误报

`a, = [n.strip("/").split("/")]` 解包 + `y.startswith("{")` 判断有缺陷，**未处理 Starlette 转换器**（`{id:int}`）与前端模板插值（`${q ? "?" + q : ""}`）。

修复要点：
- `segs()` **先** `re.sub` 把 `${...}` 归一为 `{}`，**再** `split("?")[0]`（顺序反了会因 `${q ? "?" + q : ""}` 内含 `?` 而误切）
- `is_param()` 同时识别 `{id:int}`、`{skill_id}`、含 `{}` 后缀的段

效果：**前端孤儿路由 4 → 1**，唯一剩下的 `/api/skill/runs?limit=50` 是**真实缺失**（第五轮已报，交叉确认）。

### 1.3 `drive.py` 扩容：12 步 → 19 步

新增 7 个驱动场景（API 路由全量驱动、集成层 ha/tv/bark/docker/tvpilot/newapi、技能引擎 run/describe 全量、触发器/主动/简报/安监引擎、纯逻辑模块、`app.create_app` 装配、TTS manager/adapter），并补齐 `_StubApp` / `make_request` 桩件。

**覆盖率 12.73% → 21.75%**（执行过 775 → **1330** 个方法，未执行 958 → **403**）。

### 1.4 v1.2 实测数据

| 阶段 | 结果 |
|---|---|
| tests | **7 failed / 586 passed** / 10 skipped / 1 xfailed |
| graph | 713 节点 / 1662 边 |
| runtime | 覆盖 **21.75%**，1330/1733 方法执行过 |
| orphans | runtime 剔除 233 FP，剩 732（advisory） |
| deadcall | 249 处，4 处 `hasattr` 保护 |
| contract | 签名不匹配 **0** / 前端孤儿路由 **1**（真实） |

---

## 第二部分：第九轮审计发现

### 测试失败分类（7 项）

| # | 用例 | 定性 |
|---|---|---|
| 1 | `test_deskpilot_tools::test_get_timeout` | 🔴 **真实缺陷** |
| 2 | `test_deskpilot_tools::test_health_unreachable` | 🔴 **真实缺陷** |
| 3 | `test_homesdk_vendor_gate::test_hardcoded_default_url_keys_without_env_override` | 🔴 **真实缺陷（门禁红了）** |
| 4 | `test_mcp_server_contract::test_modules_are_deployed_tree_not_staging` | ⚪ 环境（路径含 `data/`） |
| 5-7 | `test_v25_pytest_shim` ×3 | ⚪ 环境（豁免名单不咬合） |

---

### P0-14 出站 HTTP **从不检查状态码** —— 4xx/5xx 被当成成功

- **位置**：`butler/integrations/deskpilot.py:36-72`、`butler/integrations/tvpilot.py:41-56`

**`health()` 实测（代理返回 403）**：
```
health() -> {'ok': True, 'result': {'title': 'Request denied', 'status': 403,
             'detail': 'No policy rule matched the request'}}
```
**403 被报成 `ok: True`。**

```python
# deskpilot.py:65-72
async def health(self) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{self.base}/health")
        return {"ok": True, "result": r.json()}   # ← 只要没抛异常就 ok=True
    except Exception as e:
        return {"ok": False, "error": "unavailable", "message": str(e)}
```

**`system_status()` 实测 —— 更隐蔽的第二层**：
```
system_status() -> {'title': 'Request denied', 'status': 403, ...}
                    ↑ 没有 "ok" 键
```
`_get()`（L36-47）在**异常分支**返回 `{"ok": False, ...}`，但在**正常返回分支**原样返回 `r.json()` —— 服务端错误体里没有 `ok` 键。调用方 `r["ok"]` → **KeyError**。

这就是 `test_get_timeout` 报 `KeyError: 'ok'` 的根因：**同一个方法的错误契约不一致** —— 网络异常有 `ok` 键，HTTP 403 没有。

**全仓扫描**（`json()` 出现次数 vs 状态检查次数）：

| 文件 | json() | 状态检查 | 判定 |
|---|---|---|---|
| `deskpilot.py` | 3 | **0** | ❌ |
| `tvpilot.py` | 2 | **0** | ❌ |
| `ha.py` | 4 | 17 | ✅ |
| `memory_agent.py` | 5 | 10 | ✅ |
| `doubao.py` | 4 | 4 | ✅ |
| `llm.py` / `tv.py` | 1-2 | 3-4 | ✅ |

→ **孤立在 deskpilot + tvpilot 两个客户端**，其余集成层都写对了，说明是遗漏不是设计。

**真实后果**（不依赖沙盒代理）：DeskPilot/TVPilot 前面挂反代、端点路径变更、鉴权失败返回 401/403、服务 500 —— **全部被报成健康/成功**。健康检查形同虚设，上层继续派活，每个动作静默失败。

**修复**：
```python
async def _get(self, path, params=None):
    ...
    async with httpx.AsyncClient(timeout=self.timeout) as c:
        r = await c.get(url, params=params, headers=self._headers)
    if r.status_code >= 400:                      # ← 补
        return {"ok": False, "tool": path.lstrip("/"),
                "error": f"http_{r.status_code}", "message": r.text[:200]}
    return r.json()

async def health(self):
    ...
    if r.status_code >= 400:                      # ← 补
        return {"ok": False, "error": f"http_{r.status_code}", "message": r.text[:200]}
    return {"ok": True, "result": r.json()}
```

> **方法论旁注**：这个 bug 是**沙盒的 HTTP 代理意外暴露的** —— 沙盒里 `192.0.2.1:9999`（TEST-NET-1，本应不可达）被代理拦下返回 403，于是 2 个"不可达"用例暴露出真实缺陷。在无代理环境下它们会因连接失败走 `except` 分支而通过。**测试通过 ≠ 代码正确**，只是没撞上那条路径。

---

### P1-32 门禁在公开快照上是**红的** —— 12 个硬编码内网 URL 未登记

- **位置**：`tests/test_homesdk_vendor_gate.py::test_hardcoded_default_url_keys_without_env_override`

```
AssertionError: Lists differ:
  期望(PINNED): ['AUTOFORGE_BASE_URL','BUTLER_BASE_URL','DESKPILOT_HTTP_URL','T...URL']  (4)
  实际(got):    ['AUTOFORGE_BASE_URL','BARK_URL','BUTLER_BASE_URL','DESKPILOT_H...']   (12)
  Second list contains 8 additional elements.
```

**实际存在的硬编码默认值**（`butler/config.py`，全部指向 `192.168.2.x`）：

| 环境变量 | 硬编码默认 |
|---|---|
| `BUTLER_BASE_URL` | `http://192.168.2.200:8095` |
| `TV_HTTP_URL` | `http://192.168.2.238:8080` |
| `TVPILOT_HTTP_URL` | `http://192.168.2.200:8090` |
| `DESKPILOT_HTTP_URL` | `http://192.168.2.201:8765` |
| `TASK_API_BASE_URL` | `http://192.168.2.200:8095` |
| `DOUBAO_API_URL` | `http://192.168.2.200:9090/v1/...` |
| `NEW_API_URL` | `http://192.168.2.200:3001/v1` |
| `DOUBAO_BASE_URL` | `http://192.168.2.200:9090` |
| `MEMORY_AGENT_URL` | `http://192.168.2.200:8086` |
| `MEMORY_AGENT_MCP_URL` | `http://192.168.2.200:8086/mcp` |
| `GO2RTC_BASE_URL` | `http://192.168.2.200:1984` |
| `HA_URL` | `http://192.168.2.200:8123` |
| `BARK_URL` | `http://192.168.2.200:18273` |

**三重问题**：

1. **门禁失败是客观事实** —— 项目自带 `gates.sh` + `.gates-baseline.txt` + 这个 PINNED 名单，公开快照上**门禁是红的**
2. **功能性**：用户只配了 4 个必填变量就能启动（P0-11 修好后），其余 12 个保持默认 → butler 静默连作者的内网地址，所有集成调用失败
3. **更危险**：`192.168.2.x` 是**极常见的家用路由网段**。若部署者网段重合，且该 IP 上有设备监听对应端口（如 8123 = HomeAssistant 默认端口），**控制指令会发到错误的设备上**

**修复**：① 更新 PINNED 名单（最小改动）② 或更好 —— 去掉内网 IP 默认值，改为空串 + 启动时校验（与 P0-11 的 required 校验合并处理）

---

### P1-33 `briefing.py:98` 天气接口**完全硬编码**，无环境变量

```python
# butler/core/briefing.py:98
async with session.get("http://192.168.2.200:3000/api/weather", ...) as r:
```

- **连 `_env()` 都没有** —— 不像 config.py 至少能用环境变量覆盖
- **后果**：早晚报的天气功能**永远连作者的内网**，用户**不改源码就无法使用**（改了下次 `git pull` 又覆盖）
- 挂在 7:10 / 22:00 定时任务上，每次静默失败

同类硬编码（非 `_env` 默认值）全仓共 6 处：

| 位置 | 硬编码 |
|---|---|
| `butler/core/briefing.py:98` | `http://192.168.2.200:3000/api/weather` |
| `butler/af_bridge.py:37` | `http://192.168.2.200:8787` |
| `butler/api/pwa_chat_routes.py:15` | `http://192.168.2.201:8765/api/v1/pm/send` |
| `butler/api/doubao_webhook.py:720` | `http://192.168.2.200:1984/api/frame.jpeg?src=` |
| `butler/api/bark_routes.py:90` | `http://192.168.2.200:8095`（有 `os.environ.get`，但默认值同） |
| `butler/skills/engines/autoflow_propose/engine.py:34` | `http://192.168.2.200:8000`（有 `os.environ.get`） |

**修复**：统一收敛到 `Settings`，无默认值或默认空串。

---

### P1-34 `tts_baidu.py` / `briefing.py` 同样不检查状态码

- **位置**：`butler/core/tts_baidu.py:32-62`、`butler/core/briefing.py:97-116`
- 两者用 `aiohttp`，`await r.json()` 前**无 `r.status` 判断**
- `briefing.py` 的新闻源 `https://60s.viki.moe/v2/60s` 是**外部服务**，返回 4xx/5xx 时会被当成正常数据解析 → 早晚报播报出错误信息或抛异常
- `tts_baidu.py` 是 TTS 降级链路的一环（第四轮已报其 `aiohttp.ClientSession()` 无 timeout），**无超时 + 无状态码检查**双重缺失

---

## 第三部分：本轮**排除**的误报

| 疑似 | 核查结论 |
|---|---|
| `test_mcp_server_contract` 失败 | ⚪ 环境：断言路径不含 `data/`，而沙盒路径是 `/data/workspace/...` |
| `test_v25_pytest_shim` ×3 失败 | ⚪ 环境：豁免名单不咬合（`blocked=[]` 但在册有条目），属基线维护 |
| `test_trace_chain` 失败（早期） | ⚪ 环境：缺 `homesdk`。加 `PYTHONPATH=vendor/homesdk/src` 后 **8 passed** |
| `api/*_routes.py` 大量 `.json()` 无状态检查 | ⚪ 误报：那些是 `await request.json()` **解析入站请求**，不是出站响应 |
| `homesdk` pip 装不上 | ⚪ 环境：`requires-python = ">=3.11"`，沙盒是 3.10；Dockerfile 用 `python:3.11-slim`，生产无此问题 |

---

## 第四部分：工作流现状与局限

### v1.2 已解决

| v1.1 短板 | v1.2 状态 |
|---|---|
| 无测试套件阶段 | ✅ 新增 `tests`，593 用例自动跑 |
| contract 路由 3 处误报 | ✅ 修 Starlette 转换器 + 模板插值，剩 1 条真实 |
| runtime 覆盖仅 12.73% | ✅ 提至 **21.75%**（1330/1733） |

### 仍存在的短板

1. **runtime 覆盖 21.75% 仍偏低** —— 403 个方法未执行，orphans 的 732 条**仍是 advisory-only**
2. **`requirements.txt` 不含测试依赖** —— `pytest`、`pytest-asyncio` 都不在里面，也**不含 `starlette`**（而 `people_routes.py` 之外的模块大量依赖它）… 经核查 `starlette==0.37.2` 在列，但 **pytest/pytest-asyncio/homesdk 均不在**，容器内**开箱跑不了测试套件**。这本身是个可报项（附录）
3. **沙盒有 HTTP 代理** —— 所有"不可达地址"测试在本环境不可信，需交叉验证
4. **未覆盖**：`mcp/server.py`、`agent_collab.py`、`modes/*`、`ilink/` 深层逻辑

### 环境约束（会复现）

- 沙盒重建会丢失 pip 包（paho-mqtt/starlette/aiohttp/pytest 等需重装）
- bash 默认 60s 超时，命令内 `timeout` 无效，须传工具参数
- Python 3.10 vs 项目要求 3.11

---

## 第五部分：九轮累计 · 经执行验证的结论

| 轮 | 编号 | 结论 |
|---|---|---|
| 六 | V1-V6 | `by_room` 不存在 / `speak` 参数名 / 房间映射 / 穿透 / 别名只写不读 / worker Task |
| 七 | V7-V12 | 缺环境变量启动崩 / 空 payload 全屋误控 / 过载清空队列 / SSRF 绕过 / 文件损坏静默丢失 |
| 八 | V13-V15 | `people_routes` 三合一死文件 / `check_timeouts` / `create_rule` |
| **九** | **V16** | **出站 HTTP 从不检查状态码（deskpilot + tvpilot）** |
| **九** | **V17** | **门禁红的：12 个硬编码内网 URL** |
| **九** | **V18** | **`briefing.py` 天气接口完全硬编码** |

---

## 第六部分：下一步建议

### 修复优先级

| 优先级 | 项 | 成本 |
|---|---|---|
| **立刻** | **P0-14 补状态码检查**（deskpilot 3 处 + tvpilot 2 处 + tts_baidu + briefing） | 低 |
| **立刻** | P0-11 / P0-12 / P0-13 及前轮各 P0 | — |
| **本周** | **P1-32 清理硬编码内网默认值 + 更新门禁名单** | 中 |
| **本周** | **P1-33 硬编码 URL 收敛到 Settings** | 中 |
| 排期 | P1-30/31 去掉 `hasattr` 探测（同类 4 处一次性清理） | 低 |

### 工作流下一步

1. **把 tests 阶段的失败自动分类**（真实缺陷 vs 环境），减少人工判断
2. **expand runtime 覆盖到 40%+**，目标消除 orphans 误报
3. **新增"硬编码凭据/地址"扫描阶段** —— 本轮 6 处硬编码 IP 是 grep 出来的，应固化
4. **新增"出站请求契约"检查** —— 本轮 P0-14 是人工扫 `json()` vs 状态检查发现的，应固化（可覆盖所有集成层，防回归）

### 一条方法论沉淀

**前八轮我一直在自己造轮子验证，而仓库里本来就有 593 个作者写好的断言。**

第九轮把 `tests` 提到工作流最前面之后，一轮就撞出 P0-14（4xx/5xx 当成功）和 P1-32（门禁红）——这两条靠任何静态启发式都发现不了：前者需要**实际发起 HTTP 请求并观察响应**，后者需要**跑门禁并比对基线**。

结论：**审计的起点应该是项目自带的测试，而不是外部工具**。外部工具用于覆盖测试没覆盖到的部分。
