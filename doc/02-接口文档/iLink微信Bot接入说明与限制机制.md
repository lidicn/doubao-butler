# iLink 微信 Bot 接入说明与限制机制

> 文档版本：v1.0 | 更新日期：2026-09-10
> 模块位置：`butler/integrations/ilink/`

## 一、协议概述

iLink Bot 是腾讯微信官方推出的 AI 助手连接协议（ClawBot 插件底层），2026年3月开放。
- **官方域名**：`https://ilinkai.weixin.qq.com`
- **合法性**：官方开放，合法合规，不会封号
- **支持范围**：仅支持私聊，不支持群聊
- **无需 OpenClaw**：直接对接 HTTP JSON API 即可

## 二、API 端点

| 端点 | 方法 | 说明 | 认证 |
|---|---|---|---|
| `/ilink/bot/get_bot_qrcode?bot_type=3` | POST | 获取登录二维码 | 无 |
| `/ilink/bot/get_qrcode_status?qrcode=xxx` | GET | 长轮询扫码状态（40s超时） | 无 |
| `/ilink/bot/getupdates` | POST | 长轮询获取消息（35s超时） | Header Bearer |
| `/ilink/bot/sendmessage` | POST | 发送消息 | Header Bearer |
| `/ilink/bot/getconfig` | POST | 获取 typing_ticket | Header Bearer |
| `/ilink/bot/sendtyping` | POST | 发送"正在输入"状态 | Header Bearer |

### 认证方式（关键）
```
AuthorizationType: ilink_bot_token
Authorization: Bearer {bot_token}
X-WECHAT-UIN: base64(random uint32)
Content-Type: application/json
```
**不是**把 bot_token 放在请求体里。

### 消息格式
- 接收消息字段：`from_user_id`、`message_type`（1=USER, 2=BOT）、`context_token`、`item_list`
- 文本内容：`item_list[].type=1` → `item_list[].text_item.text`
- 发送消息：需要 `context_token`（从收到的消息中获取），结构为嵌套 `{"msg": {...}, "base_info": {...}}`

## 三、限制机制（三重限制）

### 限制 1：时间窗口 — 24 小时
- 用户最后一次**主动发消息**后的 **24 小时**内，BOT 才能向该用户发消息
- 超过 24 小时用户未发消息 → `ret=-14` session timeout，需要重新扫码登录
- **不是 48 小时，是 24 小时**

### 限制 2：条数配额 — 10 条/24小时
- 24 小时内，每个用户最多 **10 条** BOT→用户消息
- **包含回复的那一条**：用户发1条 → BOT回复1条 = 已用1/10，还剩9条可主动推送
- 用户再发一条消息 → 配额**重置为10**，时间窗口重新计时
- 超出配额 → `ret=-2` rate limited

### 限制 3：发送频率 — 约 7 条/5分钟
- 每个 BOT 账号（同一 bot_token）约 **7 条/5 分钟**的全局限流
- 所有客户端共享同一 bot_token 的频率配额
- 触发后 → `ret=-2` rate limited，需等 5-10 秒重试

### 错误码汇总

| 错误码 | 含义 | 处理方式 |
|---|---|---|
| `ret=-2` | 频率限制或配额耗尽 | 等5-10秒重试（频率），或等用户主动发消息（配额） |
| `ret=-14` | session 过期（24小时无用户消息） | 重新扫码登录 |
| `errcode=-14` | 同上 | 同上 |

## 四、对豆包管家的影响

### 不受限的场景
- **微信对话**（用户主动发消息→管家回复）：完全不受限，每次用户发消息都重置配额

### 受限的场景
- **主动推送**（管家→微信）：每个用户每天只有 10 条，非常宝贵
  - 建议只推最关键的：安全警报、设备离线、紧急决策请示
  - 日常通知仍走 Bark（Bark 无条数限制）
- **长回复分段**：超过单条长度限制会分段发送，每段算 1 条配额
  - 建议单条限制提高到 4000 字，减少分段

## 五、模块结构

```
butler/integrations/ilink/
├── __init__.py      # 模块说明
├── client.py        # iLink Bot API 客户端（登录/收消息/发消息/凭证持久化）
├── bridge.py        # 消息桥接（微信消息→管家agent→微信回复，按用户维护上下文）
└── api.py           # WebUI API（7个端点：status/login/check/logout/start/stop/send）
```

## 六、启用方式

1. 在 `.env` 中添加 `ILINK_ENABLED=true`
2. 重启容器：`docker compose up -d`
3. 调用 `POST /api/ilink/login` 获取二维码
4. 用手机微信扫码并确认绑定
5. 调用 `POST /api/ilink/start` 启动消息桥接（已登录时容器启动会自动启动）
6. 登录状态持久化到 `/app/data/ilink/session.json`，重启自动恢复

## 七、WebUI API

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/ilink/status` | GET | 查看微信集成状态 |
| `/api/ilink/login` | POST | 获取登录二维码 |
| `/api/ilink/check` | POST | 检查扫码状态（长轮询） |
| `/api/ilink/logout` | POST | 退出登录 |
| `/api/ilink/start` | POST | 启动消息桥接 |
| `/api/ilink/stop` | POST | 停止消息桥接 |
| `/api/ilink/send` | POST | 手动发送消息（测试用） |

所有接口需要 `Authorization: Bearer <redacted>`。
