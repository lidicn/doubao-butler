# 豆包管家 MQTT 推送 TTS 到 TV 端 — 问题排查交接单

> 编写日期：2026-09-03
> 目标读者：豆包管家开发者
> 状态：待排查修复
> 优先级：P0（核心功能不可用）

---

## 一、问题描述

豆包管家通过 MQTT 向 TV 端推送 TTS 语音播放命令时，TV 端**没有播放语音**。

**预期行为**：豆包管家发布 `tv/livingroom/cmd/tts` 消息 → TV 端收到并播放音频。
**实际行为**：消息发布后，TV 端 TTS 状态仍为 `playing: false`，没有声音。

---

## 二、TV 端已验证正常（排除 TV 端问题）

经过 TV 端开发者详细排查，**TV 端 MQTT 接收和处理逻辑完全正常**。

### 2.1 验证方法

用 Python paho-mqtt 库直接发送正确的 JSON 消息到 `tv/livingroom/cmd/tts`：

```python
import paho.mqtt.client as mqtt
import json
import time

def on_connect(client, userdata, flags, rc):
    payload = json.dumps({"url": "http://192.168.2.200:8099/test.mp3", "volume": 80})
    print("Payload:", payload)
    print("Length:", len(payload))
    result = client.publish("tv/livingroom/cmd/tts", payload, qos=1)
    print("Publish result:", result.rc)
    time.sleep(1)
    client.disconnect()

client = mqtt.Client(client_id="test-publisher")
client.username_pw_set("lidicn", "<redacted>")
client.on_connect = on_connect
client.connect("192.168.2.200", 1883, 60)
client.loop_forever()
```

### 2.2 验证结果

```
Payload: {"url": "http://192.168.2.200:8099/test.mp3", "volume": 80}
Length: 59
Publish result: 0
```

发送后立即检查 TV 端 TTS 状态：
```
GET http://192.168.2.238:8080/api/tts/status
→ {"playing": true, "volume": 80}
```

**TV 端正常播放，证明 TV 端 MQTT 接收和处理逻辑没有问题。**

### 2.3 TV 端 MQTT 配置

| 配置项 | 值 |
|--------|-----|
| Broker | `tcp://192.168.2.200:1883` |
| 用户名 | `lidicn` |
| 密码 | `<redacted>` |
| client_id | `tv-livingroom-<timestamp>` |
| 订阅主题 | `tv/livingroom/cmd/#` (QoS 1) |
| TTS 命令主题 | `tv/livingroom/cmd/tts` |
| 通知命令主题 | `tv/livingroom/cmd/notify` |

### 2.4 TV 端期望的消息格式

**主题**：`tv/livingroom/cmd/tts`

**Payload（标准 JSON）**：
```json
{
  "url": "http://192.168.2.200:8099/xxx.mp3",
  "volume": 80
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `url` | string | 是 | 音频文件的 HTTP URL，TV 端通过 MediaPlayer 播放 |
| `volume` | int | 否 | 音量 0-100，默认 80 |

**注意**：
- `url` 必须是 TV 端能访问的 HTTP URL（不能是 file:// 或相对路径）
- payload 必须是**标准 JSON 字符串**，不能有多余的换行符或特殊字符
- JSON 中的 key 必须用双引号包裹，不能用单引号

---

## 三、问题根源分析（疑似豆包管家侧）

### 3.1 之前测试中发现的 shell 转义问题（参考，非豆包管家问题）

TV 端开发者在排查时，用 `ssh + echo + docker exec -i` 方式发送测试消息，发现 JSON 结构被 shell 破坏：

```
期望: {"url":"http://192.168.2.200:8099/test.mp3","volume":80}  (长度 58)
实际: url:http://192.168.2.200:8099/test.mp3 volume:80          (长度 49)
```

花括号 `{}` 和双引号 `""` 被 shell 解释掉了，导致 TV 端 JSON 解析失败：
```
E/MqttManager: Handle command error: Value url of type java.lang.String cannot be converted to JSONObject
```

**这是测试方法的问题，不是豆包管家的问题。** 但说明 TV 端对 JSON 格式要求严格，如果 payload 不是标准 JSON，会解析失败。

### 3.2 豆包管家侧可能的问题点

#### 问题点 1：MQTT 连接是否正常？

豆包管家的 MQTT 配置：
- Broker: `192.168.2.200:1883`
- 用户名: `butler`
- 密码: `HP…`
- client_id: `butler-core`

**排查方法**：
1. 查看豆包管家容器日志，确认 MQTT 是否连接成功：
   ```bash
   docker logs doubao-butler 2>&1 | grep -i mqtt
   ```
2. 用 mosquitto_sub 订阅豆包管家发布的主题，确认消息是否发出来：
   ```bash
   docker exec mosquitto mosquitto_sub -h localhost -p 1883 -u lidicn -P <redacted> -t "tv/livingroom/cmd/#" -v
   ```
3. 在豆包管家 WebUI 触发一次 TTS 推送，看 mosquitto_sub 是否收到消息。

#### 问题点 2：Payload 格式是否正确？

豆包管家的 `TVClient.play_url()` 方法（`butler/integrations/tv.py`）：
```python
def play_url(self, url: str, volume: int | None = None) -> None:
    if self.mqtt is None:
        logger.warning("tv play_url: no mqtt client")
        return
    vol = volume if volume is not None else self.s.tts_volume
    from butler.bus.topics import PUB_TV_TTS
    self.mqtt.publish(PUB_TV_TTS, {"url": url, "volume": vol})
```

**关键问题**：`self.mqtt.publish(PUB_TV_TTS, {"url": url, "volume": vol})`

这里传入的 payload 是一个 **Python dict**，不是 JSON 字符串。

需要确认 `butler/bus/mqtt_client.py` 中的 `publish()` 方法是否会自动把 dict 序列化成 JSON 字符串。

**如果 publish() 方法没有做 json.dumps()**，那么 paho-mqtt 会把 dict 直接转成字符串，结果可能是：
```
{'url': 'http://...', 'volume': 80}
```
注意是**单引号**，不是标准 JSON 的双引号！TV 端的 JSONObject 解析器会失败。

**排查方法**：
1. 查看 `butler/bus/mqtt_client.py` 的 `publish()` 方法实现
2. 确认是否有 `json.dumps()` 处理
3. 用 mosquitto_sub 查看实际发布的 payload 内容：
   ```bash
   docker exec mosquitto mosquitto_sub -h localhost -p 1883 -u lidicn -P <redacted> -t "tv/livingroom/cmd/tts" -v
   ```
   触发一次 TTS 推送，看 payload 是 `{"url": "..."}` 还是 `{'url': '...'}`

#### 问题点 3：音频 URL 是否可访问？

TV 端通过 MediaPlayer 播放 `url` 字段指定的音频。如果 URL 不可访问，MediaPlayer 会失败。

**排查方法**：
1. 在 TV 端所在网络，用 curl 或浏览器访问豆包管家生成的音频 URL
2. 确认 URL 是完整的 HTTP URL（包含 IP/域名和端口）
3. 确认音频文件格式是 MP3（MediaPlayer 默认支持）

豆包管家的 TTS 音频服务：
- 音频目录：`/app/tts`（容器内）→ `/vol1/1000/docker/doubao-butler/tts`（NAS）
- 音频 URL 格式：`http://192.168.2.200:8095/tts/xxx.mp3`
- 需要确认豆包管家的 HTTP 服务是否正确暴露了 `/tts/` 静态文件目录

#### 问题点 4：MQTT 发布的 QoS 和主题是否正确？

**排查方法**：
1. 确认发布主题是 `tv/livingroom/cmd/tts`，不是 `tv/livingroom/cmd/tts/` 或其他变体
2. 确认发布 QoS 至少是 0（TV 端订阅 QoS 是 1，QoS 0 的消息也能收到）
3. 确认没有 retained 标志（retained 消息会在 TV 端重新连接时重复播放）

---

## 四、复现步骤

### 4.1 环境准备

1. 确保 NAS mosquitto 运行正常：`http://192.168.2.200:1883`
2. 确保 TV 端 App 运行正常：`http://192.168.2.238:8080/api/health` 返回 `{"ok": true}`
3. 确保有一个可访问的测试音频文件：`http://192.168.2.200:8099/test.mp3`

### 4.2 复现步骤

1. **打开终端，订阅 TV 端命令主题**：
   ```bash
   docker exec mosquitto mosquitto_sub -h localhost -p 1883 -u lidicn -P <redacted> -t "tv/livingroom/cmd/#" -v
   ```

2. **在豆包管家 WebUI 触发一次 TTS 推送**（或调用对话接口让管家说话）

3. **观察 mosquitto_sub 输出**：
   - 如果没有消息输出 → 豆包管家没有发布消息，问题在 MQTT 连接或发布逻辑
   - 如果有消息输出，记录 payload 内容

4. **检查 TV 端 TTS 状态**：
   ```bash
   curl http://192.168.2.238:8080/api/tts/status
   ```
   - 如果 `playing: true` → 正常工作
   - 如果 `playing: false` → TV 端没有播放，检查 payload 格式和 URL 可访问性

5. **查看 TV 端 MQTT 日志**：
   ```bash
   adb -s 192.168.2.238:5555 logcat -d MqttManager:V *:S
   ```
   - 如果有 `Command received` 日志 → TV 端收到了消息
   - 如果有 `Handle command error` 日志 → JSON 解析失败，payload 格式有问题
   - 如果没有任何日志 → TV 端没有收到消息，问题在 MQTT 发布或订阅

---

## 五、修复建议

### 5.1 必做：确认 publish() 方法正确序列化 JSON

检查 `butler/bus/mqtt_client.py` 的 `publish()` 方法，确保传入 dict 时会自动 `json.dumps()`：

```python
import json

def publish(self, topic: str, payload, qos: int = 0, retain: bool = False):
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload, ensure_ascii=False)
    elif not isinstance(payload, str):
        payload = str(payload)
    self.client.publish(topic, payload, qos=qos, retain=retain)
```

### 5.2 必做：增加发布前日志

在 `TVClient.play_url()` 中增加日志，确认发布的内容：

```python
def play_url(self, url: str, volume: int | None = None) -> None:
    if self.mqtt is None:
        logger.warning("tv play_url: no mqtt client")
        return
    vol = volume if volume is not None else self.s.tts_volume
    from butler.bus.topics import PUB_TV_TTS
    payload = {"url": url, "volume": vol}
    logger.info(f"Publishing TTS to {PUB_TV_TTS}: {payload}")
    self.mqtt.publish(PUB_TV_TTS, payload)
```

### 5.3 建议：增加 TV 端健康检查

在豆包管家启动时或定期检查 TV 端是否在线：
```python
async def check_tv_health(self) -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{self.s.tv_http_url}/api/health")
            return r.status_code == 200
    except Exception:
        return False
```

### 5.4 建议：增加 HTTP 降级通道

TV 端同时支持 HTTP API 播放 TTS：`POST http://192.168.2.238:8080/api/tts/play`

如果 MQTT 发布失败，可以降级到 HTTP API：
```python
async def play_url_with_fallback(self, url: str, volume: int = 80):
    # 先尝试 MQTT
    if self.mqtt is not None:
        self.play_url(url, volume)
        return True
    # 降级到 HTTP
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.post(
                f"{self.s.tv_http_url}/api/tts/play",
                json={"url": url, "volume": volume}
            )
            return r.status_code == 200
    except Exception as e:
        logger.warning(f"HTTP TTS fallback failed: {e}")
        return False
```

---

## 六、相关代码位置

| 文件 | 说明 |
|------|------|
| `butler/integrations/tv.py` | TV 端集成，`play_url()` 方法发布 MQTT 消息 |
| `butler/bus/mqtt_client.py` | MQTT 客户端封装，`publish()` 方法 |
| `butler/bus/topics.py` | MQTT 主题常量，`PUB_TV_TTS = "tv/livingroom/cmd/tts"` |
| `butler/tts/manager.py` | TTS 管理器，生成音频后调用 TVClient 播放 |
| `butler/tts/edge_tts.py` | edge-tts 语音合成 |

---

## 七、TV 端 API 参考（备用）

如果 MQTT 通道有问题，可以用 HTTP API 直接控制 TV 端：

### 7.1 TTS 播放

```
POST http://192.168.2.238:8080/api/tts/play
Content-Type: application/json

{
  "url": "http://192.168.2.200:8099/test.mp3",
  "volume": 80
}
```

响应：
```json
{"ok": true, "playing": true, "volume": 80, "url": "..."}
```

### 7.2 TTS 状态

```
GET http://192.168.2.238:8080/api/tts/status
```

响应：
```json
{"playing": true, "volume": 80}
```

### 7.3 弹窗通知

```
POST http://192.168.2.238:8080/api/notify
Content-Type: application/json

{
  "title": "通知标题",
  "content": "通知内容",
  "type": "info",
  "duration": 8000,
  "important": false,
  "tts_url": "http://.../xxx.mp3",
  "tts_volume": 80,
  "pause_media": false
}
```

---

## 八、联系方式

如有疑问，请联系 TV 端开发者。排查过程中可以随时用以下命令验证：

```bash
# 查看 TV 端 MQTT 日志
adb -s 192.168.2.238:5555 logcat -d MqttManager:V *:S

# 查看 TV 端 TTS 状态
curl http://192.168.2.238:8080/api/tts/status

# 订阅 TV 端命令主题
docker exec mosquitto mosquitto_sub -h localhost -p 1883 -u lidicn -P <redacted> -t "tv/livingroom/cmd/#" -v
```

---

*交接单结束。请优先排查 `mqtt_client.py` 的 `publish()` 方法是否正确序列化 JSON。*
