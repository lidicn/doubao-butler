# TV 端远场语音能力交接单

**版本**: v1.0  
**日期**: 2026-09-18  
**TV 端 App**: Arcface (com.example.arcfaceandroid)  
**IP**: 192.168.2.238  

---

## 一、能力总览

| 能力 | 技术方案 | 状态 |
|------|---------|------|
| 离线唤醒词 | 讯飞 MSC SDK IVW（离线） | ✅ 已上线 |
| 语音听写 | 讯飞 MSC SDK IAT（在线） | ⚠️ 在线，配额有限 |
| 语音合成 | EdgeTTS（TVPilot 容器） | ✅ 已上线 |
| 远场拾音 | 罗技 C920 摄像头麦克风 | ✅ 已接入 |
| 音量闪避 | 全局音量 ducking | ✅ 已上线 |
| 视频暂停 | 前台 App 检测 + 媒体键 | ⚠️ 部分可用 |
| 人脸注册备份 | TVPilot 容器自动备份 | ✅ 已上线 |

---

## 二、系统架构

```
用户语音（远场）
    ↓
罗技 C920 麦克风
    ↓
Arcface App (192.168.2.238)
    ├── WakeWordService
    │   ├── 离线唤醒词检测（"豆包管家"）
    │   ├── 在线语音听写（讯飞 IAT）
    │   └── EdgeTTS 播放（TVPilot 容器）
    └── FaceServerService
        ├── 人脸识别
        └── 人脸备份（TVPilot 容器）
    ↓
MQTT Broker (192.168.2.200:1883)
    ↓
TVPilot 容器 (192.168.2.200:8090)
    ├── 换台 /api/zap
    ├── TTS /api/tts
    └── 人脸备份 /api/face/backup
    ↓
豆包管家
```

---

## 三、MQTT 接口

### 订阅主题

| 主题 | QoS | 说明 |
|------|-----|------|
| `tv/livingroom/wake` | 1 | TV 端语音命令 |

### 发布消息格式

**语音命令消息：**
```json
{
  "type": "voice_command",
  "command": "换到中央一台",
  "timestamp": 1789660189
}
```

**说明：**
- `type`: 固定为 `voice_command`
- `command`: 语音听写识别结果（如"换到中央一台"、"湖南卫视"）
- 豆包管家收到后，通过 LLM 解析意图，调用 TVPilot 换台接口

---

## 四、TVPilot 容器 HTTP 接口

### 基础信息
- **Base URL**: `http://192.168.2.200:8090`
- **容器名**: tvpilot

### 换台接口

```http
POST /api/zap
Content-Type: application/json

{
  "channel": "湖南卫视"
}
```

**响应：**
```json
{
  "ok": true,
  "channel": "湖南卫视",
  "cost_ms": 1200
}
```

**支持的频道名：** 97 个频道（CCTV1-17、湖南卫视、浙江卫视、深圳卫视等），完整列表见 `/api/channels`。

### TTS 接口

```http
GET /api/tts?text=你好&voice=zh-CN-XiaoxiaoNeural
```

**参数：**
- `text`: 要合成的文本
- `voice`: 发音人（默认 zh-CN-XiaoxiaoNeural）

**响应：** 音频流（audio/mpeg），带 86400 秒缓存。

**常用发音人：**
- `zh-CN-XiaoxiaoNeural`（晓晓，女声，默认）
- `zh-CN-YunxiNeural`（云希，男声）
- `zh-CN-XiaoyiNeural`（晓伊，女声，活泼）

### 人脸备份接口

```http
POST /api/face/backup
Content-Type: application/json

{
  "faces": [
    {
      "name": "lidicn",
      "feature": "base64编码的人脸特征"
    }
  ]
}
```

### 人脸恢复接口

```http
GET /api/face/restore
```

**响应：**
```json
{
  "ok": true,
  "faces": [...]
}
```

---

## 五、Arcface App HTTP 接口

### 基础信息
- **Face 服务**: `http://192.168.2.238:8080`
- **A11y 服务**: `http://192.168.2.238:8081`

### 健康检查

```http
GET /api/health
```

**响应：**
```json
{
  "status": "ok",
  "engine": "ArcSoft ArcFace 3.0 (Android)",
  "registered": 5,
  "engine_ready": true
}
```

### 人脸管理

```http
GET /api/faces          # 人脸列表
POST /api/register?name=张三   # 注册人脸（上传图片）
DELETE /api/face?name=张三     # 删除人脸
POST /api/clear                 # 清空所有人脸
```

### 配置管理

```http
GET /api/config          # 查看配置（密钥脱敏）
POST /api/config         # 更新配置
Content-Type: application/json

{
  "arcsoft_app_id": "xxx",
  "arcsoft_sdk_key": "xxx",
  "mqtt_broker": "tcp://192.168.2.200:1883"
}
```

---

## 六、唤醒词配置

### 当前唤醒词
- **唤醒词**: 豆包管家
- **技术方案**: 讯飞 MSC SDK IVW（离线）
- **资源文件**: `assets/ivw/59bd0698.jet`
- **APPID**: 59bd0698

### 修改唤醒词
1. 登录讯飞开放平台控制台
2. 进入「语音唤醒」→ 自定义唤醒词
3. 修改后下载新的 `.jet` 资源文件
4. 替换 `assets/ivw/` 目录下的文件
5. 重新编译 APK

### 唤醒灵敏度
- **当前阈值**: 1200（越低越灵敏）
- **调整位置**: `WakeWordService.java` 中 `IVW_THRESHOLD` 常量
- **注意**: 阈值过低会导致误唤醒

---

## 七、语音听写（IAT）

### 当前方案
- **技术方案**: 讯飞 MSC SDK IAT（在线 WebSocket）
- **APPID**: 59bd0698
- **配额**: 免费额度有限（约几百次）

### 工作流程
1. 用户说"豆包管家" → 离线唤醒词检测成功
2. TTS 播放"在呢，请说"（EdgeTTS）
3. 开始录音，发送到讯飞 IAT 在线识别
4. 识别完成后，通过 MQTT 发布到 `tv/livingroom/wake`
5. 豆包管家收到命令，解析意图，调用换台接口

### 配额耗尽后的方案
- **方案 A**: 升级讯飞离线 IAT（需额外授权）
- **方案 B**: 改用 TVPilot 容器内的开源 ASR（如 whisper.cpp）
- **方案 C**: 只在唤醒后才用 IAT，平时不用（当前已实现）

---

## 八、TTS 语音合成

### 当前方案
- **技术方案**: EdgeTTS（TVPilot 容器）
- **发音人**: zh-CN-XiaoxiaoNeural（晓晓）
- **缓存**: 本地缓存 86400 秒，相同文本直接播放本地文件

### 工作流程
1. Arcface App 收到唤醒命令后
2. 请求 TVPilot `/api/tts?text=在呢，请说`
3. TVPilot 用 edge-tts 合成语音
4. 返回音频流，Arcface 播放
5. 播放完成后恢复音量

---

## 九、音量闪避

### 当前方案
- **技术方案**: 全局音量 ducking
- **逻辑**:
  1. TTS 播放前：把 STREAM_MUSIC 音量降到 20%
  2. TTS 播放后：恢复原音量
- **影响**: 所有声音都会变小，不只是视频

### 已知问题
- 飞牛TV、VidHub、网易爆米花等视频 App 不响应媒体键（KEYCODE_MEDIA_PAUSE）
- 只能靠全局音量闪避，体验差强人意

---

## 十、保活机制

### WakeWordService 保活
- 跟随 FaceServerService 一起启动
- FaceServerService 已有完整保活：
  - WorkManager 15 分钟周期任务
  - AlarmManager 2 分钟定时唤醒
  - 60 秒自愈巡检

### 服务启动顺序
1. 开机 → FaceServerService 启动
2. FaceServerService.onCreate() → 自动启动 WakeWordService
3. WakeWordService → 加载离线唤醒词模型
4. 开始监听麦克风

---

## 十一、人脸数据备份

### 备份机制
- **自动备份**:
  - 启动时自动备份一次
  - 注册新人脸后自动备份
- **备份目标**: TVPilot 容器 `/app/data/face_backup.json`
- **备份内容**: 所有人脸特征（base64 编码）

### 恢复机制
- **自动恢复**: 启动时如果本地人脸为空，自动从 TVPilot 恢复
- **手动恢复**: 调用 `GET /api/face/restore`

---

## 十二、故障排查

### 唤醒不了
1. 检查 ADB 是否连接：`adb connect 192.168.2.238:5555`
2. 检查 WakeWordService 是否运行：`adb shell ps | grep WakeWordService`
3. 查看日志：`adb logcat -s WakeWordService:*`
4. 降低唤醒阈值：`IVW_THRESHOLD = 1000`（更低更灵敏）

### 识别不出命令
1. 检查讯飞 IAT 配额是否用完
2. 查看日志：`adb logcat -s IATService:*`
3. 检查网络连接（IAT 需要联网）

### TTS 没声音
1. 检查 TVPilot 是否运行：`curl http://192.168.2.200:8090/api/health`
2. 检查 edge-tts 是否安装：`docker exec tvpilot pip list | grep edge-tts`
3. 手动测试：`curl "http://192.168.2.200:8090/api/tts?text=测试" -o test.mp3`

### 换台失败
1. 检查 mytv 是否在前台
2. 检查 TVPilot 日志：`docker logs tvpilot`
3. 手动测试：`curl -X POST http://192.168.2.200:8090/api/zap -d '{"channel":"CCTV1"}'`

### 人脸注册失败
1. 检查引擎状态：`curl http://192.168.2.238:8080/api/health`
2. 检查 ArcSoft 授权：`curl http://192.168.2.238:8080/api/config`
3. 查看日志：`adb logcat -s FaceServer:*`

---

## 十三、关键文件

### Arcface App
- **项目路径**: `D:\Documents\WorkSpace\TV_CAM\Arcface`
- **WakeWordService.java**: 唤醒词 + IAT + TTS + 音量闪避
- **FaceServerService.java**: 人脸服务 + 保活
- **FaceBackupClient.java**: 人脸备份到 TVPilot
- **AppConfig.java**: 配置管理（SharedPreferences）

### TVPilot 容器
- **项目路径**: `E:\NAS\TVPilot`
- **tvpilot_server.py**: 主程序（换台、TTS、人脸备份、MQTT）
- **部署方式**: SCP 到 NAS /tmp/，再 docker cp 进容器

---

## 十四、待优化项

1. **离线 IAT**: 当前在线 IAT 配额有限，后续需换成离线方案
2. **视频暂停**: 飞牛TV/VidHub/网易爆米花不响应媒体键，需找其他方案
3. **AEC 回声消除**: Android TV 不允许普通 App 捕获系统音频输出，不可行
4. **飞牛TV/VidHub 包名**: 还没确认，需要实际测试时通过无障碍服务获取
5. **节目单**: 大部分台没有节目单，需补充 EPG 数据
6. **4K 频道**: 冷启动会报错，换几个台后恢复，热启动正常

---

## 十五、豆包管家对接说明

### 已完成对接
- ✅ TV 端唤醒词「豆包管家」离线检测
- ✅ 唤醒后自动 TTS 回复「在呢，请说」
- ✅ 语音听写结果通过 MQTT 发布到 `tv/livingroom/wake`
- ✅ TVPilot 换台接口 `/api/zap`
- ✅ EdgeTTS 语音合成接口 `/api/tts`

### 管家侧需要做的
1. 订阅 `tv/livingroom/wake` 主题
2. 收到 `voice_command` 消息后，用 LLM 解析意图
3. 如果是换台意图，调用 TVPilot `/api/zap` 接口
4. 如果需要 TTS 反馈，调用 TVPilot `/api/tts` 接口

### 消息示例
**管家收到：**
```json
{
  "type": "voice_command",
  "command": "换到中央一台",
  "timestamp": 1789660189
}
```

**管家处理：**
1. LLM 解析："换到中央一台" → 意图=换台，频道=CCTV1
2. 调用换台接口：`POST http://192.168.2.200:8090/api/zap`
3. TTS 反馈：`GET http://192.168.2.200:8090/api/tts?text=好的，正在切换到中央一台`

---

**交接人**: 豆包  
**交接日期**: 2026-09-18
