# 回执：DeskPilot 工具层对接

> **回复方**：豆包管家（doubao-butler，192.168.2.200:8095）
> **接收方**：DeskPilot 项目（Windows 主机，192.168.2.201:8765）
> **日期**：2026-09-08
> **关联**：`交接单_DeskPilot工具层对接.md`、TVPilot 同构对接（已完成）

---

## 总体结论

**管家侧 ReAct 闭环已就绪，即刻可进入 M2 联调。**

v1.1 已于 2026-09-08 部署到 NAS 容器（healthy），包含：可观察 ReAct 闭环（轨迹落库/观察验证器/错误码重试矩阵/步数+时间预算/逃生通道/429 退避）、TVPilot 工具层联调通过、技能系统沉淀（tool_sequence 引擎 + 意图匹配 + 失败回退）。DeskPilot 与 TVPilot 完全同构，接入路径已验证可行。

---

## Q1：ReAct 闭环何时就绪？联调时间窗口？

**已就绪，现在即可联调。**

- v1.1 部署状态：容器 Up (healthy)，真机验证通过（技能命中路径 llm_calls=0，ReAct 路径正常）
- 闭环能力：观察器（observe_fn，支持 async）、错误码重试（_extract_error_code 结构化解析）、轨迹落库（agent_traces 表 + 查询 API）、步数预算（默认 8）+ 时间预算（默认 60s）+ 逃生（escape_fn）
- TVPilot 联调已通过（搜索播放/回桌面/错误码矩阵/存档对账），DeskPilot 复用同一套接入代码模式
- **联调时间窗口**：随时。建议先做 4 个验收场景中的场景 2（音量，最简单无副作用）和场景 1（窗口管理），再做场景 3（SSH）和场景 4（失败恢复）

---

## Q2：工具 schema 格式？要不要加 /api/v1/tools/schemas？

**管家使用 OpenAI function calling JSON 格式，与 TVPilot 一致。不需要 DeskPilot 加 schema 导出端点。**

管家侧工具注册格式（`butler/core/tools.py` TOOL_SCHEMAS）：
```json
{
  "type": "function",
  "function": {
    "name": "volume_set",
    "description": "设置 Windows 主机音量（0-100）",
    "parameters": {
      "type": "object",
      "properties": {"level": {"type": "integer", "description": "音量值 0-100"}},
      "required": ["level"]
    }
  }
}
```

- 管家侧手写工具注册（与 TVPilot 同构做法），DeskPilot 的 Swagger `/docs` 已足够参考参数定义
- **如果 DeskPilot 愿意加 `GET /api/v1/tools/schemas`**，后续可以实现自动同步（管家启动时拉取并注册），但不是 M2 必须项，可放 v1.2 迭代
- 错误码与重试策略：管家侧已按交接单 §3.2 矩阵编码（`_extract_error_code` + llm.py 重试逻辑），不需要 DeskPilot 在 schema 里重复

---

## Q3：观察是否需要截图兜底？

**当前不需要。结构化观察接口已足够覆盖验证场景。**

管家策略：结构化优先，截图兜底（P2）。DeskPilot 提供的结构化观察接口非常完善：

| 想确认 |  DeskPilot 工具 |
|--------|----------------|
| 系统状态/前台窗口 | `system_status` |
| 音量 | `volume_get` |
| 窗口激活/最大化 | `windows_list` |
| 音乐播放 | `lxmusic_status` |
| SSH 会话 | `ssh_sessions` |
| 操作历史 | `traces_query` / `traces_stats` |

这些已覆盖 §七 全部 4 个验收场景的验证需求。截图涉及用户屏幕隐私，且 DeskPilot 无现有依赖，**建议暂不实现**。

如果后续结构化观察不够用（如复杂 UI 状态无法用窗口标题判断），再加 `screen_snapshot`（建议用 `mss`，最轻量，width=480 压缩）。

---

## Q4：confirmation_required 如何向用户确认？

**管家通过豆包 app 对话 + 小爱 TTS 双通道确认，不需要 DeskPilot 返回话术模板。**

实现机制：
1. ReAct 收到 `confirmation_required` 错误时，LLM 会自然生成确认话术（如"即将锁定电脑，确认吗？"），通过对话回复给用户
2. 用户下一轮回复"确认/同意/是的"后，Agent 带 `confirm=true` 重试该工具
3. 用户回复"取消/不要/算了"则直接结束，不再重试

通道优先级：
- **豆包 app 对话**（最可靠，用户可见可回复）
- **小爱 TTS**（如果用户在音箱旁，语音播报确认）

**建议 DeskPilot**：在 `confirmation_required` 的 `message` 字段里写明具体操作内容（如"该操作为危险操作：锁定电脑，需显式携带 confirm=true"），管家可以直接转述给用户，不需要 LLM 重新组织。

---

## Q5：技能草稿结构？

**DeskPilot 的 `{name, support, steps, params}` 格式可直接作为草稿核心，管家侧包装为完整技能定义。占位符建议统一为 `{{param}}`。**

管家技能系统（M3 tool_sequence 引擎）的完整格式：
```json
{
  "id": "windows_activate_maximize",
  "name": "激活并最大化窗口",
  "version": 1,
  "status": "draft",
  "source": "agent",
  "approval": "pending_review",
  "brain": {
    "engine": "tool_sequence",
    "steps": [
      {"tool": "windows_list", "args": {}},
      {"tool": "windows_activate", "args": {"title": "{{title}}"}},
      {"tool": "windows_maximize", "args": {"title": "{{title}}"}}
    ],
    "intent_keywords": ["激活窗口", "最大化窗口", "打开窗口"],
    "params": ["title"],
    "success_text": "已激活并最大化{{title}}"
  }
}
```

DeskPilot 草稿与管家格式的映射：
- `name` → 管家 `id`（小写化+下划线）和 `name`
- `steps[].tool` → 管家 `steps[].tool`（直接复用）
- `steps[].params` → 管家 `steps[].args`（字段名从 params 改为 args）
- `params` → 管家 `brain.params`（直接复用）
- `support` → 管家 `meta.support`（参考值，不影响执行）

**占位符**：管家使用 `{{param}}`（双花括号），DeskPilot 当前用 `{param}`（单花括号）。**建议 DeskPilot 统一为 `{{param}}`**，与管家一致；如果不便修改，管家侧导入时做单花括号→双花括号转换。

管家侧自动补全的字段（不需要 DeskPilot 提供）：
- `id`：从 name 生成（小写+下划线）
- `intent_keywords`：从 name 和 steps 推断（如 windows_activate → ["激活窗口","切换窗口"]）
- `success_text`：默认"已执行 N 步操作"
- `status`/`source`/`approval`：固定为 draft/agent/pending_review

---

## Q6：轨迹拉取还是推送？

**定时拉取，与 TVPilot 一致。不需要主动推送。**

- 管家侧 `agent_traces` 表已记录每次 ReAct 运行的完整轨迹（thought/action/observation/final），包含调用的 DeskPilot 工具及参数
- DeskPilot 侧 `logs/ops.jsonl` + `GET /api/v1/traces` + `GET /api/v1/traces/stats` 用于对账和问题排查，管家按需拉取
- **不需要 Webhook/MQTT 主动推送**：推送增加复杂度，且管家的 ReAct 轨迹已足够实时（每次调用即时落库）
- 对账场景：联调验收时手动拉取 `traces_query` 核对工具序列；日常运行不需要持续同步

如果后续需要实时监控 DeskPilot 侧异常（如非管家调用的操作），可以考虑 MQTT 推送，但当前不需要。

---

## Q7：生产 Token？

**建议 DeskPilot 生成安全随机 token，给管家配置到环境变量。**

- 联调期（M2）：继续使用开发 token `deskpilot-dev-token-123456`
- M2 联调通过后：DeskPilot 生成生产 token（建议 32+ 位随机字符串），通过安全渠道（如飞书/微信私聊）发给管家开发者
- 管家侧配置：环境变量 `DESKPILOT_API_TOKEN`，默认值为开发 token，生产部署时覆盖
- DeskPilot 侧配置：`config/deskpilot.yaml` 里的 api_token 字段

**不建议管家生成 token 给 DeskPilot**：token 的校验逻辑在 DeskPilot 侧，由 DeskPilot 生成和管理更合理（便于轮换和撤销）。

---

## Q8：网络可达性？

**同一局域网可达，建议 Windows 固定 IP + 防火墙放行 8765，Tailscale 作为备用。**

- 管家（NAS 192.168.2.200）→ DeskPilot（Windows 192.168.2.201:8765），同一局域网，理论可达
- **需要 DeskPilot 确认**：
  1. Windows 防火墙是否放行了 8765 端口入站连接（管家在 NAS 上，不是 localhost，必须放行）
  2. Windows 主机 IP 是否固定（或路由器 DHCP 保留），避免 IP 变化
  3. Windows 睡眠策略：交接单提到已设 `standby-timeout=0`、屏保关闭，确认夜间也能响应
- 管家侧配置：环境变量 `DESKPILOT_HTTP_URL`，默认 `http://192.168.2.201:8765`，可配置为 Tailscale 地址 `http://100.76.68.118:8765`
- **Tailscale 作为备用通道**：局域网不通时（如 Windows 切换网络/VPN），管家自动降级到 Tailscale 地址（v1.2 实现，M2 联调期先用局域网）
- 健康检查：管家启动时调 `GET /health`（免鉴权）验证可达性，不可达时 DeskPilot 工具返回 `unavailable` 错误，ReAct 按矩阵退避重试

---

## 额外要求与建议

### E1：管家侧 DeskPilot 工具接入计划（v1.2 M5）

管家侧将参照 TVPilot 接入模式，新建 `butler/integrations/deskpilot.py`：
- DeskPilotClient 类，封装 §四 首批工具（system/volume/windows/ssh/lxmusic/traces）
- 统一返回 dict，含 `ensure_*` 便捷验证方法
- config.py 加 `deskpilot_http_url` + `deskpilot_api_token`
- tools.py 注册首批 10-12 个核心工具
- 观察验证器：volume_set 后 volume_get 验证、windows_activate 后 windows_list 验证
- 测试：test_deskpilot_tools.py

预计 1-2 天完成，完成后即可进入 §七 4 场景联调。

### E2：危险操作确认的 UX 细节

`confirmation_required` 场景下，管家 ReAct 的行为：
1. LLM 收到错误后，会在回复中向用户确认（不会自动带 confirm=true 重试）
2. 用户确认后，下一轮对话中 LLM 会重新调用该工具并带 `confirm=true`
3. **建议 DeskPilot**：危险操作的 `message` 字段尽量具体（如"即将关闭电脑，30 秒后执行，确认？"），管家直接转述

### E3：技能草稿导入流程

DeskPilot `POST /api/v1/skills/mine` 产出的草稿，管家侧导入流程：
1. 管家开发者（或定时任务）调 DeskPilot `skills_mine` 获取草稿列表
2. 管家侧 `agent_skill.py` 将草稿转换为完整技能定义（补 id/intent_keywords/success_text）
3. 存到 `data/skills/agent/` 目录，approval=pending_review
4. 人工审核后改 approval=approved + status=enabled，即可被意图匹配命中
5. 执行结果回写 success_count/fail_count（v1.2 M6）

---

## 联调准备清单（DeskPilot 侧）

进入 M2 联调前，请确认：
- [ ] Windows 防火墙放行 8765 入站
- [ ] Windows IP 固定（192.168.2.201）
- [ ] `GET /health` 从 NAS 可达（`curl http://192.168.2.201:8765/health`）
- [ ] 开发 token `deskpilot-dev-token-123456` 已配置
- [ ] 危险操作二次确认闸已开启（`system.dangerous_ops.require_confirm=true`）
- [ ] `logs/ops.jsonl` 正常写入

---

*回复：豆包管家（脑）开发者 | 2026-09-08*
