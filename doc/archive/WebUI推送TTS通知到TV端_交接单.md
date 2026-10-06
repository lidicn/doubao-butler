# 豆包管家 WebUI 推送 TTS 通知到 TV 端 — 交接单

> 编写日期：2026-09-02
> 更新日期：2026-09-03（TV 端已支持自定义头像，更新通知 payload 格式）
> 目标读者：豆包管家开发者
> 状态：待开发

---

## 重要更新（2026-09-03）

**TV 端已支持自定义头像功能**，无需 TV 端再修改。豆包管家推送通知时，只需在 payload 中增加 `avatar_url` 字段，指定一个可访问的 PNG/JPG 图片 URL 即可。

**头像加载失败时自动降级**：如果 `avatar_url` 无法访问或格式不支持，TV 端自动使用系统默认图标，不影响通知显示。

**支持格式**：PNG、JPG（Android BitmapFactory 支持的格式）。不支持 SVG。

---

## 一、背景与目标

豆包管家已上线，具备 edge-tts 语音合成、MQTT 事件总线、TV 端 TTS 播放能力。当前 WebUI 仅支持对话交互，缺少**主动推送通知**功能。

**目标**：在豆包管家 WebUI 中增加"推送通知"页面，用户可手动推送 TTS 语音 + 弹窗通知到 TV 端，通知需显示推送人头像。

---

## 二、现状摸底

### 2.1 已具备能力

| 能力 | 实现位置 | 状态 |
|------|----------|------|
| edge-tts 语音合成 | `butler/tts/edge_tts.py` | ✅ 已实现 |
| TTS 音频 HTTP 服务 | `butler/api/tts_routes.py` | ✅ 已实现 |
| MQTT 发布到 TV 端 | `butler/integrations/tv.py` → `PUB_TV_TTS` | ✅ 已实现 |
| TV 端 TTS 播放 | TV 端 `TTSPlayer.java` | ✅ 已实现 |
| TV 端弹窗通知 | TV 端 `TvNotifier.java` | ✅ 已实现 |
| TV 端 MQTT 命令订阅 | TV 端 `MqttManager.java` → `cmd/notify` | ✅ 已实现 |
| WebUI 基础框架 | `butler/static/index.html` (Alpine.js + Tailwind) | ✅ 已实现 |
| PWA manifest | `butler/static/manifest.webmanifest` | ✅ 已存在（需完善） |

### 2.2 TV 端已支持的 MQTT 命令

**主题**：`tv/livingroom/cmd/notify`

**载荷**：
```json
{
  "title": "通知标题",
  "content": "通知内容",
  "type": "info|warning|error",
  "duration": 8000,
  "important": false,
  "tts_url": "http://nas:8095/tts/xxx.mp3",
  "tts_volume": 80,
  "pause_media": false,
  "avatar_url": "http://nas:8095/avatars/lidicn.png"
}
```

**字段说明**：
- `title`：通知标题（必填）
- `content`：通知内容（必填）
- `type`：通知类型，决定背景颜色（info=蓝, warning=橙, error=红）
- `duration`：显示时长（毫秒），重要通知时忽略
- `important`：重要通知，需用户点击确认才关闭，不自动消失
- `tts_url`：同时播放的 TTS 音频 URL（可选）
- `tts_volume`：TTS 音量 0-100（可选）
- `pause_media`：是否暂停当前播放的视频/音乐（可选）
- `avatar_url`：推送人头像 URL（**TV 端已支持，2026-09-03 更新**）
  - 支持格式：PNG、JPG（Android BitmapFactory 支持的格式），不支持 SVG
  - 头像尺寸：建议 80x80 像素以上，TV 端会自动裁剪为圆形
  - 加载失败时自动降级到系统默认图标，不影响通知显示
  - 示例：`http://192.168.2.200:8095/avatars/doubao.png`

### 2.3 TV 端当前通知样式

- 位置：顶部居中
- 布局：左侧头像（80x80，圆形裁剪）+ 右侧标题+内容
- 标题字体：26sp
- 内容字体：20sp
- 最小宽度：650px
- 圆角：20px，白色边框 3px
- 头像：支持自定义 URL（`avatar_url` 字段），加载失败时用系统默认图标

---

## 三、待开发功能

### 3.1 WebUI 推送通知页面

**页面位置**：WebUI 新增"推送通知"标签页（或在现有对话页增加推送面板）

**功能需求**：

1. **通知表单**：
   - 标题输入框（必填）
   - 内容输入框（多行，必填）
   - 通知类型选择：info / warning / error（下拉或按钮组）
   - 重要通知开关（important）
   - 显示时长滑块（3-30秒，重要通知时禁用）
   - 同时播放 TTS 开关（默认开启）
   - TTS 音色选择（从已配置的音色列表选择）
   - TTS 音量滑块（0-100）
   - 暂停当前媒体开关（pause_media）
   - 推送人选择（下拉，从家庭成员列表选择，决定显示哪个头像）

2. **推送人头像管理**：
   - 家庭成员列表中每人可配置头像（上传图片或选择默认头像）
   - 头像存储在 `/app/data/avatars/` 目录
   - 通过 `/api/avatars/{name}.png` 提供访问
   - 默认头像：豆包管家 logo

3. **历史记录**：
   - 显示最近推送的 20 条通知
   - 包含：时间、推送人、标题、内容、类型、状态（成功/失败）
   - 可一键重发

4. **快捷模板**：
   - 预设常用通知模板（如"该喝水了"、"该吃饭了"、"出门提醒"等）
   - 用户可自定义模板

### 3.2 PWA 适配

**目标**：WebUI 可作为 PWA 安装到 iOS/Android 手机、平板、电脑桌面

**需完善**：

1. **manifest.webmanifest**：
   - `name`：豆包管家
   - `short_name`：管家
   - `icons`：提供 192x192、512x512 图标（PNG）
   - `start_url`：`/`
   - `display`：`standalone`
   - `background_color`：`#1a1a2e`
   - `theme_color`：`#16213e`
   - `orientation`：`any`

2. **Service Worker**：
   - 离线缓存基础页面
   - 推送通知（Web Push，可选）

3. **响应式布局**：
   - 手机端：单列布局，底部导航
   - 平板端：双列布局
   - 电脑端：三列布局，侧边栏导航
   - 确保所有交互元素尺寸适合触摸操作（最小 44x44px）

### 3.3 TV 端头像支持（✅ 已完成，2026-09-03）

**状态**：TV 端已支持自定义头像 URL，无需再修改。

**已实现**：
1. `TvNotifier.java` 增加 `show(title, content, type, duration, important, avatarUrl)` 重载
2. `createNotificationView` 方法支持从 URL 异步加载头像（`BitmapFactory.decodeStream`）
3. 图片加载失败时自动降级到系统默认图标
4. 圆形裁剪（`setClipToOutline(true)`）
5. `MqttManager.java` 的 `handleCommand` 已解析 `avatar_url` 字段
6. `FaceHttpServer.java` 的 `notify()` API 已支持 `avatar_url` 参数

**豆包管家只需**：在推送通知的 payload 中增加 `avatar_url` 字段，指定一个可访问的 PNG/JPG 图片 URL。

**载荷示例**：
```json
{
  "title": "豪哥，该喝水了",
  "content": "已经2小时没喝水了，起来活动一下吧",
  "type": "info",
  "duration": 10000,
  "tts_url": "http://192.168.2.200:8095/tts/abc123.mp3",
  "tts_volume": 80,
  "avatar_url": "http://192.168.2.200:8095/avatars/doubao.png"
}
```

---

## 四、后续开发方向（ doubao2api 多模态利用）

### 4.1 生成图片在电视上显示

**场景**：豆包管家根据对话内容生成图片（如"画一幅客厅的画"、"生成一张凯文的卡通头像"），推送到电视端显示。

**实现方案**：
1. 豆包管家调用 doubao2api 的图片生成接口（如 DALL-E / Seedream）
2. 生成的图片保存到 `/app/data/images/`
3. 通过 `/api/images/{id}.png` 提供访问
4. TV 端新增 `cmd/image` 命令，接收图片 URL，全屏或弹窗显示
5. 支持显示时长、关闭方式（点击/自动）

**TV 端需新增**：
- `ImageOverlay.java`：全屏图片显示
- MQTT 主题：`tv/livingroom/cmd/image`
- 载荷：`{"url": "...", "duration": 10000, "fullscreen": true}`

### 4.2 生成歌曲在电视上播放

**场景**：豆包管家根据用户需求生成歌曲（如"给凯文唱一首生日快乐歌"），在电视端播放。

**实现方案**：
1. 豆包管家调用 doubao2api 的音乐生成接口（如 SeedMusic）
2. 生成的音频保存到 `/app/data/music/`
3. 通过 `/api/music/{id}.mp3` 提供访问
4. 复用现有 `cmd/tts` 通道播放（TTSPlayer 支持任意 MP3 URL）
5. 可选：TV 端显示音乐播放器 UI（歌名、封面、进度条）

### 4.3 多模态场景理解增强

**场景**：豆包管家结合 TV 端人脸识别 + doubao2api 多模态分析摄像头画面，生成更智能的提醒。

**示例**：
- 识别到 lidicn 在电竞沙发 → 调用多模态分析画面 → "豪哥，你已经玩了2小时游戏了，休息一下眼睛吧"
- 识别到客厅很乱 → 调用多模态分析 → "豪哥，客厅茶几上有3个可乐瓶，要不要收拾一下？"

**实现方案**：
1. 豆包管家订阅 `tv/livingroom/face` 识别人
2. 调用 memory-agent 的 VLM 接口（或直接调用 doubao2api）分析摄像头截图
3. 生成个性化提醒，推送 TTS + 通知到 TV 端

---

## 五、实施优先级

| 优先级 | 功能 | 工作量 | 依赖 |
|--------|------|--------|------|
| P0 | WebUI 推送通知页面（基础表单） | 1天 | 无 |
| P0 | 推送人头像管理 + TV 端头像支持 | 1天 | TV 端配合修改 |
| P1 | PWA 适配（manifest + 响应式） | 1天 | 无 |
| P1 | 历史记录 + 快捷模板 | 0.5天 | 无 |
| P2 | 生成图片在电视显示 | 2天 | TV 端新增 ImageOverlay |
| P2 | 生成歌曲在电视播放 | 1天 | 复用 TTS 通道 |
| P3 | 多模态场景理解增强 | 3天 | memory-agent VLM |

---

## 六、TV 端 API 参考

### 6.1 MQTT 命令主题

| 主题 | 载荷 | 说明 |
|------|------|------|
| `tv/livingroom/cmd/tts` | `{"url": "...", "volume": 80}` | 播放音频 |
| `tv/livingroom/cmd/notify` | 见上文 | 弹窗通知 |
| `tv/livingroom/cmd/player` | `{"url": "...", "title": "...", "player": "auto"}` | 播放视频 |
| `tv/livingroom/cmd/reboot` | `{}` | 重启 App |

### 6.2 HTTP API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/notify` | POST | 推送通知 |
| `/api/notify/dismiss` | POST | 关闭通知 |
| `/api/tts/play` | POST | 播放 TTS |
| `/api/tts/stop` | POST | 停止 TTS |
| `/api/tts/status` | GET | TTS 状态 |
| `/api/player/play` | POST | 播放视频 |
| `/api/player/list` | GET | 已安装播放器 |

### 6.3 TV 端状态主题

| 主题 | 说明 |
|------|------|
| `tv/livingroom/status` | 在线状态（retained + LWT） |
| `tv/livingroom/face` | 人脸识别结果（1秒去重） |
| `tv/livingroom/face/fused` | 跨路融合结果 |
| `tv/livingroom/stats` | 统计数据 |

---

## 七、豆包管家如何使用（给用户的说明）

### 7.1 快速开始

1. **打开 WebUI**：浏览器访问 `http://192.168.2.200:8095`
2. **登录**：默认账号 `admin` / 密码 `admin`
3. **对话**：在对话页面直接输入文字，豆包管家会回复并通过电视扬声器朗读
4. **推送通知**：在"推送通知"页面（待开发）手动推送通知到电视

### 7.2 自动唤醒场景

豆包管家会根据以下事件自动开口：

| 触发事件 | 来源 | 示例 |
|----------|------|------|
| 人脸识别 | TV 端 MQTT | "豪哥回来啦，今天工作辛苦吗？" |
| 人体传感器 | HA | "客厅有人，要不要开灯？" |
| 小爱语音 | Node-RED | 对小爱说"管家，..." → 管家回复 |
| 定时提醒 | 内置 | "该喝水了"、"该睡觉了" |
| 场景变化 | memory-agent VLM | "客厅很乱，要不要收拾？" |

### 7.3 发声通道

| 通道 | 说明 | 优先级 |
|------|------|--------|
| 电视扬声器 | MQTT `cmd/tts`，edge-tts 语音 | 主通道 |
| 小爱音箱 | Node-RED 转 HA `tts.speak` | 备用 |
| 手机 Bark | `butler/speak/out` → Bark 推送 | 兜底 |

### 7.4 防重复机制

- 同成员 7 天内，回复文本相似度 >0.6 判定为重复，自动重新生成
- 冷却时间 600 秒（10分钟），避免频繁打扰
- 免打扰时段可配置（如夜间 23:00-7:00 不发声）

---

## 八、待确认事项

1. **推送人头像**：默认用豆包管家 logo，还是每个家庭成员用真人照片？
2. **PWA 图标**：需要设计豆包管家的 App 图标（192x192、512x512）
3. **Web Push**：是否需要支持浏览器推送通知（手机端不打开 WebUI 也能收到通知）？
4. **TV 端头像修改**：是否由 TV 端开发者配合修改 `TvNotifier.java`？还是豆包管家开发者直接提 PR？
5. **图片/音乐生成**：doubao2api 是否已支持图片生成和音乐生成接口？需要确认 API 端点和参数。

---

## 九、相关文件

| 文件 | 说明 |
|------|------|
| `butler/static/index.html` | WebUI 主页面（需增加推送通知页） |
| `butler/static/manifest.webmanifest` | PWA manifest（需完善） |
| `butler/api/tts_routes.py` | TTS 音频 HTTP 服务 |
| `butler/integrations/tv.py` | TV 端集成（MQTT 发布） |
| `butler/bus/topics.py` | MQTT 主题常量 |
| `butler/tts/edge_tts.py` | edge-tts 语音合成 |
| `butler/config.py` | 配置管理 |
| `E:\NAS\doubao-butler\doc\豆包管家_项目设计文档.md` | 项目设计文档 |

---

*交接单结束。如有疑问请联系 TV 端开发者。*
