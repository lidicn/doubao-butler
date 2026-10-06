# doubao-butler 第十三轮审计：代码图谱层

> 审计对象：`lidicn/doubao-butler`（main, `836df79`）
> 本轮方法：**GitHub 找图谱化工具 → 安装 4 个 → 建图 → 按 god node（爆炸半径）选审计目标**
> 交付：工作流新增 `codemap.py`；新增 **V27（P3）**；**3 条经核实的负面结论**；**1 个第三方工具结论被证伪**；套件 40 条；全仓库 7 failed / 625 passed

---

## 0. 本轮核心结论

**图谱层的价值不在"找 bug"，在"决定下一步看哪里"。**

前十二轮所有层都是「针对某处提问」——给它一个假设、一个形状、一条不变量。但审计到第十三轮，真正的瓶颈不是"怎么验"，而是**"220 个文件里下一步看哪里"**。图谱层回答的就是这个。

本轮用它算出全仓爆炸半径排名，据此审了 3 个此前完全没读过的枢纽模块（`runtime.py` 47 入度、`api/deps.py` 42 入度、`logging_setup.py` 153 入度）。

---

## 1. 工具选型与安装（GitHub → 沙箱实测）

| 工具 | 安装 | 产出 | 可用性 |
|---|---|---|---|
| **projetmap** | ✅ | 2608 实体 / 864 关系 / 23 社区 | 可用，但为一次性产物 |
| **depcycle** | ✅ | 循环依赖检测 + HTML 可视化 | **结论不可信，已证伪** |
| code-review-graph | ✅ | 模块装上了 | 建图命令受阻，未用 |
| netimport | ❌ | — | `ModuleNotFoundError` |
| ftaudit | ❌ | — | 需 Python 3.14t，沙箱 3.10 |

沙箱仍是**出站受限**（GitHub Releases 403），只有 PyPI 可用。

---

## 2. 工作流新增：`codemap.py`

不依赖任何第三方——**自写 AST 精确解析 `butler.*` 导入**，产出：

```
模块 183  依赖边 564
循环依赖 0 条

God nodes（入度 = 被依赖数 = 爆炸半径）：
   153  butler.logging_setup
    47  butler.runtime
    42  butler.api.deps
    39  butler.config
    26  butler.store
    17  butler.skills.runner_types
    14  butler.store.db
```

零入度模块 75 个（多为 `api/*_routes`——它们经 `routes()` 聚合注册，零入度**不代表死代码**，这点必须说明，否则会误判）。

---

## 3. 最重要的负面结论：depcycle 的循环依赖报告全部证伪

**depcycle 报了 6 条循环依赖，逐条验证后 0 条成立。**

典型误报：`perception_engine.py ↔ event_stream`。追查发现 `perception_engine.py:66` 那里只是个**参数名/类型注解** `event_stream: EventStream`，并非 import。

**决定性验证**：真 import 两个模块，都能成功：

```
import butler.core.event_stream      → OK
import butler.core.perception_engine → OK
```

能 import 成功 ⇒ 不存在启动期硬循环。

**教训**：第三方循环检测工具把类型注解、参数名、字符串误判成导入。**这类结论一律要用自写 AST 或实证 import 复核**——这是本轮最有价值的方法论产出。

---

## 4. V27（P3）：Runtime 是"伪 dataclass"

**位置** `butler/runtime.py:6-53`

```python
@dataclass
class Runtime:
    settings = None      # ← 无类型注解
    state = None
    ...                  # 共 41 个这样的属性
    sse_subscribers: set = field(default_factory=set)   # ← 只有这个有注解
```

**实测后果**：

| | 结果 |
|---|---|
| `dataclasses.fields(Runtime)` | **1 个**（`sse_subscribers`），实际 42 个类属性 |
| `asdict(_RT)` | 只产出 `{'sse_subscribers': ...}`，**41 个字段静默丢失** |
| `Runtime(settings="X")` | `TypeError: unexpected keyword argument` |

**为什么定 P3 而非缺陷**：全仓核查确认**无任何代码对 Runtime 做 `asdict`/`fields`/`replace`，也无带参构造**。当前无害。

但它是陷阱——任何人按"它是 dataclass"的直觉去序列化或带参构造，就会**静默出错**（asdict 不报错，只是少给 41 个字段）。这正是本项目"兜底掩盖故障"病灶的又一例：错误表现为"数据莫名变少"，而非异常。

**修法**：给 41 个属性补注解（反测已验证补注解即可修复），或干脆去掉 `@dataclass`。

---

## 5. 两条经核实的负面结论（按 god node 审的模块）

### 5.1 认证中枢 `api/deps.py`（42 入度）—— 健壮

逐条核查：

- **未配置用户 → 默认拒绝**（`auth_disabled_allowed()` 默认 False，fail-closed，非 fail-open）
- **服务令牌用 `hmac.compare_digest`** 常数时间比较
- **密码作 Bearer 默认关闭**（`ALLOW_PASSWORD_AS_BEARER` 默认 false）
- **`bearer_locked` 先刹车再验凭证**（顺序正确，避免暴力破解）
- **限流键取 `request.client.host`**（TCP 源 IP），**不是 X-Forwarded-For** —— 不可伪造，限流有效

一个理论问题：`note_bearer_failure` 的清理只在表长 >512 时触发，且按 60s 窗口过滤——极端情况下内存可增长。但需 60 秒内 >512 个不同源 IP，家庭 NAS 内网场景不成立。定 P3。

**结论：认证面无新缺陷。** 这印证了第二轮我撤回"登录无速率限制"时的观察——这个项目的安全面确实比同类家用项目扎实。

### 5.2 第一 god node `logging_setup.py`（153 入度）—— 无新缺陷

70 行，脱敏实现正确：`format` 后再 `sub(r"\1=***")`，`\1` 捕获键名本身，替换形态 `password=***` 正确。

已知项（非新发现）：脱敏名单含裸 `key`，会命中 `monkey`/`keywords`/`room_key` → 过度脱敏损害排障。仍为 P3。

---

## 6. 十三轮元结论

| 轮次 | 方法 | 新缺陷 |
|---|---|---|
| 10 | 扩散 new-loop | 2 |
| 11 | 扩散 direct-speak | 1 |
| 12 | 属性测试 | 0（17 条通过 = 机器证明） |
| **13** | **代码图谱** | **1（P3）** |

**图谱层重新定义了"下一步看哪里"**：不再靠信号分猜，而是按**入度（爆炸半径）**排。本轮审的 3 个模块此前全未读过，其中 `runtime.py` 是全局状态中枢、`api/deps.py` 是认证中枢——这两个恰恰是"一处出错影响全站"的位置。

---

## 7. 诚实边界

1. **projetmap 是一次性产物**，未接入流水线常驻（它跑一次约数十秒，且输出 Markdown 报告而非结构化数据）。当前 `codemap.py` 是自写的轻量替代。
2. **code-review-graph 装上了但建图受阻**（沙箱 502 + 模块路径不一致），未用。
3. **零入度 ≠ 死代码**：75 个零入度模块里大量是 `api/*_routes`，经 `routes()` 聚合注册。误判它们会导致大规模误报。
4. **十一轮零进展的仍是运行时验证**：完整 lifespan、真实 MQTT、SQLite 并发。
5. 覆盖度 12.3%（27/220）。

---

## 8. 建议下一步

图谱层给出的未读枢纽还有：`config.py`(39)、`store/`(26)、`skills/runner_types.py`(17)。

但按投入产出比，我更建议转向**变异测试（mutmut）**——第十二轮装上但未用。它能回答一个当前**证据最弱**的问题：**这 625 个测试里，有多少真的在测东西？** 若有一批是"永远通过"的空测试，前面所有基于测试的结论都要打折。

---

## 9. 修复优先级（累计）

1. **V23（P0，5 分钟）**——`_run_coro` 改同 loop await（唯一能挂死进程）
2. **V18（P0，5 分钟）**——`docker_tools` async 内 `time.sleep` + 缺 import
3. **V25（P0，15 分钟）**——SQLite 发布顺序 + 写操作加锁
4. **V26（P1，10 分钟）**——anomaly 补 `device_id`、删 `priority`
5. **V24（P1，1 行）**——`await asyncio.to_thread(...)`
6. **V13（P1，30 分钟）**——15 处 `.result(timeout=30)`
7. **V21 / V22 / V7 / V20 / P0-1 / P0-3**
8. **V27（P3，10 分钟）**——Runtime 补注解或去 `@dataclass` ← 本轮新增

**累计 4 个 P0 + 11 个 P1，零修复。** 前三条合计约 25 分钟。
