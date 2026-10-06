# 个性化 mytv — 豆包管家对接文档

> 编写时间：2026-09-07
> 最后更新：2026-09-07（确认决策：包名 com.tvcam.mytv、截图复用 Arcface、MQTT 主动推送、冷启动报错记录待查）
> 对接方：doubao-butler（决策大脑）
> 项目：个性化 mytv（基于开源 yaoxieyoulei/mytv-android 二次开发）
> 包名：com.tvcam.mytv（与原版 top.yogiczy.mytv.tv 共存，不覆盖）
> 电视 IP：192.168.2.238
> mytv HTTP API 端口：10481（复用 mytv 内嵌 AndroidAsync HTTP 服务器）
> MQTT Broker：tcp://192.168.2.200:1883（账号 butler）

---

## 一、对接概述

个性化 mytv 在开源 mytv-android 基础上扩展，对外提供 HTTP API，供豆包管家查询当前观看状态、触发换台、获取截图。

**与现有方案的关系：**
- 当前换台链路：豆包管家 → MQTT `tv/livingroom/control` → zap-tv 容器 → ADB 数字键 → mytv
- 新方案（推荐）：豆包管家 → HTTP `http://192.168.2.238:10481/api/zap` → mytv 内部直接切换频道
- 两种方案并行，mytv HTTP API 优先（更快、无需 ADB、无数字键 debounce 等待），zap-tv 作为兜底

---

## 二、HTTP API 接口规范

基础 URL：`http://192.168.2.238:10481`

所有接口已设置 CORS（`Access-Control-Allow-Origin: *`），响应格式为 JSON。

### 2.1 换台接口

**POST /api/zap**

直接切换到指定频道，内部调用播放器切换源，无需数字键模拟。

**请求体：**
```json
{
  "channel": "湖南卫视",
  "request_id": "butler_20260907_120000_001"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| channel | string | 是 | 频道名，支持别名（湖南台→湖南卫视）和 cctv/央视数字归一化（cctv2→中央二台） |
| request_id | string | 否 | 请求唯一标识，响应中原样返回，用于异步回执匹配 |

**响应（成功）：**
```json
{
  "ok": true,
  "channel": "湖南卫视",
  "channel_no": 24,
  "request_id": "butler_20260907_120000_001",
  "cost_ms": 320,
  "note": "已切换到 湖南卫视（第24台）"
}
```

**响应（频道不存在）：**
```json
{
  "ok": false,
  "error": "channel_not_found",
  "message": "频道表中没有 \"某某台\"，可用 GET /api/channels 查询全部",
  "candidates": ["某某卫视", "某某台"]
}
```

**响应（模糊匹配多个）：**
```json
{
  "ok": false,
  "error": "ambiguous_channel",
  "message": "频道名模糊，匹配到多个: [中央一台, 中央二台]",
  "candidates": ["中央一台", "中央二台"]
}
```

**频道名归一化规则（mytv 内置）：**
- 别名映射：湖南台→湖南卫视、浙江台→浙江卫视、深圳台→深圳卫视 等 33+ 别名
- CCTV 数字归一化：cctv1 / CCTV-1 / 央视一套 → 中央一台
- 大小写不敏感、空格不敏感
- 模糊匹配：精确名不存在时，子串匹配唯一候选则自动切换

---

### 2.2 当前观看状态接口

**GET /api/current**

返回当前播放频道的完整信息，供豆包管家回答"现在在看什么"。

**响应：**
```json
{
  "ok": true,
  "playing": true,
  "channel": {
    "name": "湖南卫视",
    "no": 24,
    "group": "卫视",
    "url": "http://192.168.2.200:8089/hls/湖南卫视/index.m3u8",
    "url_index": 0
  },
  "epg": {
    "current": {
      "title": "快乐大本营",
      "start": "2026-09-07T20:00:00+08:00",
      "end": "2026-09-07T21:30:00+08:00",
      "progress": 0.45
    },
    "next": {
      "title": "天天向上",
      "start": "2026-09-07T21:30:00+08:00",
      "end": "2026-09-07T22:30:00+08:00"
    }
  },
  "user": {
    "name": "lidicn",
    "login_method": "face_id",
    "login_at": "2026-09-07T19:30:00+08:00"
  },
  "watch_session": {
    "started_at": "2026-09-07T20:10:00+08:00",
    "duration_s": 1800,
    "source": "voice_zap"
  },
  "player": {
    "state": "playing",
    "volume": 15,
    "muted": false,
    "resolution": "1920x1080",
    "codec": "h264"
  },
  "ts": 1757246400000
}
```

| 字段 | 说明 |
|------|------|
| playing | 是否正在播放（非暂停/非空闲） |
| channel | 当前频道信息（name/no/group/url） |
| epg | 当前和下一个节目（EPG 可用时），progress 为 0-1 的播放进度 |
| user | 当前登录用户（face_id 登录时），未登录时为 null |
| watch_session | 当前观看会话（开始时间、持续时长、切换来源） |
| player | 播放器状态（播放/暂停、音量、分辨率、编码） |

**未播放时响应：**
```json
{
  "ok": true,
  "playing": false,
  "channel": null,
  "epg": null,
  "user": { "name": "lidicn", "login_method": "face_id", "login_at": "..." },
  "ts": 1757246400000
}
```

---

### 2.3 截图接口

**GET /api/screenshot**

获取电视当前屏幕截图（JPEG）。

**实现方案（第一期决策）：** mytv 不自实现截屏，统一复用 Arcface App 的截图接口 `http://192.168.2.238:8080/api/screenshot`。该接口基于 MediaProjection API，已稳定运行，无需额外授权。

mytv 的 `/api/screenshot` 端点作为长期方案（第二期自实现 MediaProjection），第一期返回 501 提示使用 Arcface 接口。

**查询参数：**
| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| fmt | string | jpeg | 输出格式：jpeg / json（json 返回 base64 编码） |
| quality | int | 80 | JPEG 质量 1-100 |
| width | int | 0 | 缩放宽度，0 表示原始分辨率 |

**响应（fmt=jpeg，默认）：**
直接返回 JPEG 二进制，`Content-Type: image/jpeg`

**响应（fmt=json）：**
```json
{
  "ok": true,
  "format": "jpeg",
  "width": 1920,
  "height": 1080,
  "size": 123456,
  "image_base64": "/9j/4AAQSkZJRg..."
}
```

**未授权时响应：**
```json
{
  "ok": false,
  "error": "screenshot_not_authorized",
  "message": "截屏需要用户授权，请在电视上点击授权弹窗，或使用 Arcface App 的 /api/screenshot 接口"
}
```

**备选方案（第一期实际使用）：** 直接调用 Arcface App 的截图接口 `http://192.168.2.238:8080/api/screenshot`，该接口已稳定运行，无需额外授权。这是第一期的正式方案，mytv 不自实现截屏。

---

### 2.4 频道列表接口

**GET /api/channels**

返回全部频道列表，供豆包管家做频道名匹配参考。

**查询参数：**
| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| group | string | 空 | 按分组筛选（如 "卫视"、"央视"），空返回全部 |
| q | string | 空 | 搜索关键词，模糊匹配频道名 |

**响应：**
```json
{
  "ok": true,
  "count": 80,
  "groups": ["央视", "卫视", "广东", "其他"],
  "channels": [
    { "name": "深圳卫视", "no": 1, "group": "卫视" },
    { "name": "中央一台", "no": 5, "group": "央视" },
    { "name": "湖南卫视", "no": 24, "group": "卫视" }
  ]
}
```

---

### 2.5 健康检查接口

**GET /api/health**

```json
{
  "ok": true,
  "app": "mytv-personalized",
  "version": "1.0.0",
  "package": "com.tvcam.mytv",
  "uptime_s": 3600,
  "channels_loaded": 80,
  "epg_loaded": true,
  "face_id_connected": true,
  "ts": 1757246400000
}
```

---

## 三、MQTT 主动推送（核心通信方式）

mytv 内置 MQTT 客户端（Eclipse Paho，与 Arcface App 同一依赖），主动向豆包管家推送状态变化和事件。HTTP API 作为补充查询和控制通道。

**Broker**: `tcp://192.168.2.200:1883`（账号 `butler`，密码同 Arcface 配置）
**Client ID**: `mytv-livingroom`
**Topic 前缀**: `tv/livingroom/mytv`

### 3.1 mytv 发布（mytv → 豆包管家）

| Topic | QoS | Retained | 触发时机 | 说明 |
|-------|-----|----------|----------|------|
| `tv/livingroom/mytv/status` | 1 | ✅ | 启动/退出/每 60 秒 | mytv 在线状态，格式同 `/api/health` |
| `tv/livingroom/mytv/current` | 1 | ✅ | 换台时立即 + 每 30 秒 | 当前观看状态，格式同 `GET /api/current` |
| `tv/livingroom/mytv/foreground` | 1 | ✅ | 前台/后台切换时 | mytv 是否在前台（true/false），供豆包管家判断语音指令是否应路由到 mytv |
| `tv/livingroom/mytv/zap_result` | 0 | ❌ | 换台完成/失败时 | 换台结果回执（带 request_id），格式同 `/api/zap` 响应 |
| `tv/livingroom/mytv/error` | 0 | ❌ | 播放错误/源切换时 | 播放错误事件，供豆包管家感知（如"频道播放失败，已自动切换线路"） |

### 3.2 mytv 订阅（豆包管家 → mytv）

| Topic | 说明 |
|-------|------|
| `tv/livingroom/mytv/cmd/zap` | 换台命令，格式 `{"channel":"湖南卫视","request_id":"..."}`。mytv 收到后内部切换，完成后发布 `zap_result` 回执 |
| `tv/livingroom/mytv/cmd/query` | 查询命令，格式 `{"type":"current"\|"health"\|"channels","request_id":"..."}`。mytv 收到后发布对应结果到 `tv/livingroom/mytv/query_result`（带 request_id） |

### 3.3 通信流程

**换台（MQTT 方式，推荐）：**
```
豆包管家 → publish tv/livingroom/mytv/cmd/zap {"channel":"湖南卫视","request_id":"butler_xxx"}
mytv → 内部切换频道（<1秒）
mytv → publish tv/livingroom/mytv/zap_result {"ok":true,"channel":"湖南卫视","channel_no":24,"request_id":"butler_xxx"}
mytv → publish tv/livingroom/mytv/current（retained，更新当前状态）
豆包管家 → 收到 zap_result，按 request_id 匹配，回复"已切换到 湖南卫视（第24台）"
```

**换台（HTTP 方式，可选）：**
豆包管家也可直接 `POST http://192.168.2.238:10481/api/zap`，同步等待响应。适合不希望维护 MQTT 订阅的场景。

**当前状态获取（推荐 MQTT retained）：**
豆包管家订阅 `tv/livingroom/mytv/current`（retained），订阅后立即收到最新状态，后续状态变化自动推送。无需轮询。

**兜底：** 如果 mytv 离线（MQTT 无响应 + HTTP 超时），豆包管家回退到现有方案：MQTT `tv/livingroom/control` → zap-tv → ADB 数字键。

---

## 四、豆包管家侧需要做的改动

### 4.1 换台工具升级

当前 `switch_channel` 工具调用链路：
```
switch_channel → TVClient.zap() → MQTT publish tv/livingroom/control → zap-tv → ADB
```

建议改为：
```
switch_channel → HTTP POST http://192.168.2.238:10481/api/zap
  → 成功：直接返回结果
  → 失败（mytv 未运行/接口不可用）：回退到 MQTT → zap-tv 方案
```

**超时设置：** HTTP 换台超时 10 秒（内部切换通常 <1 秒，含频道预热可能 3-5 秒）。

**回复话术优化：** 拿到 `channel_no` 后回复"已切换到 湖南卫视（第24台）"，比当前只说"已切换"更友好。

### 4.2 "现在在看什么"查询

新增工具或在现有工具中增加：
```
GET http://192.168.2.238:10481/api/current
```

根据返回的 `channel.name` + `epg.current.title` 回答用户。例如：
- "正在看 湖南卫视 的 快乐大本营，已经看了 30 分钟"
- "当前在 lidicn 的个性化模式下观看 深圳卫视"

### 4.3 截图多模态识别

当用户问"电视上在演什么"时：
1. 调用 `GET /api/current` 获取频道和 EPG 信息（快速回答）
2. 如需精确画面内容，调用 `GET /api/screenshot`（或 Arcface 的 `/api/screenshot`）获取截图
3. 截图送 doubao2api 多模态识别

建议优先用 EPG 信息回答（快、准），截图作为补充（EPG 不准或用户问具体画面时）。

---

## 五、与现有 zap-tv 的共存策略

| 场景 | 推荐方案 | 说明 |
|------|----------|------|
| mytv 个性化版已安装且运行 | HTTP /api/zap | 最快最稳，直接内部切换 |
| mytv 未运行或接口超时 | MQTT → zap-tv → ADB | 兜底方案，zap-tv 会先 ensure_mytv_foreground 再注入数字键 |
| 控制其他 App（飞牛TV等） | MQTT → zap-tv | mytv API 只控制 mytv 自身，其他 App 仍由 zap-tv 控制 |
| 截图 | Arcface /api/screenshot（推荐） | mytv 截图需额外授权，Arcface 已稳定 |

**建议：** zap-tv 容器保留，作为 mytv 未运行时的兜底和其他 App 控制入口。mytv HTTP API 作为 mytv 换台的首选通道。

---

## 六、接口版本与兼容性

- API 版本通过 `/api/health` 的 `version` 字段标识
- 所有接口保持向后兼容，新增字段不影响旧调用方
- 破坏性变更会提前通知，并在 URL 中加入版本号（如 `/api/v2/zap`）
- mytv 包名计划改为 `com.tvcam.mytv`（与原版 `top.yogiczy.mytv.tv` 共存，不覆盖）

---

## 七、已确认决策（2026-09-07）

| 事项 | 决策 | 说明 |
|------|------|------|
| 包名 | `com.tvcam.mytv` | 与原版 `top.yogiczy.mytv.tv` 共存，不覆盖 |
| 截图方案 | 第一期复用 Arcface `/api/screenshot` | mytv 不自实现截屏，第二期再考虑 |
| 通信方式 | MQTT 主动推送为主 + HTTP API 补充 | mytv 内置 Paho MQTT 客户端，主动推送状态/事件 |
| 冷启动报错 | 先记录为已知问题 | 很可能是播放源（iptv_gw 转码）问题，非 mytv 代码问题，后续排查 |
| 观看历史保留 | 默认 90 天，UI 可设定 | 数据模型细节待后续讨论（见 memory-agent 对接文档） |

## 八、待确认事项（剩余）

1. **换台预热**：是否需要 mytv 内部做频道预热（提前 HTTP 请求 HLS URL 拉起 iptv_gw ffmpeg），还是依赖 iptv_gw 的按需转码？
2. **用户身份传递**：豆包管家发换台命令时是否需要带上用户身份（用于个性化推荐和观看历史关联）？还是 mytv 自己通过 face_id 登录状态确定当前用户？
3. **播放错误处理**：mytv 发布 `mytv/error` 事件时，豆包管家是否需要主动介入（如提示用户、自动切换频道），还是仅记录？
4. **前台状态用途**：`mytv/foreground` 事件是否足够？豆包管家判断语音指令路由时，是否还需要知道当前前台 App 包名（可从 Arcface 的 `tv/livingroom/state` 获取）？

---

*文档结束。如有疑问请联系 TV_CAM 开发者。*
