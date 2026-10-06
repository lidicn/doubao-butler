# 交接单：MA ↔ 豆包管家 对接「成员档案 + 在场查询」

> 提出方：豆包管家（doubao-butler, 192.168.2.200:8095）
> 接收方：memory-agent（MA, 192.168.2.200:8086）
> 日期：2026-09-03 · 优先级：高

## 背景

豆包管家要做「拟人化主动打招呼」：早/中/晚按成员作息个性化问候（含课程提醒、"今天怎么这么早"等）。
按现有分工：**眼睛与记忆归 MA，耳朵与表达归豆包管家**。管家不建感知、不建记忆库，
需要 MA 补齐 **3 个窄接口**（均为对既有能力的薄封装，改动很小）。

## 需求 1：成员档案扩展（核心）

### 现状

`members` 表已有 name / avatar / note / appearance_json / face_feature，
但**没有作息、兴趣、课程等档案字段**。

### 请求

`members` 表新增一列 `profile_json`（TEXT，自由 JSON，schema 由管家定义，MA 不解释内容），
并暴露读写 API：

```
GET  /api/members                     # 已有，响应中带上 profile_json
GET  /api/members/{id}                # 已有，响应中带上 profile_json
PATCH /api/members/{id}               # 新增：支持更新 note / profile_json
```

### profile_json 约定 schema（v1）

```json
{
  "nickname": "爱美丽",
  "school": "黄麻布学校五年级",
  "routine": {
    "weekday": {
      "wake": "07:00", "leave_morning": "07:40",
      "return_lunch": "12:10", "leave_afternoon": "13:30",
      "return_evening": ["19:00", "22:00"]
    },
    "weekend": { "wake": "08:30", "note": "上午可能有兴趣班" }
  },
  "courses": {
    "mon": ["语文", "体育", "英语", "语文", "数学", "音乐", "道法", "延时英语"],
    "tue": ["信息", "英语", "数学", "英语", "体育"],
    "wed": ["语文阅读", "数学", "数学", "语文", "体育"],
    "thu": ["数学", "数学", "数学", "体育", "语文"],
    "fri": ["数学", "英语", "劳动", "语文", "体育"]
  },
  "interests": ["画画"],
  " reminders": ["早上出门提醒带学习用品", "作业未完成时温和提醒"]
}
```

> 字段解释权归管家；MA 只做透明存取。courses 等内容由管家侧 LLM
> 从自由文本/课表文档整理生成后 PATCH 过来，MA 无需理解。

## 需求 2：在场查询 API（触发源）

### 现状

`behavior_events` 已记录每次识别结果，但管家轮询需要按人员过滤的轻量查询。

### 请求

```
GET /api/vision/presence?room=客厅&minutes=10
→ { "ok": true,
    "items": [ { "name": "Emily", "via": "arcface", "confidence": 0.9,
                 "last_seen": "2026-09-03T18:32:10", "room": "客厅" } ] }
```

- 语义：最近 N 分钟内各成员最后被识别到的时间（含 via=arcface / appearance_matched）。
  实现可直接查 `behavior_events`（persons_json）+ TV 人脸事件，**无需新表**。
- 用途：管家每 30~60s 轮询一次，发现「某成员新出现且该时段配额未用完」→ 触发问候。
  （如后续 MA 愿意在 `record_face_event` 后加 HTTP 回调/MQTT 转发可替代轮询，非必需，另行沟通。）

## 需求 3（可选，低优先）：作息规律的自动佐证

MA 已有 `detected_activities` / insights 能力。若能按成员输出「最近 N 天回家时间分布」类摘要
（`GET /api/insights/member-schedule?name=Kevin&days=14`），管家可用实测数据校准作息档
（比如发现 Kevin 实际常 19:30 回家，就把问候基准调早）。非必需，可后续迭代。

## 非目标（明确不做）

- MA 不做：TTS、问候配额、对话状态机、时段窗口判断 —— 全部归管家
- 管家不做：取帧、VLM、人脸识别、语义记忆库 —— 全部走 MA 现有能力
- 不合并两个项目：契约隔离，各自迭代

## 验收

1. PATCH members/{id} 写入 profile_json 后 GET 能原样读回
2. presence 接口在 TV 前站人 10s 内能查到该成员（含 ArcFace via）
3. 现有功能（视觉巡检、人脸事件、语义记忆）回归无异常
