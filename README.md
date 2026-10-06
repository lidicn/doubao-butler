# 豆包管家 (doubao-butler)

运行在 NAS Docker 上的**家庭 AI 管家决策大脑**。自己不做感知/存储/设备控制，只负责：

> 知道谁在客厅 → 结合场景与记忆 → 在合适时机用合适的语气主动开口，也能接住家人对小爱说的话。

## 架构

```
TV 人脸识别 / memory-agent VLM ──MQTT──▶ butler(决策) ──MQTT/TTS/HA/Bark──▶ 电视/小爱/手机
小爱语音 ──Node-RED──▶ butler/event/# ─────────────┘
```

- **感知层（已有·零开发）**：TV Arcface 人脸识别、memory-agent VLM 场景、HA 传感器、小爱音箱
- **大脑层（本仓库）**：MQTT 总线、对话状态机、人格装配、bigram 防重复、TTS 双引擎降级、memory-agent 记忆对接
- **表达层（已有）**：电视扬声器（MQTT cmd/tts）、小爱（HA tts.speak）、Bark 推送兜底

## 技术栈

Python 3.11 + Starlette + Uvicorn · paho-mqtt · doubao2api(OpenAI 兼容) · edge-tts / Kokoro(可选) · SQLite(WAL) · memory-agent(MCP) · 原生 HTML+Alpine+Tailwind WebUI

## 部署（NAS）

```bash
# 1) 同步代码到 NAS（本机工作区 e:\NAS\doubao-butler 与 NAS 非实时挂载，需 scp）
scp -r e:/NAS/doubao-butler/. lidicn@192.168.2.200:/vol1/1000/docker/doubao-butler/

# 2) 构建并启动
ssh lidicn@192.168.2.200
cd /vol1/1000/docker/doubao-butler
docker compose up -d --build

# 3) 验证
curl http://192.168.2.200:8095/api/health     # {"ok":true}
```

WebUI：http://192.168.2.200:8095 （默认 <redacted>/<redacted>，可在 compose 环境变量修改）

可选本地 TTS 备引擎：`docker compose --profile kokoro up -d`（镜像需自行确认）

## Node-RED 桥接（小爱语音闭环）

管家自身只负责「决策 + 电视发声」，小爱音箱的**耳朵**与可选的**手机通知**由 Node-RED 桥接：

1. 浏览器打开 Node-RED：http://192.168.2.200:1880 → 菜单 → **Import**
2. 导入本仓库的 `nr_flow_butler.json`（新增「豆包管家转发」标签页，**不动现有流程**）
3. 按实际环境修改占位：
   - `xiaomi/asr` 主题：小爱识别文本的来源（若你的小爱经 HA 出词，可改用 `server-state-changed` 节点监听 ASR 实体，默认 `sensor.xiaomi_asr`）
   - `home-assistant` 节点 `token`：填入 HA 长期令牌（启用「小爱嘴巴」分支时需要）
   - Bark `http request` 的 URL 设备 key：`__FILL_BARK_KEY__` → 改成手机端 Bark App 注册得到的设备 key
4. 部署后：家人对小爱说「管家，……」→ `butler/event/voice` → 管家思考并回话（电视/小爱/Bark 发声）

> 管家每次开口还会发布 `butler/speak/out`，Node-RED 据此推送一条 Bark 手机通知（可见「管家说了什么」）。
> 小爱「嘴巴」分支（`tts.speak`）默认**关闭**：若启用，建议在 compose 把 `BUTLER_` 的 HA 兜底关闭以免电视与小爱重复念。

## Bark 兜底推送

自托管 Bark 服务端按**设备 key** 路由。配置 `BARK_KEY`（手机 Bark App 注册后获得），推送地址自动变为
`{BARK_URL}/{BARK_KEY}/push`。未配 key 时 Bark 会返回 `device key is empty`，仅 TV/小爱通道生效。

## 配置

所有连接配置经环境变量注入（见 `.env.example` / `docker-compose.yml`），**密码与 token 不落盘**。
人格、成员称呼、免打扰时段等可在 WebUI「人格 / 设置」页修改，持久化到 `/app/data/config.json`。

## 关键设计

- **全面 MQTT**：订阅 `tv/livingroom/#` 与 `butler/event/+`，事件推送 + 离线感知；HTTP `/api/tts/play` 为降级通道
- **防重复**：回复文本 bigram Jaccard，同成员 7 天窗口 >0.6 判重复，纯 Python 零额外内存
- **TTS 三级降级**：edge-tts → Kokoro(可选) → Bark 文字推送，任一级失败不阻塞对话
- **分库**：对话流水/指纹存本地 SQLite；只有可溯源家庭事实才写 memory-agent 语义记忆（强制 source_refs）
- **稳定性**：全局异常兜底，绝不崩溃退出；`/api/health` 不因下游不可用而失败；日志脱敏、高频事件节流

## 目录

```
butler/
  bus/        MQTT 主题与封装
  core/       对话状态机 / 唤醒 / 人格 / 防重复 / 状态
  tts/        双引擎抽象与管理器
  integrations/  LLM / memory-agent(MCP) / HA / Bark / TV
  store/      SQLite + 仓库
  api/        路由与响应约定
  static/     WebUI
  app.py      Starlette 装配
tests/        单元测试
```
