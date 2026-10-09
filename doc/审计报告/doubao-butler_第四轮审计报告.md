# doubao-butler 审计报告 · 第四轮

- **审计对象**：`lidicn/doubao-butler`
- **轮次**：`round-004`
- **产物**：`/data/workspace/repos/adm/projects/doubao-butler/round-004/`
- **报告日期**：2026-10-07

---

## 一句话结论

**本轮挖出 D10（TTS 取令牌无超时且异常会穿透），并首次系统核验了 README 的两条设计契约——结论是「绝不崩溃退出」基本达成、「health 不因下游失败」达成但 body 有矛盾字段。但更重要的产出是：73 条 high 里挑出来核验的 9 条，有 3 条是假阳性（全部是 fail-closed 的有意设计），以及修掉了一个潜伏多轮的工具 bug——所有「按位置传路径」的构造候选从来没成功过。**

---

## 一、确证缺陷（本轮新增）

### D10【P1·medium】TTS 取令牌无超时、无兜底，异常会穿透调用方的 try

`butler/core/tts_baidu.py`：

```python
async def _get_token(self):                       # ← 无 timeout、无 try/except
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:         # ← 无 timeout
            data = await r.json()
    self._token = data["access_token"]            # ← KeyError 会抛出

async def synthesize(self, text):
    token = await self._get_token()               # ← 43 行，在 try **之外**
    try:
        async with session.post(..., timeout=aiohttp.ClientTimeout(total=15)):
```

**两个问题叠加**：

1. **超时**：合成请求显式设了 15 秒，但第一步取令牌**没有任何 timeout**，只能等 aiohttp 默认的 **300 秒**
2. **异常穿透**：网络异常、`KeyError`（令牌端点返回错误体）都会向上抛——而 `await self._get_token()` 在 `synthesize` 的 try **外面**，异常直接穿到调用方（`butler/app.py:366`）

对照 README「TTS 三级降级链（任一级失败不得阻塞对话）」和「全局异常兜底，绝不崩溃退出」，这是一条**两处都没兜住**的路径。

（静态确证，置信度高，无需运行：同文件 58 行的合成请求有 `total=15`，取令牌没有。）

### D11【P3·low】`/api/health` 的 `online` 字段恒为 True

```python
return ok({"online": True,                                  # ← 字面常量
           "degraded": not all(comps.values()),
           "components": comps})
```

MQTT 断、调度器没起来时响应仍是 `{"online": true, "degraded": true}`。源码注释自己写着：

> 但 body ⛔ 说谎：V33 报的就是「MQTT 断了、调度器没起来，监控一片绿」

**注意：README 的契约是「`/api/health` 不因下游不可用而失败」——这条满足。** 状态码恒 200 是有意为之且正确（Dockerfile HEALTHCHECK 只认 200）。问题只在 body 的 `online` 字段，属观测语义，不影响契约达成，故定 low。

---

## 二、README 设计契约核验（本轮首次系统做）

| 契约 | 结论 | 证据 |
|---|---|---|
| 「全局异常兜底，绝不崩溃退出」 | **基本达成** | `app.py:99` `_consume` 主循环 `except Exception: logger.exception("consume error")`；`bus/mqtt_client.py:212` adm_heartbeat 有 try/except warning；`integrations/llm.py:82` 与 `ha.py:73` 循环内均有兜底。**唯一例外是 D10** |
| 「`/api/health` 不因下游不可用而失败」 | **达成** | 状态码恒 200（注释说明是有意为之），下游状态走 `degraded` + `components`。**附带发现 D11** |

---

## 三、73 条 high 抽样核验：3 条假阳性

| 位置 | 规则 | 判定 | 理由 |
|---|---|---|---|
| `modes/auto_switch.py:142` `_get_presence_snapshot` | AFS-01 / FO-01 | **假阳性** | 失败返回 `{}`，但调用方 `check_and_switch` 有 `if not presence: return result` **早退**，不误切。fail-closed 且正确 |
| `skills/conflict.py:147` `_time_ranges_overlap` | AFS-01 | **假阳性** | 解析失败返回 `True`（保守判定为重叠）⇒ 后果是**多报冲突**，不是漏报。fail-safe |
| `morning/routine.py:55` `check_and_broadcast` | FO-01 | **假阳性** | 失败返回 `False` = 不播报。fail-closed，符合预期 |

**共同点**：三条都是「读失败 → 保守/拒绝」，而规则把它们当成了「读失败 → 放行」。

这引出一个判据缺口：**静态规则看不出调用方对失败值做了什么**。同一个 `return {}` / `return False`，在有的调用点被当作"无约束放行"（AutoForge F12、本项目的 D1–D9），在有的调用点被当作"拒绝"（这三条）。分诊层需要把「调用方如何处理该返回值」作为信号，否则这类命中永远要靠人工逐条读。

---

## 四、工作流迭代：W60（本轮最重要）

### W60：修掉一个潜伏多轮的 latent bug

`core/poc_state.py` 的 `ctor_candidates()` 返回**混合形态**的候选——既有位置参数元组 `(ROOT,)`，也有关键字形式 `("kw", {...})`——而 `make()` 用 `isinstance(cand[0], str)` 来分派。

`("kw", {...})` 的 `cand[0]` 是字符串 `"kw"`，但 `(ROOT,)` 的 `cand[0]` 是路径字符串 `"/tmp/xxx"`——**也是字符串**！于是位置候选被当成关键字形式处理，接着取 `cand[1]` → **IndexError**。

**后果：所有「按位置传路径」的构造候选从来没成功过。** 这个 bug 从第一轮潜伏至今，因为 AutoForge 的状态类恰好都能走通别的分支，换到 doubao-butler（`FastRouteStore()` 零参、`RoleConversationStore(data_dir)` 位置传参）才暴露。

改法：候选改为**带标签**返回 `("pos", args)` / `("kw", dict)`，`make()` 按标签分派，不再靠 `cand[0]` 的类型猜。

**这是本项目记录的第十六次「工具静默失效」，且失效方向永远是"测不到"而非"报错"。**

### W60b / W60c

- **W60b**：非路径的必需对象形参，按类型注解尝试构造，失败退回 `_Duck()` 替身
- **W60c**：自动把 `vendor/*/src` 与 `vendor/**/*.whl`（解包后）挂进 `sys.path`。`homesdk` 要求 Python ≥3.11 而沙箱是 3.10，`pip install` 拒绝——解包绕过

**W60 后 PoC 结果**：10 目标 → **data_lost 4 / no_write 3 / unavailable 3**
（修复前：data_lost 4 / unavailable 6）

`cron_task` 三条从 `unavailable` 转为 `no_write`——**能导入了，但探针找不到公开 seeder 触发写盘**。D8/D9 已由手工构造确证，PoC 缺口如实记为 `no_write`，不冒充"已自动确证"。

---

## 五、审计结果

| 指标 | 数值 |
|---|---|
| DO 命中 | 10 |
| 状态 PoC（W60 后） | 10 目标 → data_lost 4 / no_write 3 / unavailable 3 |
| 本轮新增确证 | **D10 / D11** |
| 本轮判假阳性 | **3** |
| 台账累计 | **D1–D11** |
| 门禁 | ok=True，analyzers=33 |

---

## 六、按根因合并（更新）

| 根因 | 缺陷 | 计数 |
|---|---|---|
| 读路径的失败被当成「没有数据」 | D1 D2 D3 D4 D6 D7 D8 D9 | 8 |
| 配置面不一致 | D5 | 1 |
| 失败路径没兜住 / 无超时 | D10 | 1 |
| 观测语义矛盾 | D11 | 1 |

**8/11 仍收敛到同一句话。**

---

## 七、未覆盖 / 如实说明

- **`llm.py` / `newapi.py` / `feeder.py` 三条 PoC 仍 `unavailable`**：需要 `Settings` 实例 / `db_path` / `rt` 运行时对象，替身注入后仍报 `TypeError` / `AttributeError`。**这三条只能用静态审计，不能声称已实测**
- **`cron_task` 三条 `no_write`**：D8/D9 靠手工构造确证，非自动 PoC
- **依赖 CVE 面未扫**：pip-audit 网络受限
- **semgrep 不可用**：`OSError [Errno 27] File too large`
- **1339 条通用命中仅抽样核验 9 条**，剩余未处理
- **未核验**：RSC-02 递归 7 条、ERRH-05 超时 4 条（仅 tts_baidu 已确认）、PAT-04 模块级 env 解析 2 条、AFS-01 剩余约 12 条

---

## 八、下一轮方向

1. **补 homesdk 到环境**（Python 3.11 或改 wheel 元数据），解掉 3 条 unavailable
2. **把 D1–D9 的根因写成补丁副本**（`db-patched`），跑回归把「确证」推进到「修复并验证」
3. **核验 RSC-02 递归 7 条**（`af_bridge`、agent_routes、skills/engines 等）
4. **给分诊层加「调用方如何处理失败返回值」信号**——本轮 3 条假阳性全靠人工读出，这个信号能自动化其中大部分
5. **继续分诊剩余 high**
