# doubao-butler 审计报告 · 第二轮

- **审计对象**：`lidicn/doubao-butler`
- **轮次**：`round-002`
- **产物**：`/data/workspace/repos/adm/projects/doubao-butler/round-002/`
- **报告日期**：2026-10-07

---

## 一句话结论

**推翻第一轮的一个判断，并确证 3 项新缺陷。最重要的发现是 D6：`fast_routes.py` 源码注释明确写着「⛔ 在这里 `_save()`：盖回去＝抹掉现场」，而 `match()` 每次命中都 `_save()` 全量写回——开发者防住了这条路径，写操作却发生在另一条路径上。这与 AutoForge 的 F1 是同一个错误：护栏装在了不会被走到的地方。**

---

## 一、推翻第一轮结论：fast_routes「做对了」是误判（D6）

第一轮我读 `_load()` 时看到这段注释，判定它做对了：

```python
except Exception as e:
    # 表行 94 T-04：回退空列表＝快速路由永久静默失效 ⇒ 兜底成内置规则。
    # ⛔ 在这里 _save()：盖回去＝抹掉现场，人工无从判断丢了哪些用户自加规则。
    logger.error("fast_routes.json unreadable, fallback to builtin rules: %s", e)
    self.routes = self._builtin()
```

但写操作不在 `_load()` 里，在 `match()` 里：

```python
def match(self, text):
    for rule in self.routes:
        if any(k in t for k in rule.get("keywords", [])):
            rule["hits"] = rule.get("hits", 0) + 1
            rule["last_hit"] = time.time()
            self._save()          # ← 每次命中都全量写回
            return rule
```

**实测复现**：

| 步骤 | 结果 |
|---|---|
| 播种用户自加规则 `user-1` / `user-2` | 盘上有 |
| 损坏 fast_routes.json | 坏文件在，现场保留 |
| `_load()` | 降级为内置 6 条（注释生效，未写回）✓ |
| **`match("现在几点")`**（命中内置 current_time） | **盘上被内置 6 条完整覆盖** |
| user-1 / user-2 / 损坏字节 | **全部消失** |

用户只要说一句"现在几点"，唯一可人工恢复的坏文件就被抹掉，用户自加规则永久丢失。**护栏装在 `_load()`，写发生在 `match()`——注释防的那条路根本没有写操作。**

这和 AutoForge F1 是同型错误：F1 是无预算的 `_check_vars` 排在有预算的 `_check_expr` 前面，护栏永远等不到；第十轮我自己写补丁时也犯过一次（`barrier()` 装在不被走到的写盘方法上）。**这是本项目第十次遇到"护栏位置错"这一族。**

---

## 二、确证缺陷

### D4【P0·high】别名库被静默清零

`butler/core/aliases.py`：

```python
def _load(self):
    try:    self.aliases = json.loads(...)
    except Exception as e:
        logger.warning("Failed to load aliases: %s", e)
        self.aliases = {}                      # ← 读侧清零

def learn(...):
    with _lock:
        ...
        self._save()                           # ← 全量覆盖
```

**实测**：播种 `客厅灯` / `卧室空调` → 损坏 → `_load()` 后 `{}` → `learn("打开客厅的灯", ...)` → 盘上只剩 `['打开客厅的灯']`，两条历史映射全丢。

**后果比 D1/D2 更糟**：别名库是「用户说过的话 → 真实 entity_id」的自学习映射（口语控设备的核心），**丢失后无法通过配置界面重建**——只能让用户重新教一遍。

源码注释自己写着"下次 `_load` 静默把别名库清零"，开发者清楚这个后果，但只做了 tmp+replace（防截断），没防读失败。

### D5【P1·high】role_state.json 双路径分叉，DATA_DIR 非默认时手机端消息全被拒

```
roles/state.py:20        self.path = Path(data_dir) / "role_state.json"      ← 受 DATA_DIR 控制
doubao_webhook.py:60     state_path = Path("/app/data/role_state.json")      ← 硬编码
```

用户按文档把 `DATA_DIR` 指到 NAS 挂载点后，**两个文件变成两个不同的文件**：写手写 A，读取方读 B ⇒ `_conversation_to_role()` 恒返回 `None` ⇒ `doubao_webhook.py:513-516`：

```python
if not role_id:
    return {"ok": False, "error": "unknown_conversation", ...}
```

**手机豆包 App 发来的消息，除一个硬编码 CID 外全部被丢弃。** 失败方向反直觉：不是报错崩溃，是消息静默消失。

同类硬编码共 **10 处**：`deps.py:34` sessions.json、`push_routes.py:16` skills/user、`core/aliases.py:14`、`core/fast_routes.py:26`、`core/self_evolve.py:15` butler.db、`config.py:105` new-api.db、`ilink/bridge.py:42`、`ilink/client.py:44`、`triggers/audit.py:58` push_guard.db。

（本条为**静态取证**，未端到端实测——环境缺 starlette。）

### D1 / D2 / D3（第一轮确证，仍 open）

见第一轮报告：`config.json`、 `role_state.json`、`devices.json`。

---

## 三、工作流迭代：W51 出站规则泛化

第一轮 `outbound_guard_defects` **完全失效**——规则只认 `urllib.request.urlopen`，而本项目**全部用 httpx**（12+ 处出站调用）。

W51 已把规则泛化到 httpx / requests / aiohttp / urllib / urllib3 / http.client 全家族，并新增 `follow_redirects=True` 未重校验检测（`kind` 字段：`no-host-guard` / `follow-redirect-no-recheck` / `scheme-only`）。

| 轮次 | 命中 |
|---|---|
| round-001 | **0**（规则不适用） |
| round-002（W51 后） | **57**（no-host-guard 53 / follow-redirect-no-recheck 3 / scheme-only 1） |

**SSRF 护栏确证为 fail-open**（第一轮完全没扫到）：

- `cron_validator.validate_api_url` 只拦**字面 IP**；域名走 `except ValueError: pass` 放行
- 实测：`localhost`、`localtest.me`、`127.0.0.1.nip.io`、`homeassistant.local` **全部放行**，无 DNS 解析
- 更关键：`validate()` 主流程**从不调用** `validate_api_url`——保存期对 `api.url` **零校验**

---

## 四、一个假阳性，值得记

`CONC-03` 报 `docker_tools.py:108` "async 函数里用 `time.sleep()` 阻塞事件循环"。实际代码是：

```python
time.sleep(2) if False else None  # 不 sleep，让调用方观察
```

**死代码，永不执行。** 静态分析只看语法不看 `if False` 求值 ⇒ 误报。已记录为假阳性。

---

## 五、审计结果

| 指标 | 数值 |
|---|---|
| 原始命中 | **1370**（去重 1339） |
| high | **73**（第一轮 71） |
| 确证缺陷 | **D1–D6** 共 6 项（high 5 / low 1） |
| 出站规则（W51 后） | **57** |

high 分布：`AF-AST-MIXED-RETURN` 27 · `AFS-01-silent-failure` 20 · `RSC-02-recursion-no-budget` 7 · `ERRH-05-no-timeout` 4 · `DO-03` 2 · `FO-01` 2 · `PAT-04` 2

**1339 条中绝大多数仍未分诊。** 本轮人力集中在：修 W51、确证 D4/D5/D6、推翻第一轮误判。

---

## 六、工具状态

| 项目 | 状态 |
|---|---|
| `outbound_guard_defects` | ✅ **已修（W51）**，0 → 57 |
| `destructive_overwrite_defects` | ✅ 已修（W50，第一轮） |
| `external_tool_defects` | ✅ 已修（W49，第一轮） |
| `resource_defects` | ⚠️ 不适用——不覆盖 MQTT 长连接 / SQLite / httpx 连接池 |
| `semgrep` | ❌ 安装失败（`OSError [Errno 27] File too large`） |

全量扫描时 `registry.py` 主流程触发沙箱 502，已改为**逐分析器补跑**绕过，24 个分析器产物齐全。

---

## 七、下一轮方向

1. **分诊 1339 条**（high 73 优先）
2. **验证 SSRF 护栏 fail-open 的端到端后果**——`validate()` 不校验 URL，能否真造成内网穿透
3. **ERRH-05 无超时 4 处实测**：`tts_baidu.py:28` 已确认 `aiohttp.ClientSession()` 与 `session.get()` 均无 timeout（TTS 链路），需确认上层是否有兜底
4. **第一轮遗留的 7 个风险面**：MQTT 重连、TTS 三级降级、SQLite WAL 并发、bigram 7 天窗口、DND 时区、HA/Bark 超时、persona 热重载竞态
5. **README 两条设计契约实测**：「绝不崩溃退出」「`/api/health` 不因下游不可用而失败」——health 端点已确认实现正确

---

## 八、本轮最该记住的一条

**第一轮的"fast_routes 做对了"结论，是读注释得出的，不是实测得出的。**

注释说"不要在这里 `_save()`"，我据此判定它有防护。但注释描述的是 `_load()` 这条路径的意图，**防护是否生效取决于写操作发生在哪**——而写在 `match()` 里。

推论：**源码注释是分诊的强信号，但不能替代实测。** 这也修正了我在 AutoForge 第五轮定的"缓释标记答非所问"信号——该信号能识别出"注释说的防护与缺陷形态不匹配"，但它识别不出"注释说的防护装在了错误的路径上"。后者只能靠实测。
