# NowVoice TTS API 文档

> 来源：逆向工程 https://nowvoice.ai/
> 版本：v1.0 | 日期：2026-09-14
> 用途：豆包管家 TTS 引擎（中文音色比 EdgeTTS 更自然）

---

## 一、API 架构概述

| 项目 | 值 |
|---|---|
| Base URL | `https://api.nowvoice.ai` |
| 认证方式 | 匿名 JWT Token（无需注册） |
| 加密方式 | AES-256-GCM（共享密钥硬编码） |
| 音色数量 | 408 个（覆盖 13+ 语言） |
| 免费额度 | 匿名用户有免费额度（具体限制未知） |

---

## 二、加密与认证

### 2.1 AES 密钥

```
AES_KEY_STRING = "k5l6vIpD59MvGFR+gC5KDSH6NdVERt7hwKPsoHzP6Ko="
AES_KEY = AES_KEY_STRING.encode('utf-8')[:32]  # 前 32 字节
```

### 2.2 加密格式

**请求加密：**
```
格式：iv(12 bytes) + ciphertext(encrypted JSON) + tag(16 bytes)
```

**响应解密：**
```
格式：iv(12 bytes) + ciphertext + tag(16 bytes)
```

### 2.3 认证流程

1. **获取匿名 Token**：`POST /u/a`
2. **刷新 Token**：`POST /u/t`
3. **调用 API**：在 Header 里带 `Authorization: Bearer <token>`

---

## 三、API 端点

### 3.1 获取匿名 Token

```
POST https://api.nowvoice.ai/u/a
Content-Type: application/octet-stream
Accept: application/octet-stream
```

**请求数据（加密前）：**
```json
{
  "referrer": "https://nowvoice.ai/zh/",
  "locationHref": "https://nowvoice.ai/zh/"
}
```

**响应数据（解密后）：**
```json
{
  "token": "eyJ…",
  "isGuest": true,
  ...
}
```

---

### 3.2 刷新 Token

```
POST https://api.nowvoice.ai/u/t
Authorization: Bearer <token>
```

**请求数据（加密前）：**
```json
{
  "token": "<current_token>"
}
```

**响应数据（解密后）：**
```json
{
  "token": "<new_token>",
  ...
}
```

---

### 3.3 验证音色

```
POST https://api.nowvoice.ai/tts/speech/verify
Authorization: Bearer <token>
```

**请求数据（加密前）：**
```json
{
  "voiceId": "afeb4759",
  "languageCode": "zh-CN"
}
```

**响应：** 空对象 `{}` 表示有效

---

### 3.4 生成语音

```
POST https://api.nowvoice.ai/tts/speech
Authorization: Bearer <token>
```

**请求数据（加密前）：**
```json
{
  "text": "你好世界",
  "voiceId": "afeb4759",
  "params": {
    "rate": 0.0,
    "volume": 0.0,
    "pitch": 0.0
  }
}
```

**响应（两种格式）：**

**格式 1：文件下载**（`X-Response-Encoding: identity`）
```
X-File: output.mp3
Content-Type: audio/mpeg
Body: <mp3 binary>
```

**格式 2：JSON 响应**
```json
{
  "id": "uuid",
  "url": "https://..."
}
```

---

## 四、常用音色 ID

### 4.1 中文音色（推荐）

| 名称 | voice_id | 性别 | 特点 |
|---|---|---|---|
| **Xiaochen（小晨）** | `afeb4759` | 男 | 沉稳男声，推荐用于播报 |
| Xiaoxiao（晓晓） | `ed76d459` | 女 | 温柔女声 |
| Yunxi（云希） | `55a0f21e` | 男 | 年轻男声 |
| Yunjian（云健） | `43b96be6` | 男 | 成熟男声 |
| Xiaoyi（小艺） | `5bc21988` | 女 | 活泼女声 |
| Yunyang（云扬） | `fc7f98b9` | 男 | 新闻男声 |
| Xiaomeng（小梦） | `e2731b46` | 女 | 知性女声 |
| Xiaomo（小墨） | `5ed3ae20` | 男 | 低沉男声 |
| Xiaohan（小涵） | `6f7ed9f0` | 女 | 亲切女声 |
| Yunze（云泽） | `9acc37a6` | 男 | 温暖男声 |

### 4.2 英文音色

| 名称 | voice_id | 性别 |
|---|---|---|
| Ava Multilingual | `7c05d1f7` | 女 |
| Andrew | `7251c0f9` | 男 |
| Jenny | `88f3533a` | 女 |
| Guy | `af00993d` | 男 |
| Aria | `7e4ce63a` | 女 |
| Davis | `b9a0ca12` | 男 |

---

## 五、使用示例

### 5.1 Python 完整示例

```python
import json
import base64
import os
import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

API_BASE = "https://api.nowvoice.ai"
AES_KEY = "k5l6vIpD59MvGFR+gC5KDSH6NdVERt7hwKPsoHzP6Ko=".encode('utf-8')[:32]

def encrypt_payload(data):
    iv = os.urandom(12)
    cipher = Cipher(algorithms.AES(AES_KEY), modes.GCM(iv))
    enc = cipher.encryptor()
    plaintext = json.dumps(data).encode('utf-8')
    ciphertext = enc.update(plaintext) + enc.finalize()
    tag = enc.tag
    return iv + ciphertext + tag

def decrypt_payload(data):
    iv = data[:12]
    ct = data[12:]
    ciphertext = ct[:-16]
    tag = ct[-16:]
    cipher = Cipher(algorithms.AES(AES_KEY), modes.GCM(iv, tag))
    dec = cipher.decryptor()
    plaintext = dec.update(ciphertext) + dec.finalize()
    return json.loads(plaintext.decode('utf-8'))

# 1. 登录获取 token
login_data = {
    "referrer": "https://nowvoice.ai/zh/",
    "locationHref": "https://nowvoice.ai/zh/"
}
encrypted_login = encrypt_payload(login_data)
resp = httpx.post(f"{API_BASE}/u/a", content=encrypted_login, 
                  headers={'Content-Type': 'application/octet-stream'})
login_result = decrypt_payload(resp.content)
token = login_result['token']
print(f"Token: {token[:50]}...")

# 2. 生成语音
tts_data = {
    "text": "你好世界！这是NowVoice API的测试。",
    "voiceId": "afeb4759",  # Xiaochen
}
encrypted_tts = encrypt_payload(tts_data)
resp = httpx.post(f"{API_BASE}/tts/speech", content=encrypted_tts,
                  headers={
                      'Content-Type': 'application/octet-stream',
                      'Authorization': f'Bearer {token}'
                  })

# 3. 处理响应
if resp.headers.get('X-Response-Encoding') == 'identity':
    # 文件下载
    with open('output.mp3', 'wb') as f:
        f.write(resp.content)
    print("音频已保存到 output.mp3")
else:
    # JSON 响应
    result = decrypt_payload(resp.content)
    print(f"音频 URL: {result.get('url')}")
```

### 5.2 豆包管家配置

**环境变量：**
```env
NOWVOICE_TOKEN=eyJ…  # 可选，不填则自动匿名登录
```

**TTS 调用：**
```python
from butler.api.tts_routes import ...

# 调用 TTS
POST /api/tts/speak
{
  "room": "客厅",
  "text": "你好世界",
  "backend": "nowvoice",
  "voice": "afeb4759"  # Xiaochen
}
```

---

## 六、错误处理

| 错误码 | 说明 | 解决方案 |
|---|---|---|
| `HTTP 401` | Token 过期 | 重新登录或刷新 token |
| `HTTP 429` | 限流 | 等待后重试，或降低调用频率 |
| `HTTP 500` | 服务器错误 | 重试，或检查请求格式 |
| `invalid_voice` | 音色 ID 无效 | 检查 voice_id 是否正确 |
| `text_too_long` | 文本过长 | 减少文本长度 |

---

## 七、性能与限制

| 项目 | 说明 |
|---|---|
| 响应时间 | 约 2-5 秒（取决于文本长度） |
| 最大文本长度 | 约 500 字（实测） |
| 并发限制 | 未知（匿名用户） |
| 音频格式 | MP3 |
| 采样率 | 24000 Hz（推测） |

---

## 八、和其他 TTS 对比

| TTS 引擎 | 中文自然度 | 免费 | 延迟 | 音色选择 |
|---|---|---|---|---|
| **NowVoice** | ⭐⭐⭐⭐⭐ | ✅ | 中 | 多 |
| EdgeTTS | ⭐⭐⭐ | ✅ | 低 | 中 |
| 百度 TTS | ⭐⭐⭐ | ✅ | 低 | 少 |
| kokoro（本地） | ⭐⭐ | ✅ | 高（需要 GPU） | 少 |

---

## 九、注意事项

1. **Token 自动刷新**：匿名 token 有有效期，客户端会自动处理
2. **加密是必须的**：所有请求和响应都是加密的，不能明文调用
3. **音色 ID 是 8 位十六进制**：不是人类可读的名称
4. **免费额度**：匿名用户有免费额度，但具体限制未知
5. **稳定性**：逆向 API 可能随时变化，需要定期测试
