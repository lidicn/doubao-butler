# doubao-butler 审计报告 · 第十六轮（收官轮）

- **审计对象**：`lidicn/doubao-butler`
- **补丁副本**：`/data/workspace/repos/db-patched/`（**原仓库未改动**）
- **轮次**：`round-016`
- **报告日期**：2026-10-08

---

## 一句话结论

**D17（SSRF）、D11（health 说谎）、D13（ws 无超时）、D16（homesdk 递归）四项全部修复并实测验证，台账 D1–D18 全部 `fixed_in_patch_copy`，still_open 归零。D16 第一版补丁又犯了"只改一半"——签名和递归调用都改了，唯独漏了守卫，实测仍崩；补齐后才生效。**

---

## 一、D17 SSRF：修复前后九样例对照

| 输入 | 修复前 | 修复后 |
|---|---|---|
| `http://127.0.0.1/x` | 拦截 | 拦截 |
| `http://192.168.2.200/x` | 拦截 | 拦截 |
| `http://169.254.169.254/latest` | 拦截 | 拦截 |
| `file:///etc/passwd` | 拦截 | 拦截 |
| **`http://localhost/x`** | **放行** | 拦截 |
| **`http://127.0.0.1.nip.io/x`** | **放行** | 拦截 |
| **`http://homeassistant.local/x`** | **放行** | 拦截 |
| **`http://[::1]/x`** | **放行** | 拦截 |

修复内容：
- 新增 `_LOCAL_HOSTNAMES`（localhost / localhost.localdomain / ip6-localhost / ip6-loopback）
- 新增 `_resolve_host()`，域名解析出内网 IP 即拦截；`.local` / `.internal` 后缀直接拦
- IPv6 处理（`is_loopback` / `is_link_local` / `is_private`，含 ipv4-mapped 转换）

**最关键的一条**：`register_api` 保存路径**从不调用** `validate_api_url`——那个名字只出现在独立端点内部和路由注册处，是个"顾问端点"，前端可查但不强制。已改为保存时强制校验，校验器自身异常也拒绝（不静默放行）。

配合 `/apis/{id}/test` 真发 httpx 请求 ⇒ 已认证用户可让服务端访问任意 URL。

### 一个需要开发者定夺的取舍

`_resolve_host()` 对**解析失败**的域名一律拒绝（fail-closed）。实测沙箱无 DNS，连 `https://api.example.com/x` 都被拦。

- 理由：解析不了就无法证明它不是内网地址
- Escape hatch：`allow_internal_ips=True`
- 影响：注册是管理员一次性操作、可重试，但**外部 API 在 DNS 临时故障时会被拒**

---

## 二、D11 / D13

**D11**：`/api/health` 的 `online` 从字面常量 `True` 改为 `all(comps.values())`。**状态码恒 200 保留**——README 那条契约（health 不因下游失败）是有意为之且正确，问题只在 body 语义。

**D13**：`while not snapshot_done: await ws.receive()` 包 `asyncio.wait_for(timeout=30)`。heartbeat 是保活，**不能替代接收超时**——HA 半开连接下会永久挂起。

---

## 三、D16：第一版补丁"只改一半"

三处递归函数（`_numeric_literal` / `_dotted` / `_literal_secret`）在 vendor wheel 里。第一版补丁做了两件事：

1. ✅ 签名加 `_depth` 参数
2. ✅ 递归调用传 `_depth + 1`

**漏了第三件：插守卫。** 结果实测仍然 RecursionError——参数传了，没人用它做判断。

这与 AutoForge F1 同族：**防护的"一半"装好了，另一半没接上**。而且这次发生在补丁脚本里，`--check` 报 7/7 全部命中、语法检查也通过——**只有实测能发现**。

补齐 `if _depth > _MAX_AST_DEPTH` 后的函数级对照：

| | depth 1500 | depth 2500 |
|---|---|---|
| `_numeric_literal` 修复前 | **RecursionError** | **RecursionError** |
| `_numeric_literal` 修复后 | ok（返回 None） | ok |
| `_dotted` 修复前 | **RecursionError** | **RecursionError** |
| `_dotted` 修复后 | ok（返回空串） | ok |

⚠ **正确修复应上溯 homesdk 上游仓库。** 本轮只改了解包副本（`/tmp/hsk3`），未回写 wheel。它与 AutoForge F16 是**同一个 wheel 的同一处缺陷**，修上游一处两边受益。

---

## 四、全项目收官

| 指标 | 数值 |
|---|---|
| 台账 | **D1–D18 全部 `fixed_in_patch_copy`**，still_open 归零 |
| 补丁批次数 | 4（第十三轮 19 处 / 第十四轮 12 处 / 第十五轮 10 处 / 本轮 7 处）|
| 门禁 | ok=True，33 项，drift=4 |
| patch_lint | db-patched 8 文件 OK，af-patched 13 文件 OK |

**按根因合并只有五族：**

1. **读路径的失败被当成"没有数据"**（D1–D4、D6–D9、D18）—— 9 项，占一半
2. **护栏装了但主流程不调用**（D17）—— 与 AutoForge F1 同族
3. **防护只做了一半**（D10 取令牌无超时、D16 依赖侧）—— 与 AutoForge BUG-20 注释同源
4. **check-then-act 未原子化**（D14/D15）
5. **配置面不一致**（D5 路径分叉、D11 health 说谎）

---

## 五、工作流迭代汇总（第十三轮起）

| 轮次 | 编号 | 内容 |
|---|---|---|
| 13 | W76/b/c/d/e/f | patch_lint：调用而非名字、同名局部变量、递归扫描、模块名自适应、绝对导入、属性调用排除 |
| 13 | W77 | `mark_poisoned` 路径必须显式传（6 处只有 1 处叫 `self.path`） |
| 14 | W78/b/c | 未定义名检查（含负向测试）、全文件覆盖、元组解包 |
| 15 | — | 正则提取差异（六处日志文案不同） |
| 16 | — | D16 守卫补齐 |

---

## 六、如实说明

- **补丁只在只读副本 `db-patched`，原仓库未改动**
- **D16 未回写 wheel**，且 `homesdk.gates` 在本沙箱无法 import（`tomllib` 需 Python 3.11，沙箱 3.10）⇒ **未能端到端验证 CI 门禁路径**，只有函数级对照
- **D17 的 fail-closed 取舍**需开发者确认（DNS 临时故障时会拒绝注册）
- **依赖 CVE 面始终未扫**（pip-audit 需联网，被网络策略阻断）
- **前七轮产物在第八轮重建工具链时损坏**，跨轮曲线只能从 round-008 起画

---

## 七、给开发者的建议（按优先级）

1. **先定一条"读不出来 ≠ 没有"的约定**，再把九个文件统一过去 —— 不要逐条修。同一项目里 `fast_routes.py` 的注释已经写对了这个语义，只是没推广开
2. **D17 的保存路径强制校验**优先于补域名解析 —— 前两条修好但主流程不调用，**等于没修**
3. **D16 上溯 homesdk 上游**，不要在每个下游项目各改各的
4. **D14/D15 用一个 `singleton()` 辅助统一收口**，不要 26 处各改各的

---

## 八、十六轮下来最硬的一条纪律

**任何"看起来修好了"都要先怀疑工具与环境，再相信结论。**

本项目累计二十二次"静默失效"，失效源位置一路外移：

审计工具自己 → 补丁 → 外部工具 → 环境 → 探针自身 → **补丁脚本（本轮 D16）**

共同点无一例外：**表现为"结论变好/命中变少"，而不是报错。**

本轮 D16 是这条纪律的又一次兑现——`--check` 7/7 通过、语法检查通过、patch_lint 通过，**只有实测发现守卫根本没插**。如果当时信了"补丁已应用"这个结论，就会带着一处未修复的 CI 门禁崩溃缺陷收官。
