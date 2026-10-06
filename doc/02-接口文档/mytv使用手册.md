# myTV 使用手册（TVPilot 外挂遥控器模式）

## 当前架构

```
豆包管家 / TVPilot PWA / 语音 → HTTP API → TVPilot 服务器 → ADB 数字键 → 电信 IPTV 原生播放器（前台全屏）
                                      ↑
                              myTV 后台保活（不播放，只做壳）
```

- **播放**：电信 IPTV 原生播放器（包名 `com.android.smart.terminal.iptv`）
- **换台**：TVPilot 通过 ADB 发数字键到 IPTV
- **当前频道反馈**：TVPilot 读 IPTV 的 `SharedPreferences/iptv_prefs.xml` 里的 `Service/LastchannelID`
- **myTV**：后台运行，保活 Service，不参与播放和换台

## 设备信息

| 项目 | 值 |
|------|-----|
| 电视 ADB | 192.168.2.238:5555 |
| 电视型号 | 红米 75 寸游戏电视 |
| myTV 包名 | `com.tvcam.mytv` |
| myTV HTTP 端口 | 10482（已从 10481 改，避免和新版 mytv 冲突） |
| IPTV 包名 | `com.android.smart.terminal.iptv` |
| IPTV Activity | `com.amt.app.IPTVActivity` |
| IPTV 配置文件 | `run-as com.android.smart.terminal.iptv cat shared_prefs/iptv_prefs.xml` |
| TVPilot 地址 | http://192.168.2.200:8090 |
| TVPilot PWA | http://192.168.2.200:8090/remote |
| go2rtc | http://192.168.2.200:1984（仅米家摄像头，不用于 IPTV） |

---

## 完整 API 接口（豆包管家调用）

### 1. 换台

```
POST /api/zap
Content-Type: application/json

{"channel": "中央一台"}
```

**响应：**
```json
{
  "ok": true,
  "channel": "中央一台",
  "channel_no": 21,
  "method": "adb_digits",
  "zap_time_ms": 5000
}
```

**说明：** 传入频道名称（如"中央一台"、"湖南卫视"），TVPilot 自动映射到 IPTV 频道号，通过 ADB 发数字键换台。

---

### 2. 当前播放频道

```
GET /api/current
```

**响应：**
```json
{
  "ok": true,
  "channel": {
    "name": "深圳都市",
    "no": 2,
    "program": "午间新闻"
  }
}
```

**说明：** 返回当前 IPTV 正在播放的频道名称、台号、以及 EPG 当前节目名。通过读取 IPTV 的 SharedPreferences 实现。

---

### 3. 频道列表

```
GET /api/channels
```

**响应：**
```json
{
  "ok": true,
  "count": 97,
  "groups": [
    {"name": "广东本地", "channels": [1, 2, 3, 4, 5, 50]}
  ],
  "channels": [
    {"no": 1, "name": "深圳卫视", "group": "广东本地", "logo": ""}
  ]
}
```

**说明：** 从 M3U 文件读取，按 IPTV 真实台号排序。

---

### 4. 节目单（EPG）

```
GET /api/epg?channel=中央一台
```

**响应：**
```json
{
  "ok": true,
  "channel": "中央一台",
  "programs": [
    {"start": "06:00", "stop": "06:30", "title": "朝闻天下", "desc": ""}
  ]
}
```

**说明：** 返回指定频道今日节目单。数据源：epg.51zmt.top，缓存 6 小时。

---

### 5. 健康检查

```
GET /api/health
```

**响应：**
```json
{
  "ok": true,
  "tool": "health",
  "result": {
    "tvpilot_version": "0.7.0",
    "tv_online": true,
    "adb_connected": true,
    "foreground_package": "com.android.smart.terminal.iptv"
  }
}
```

---

## 频道号映射表

| 频道名 | IPTV 频道号 |
|--------|-------------|
| 深圳卫视 | 001 |
| 深圳都市 | 002 |
| 深圳电视剧 | 003 |
| 深圳少儿 | 004 |
| 宜和购物 | 005 |
| 中央一台 (CCTV-1) | 021 |
| 中央二台 (CCTV-2) | 022 |
| 中央三台 (CCTV-3) | 023 |
| 中央四台 (CCTV-4) | 024 |
| 中央五台 (CCTV-5) | 025 |
| 中央五台+ | 026 |
| 中央六台 (CCTV-6) | 027 |
| 中央七台 (CCTV-7) | 028 |
| 中央八台 (CCTV-8) | 029 |
| 中央九台 (CCTV-9) | 030 |
| 中央十台 (CCTV-10) | 031 |
| 中央十二台 (CCTV-12) | 033 |
| 中央十三台 (CCTV-13) | 034 |
| 中央十四台 (CCTV-14) | 035 |
| 中央十七台 (CCTV-17) | 037 |
| CCTV-11高清 | 038 |
| CCTV-15高清 | 039 |
| 国际频道 (CGTN) | 043 |
| 广东卫视 | 050 |
| 湖南卫视 | 051 |
| 浙江卫视 | 052 |
| 东方卫视 | 053 |
| 江苏卫视 | 054 |
| 北京卫视 | 055 |
| 睛彩竞技 | 260 |
| 睛彩篮球 | 261 |
| 睛彩青少 | 262 |
| 睛彩广场舞 | 263 |

---

## PWA 能力边界

### ✅ 已支持

| 能力 | 说明 |
|------|------|
| 换台 | 点击频道列表或搜索 |
| 当前频道显示 | 顶部卡片显示正在播放的频道和节目 |
| 节目单 | 左侧频道列表 + 右侧当日节目 |
| 收藏 | 标记常用频道 |
| 多屏同播 | 手机和电视同步 |
| 离线缓存 | Service Worker 缓存静态资源 |

### ❌ 不支持（PWA 限制）

| 能力 | 原因 | 替代方案 |
|------|------|----------|
| **语音唤醒** | PWA 无法后台持续监听麦克风 | 豆包管家 App / 小爱同学 |
| **推送通知** | 需要服务器 + FCM/厂商推送通道 | 暂无（内网应用） |
| **摇一摇** | DeviceMotion API 需要 HTTPS + 用户授权 | 暂无 |
| **震动反馈** | Vibration API 支持但 PWA 需用户手势触发 | 可加（点击时震动） |
| **后台运行** | 浏览器关闭后 PWA 停止 | 豆包管家 App 常驻 |

### 手机端语音入口方案

**当前：** 电视语音（小米电视自带语音）→ 直接换台

**推荐：** 豆包管家 App 内置语音按钮 → 调用 `/api/zap` 换台

**架构：**
```
手机麦克风 → 豆包管家 ASR → 意图识别 → POST /api/zap {"channel": "中央一台"}
```

---

## 故障排查

### 换台失败

1. 检查 ADB 连接：`adb -s 192.168.2.238:5555 shell echo ok`
2. 检查 TVPilot 容器：`docker ps | grep tvpilot`
3. 检查 IPTV 是否在前台：`adb shell dumpsys window | grep mCurrentFocus`
4. 检查当前频道：`curl http://192.168.2.200:8090/api/current`

### PWA 打不开

1. 检查 TVPilot 容器：`docker ps | grep tvpilot`
2. 重启 TVPilot：`docker restart tvpilot`

### 当前频道显示不对

1. 检查 IPTV 配置：`adb shell run-as com.android.smart.terminal.iptv cat shared_prefs/iptv_prefs.xml | grep LastchannelID`
2. 如果值不对，手动换台后再查

---

## 已归档方案（不再使用）

| 方案 | 状态 | 说明 |
|------|------|------|
| v1: myTV 自建 go2rtc 播放 | 已归档 | myTV 自己播放 RTSP 流，已淘汰 |
| v2: myTV HTTP API 换台 | 已归档 | myTV 后台发 keyevent，无权限失败 |
| v3: 新版 mytv-android (mytv-android/mytv-android) | 已归档 | WebView 版本，不适合原生播放 |
| **当前: TVPilot ADB 换台** | **生产** | 直接 ADB 发数字键到 IPTV |

## go2rtc 说明

go2rtc 已恢复为**米家摄像头专用**，不再用于 IPTV 流转发。
IPTV 播放由电信 IPTV 原生播放器自己处理，不经过 go2rtc。
