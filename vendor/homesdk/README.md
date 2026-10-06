# homesdk

ADM 生态（doubao-butler / AutoForge / memory-agent）的共享安全地基。

**它不是服务。** 没有端口、没有进程、没有网络往返，不占容器、不吃 token。三家 `import` 它，
像 `import requests` 一样。它存在的唯一理由是把同一个问题的多份互相矛盾的答案收成一份。

---

## 当前只有一个模块：`consent`（同意判定）

```python
from homesdk.consent import classify_answer, allows_execution

classify_answer("不要")        # -> "no"
allows_execution("不要")       # -> False
allows_execution("好的，打开吧") # -> True
allows_execution("关掉它")      # -> False（语义要上下文，判定器不替用户决定）
```

三条取值：

| 取值 | 含义 | 闸门行为 |
|---|---|---|
| `yes` | 命中精确肯定表 | 放行 |
| `no` | 含否决字 / 命中否定表 | 拒绝 |
| `unknown` | 表外的一切 | **再问一次**，不放行也不终止 |

## 设计不变式

> **子串只用来否决，永远用来不放行。**

`yes` 只能来自整句精确匹配；否决检查先于放行检查执行。这不是风格偏好，是修 bug 的方式：

| 输入 | 线上旧实现（已复核） | homesdk |
|---|---|---|
| 「不要」 | `af_executor.py:53-66` `_YES_WORDS` 含「要」，子串先判 → **yes** | `no` |
| 「别开」 | 同上，含「开」 → **yes** | `no` |
| 「不需要」 | 同上，含「要」 → **yes** | `no` |
| 「不可以」 | `dialog.py:265` 肯定表含「可以」且先判肯定 → **确认**（AutoForge 在这条上是判对的） | `no` |
| 「不要创建」 | `dialog.py:265` 肯定表含「创建」 → **确认**；AutoForge 同时因含「要」也判错 | `no` |

两家的爆炸半径不重叠，`tests/test_consent.py::TestAgainstOldImplementations` 把两份旧逻辑忠实复述进
`BLAST_RADIUS` 表逐条钉住——**任何一侧改了词表，那一组立刻变红**。

## 已知且刻意的保守性

- 「不错」「没问题」→ `no`（含否决字）。它们确实是日常口语里的肯定，但**放行侧不留例外表**是有意为之：
  多一个例外就是多一个 `af_executor.py`。代价是用户被反问一次，收益是不存在误开。
- 「关掉它」→ `unknown`。回答"要不要开灯"和"要不要关窗"意思相反，只有上文能定，判定器无权猜。
- 表外的长肯定句（「那就这么办」）→ `unknown` → 再问一次。**宁可反问，不可误开。**

## 接入方式

```bash
pip install -e E:\NAS\homesdk        # 或容器内 COPY homesdk/ + pip install --no-deps
```

**AutoForge 迁移契约**：旧代码第三值是 `"default"`。`default` 和 `unknown` 在闸门里必须走同一分支
（= 不放行）。若 `default` 分支现在会执行写操作，迁移时那一支必须同时改掉，否则换个名字继续错。

**净减少原则**：一张工单只有把**旧的词表删掉**才算完成，不是"新调用能跑"就算完成。
四份表变五份不是进展。

## 刻意不做的事

- **不做风险分级表。** 一旦 AutoForge 成为唯一 HA 写入口，butler / memory-agent 就不再自己判风险，
  `af_affordance.py:37-39` 与 `af_adapters/base.py:57-58` 的矛盾在 AF 仓内合一次即可。放进来反而多一处分发。
- **不预先建 `resolve_entity` / `snapshot` / `auth` 模块。** 等第二个真消费者出现再抽。
  `resolve_entity` 长在 9,880 行的 `autoflow/src/autoflow_gateway/gateway.py:1887` 里，摘它是外科手术不是搬运。
- **不做成第五个仓之外的服务，也不建第五个部署单元。** 版本靠三家各自的 CI 出证据。

## 验收由谁出绿灯

homesdk 的作者不给自己发合格证。本仓 CI 只证明「实现自洽」；
**adopt 是否成立由三家消费者各自的 CI 证明**（跑通了、旧表删了、闸门行为变了）。

## 现状登记

| 项 | 值 |
|---|---|
| 来源 | 本轮新写，未 fork 任何既有实现（无 fork rev 需登记） |
| 依赖 | 无 |
| 测试 | `pytest -q`，零设备、零网络 |
| 生产复核时间 | 2026-09-19（NAS `192.168.2.200` 实机确认 `af_executor.py:53-66` 缺陷仍在运行） |
