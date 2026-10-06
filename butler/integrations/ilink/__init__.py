"""iLink Bot 微信集成（v1.9 新增）。

基于腾讯微信官方 iLink Bot 协议（ClawBot 插件底层），直接对接微信 HTTP API，
无需 OpenClaw 依赖。把微信消息路由到豆包管家 agent，实现微信 AI 助手。

iLink Bot API 端点（域名 ilinkai.weixin.qq.com）：
- POST /ilink/bot/get_bot_qrcode   获取登录二维码
- GET  /ilink/bot/get_qrcode_status  长轮询扫码状态
- POST /ilink/bot/getupdates          长轮询获取消息
- POST /ilink/bot/sendmessage         发送回复
- POST /ilink/bot/sendtyping          发送输入状态

架构：
- ILinkClient：iLink Bot API 客户端（登录/收消息/发消息）
- ILinkBridge：消息桥接（微信消息 → 豆包管家 agent → 微信回复）
- 会话管理：按微信用户独立维护对话上下文
- 凭证持久化：登录状态保存到 /app/data/ilink/session.json，重启自动恢复
"""
