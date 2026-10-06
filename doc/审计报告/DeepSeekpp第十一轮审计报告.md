# DeepSeek++ 第十一轮审计 — 首次审视"扩展配置层"与"跨运行时契约"

```
Audit target : github.com/lidicn/deepseekpp (DeepSeek++ v1.16.0)
Toolkit      : /data/workspace/audit-kit
New dimension: ① manifest 权限/CSP/资源暴露面  ② 跨运行时消息协议一致性
Audit date   : 2026-10-05
Mode         : Audit-Only（不改代码）
```

---

## 一、为什么换到这两个维度

前十轮**全部在审 TypeScript 源码**。但浏览器扩展有两条边界，是读源码看不出来的：

1. **manifest 声明层** —— 权限、CSP、资源暴露面。声明了却没用 = 无谓攻击面；代码用了却没声明 = MV3 里静默失败。这类问题只有把 manifest 与代码**交叉比对**才能发现。
2. **跨运行时契约** —— 扩展有多个隔离运行时（SW / content / sidepanel / sandbox / offscreen），只能靠消息通信。消息协议被破坏不会本地报错，只会静默失效。

本轮针对这两条边界各做了一个工具 + 一次委托。

---

## 二、新增 `lib/manifest.py`：权限一致性审计

项目的 manifest 由 `wxt.config.ts` 动态生成（没有静态 `manifest.json`），所以工具直接从 TS 里抽取声明，与代码中的 `chrome.*` 调用、URL 主机做交叉比对。

### 2.1 权限比对结果：完全一致，无过度声明

```
=== 声明的权限 ===
  alarms, contextMenus, debugger, identity, nativeMessaging,
  offscreen, sidePanel, storage, tabs

=== 代码实际需要 ===
   112 次  storage      13 次  tabs       7 次  contextMenus
     6 次  offscreen     5 次  debugger    5 次  alarms
     3 次  sidePanel     1 次  nativeMessaging  1 次  identity

过度声明：（无）
可能缺失：（无）
```

**这是好消息，但也说明这条维度没挖出缺陷** —— 项目权限声明与实际使用严格对齐，没有多余权限。

### 2.2 声明了的高危权限（不是缺陷，是风险面清单）

| 权限 | 能力 |
|---|---|
| `debugger` | 可附加到任意标签页，读取全部网络请求/响应并执行 JS |
| `nativeMessaging` | 可与本地原生进程通信（本项目 shell-host） |
| `tabs` | 可读取全部标签页 URL/标题 |

三者**都被代码实际使用**（debugger 5 次、tabs 13 次、nativeMessaging 1 次），所以不是过度声明。但组合起来能力很强：`tabs` + `optional_host_permissions: <all_urls>` 意味着一旦用户授予可选权限，扩展可窥探全部浏览历史。

### 2.3 暴露面：3 条 P3

**[P3] `sidepanel.html` 暴露给 `<all_urls>`，且无 `frame-ancestors`**

```ts
{
  resources: ['sidepanel.html', 'pet/deepseek-whale-pet-states.png'],
  matches: ['<all_urls>'],
}
```

`App.tsx:34` 还会读 URL 参数：

```tsx
&& new URLSearchParams(window.location.search).get('surface') === 'floating-chat';
```

任意站点可以 iframe 嵌入侧边栏页并传 `surface=floating-chat` 参数。CSP 里**没有 `frame-ancestors` 指令**做限制。

需要说明：这个暴露是**有设计理由的** —— 注释写明"floating-chat 悬浮球需要在任意页面嵌入 sidepanel"。所以定为 P3 加固项，不是缺陷。

**[P3] 三条主机权限允许明文 `http://`**

```
*://chat.deepseek.com/*      ← scheme 通配，含 http
*://cn.bing.com/*
*://www.bing.com/*
```

对比 OAuth 那五条都显式写了 `https://`。这三条用 `*://` 通配，允许明文访问。属加固项。

**[P3] sandbox CSP 允许 `data:` iframe，可绕过信标限制**

```ts
"img-src 'self' blob: data:",     // 注释明确说：pin 住 img-src 防止沙箱 HTML 用远程图片当网络信标
"child-src 'self' blob: data:",   // ← 但 child-src/frame-src 允许 data:
"frame-src 'self' blob: data:",
```

我复核了执行链：用户 HTML 在 `sandbox-runner/main.ts:97` 创建的 iframe 里执行，该 iframe 只有 `allow-scripts`：

```ts
frame.sandbox.add('allow-scripts');
frame.srcdoc = createHtmlDocument(request.code, htmlRequestId);
```

**`sandbox` 属性未加 `allow-same-origin`，所以是 opaque origin，不继承父页 CSP。** 而 `data:` iframe 同样不继承父 CSP —— 用户代码可以在 opaque iframe 里嵌套 `data:` iframe，在那里用外发请求做信标，绕过 `img-src` 的 pin。

需要说明：这需要用户主动运行含恶意代码的 skill，且 `connect-src 'self'` 会限制一部分。定为 P3 纵深防御缺口。

---

## 三、跨运行时消息协议审计

派了一张委托，比对三集合：

| 集合 | 数量 |
|---|---|
| `MessageAction` 判别联合分支 | 98 |
| `RUNTIME_COMMAND_CONTRACTS` 键 | 131 |
| `TypedRuntimeCommandContracts` 扩展键 | 2 |

### 3.1 结论：协议闭合，无孤儿消息

委托给出了一个**机制性的强保证**，我复核后认可：

```ts
// runtime-command-registry.ts:121-125
// 遍历 TYPED_RUNTIME_COMMAND_TYPES，每个都必须有 handler 注册，否则抛错
throw new Error(`Missing typed runtime command handler: ${type}`);
```

registry 在 background **启动时**执行完整性校验 —— 任何 typed-handler 类型无人处理就会立即抛错。这从机制上排除了"发出但无人处理"这类缺陷的可达性。

两个差集也都有解释：

- **`MessageAction` 独有 2 条**（`MEMORIES_UPDATED`/`TOUCH_MEMORIES` 类）：被显式声明为 `client-only, declared-only, unrouted` —— 是广播事件通道的设计，测试有覆盖
- **Contracts 独有 33 条**：全部标注 `live-only` —— 是 UI 层不可见的 background 内部通道，各有 contracts + codec + handler 三件套

**这是分层设计，不是错配。**

### 3.2 剔除 1 条：引号风格导致的漏匹配

委托报了一条 MEDIUM：`TOUCH_MEMORIES` 契约声明了但"全代码库无任何发送点"。

**这条是误报。** 我复核发现代码里写的是**双引号**：

```ts
// entrypoints/content.ts:1303
await sendRuntimeMessage({ type: "TOUCH_MEMORIES", payload: { ids: data.ids as number[] } });
// entrypoints/content.ts:1732
await sendRuntimeMessage({ type: "TOUCH_MEMORIES", payload: { ids: result.usedMemoryIds } });
```

而 handler 也确实注册了：

```ts
// entrypoints/background/memory-handlers.ts:61
definePersistencePayloadRuntimeCommandHandler('TOUCH_MEMORIES', async (payload) => {
  await dependencies.touchMemories(payload.ids);
```

`TOUCH_MEMORIES` 是**完整的活链路**（2 个发送点 + 1 个 handler + codec + 白名单）。委托用单引号搜索，漏掉了双引号的发送点。

**这条已加入 focus 工单的自查清单**（第 6 条）：凡下"全代码库无人引用"这类结论，必须换单引号、双引号、反引号各搜一次。

---

## 四、工具自身的三个 bug（都修了）

`manifest.py` 首跑时给出了错误结果，逐一修正：

| Bug | 症状 | 修复 |
|---|---|---|
| **key 前缀吞并** | `permissions:` 把 `optional_host_permissions:` 也匹配进去，导致 `http://*/*` 被误算成"过度声明" | 正则加 `(?<![A-Za-z_])` 边界断言 |
| **注释里的所有格** | `// ...worker's fetch` 里的 `'s` 被当成字符串起始，把整段注释吞成权限项 | 抽取前先剥离行注释，且按引号状态扫描（保留 `http://` 这类字符串内的 `//`） |
| **子串匹配主机** | `*://chat.deepseek.com/*` 与 `chat.deepseek.com` 纯子串比较失败，把已声明主机误报成"未声明" | 改用 `fnmatch` 做 glob 匹配 |

另修正了 `cluster.py` 的**兜底桶误标** —— "其他"这个桶攒到 9 条、主导 67% 被打上"整体重写"，但兜底桶不是子系统，给重构建议是无意义的。现在标注为"需补全归属表"，并把 `wxt.config.ts` 等归入新增的"扩展配置"子系统。

---

## 五、问题库与聚类

共 **108 条**：58 条 finding + 50 条已证伪误报。本轮新增 3 条（DPP-056~058）+ 1 条误报（FP-050）。

聚类最终视图：

```
★★ 整体重写：自动化调度（状态机/事务错序 80%）
★  集中修复：编排入口、工具授权、侧边栏 UI、远端代理、持久化
   扩展配置：3 条（本轮新增，均为 P3 加固项）
⚠  兜底桶 6 条：需补全归属表
```

---

## 六、十一轮趋势

| 轮次 | 方法 | 净新增 | 剔除 |
|---|---|---|---|
| 第一~二轮 | 模块人工通读 / skill 四阶段 | 53 | — |
| 第三轮 | 图谱驱动提名 | 5 | 7 |
| 第四轮 | 整合工作流 | 5* | 3 |
| 第五轮 | 全量覆盖 | 9 | 4 |
| 第六轮 | 覆盖驱动 focus | 2 | 5 |
| 第七轮 | 修正覆盖度量 | 5 | 2 |
| 第八轮 | 台账 + 装箱 | 1 | 7 |
| 第九轮 | 测试缺口 + tsc 尝试 | 3 | 1 |
| 第十轮 | 根因聚类 + 重构级审查 | 2 | 4 |
| **第十一轮** | **manifest 权限 + 消息协议** | **3** | **1** |

累计约 85 条 finding。

**连续四轮净新增个位数（2 / 3 / 2 / 3）**，且本轮两条新维度中：权限比对结果完全干净、消息协议闭合。这两条是**负面结果**，但负面结果也有信息 —— 说明这个项目的配置层与契约层质量是好的。

---

## 七、局限

**① manifest 抽取靠正则解析 TS**

不是真正执行 `wxt.config.ts`。三元分支、模板字符串可能抽取不全（本轮已修 3 个 bug，不排除还有）。真正的验证需要实际构建后读产物 `manifest.json`。

**② 消息协议只比对了 type 集合，未深查 payload 形状**

委托因轮次上限，sandbox 消息通道（`SANDBOX_MESSAGE_TYPES` 7 个常量）与 main-world `window.postMessage` 的 payload 一致性**未做**。

**③ CSP 分析基于静态阅读**

`data:` iframe 绕过信标限制这条，我基于 CSP 继承规则推断，未实际构造 PoC 验证。

**④ 仍然没跑起来**

`tsc` 与 vitest 依旧无法运行（registry 受限）。这是十一轮下来最大的空白。

---

## 八、下一步

按性价比：

**1. 补真实环境验证（最高）**

完整 `npm install` + `wxt prepare` → 跑 `tsc --noEmit` 与 `vitest`，并读构建产物的真实 `manifest.json` 校验本轮推断。这是唯一能一次性验证多条 finding 的手段。

**2. 自动化调度状态机整体重写**

DPP-002/011/012/016/034 五条同源（状态机/事务错序 80%），聚类唯一达标项。

**3. 消息通道剩余部分**

sandbox 消息与 main-world postMessage 的 payload 一致性，本轮未覆盖。

**4. P1 清单（7 条，发布前应修）**

DPP-005 凭据明文落盘 / DPP-006 console 钩子写宿主 localStorage / DPP-029 telemetry promptText 落盘 / DPP-001 sync 无超时 / DPP-002 自动化删除不等待终态 / DPP-034 deadlineAt 可空 / DPP-043 导入 Skill 静默追加写入授权。
