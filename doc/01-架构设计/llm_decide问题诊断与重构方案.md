# llm_decide 引擎问题诊断与重构方案

> 日期：2026-09-18
> 场景：morning_lidicn_v2（人脸识别 lidicn → LLM 决定是否开电视）
> 修订：根据顾问评审修正 classify() 误判、第一轮循环根治、措辞

## 一、当前架构

```
人脸触发 → LLM第一轮（查电视+天气→生成问句）
  → ask挂起（30秒等用户回答）
  → 用户语音回答 → LLM第二轮（根据回答决定是否调工具）
```

## 二、遇到的问题

### 问题1：第二轮 LLM 条件判断不稳定

**现象**：
- 用户说"好" → LLM 有时调 tv_launch_app，有时只说"好的"不调
- 用户说"不用" → LLM 有时也调 tv_launch_app
- 同一个 prompt，不同场景结果不一致

**根因**：
1. 第二轮 LLM 没有第一轮对话历史，只看到一段文字描述
2. function calling 模型倾向于调工具——尤其 prompt 里明确提了工具名后
3. LLM 把"必须调用 tv_launch_app"当成主指令，不检查用户回答是否真的肯定
4. 对比简单工具层为什么能行：用户说"打开书房空调"→意图直接明确→LLM 直接映射到工具。llm_decide 第二轮：用户说"好"→意图需要推理→LLM 推理不稳定

### 问题2：第一轮工具调用循环

**现象**：LLM 反复调 tv_foreground/get_weather 5次，达到 max_iter=5 步数上限才停。

**根因**：ReAct 经典循环问题——LLM 查完状态后没有生成最终回复，而是继续调工具。

**根治方案**：第一轮根本不给 LLM 工具。状态查询（tv_foreground、get_weather）在确定性代码里预查，结果注入 prompt，LLM 纯文本生成问句。

### 问题3：两轮 system prompt 矛盾（已修复）

**现象**：第一轮 system 说"不要执行操作"，第二轮又说"允许调用工具"，LLM 被矛盾指令搞混。

**修复**：第二轮用独立的 system2，不再复用第一轮的 system。

### 问题4：白名单写了不存在的工具（已修复）

**现象**：白名单里写了 `tv_turn_on`，但实际工具是 `tv_launch_app`（package=com.tvcam.mytv）。

**修复**：改为 `tv_launch_app`，并在 followup prompt 中明确参数。

## 三、调研发现

对比 HomeBotAI、home-generative-agent (HGA)、OpenClaw：

| 机制 | HGA | HomeBotAI | 我们 |
|---|---|---|---|
| LLM 是否直接执行动作 | ❌ LLM只建议，Sentinel确定性执行 | ✅ LLM ReAct直接调工具 | ✅ LLM直接调工具 |
| 三层记忆 | pgvector语义记忆 | 情景+语义+程序记忆 | MA有，管家自身无 |
| 实时状态缓存 | HA WebSocket镜像 | WebSocket镜像307实体 | 无，每次工具调用才查 |
| 模型路由 | 云端/本地可选 | 云端复杂+本地简单 | 单模型 |

**关键结论**：HGA 的 Sentinel 设计——LLM 只建议，确定性规则决定是否执行。我们的架构把太多决策压给了 LLM。

## 四、重构方案

### 核心思路：LLM 只做表达，确定性代码做决策

| 层 | 谁做 | 做什么 |
|---|---|---|
| **状态查询** | 确定性代码 | tv_foreground + get_weather，不经过 LLM |
| **决策层** | 确定性代码 | 判断用户回答是肯定/否定/模糊（关键词分类器） |
| **表达层** | LLM | 生成自然问句和确认话术（纯文本，不带工具） |
| **执行层** | 确定性代码 | 调用 tv_launch_app |

### 新流程

```
人脸触发
  → 确定性代码预查：tv_foreground + get_weather
  → 确定性代码判断：电视关着+7-8点 → 需要问
  → LLM 纯文本生成问句（注入事实：电视关着、天气33度）
  → TTS 播报问句
  → ask挂起（等用户回答）
  → 用户回答
  → 确定性关键词分类器：
      NEGATIVE 命中 → negative → 不执行
      POSITIVE 命中 → positive → 执行 tv_launch_app
      都没命中 → unknown → LLM 兜底判断（埋点统计命中率）
  → 如果执行：LLM 纯文本生成确认话术
  → 如果不执行：LLM 纯文本生成简短回应
```

### 关键词分类器（修正版）

```python
# NEGATIVE 优先判断（必须放前面）
NEGATIVE = [
    # 直接否定
    "不用", "不要", "算了", "暂时不", "不用了", "不了", "先不",
    "不开", "别开", "不打开",
    # 拖延/推脱（归 negative，不立即执行）
    "等会", "等一下", "待会", "再说", "先…", "缓缓",
    "我先", "稍后", "过会",
]

# POSITIVE（只有没有 NEGATIVE 命中时才检查）
POSITIVE = [
    "好", "行", "可以", "要", "嗯", "是的", "对",
    "打开吧", "开吧", "好的",
]

def classify(answer: str) -> str:
    """返回 'positive' / 'negative' / 'unknown'。"""
    # 1. 先查否定（含拖延）
    for neg in NEGATIVE:
        if neg in answer:
            return "negative"
    # 2. 再查肯定
    for pos in POSITIVE:
        if pos in answer:
            return "positive"
    # 3. 都没命中
    return "unknown"
```

### unknown 兜底

- unknown 命中时调一次 LLM（纯文本，不带工具）判断
- 埋点统计 unknown 触发率：如果 >20%，说明关键词覆盖不够，需要补充
- 如果 unknown 很少，说明关键词方案够用

### LLM 只负责生成话术

- **问句生成**：确定性代码预查事实 → 注入 prompt → LLM 生成自然问句（不超过50字，不重复）
- **确认话术**：执行后 → LLM 生成自然确认（不超过30字）
- **简短回应**：不执行时 → LLM 生成简短回应（不超过10字）
- **unknown 兜底**：LLM 判断 yes/no（纯文本，不带工具）

### 好处

1. **确定性可预测**：同输入同输出，不依赖模型情绪波动
2. **更快**：第一轮不用 function calling，只纯文本生成
3. **更稳**：关键词优先判断否定，不会出现"不用"也调工具
4. **LLM 只做擅长的事**：生成自然语言，不做条件判断+工具调用决策
5. **埋点可观测**：unknown 命中率暴露关键词覆盖盲区

### 诚实表述

- 关键词分类不是"100%准确"，是"确定性可预测"——同输入同输出，但 ASR 噪声和口语化表达会导致误判，准确率取决于词表覆盖
- unknown 兜底仍用 LLM，不是完全不需要第二轮，而是第二轮只在模糊场景触发
- 需要实测 unknown 触发率来决定词表是否需要扩充

## 五、待实施清单

- [ ] ask.py：第二轮改为确定性关键词分类 + LLM 只生成话术
- [ ] engine.py：第一轮改为确定性代码预查状态，LLM 纯文本生成问句（不带 tools）
- [ ] mock.py：同步修改，增加关键词分类器的 mock 测试
- [ ] unknown 埋点：统计 unknown 触发率，写入日志
- [ ] 明早真实触发验证

## 六、Ask 应答通道（已实现）

ask 挂起后，根据房间自动选择应答通道：

### TV 端（客厅）
- TTS 播完 → TVPilot `/api/ask/beep` 提示音 → `/api/ask/listen` 免唤醒聆听 30 秒
- 回答通过 MQTT `tv/livingroom/wake` 捕获
- 超时自动调 `/api/ask/stop`

### 小爱音箱（备用，通用）
完整复刻 NR 版流程：
1. `button.{core}_wake_up` 唤醒小爱
2. 等 2 秒
3. `text.{core}_play_text` 注入问句（小爱自己播报，播完自动聆听）
4. 回答通过 `sensor.{core}_conversation` 捕获（xiaomi_ear 已有）

已映射房间：书房、主卧室、客厅、客厅右、Kevin房间、Emily房间、主卧室浴室、卫生间
