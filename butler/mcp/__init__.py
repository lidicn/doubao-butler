"""薄 MCP 适配层（v1.8 P0-7）。

做薄做窄，只服务于"外部 agent 编写管家实时决策触发规则"这个核心需求。
不搞大而全，不实现完整 MCP 协议的所有扩展。

设计原则：
1. Schema 从 core/tools.py 自动生成，不手写第二套
2. 鉴权/审计复用管家现有体系，不另建
3. 高风险操作 Server 侧硬编码授权需 confirm=true
4. HTTP 传输为主（stdio 可选，后续版本）
5. 只暴露触发规则管理 + 技能执行 + Bark 推送，不暴露设备控制

模块结构：
- server.py：MCP Server 核心（JSON-RPC 处理、能力发现、工具调用）
- schema_gen.py：从 core/tools.py 自动生成 MCP Tool Schema
- auth.py：Token 鉴权（复用管家现有 Token 体系）
- adapters/：工具适配层（把管家内部工具包装成 MCP Tool）
"""
