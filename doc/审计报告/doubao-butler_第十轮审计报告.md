# doubao-butler 审计报告 · 第十轮

- **审计对象**：`lidicn/doubao-butler`
- **轮次**：`round-010`
- **产物**：`/data/workspace/repos/adm/projects/doubao-butler/round-010/`
- **报告日期**：2026-10-07

---

## 一句话结论

**W69 是本轮最大的产出：发现「async 语法节点不是对应同步节点的子类」，导致全项目 10 处 `isinstance(x, ast.With)` 判定全部漏掉 `async with` —— 而 doubao-butler 是 async 重度项目。修掉后 MIXED-RETURN 的 high 从 11 降到 1（10 条假阳性）。同时确证 D17：SSRF 护栏两层失效，实测 localhost/nip.io/IPv6 全放行，且注册路径根本不调用校验。**

---

## 一、W69：async 语法节点被系统性漏掉

**实测确认（CPython 3.10）**：

```python
issubclass(ast.AsyncWith, ast.With)             → False
issubclass(ast.AsyncFor,  ast.For)              → False
issubclass(ast.AsyncFunctionDef, ast.FunctionDef) → False
```

**这不是某条规则的疏忽，是一整类节点被漏掉。**

后果链条（以 `AF-AST-MIXED-RETURN` 为例）：

```
block_terminates(fn.body)
  → 末句是 Try
    → try.body 末句是 AsyncWith
      → `isinstance(stmt, ast.With)` 为 False
        → return False（判"不保证终止"）
          → 判"存在 fall-through 隐式 None"
            → 标注非 Optional ⇒ high
```

而 `ha.py:251 call_service_strict` 的实际情况是：

```python
async def call_service_strict(...) -> dict:
    if not self.s.ha_token: raise HAError(...)
    try:
        async with httpx.AsyncClient(timeout=10) as c:   # ← AsyncWith
            ...
            if r.status_code == 200: return r.json() if r.content else {}
            if 404: raise ...
            ...
    except httpx.TimeoutException: raise HATimeout(...)
    except HAError: raise
    except Exception as e: raise HAError(...)
```

**所有路径要么 return dict，要么 raise。根本不存在 fall-through。**

**影响面**：全项目 10 处 `isinstance(..., ast.With)`、0 处处理 AsyncWith。批量替换为 `WITH_NODES = (ast.With, ast.AsyncWith)`（8 个文件，另加 6 处 For）。

**修后效果**：MIXED-RETURN 的 high **11 → 1**。

**为什么这条二十轮才暴露**：`ast.FunctionDef` 因为常见，**大部分规则已经写了** `(ast.FunctionDef, ast.AsyncFunctionDef)`——说明开发者知道这个坑。但 `With`/`For` 两处没跟上。同一个坑，在同一个文件里，一半修了一半没修。这与第三轮 W54、第五轮 W61、第六轮 W62 是同一条主线：**规则对语法/语义的覆盖不完整，且修的时候只修了当时遇到的那一处。**

---

## 二、确证缺陷：D17【P2·medium】SSRF 护栏两层失效

`butler/core/cron_validator.py:130` + `butler/api/cron_task_routes.py:36`

### 第一层：校验本身不完整

```python
try:
    ip = ipaddress.ip_address(hostname)
    for net in _PRIVATE_NETS:
        if ip in net: return False, ...
except ValueError:
    pass          # ← 域名直接跳过检查
```

`_PRIVATE_NETS` 只列 IPv4（127/10/172.16/192.168/169.254），**无 IPv6**。

**实测 7 个样例**：

| URL | 结果 |
|---|---|
| `http://127.0.0.1:8123/api` | 拦截 ✓ |
| `http://192.168.2.200:8123/api` | 拦截 ✓ |
| `file:///etc/passwd` | 拦截 ✓ |
| **`http://localhost:8123/api`** | **放行 ★** |
| **`http://127.0.0.1.nip.io:8123/api`** | **放行 ★** |
| **`http://homeassistant.local:8123/api`** | **放行 ★** |
| **`http://[::1]:8123/api`** | **放行 ★** |

### 第二层：保存路径根本不调用校验

```python
async def register_api(request: Request):
    g = guard(request)
    ...
    if not api_id or not url:
        return err("api_id and url are required")
    executor.register_api(api_id, url, headers, ...)    # ← 无 validate_api_url
```

`validate_api_url` 这个名字在 `cron_task_routes.py` 里**只出现在两处**：169 行（独立的 `/validate_api` 端点内部）和 201 行（路由注册）。`register_api` 里没有。

⇒ 那个校验端点是个**顾问端点**——前端可以调它显示提示，但保存完全不强制。

配合 `/api/cron_task/apis/{id}/test` → `executor.test_api()` → `_test_api_async()` → `httpx.AsyncClient` **真发请求**。

### 为什么定 medium 而非 high

需通过 `guard()` 鉴权，且管家是家庭内网部署 ⇒ 已认证用户的 SSRF，不是未授权。

### 与 AutoForge 的对照

**第二层和 AutoForge F1 是同一个错误的两种写法**：护栏装了，但装在一条调用方不走的路径上。第一层则与 AutoForge F15 同族（出站护栏被 bypass）。

**修复优先级：第 ③ 条最关键**——前两条修好但保存路径不调用，等于没修。

---

## 三、MIXED-RETURN 最后 1 条：判为假阳性

`butler/mcp/server.py:38 handle_request` 标注 `-> dict`，但有一条 `return None`：

```python
elif method == "notifications/initialized":
    self._initialized = True
    return None  # notification 不需要响应
```

调用方 `mcp/http_transport.py:66`：

```python
response = await server.handle_request(body)
if response is None:                          # ← 明确处理
    return JSONResponse({}, status_code=202)
```

**功能正确，是标注不准**（应为 `dict | None`）。不影响稳定性/功能性，不立缺陷，建议顺手改标注。

---

## 四、PoC 回归（本轮)

| PoC | 结果 |
|---|---|
| 状态损坏 | 10 目标 → **data_lost 4 / no_write 3 / unavailable 3**（与第四轮一致） |
| 出站护栏 | **bypass_confirmed_unfixed 1**（与 D17 手工实测吻合） |
| 失败开放 | 2 目标 **unavailable**（doubao-butler 上无确证的 FO-01 缺陷，正常） |

cron_task 三条仍为 `no_write`——探针找不到公开 seeder 触发写盘，D8/D9 靠手工构造确证，不冒充自动确证。

---

## 五、审计结果

| 指标 | 数值 |
|---|---|
| MIXED-RETURN high | **11 → 1**（W69 消除 10 条假阳性） |
| 本轮新增确证 | **D17** |
| 本轮判假阳性 | **10**（W69）+ 1（mcp handle_request） |
| 台账累计 | **D1–D17** |
| 门禁 | ok=True，analyzers=33 |

---

## 六、按根因合并（更新）

| 根因 | 缺陷 | 计数 |
|---|---|---|
| 读路径的失败被当成「没有数据」 | D1 D2 D3 D4 D6 D7 D8 D9 | 8 |
| 失败路径没兜住 / 无超时 | D10 D12 D13 | 3 |
| 并发初始化竞态 | D14 D15 | 2 |
| 无预算递归（依赖侧） | D16 | 1 |
| **护栏装了但主流程不调用** | **D17** | 1 |
| 配置面不一致 | D5 | 1 |
| 观测语义矛盾 | D11 | 1 |

D17 单列一族。**它与 AutoForge F1/F15 完全同型**：三个项目（AutoForge、doubao-butler）在完全独立的情况下，都出现了"防护写了但没接上主流程"。这已经是跨项目第三次。

---

## 七、未覆盖 / 如实说明

- **失败开放 PoC 2 个目标 unavailable**：doubao-butler 上没有确证的 FO-01 缺陷（上一轮判的 2 条都是假阳性），非工具失效
- **cron_task 三条 PoC 仍 no_write**：D8/D9 靠手工构造确证
- **依赖 CVE 面未扫**（pip-audit 缺 venv + 网络阻断）
- **D17 的 `test_api` 实际发起请求未端到端实测**（只确证了 URL 校验绕过 + 调用链存在），因为需要起 HTTP 服务且要过鉴权
- **W69 的 For 替换（6 处）未单独验证效果**，只过了门禁无异常

---

## 八、下一轮方向

1. **D17 的补测**：起本地靶子，端到端验证 `register_api` → `test_api_direct` 是否真能访问内网地址
2. **写补丁副本（db-patched）**——D1–D17 证据已足够，应推进到"修复并验证"
3. **核验剩余 high**（CONC-08 / CONC-03 / FO-01 等此前判过的，在 W69 后重跑看有无变化）
4. **把 W69 的教训外推**：还有其他"async 版本被漏掉"的节点吗？（`ast.Await`、异步推导式）

---

## 九、本轮最该记住的一条

**同一个坑，在同一个文件里，一半修了一半没修。**

`ast.FunctionDef` 需要配 `ast.AsyncFunctionDef` 这件事，规则作者是知道的——几乎每条规则都写了那个元组。但 `ast.With` 和 `ast.For` 同样需要配 async 版本，却 10 处全漏。

这不是知识盲区，是**修复没有外推**。第三轮 W54（写盘委托）、第五轮 W61（同名遮蔽）、第六轮 W62（跨函数 timeout）、本轮 W69（async 节点）——四次都是"遇到一处修一处"。第六轮我就记下过"该做通用化改造"，第七轮做了 W65，但 W65 只覆盖了"跨函数属性"，没覆盖"语法节点覆盖度"。

**推论：修一条规则时，应该问的不是"这个 case 怎么修"，而是"这个 case 属于哪一类，这一类还有几个"。** 前四次都没问这个问题，于是同一个结构性问题以四种不同面貌各出现一次。
