# doubao-butler 第十轮审计报告 · 双新阶段驱动

- **审计对象**：`lidicn/doubao-butler` @ `836df79`
- **本轮主线**：把第九轮**人工 grep 的两个发现固化为工作流阶段**，然后用它们扩大扫描面
- **结论**：**1 个 P0 + 3 个 P1**，全部经执行验证

---

## 第一部分：工作流迭代（v1.2 → v1.3）

### 1.1 新增 `secrets` 阶段 —— 硬编码地址/凭据扫描

固化第九轮手工 grep 的 6 处内网 IP。判据升级：

- LAN 字面量（`192.168.` / `10.` / `172.16-31.`）
- **同行是否出现 `_env(` / `os.environ` / `getenv`** → `severity=low`（可用环境变量覆盖）
- 否则 `severity=high`（**用户不改源码就无法使用**）

实测：**36 处硬编码内网地址，其中 21 处 high**。

### 1.2 新增 `httpcontract` 阶段 —— 出站请求契约检查

固化第九轮 P0-14（`json()` 次数 vs 状态检查次数的人工比对）。AST 识别两类响应绑定（`async with ... as r` / `r = await c.get(...)`），回溯后续 25 行是否出现 `status`/`status_code`/`raise_for_status`。

实测：**16 处出站响应解析前未检查状态码**（含第九轮已报的 deskpilot/tvpilot/tts_baidu/briefing，另有 7 处新增）。

### 1.3 依赖恢复（沙盒重建后）

沙盒重建会清空 pip 包。恢复清单已固化：
```
paho-mqtt starlette==0.37.2 aiohttp httpx pytest pytest-asyncio
coverage edge-tts apscheduler onecode-pycg
```
> 注意：`pycg` 包在本环境安装后目录名为 `PyCG` 且内容为空，**必须装 `onecode-pycg`** 才能 `import pycg`。

### 1.4 v1.3 实测数据

| 阶段 | 结果 |
|---|---|
| tests | 7 failed / 586 passed / 10 skipped / 1 xfailed |
| **secrets** | **36 处硬编码内网地址（21 处 high）/ 2 处疑似硬编码凭据** |
| **httpcontract** | **16 处未检查状态码** |
| graph | 707 节点 / 1638 边 |
| runtime | 覆盖 **22.07%**（1362/1733） |
| orphans | runtime 剔除 234 FP，剩 731 |
| deadcall | 249 处，4 处 `hasattr` 保护 |
| contract | 签名不匹配 0 / 前端孤儿路由 1（真实） |

执行顺序：`bootstrap / static / graph / tests / secrets / httpcontract / runtime / orphans / deadcall / cycles / contract`

---

## 第二部分：第十轮审计发现

### P0-15 五个**分叉的孤儿模块**（769 行）—— 含已修复 bug 的旧版本

- **位置**：`butler/deskpilot.py`、`butler/doubao.py`、`butler/engine.py`、`butler/defaults.py`、`butler/core/agent_routes.py`

这是本轮最重要的发现，由 `httpcontract` 阶段扫出 `butler/deskpilot.py:40`（而第九轮只注意到 `butler/integrations/deskpilot.py`）后深挖得到。

#### 孤立性证据（三重）

| 文件 | 行数 | 精确模块引用 | coverage 执行过的方法 |
|---|---|---|---|
| `butler/deskpilot.py` | 206 | **0** | **0** |
| `butler/doubao.py` | 129 | **0** | **0** |
| `butler/engine.py` | 249 | **0** | **0** |
| `butler/defaults.py` | 111 | **0** | **0** |
| `butler/core/agent_routes.py` | 74 | **0** | **0** |
| *对照* `butler/integrations/deskpilot.py` | 248 | ✅ 被 app.py:77 导入 | **32** |

精确引用检查已排除子串误命中（如 `engine`、`defaults`、`doubao` 在别处的普通出现），并覆盖 `importlib`/字符串形式。

#### 它们不是"没用的旧文件"，而是**已修复 bug 的旧版本**

每个文件都有在役对应物，且**在役版含孤儿版没有的 bug 修复**：

| 孤儿 | 在役对应物 | 差异行数 | 孤儿缺失的修复（举例） |
|---|---|---|---|
| `engine.py` | `triggers/engine.py` | **289** | `_only_terminal()`：旧写法把 `status=="quarantined"` 计入 `_fail_count`，**技能一日不解除隔离就永不自愈**（现网证据：计数 8 > 阈值 5）。孤儿版**没有这个修复** |
| `core/agent_routes.py` | `api/agent_routes.py` | **235** | 孤儿版只有 74 行（在役 285 行），缺 `list_fast_routes`、`analyze_fast_routes` 及后续全部处理器 |
| `deskpilot.py` | `integrations/deskpilot.py` | **50** | 缺 v2.5 桌面操控（`desktop_screenshot`/`click`/`type`）；`system_run` 仍是旧签名（无 `list` args / 无 `timeout`） |
| `doubao.py` | `integrations/doubao.py` | **47** | 缺 M4 视觉缓存（`_vision_cache`，同截图同 prompt 5 分钟不重复分析）、缺 `silent` 参数 |
| `defaults.py` | `triggers/defaults.py` | **37** | 播种规则已演进 |

#### 后果

1. **769 行死代码** —— 占 `butler/` 总量约 6%，误导阅读与 AI 辅助开发
2. **重构陷阱**：未来任何人 `from butler.deskpilot import DeskPilotClient`（路径更短、更符合直觉）会拿到**缺 v2.5 能力且带 P0-14 状态码 bug 的旧客户端**，且不会报错
3. **误修风险**：在孤儿文件里修 bug，线上毫无变化
4. **安全面**：孤儿 `deskpilot.py` 同样从不检查状态码（已被 httpcontract 命中）

**修复**：直接删除 5 个文件（各自在役对应物均已就位且被实际引用）。删除前建议 `git log` 确认无外部依赖。

```bash
git rm butler/deskpilot.py butler/doubao.py butler/engine.py \
       butler/defaults.py butler/core/agent_routes.py
```

---

### P1-35 `secrets` 阶段新扫出的 3 处 high 级硬编码（第九轮漏掉）

第九轮只报了 `briefing.py` 一处，本轮扩大扫描面后新增：

| 位置 | 硬编码 | 后果 |
|---|---|---|
| `butler/tools/registry.py:930` | `http://192.168.2.200:3000/api/weather` | `get_weather` **工具**（LLM 可调用）永远连作者内网，天气工具对用户 100% 不可用 |
| `butler/skills/engines/llm_decide/ask.py:55` | `http://192.168.2.200:8090` | `llm_decide` 技能的 TVPilot 提示音 + 免唤醒，**所有该类型技能**均失效 |
| `butler/api/pwa_chat_routes.py:15/41` | `http://192.168.2.201:8765/api/v1/pm/send` | PWA 聊天转发到 DeskPilot PM，**整个 PWA 聊天转发链路**不可用；且此处**同时**不检查状态码 |

> `pwa_chat_routes.py` 是三重叠加：硬编码地址 + 无状态码检查 + 使用 `get_settings().deskpilot_api_token`（P0-11 的必填变量）。

`butler/config.py` 的 13 处虽标 high（字段默认值不含 `_env`），但实际由 `Settings.load()` 的 `_env()` 覆盖，属**判据局限**，已在下方说明。

---

### P1-36 `httpcontract` 新扫出的 7 处状态码缺失

第九轮人工只找到 4 处（`deskpilot` ×3、`tvpilot` ×2、`tts_baidu` ×2、`briefing` ×2 中的 4 个），本轮 AST 全量扫出 16 处。新增确认：

| 位置 | 说明 |
|---|---|
| `butler/deskpilot.py:40/54/69` | 孤儿副本（P0-15），同样有此缺陷 |
| `butler/tools/desk_pilot.py:24` | 第三份 DeskPilot 客户端实现（413 行），同样缺失 |
| `butler/api/pwa_chat_routes.py:41` | PWA 转发结果 `r.json()` 直接取用 |
| `butler/integrations/ha.py:97` | `r1.text` 未判状态（虽 ha.py 总体有 17 处状态检查，此点是遗漏） |
| `butler/tools/registry.py:930` | `get_weather` 工具 |

---

### P1-37 三份 DeskPilot 客户端并存，行为不一致

`butler/deskpilot.py`（206 行）/ `butler/integrations/deskpilot.py`（248 行）/ `butler/tools/desk_pilot.py`（413 行）—— **三份独立实现，无继承关系**。

- `app.py:77` 只导入 `integrations.deskpilot`
- `tools/__init__.py:19`、`tools/registry.py:895` 导入 `tools.desk_pilot`
- `butler/deskpilot.py` **无人引用**（P0-15）

→ 同一后端存在**两套在役客户端**，任一处修 bug 另一处不会同步。这是 P0-14 修复时需要特别注意的：**必须同时修 `integrations/deskpilot.py` 和 `tools/desk_pilot.py`**，否则工具链路仍会漏判。

---

## 第三部分：本轮**排除**的误报

| 疑似 | 核查结论 |
|---|---|
| `butler/config.py` 13 处硬编码标 high | ⚠️ **判据局限**：字段默认值不含 `_env`，但 `Settings.load()` 用 `_env()` 覆盖。真实 high 只有 8 处（见下方） |
| `push_guard.py:285` `key = "tts:critical"` | ❌ 误报：Redis 键名，非凭据 |
| `morning/routine.py:243` `event_key="morning_action_inquiry"` | ❌ 误报：事件标识，非凭据 |
| `api/*_routes.py` 的 `.json()` | ❌ 误报：入站 `request.json()`，已由 `INBOUND_JSON_CTX` 逻辑排除 |
| `butler/devices.py` / `butler/integrations/doubao.py` 是孤儿 | ❌ 误报：`devices.py` 被 `app.py:88` 导入（**是在役版**）；`integrations/doubao.py` 被实际使用。同名 `tools/devices.py`（72 行）是不同用途的辅助模块 |

**真正的 high 级硬编码（8 处）**：`af_bridge.py:37`、`doubao_webhook.py:720`、`pwa_chat_routes.py:15`、`app.py:802`、`briefing.py:98`、`llm_decide/ask.py:55`、`tools/desk_pilot.py:11`、`tools/registry.py:930`

---

## 第四部分：工作流现状与局限

### v1.3 已解决

| v1.2 短板 | v1.3 状态 |
|---|---|
| 硬编码靠人工 grep | ✅ `secrets` 阶段固化，检出 36 处（人工只找到 6 处） |
| 状态码缺失靠人工比对 | ✅ `httpcontract` 阶段固化，检出 16 处（人工 4 处） |
| 沙盒重建丢依赖 | ✅ 恢复清单已记录 |

### 仍存在的短板

1. **`secrets` 的 `severity` 判据有局限** —— `config.py` 的 dataclass 字段默认值不含 `_env`，被误标 high（13 处中仅部分为真）。应改为"字段默认值 + `Settings.load()` 的 `_env` 调用"联合判定
2. **runtime 覆盖 22.07%** —— 371 个方法未执行，orphans 的 731 条仍是 advisory-only
3. **沙盒有 HTTP 代理** —— 所有"不可达地址"测试不可信，需交叉验证
4. **未覆盖**：`mcp/server.py`、`agent_collab.py`、`modes/*`、`ilink/`、 `morning/`、`notifier/` 深层逻辑
5. **tests 失败仍需人工分类** —— 7 个失败中 2 真 5 环境，分类未自动化

---

## 第五部分：十轮累计 · 经执行验证的结论

| 轮 | 编号 | 结论 |
|---|---|---|
| 六 | V1-V6 | `by_room` 不存在 / `speak` 参数名 / 房间映射 / 穿透 / 别名只写不读 / worker Task |
| 七 | V7-V12 | 缺环境变量启动崩 / 空 payload 全屋误控 / 过载清空队列 / SSRF 绕过 / 文件损坏静默丢失 |
| 八 | V13-V15 | `people_routes` 三合一死文件 / `check_timeouts` / `create_rule` |
| 九 | V16-V18 | 出站 HTTP 从不检查状态码 / 门禁红的：12 个硬编码内网 URL / 天气接口硬编码 |
| **十** | **V19** | **五个分叉孤儿模块（769 行），含已修复 bug 的旧版本** |
| **十** | **V20** | **`get_weather` 工具 / `llm_decide` 技能 / PWA 转发 硬编码内网地址** |
| **十** | **V21** | **三份 DeskPilot 客户端并存，修 bug 需同步两处** |

---

## 第六部分：下一步建议

### 修复优先级

| 优先级 | 项 | 成本 |
|---|---|---|
| **立刻** | **P0-15 删 5 个孤儿文件**（769 行） | **极低**（`git rm`） |
| **立刻** | **P0-14 补状态码检查** —— 注意**同时修 `integrations/deskpilot.py` + `tools/desk_pilot.py`**（P1-37） | 低 |
| **立刻** | 前轮各 P0 | — |
| **本周** | P1-32/35 清理硬编码（共 8 处真 high）收敛到 Settings | 中 |
| 排期 | P1-30/31 去掉 `hasattr` 探测（4 处一次性清理） | 低 |

### 工作流下一步

1. **修 `secrets` 的 severity 判据** —— 联合 `Settings.load()` 的 `_env` 调用判断，消除 13 处误标
2. **新增 `dupfiles` 阶段** —— 本轮 P0-15 是先扫出 `butler/deskpilot.py` 再人工扩展发现的，应固化为"同名/近同内容模块 + 零引用"自动检测
3. **tests 失败自动分类** —— 用"重跑 + 环境标记"区分真实缺陷与环境差异
4. **runtime 覆盖提到 40%+**

### 一条方法论沉淀

本轮 P0-15 的发现路径值得记录：

**`httpcontract` 扫出 `butler/deskpilot.py:40` → 发现该文件零引用 → 扩展扫描发现同类共 5 个 → 对比在役版发现是含已修复 bug 的旧分叉。**

前九轮我一直在找"代码里写错了什么"，第十轮转向"**哪些代码根本不该存在**"。769 行死代码不只是噪音——它们是**带已知 bug 的旧版本**，是未来重构时最容易踩的陷阱（路径更短、名字更直觉的 `butler.deskpilot` 就在那里等你 import）。

**审计的产出不该只有"改哪里"，也该有"删哪里"。**
