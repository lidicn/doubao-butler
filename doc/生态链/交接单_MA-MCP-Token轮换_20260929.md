# 交接单：MA MCP Token 已轮换（2026-09-29）

## 背景

旧 `mcp_auth_token` 在 QA-MA-003 安全审计报告中明文泄露（DCD v4.1 路线图任务 1.3）。
MA 侧已于 2026-09-29 07:35 轮换 token 并重启服务。

## 变更内容

- **旧 token**：`FpzU-sI_nv4Y...Kztk`（43 字符，已失效）
- **新 token**：见 MA 容器内 `/data/config.json` 的 `mcp_auth_token` 字段
  （前缀 `glVwt76Jfap4...gEPf`，64 字符）

## 需要 DB/Butler 侧做的事

1. 将 DB / butler / autoflow 等所有连接 MA MCP 端点的客户端配置中的 `mcp_auth_token` 替换为新值。
2. MA MCP 端点不变：`http://192.168.2.200:8086/mcp`
3. 不更新的客户端将持续收到 `MCP 鉴权失败: Token 无效或缺失`。

## 同时变更（不影响鉴权，但需知悉）

- MQTT 巡检异常推送主题从 `butler/trigger/gu_anheng_alert` 改为 `ma/insights/security_alert`。
  DB 侧如订阅了旧主题，请改订 `ma/insights/security_alert` 或 `ma/insights/#`。

## 出口验证

- MA 健康：`curl http://192.168.2.200:8086/health` → `{"ok":true,"service":"memory-agent"}`
- 新 token 验证：用新 token 调 `POST /mcp` 应返回 200/正常 MCP 握手。

—— MA 工程卫生 v1.1.1 · 2026-09-29
