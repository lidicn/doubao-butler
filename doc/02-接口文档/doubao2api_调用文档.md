# doubao2api 调用文档（管家侧）

> 整理时间：2026-09-16
> 基于实际运行版本源码（`E:\NAS\doubao2api\doubao2api\unified_server.py`）
> 适用对象：豆包管家（butler）、memory-agent（MA）等程序化调用方
> 本文档修正了旧文档中 `/v1/chat/completions` 可发图片、`keep_conversation` 为计划增强等过时描述。

---

## 1. 基本信息

| 项 | 值 |
|----|----|
| Base URL | `http://192.168.2.200:9090` |
| 鉴权 | `Authorization: Bearer <redacted>` |
| 内容类型 | `application/json` |
| 框架 | FastAPI，OpenAI 兼容格式 |

---

## 2. 核心端点速查

| 端点 | 用途 | 关键参数 |
|------|------|----------|
| `POST /v1/chat/completions` | 纯文本对话 | `keep_conversation`, `conversation_id`, `silent` |
| `POST /v1/images/analyses` ⭐ | **图片识别（必须用这个）** | `keep_conversation`, `conversation_id` |
| `POST /v1/images/generations` | 文生图/图生图 | `prompt`, `ratio` |
| `POST /v1/audio/speech` | TTS 语音合成 | `input`, `voice` |
| `POST /v1/audio/generations` | 音乐生成 | `prompt`, `lyric` |
| `POST /v1/videos/generations` | 视频生成（异步） | `prompt`, `ratio` |
| `GET /v1/models` | 模型列表 | — |
| `GET /health` | 健康检查 | 无需鉴权 |

---

## 3. 会话保留机制（重要）

### 3.1 keep_conversation 参数

| 值 | 行为 |
|----|------|
| 不传 / `false` | 回复后**自动删除**会话，豆包端看不到历史 |
| `true` | 保留会话，同步到豆包 App/网页端 |

### 3.2 conversation_id 参数

- 传了 `conversation_id` 时，自动 `keep_conversation=true`
- 不传时新建会话，响应体返回新的 `conversation_id`
- **同 conversation_id 会路由到同一账号 slot**（sticky）

### 3.3 silent 参数

`silent: true` 时不发送 system_prompt，用于"静默写入"——让回复直接出现在对话里但不暴露人设指令。

---

## 4. 视觉识别（/v1/images/analyses）⭐

### 为什么不用 /v1/chat/completions？

`/v1/chat/completions` 对 `image_url` 返回 **500 Internal Server Error**。
必须用 `/v1/images/analyses`，它专门处理图片上传+识别。

### 请求示例

```bash
curl -X POST http://192.168.2.200:9090/v1/images/analyses \
  -H "Authorization: Bearer <redacted>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "doubao-seed-1-6",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "text", "text": "这是客厅摄像头画面，简要汇报"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ..."}}
      ]
    }],
    "stream": false,
    "keep_conversation": true,
    "conversation_id": "38440360274498562"
  }'
```

### image_url 支持三种格式

| 格式 | 说明 | 推荐度 |
|------|------|--------|
| `data:image/jpeg;base64,...` | base64 内联 | ⭐ 程序化首选 |
| CDN URL（`byteimg.com/...`） | 已上传的图片 URL | 复用 |
| HTTP URL | 服务端下载后上传 | 不推荐（内网地址可能不通） |

### 响应

```json
{
  "choices": [{"message": {"content": "总体结论：安全..."}}],
  "conversation_id": "38440360274498562"
}
```

---

## 5. 纯文本对话（/v1/chat/completions）

### 请求示例

```bash
curl -X POST http://192.168.2.200:9090/v1/chat/completions \
  -H "Authorization: Bearer <redacted>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "doubao-seed-1-6",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": false,
    "keep_conversation": true,
    "conversation_id": "38440360274498562",
    "silent": true
  }'
```

### 角色对话 ID 映射（/app/data/role_state.json）

| 角色 | conversation_id |
|------|-----------------|
| gu_anheng（顾安恒/安防专员） | `38440360274498562` |
| butler（管家主角色） | `38440799895645442` |
| xiaoyue（小月） | `38440346897148674` |
| jarvis（贾维斯/书房） | `38440435372313602` |
| caesar（凯撒） | `38440300276672258` |
| luna（露娜） | `38440300318818050` |

---

## 6. 管家侧调用方式

### Python 代码（butler/integrations/doubao.py）

```python
from butler.integrations.doubao import DoubaoClient

client = DoubaoClient(settings)

# 纯文本对话
text, conv_id = await client.chat(
    "你好",
    keep_conversation=True,
    conversation_id="38440360274498562",
    silent=True,
)

# 图片识别（自动走 /v1/images/analyses）
text, conv_id = await client.vision(
    prompt="这是客厅摄像头画面，简要汇报",
    image_url="data:image/jpeg;base64,...",
    keep_conversation=True,
    conversation_id="38440360274498562",
)
```

### 管家 API 端点

| 端点 | 用途 |
|------|------|
| `POST /api/roles/{role_id}/push` | 推送文字到角色对话 |
| `POST /api/roles/{role_id}/push_image` | 推送图片到角色对话 |

```bash
# 推送图片
curl -X POST http://192.168.2.200:8095/api/roles/gu_anheng/push_image \
  -H "Authorization: Bearer <redacted>" \
  -H "Content-Type: application/json" \
  -d '{
    "image_url": "http://192.168.2.200:1984/api/frame.jpeg?src=cam_客厅",
    "prompt": "我是你的安防专员顾安恒。请简要汇报当前客厅画面"
  }'
```

---

## 7. go2rtc 摄像头取帧

### 流名列表

| 流名 | 说明 |
|------|------|
| `cam_客厅` | 客厅摄像头 |
| `cam_客厅标清` | 客厅标清流 |
| `cam_书房` | 书房摄像头 |
| `cam_小黄人` | 起居室摄像头 |

### 取帧 URL

```
http://192.168.2.200:1984/api/frame.jpeg?src=<URL编码的流名>
```

管家会自动下载并转 base64 传给 doubao2api，调用方直接传 HTTP URL 即可。

---

## 8. 已知限制

1. **图片 + keep_conversation 必须用 `/v1/images/analyses`**，`/v1/chat/completions` 发图片返回 500
2. 服务端有并发限流（bucket 信号量），撞 429 需退避重试
3. 未传 `keep_conversation` 时回复后自动删除会话
4. 多账号池 sticky 路由：同一 `conversation_id` 路由到同一账号 slot
