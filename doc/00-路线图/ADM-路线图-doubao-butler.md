# ADM 路线图 — doubao-butler（DB）

> 维护人：DB 开发者 | 更新频率：每日进度 + 每周一对齐
> 标准文档：`E:\NAS\AgentOps\doc\总路线图-ADM-v1-20260923.md`
> 里程碑：M1=09-22 / M2=09-25(=G1) / M3=09-28 / 交付=09-30

---

## 一、当前版本

**v2.1 — Koin 决策 + 基建稳定**

主题：从"简单工具层"升级到"LLM 决策 + 确定性执行"，同时补齐基建稳定性。

---

## 二、已完成（v2.0 → v2.1）

### 基建稳定
| 项 | 状态 | 说明 |
|---|---|---|
| WO-BUT-005 健康检查根因 | ✅ | async 函数内同步 urlopen(timeout=25) 挂事件循环，改 httpx.AsyncClient |
| WO-BUT-006 事件循环阻塞扫描 | ✅ | 全域扫描，三档分类 |
| WO-BUT-019 async 阻塞修复 | ✅ | docker_tools.py asyncio.to_thread + mcp/server.py wrap_future |
| WO-BUT-022 G1 安全批次 | ✅ | web_password 空默认 + 登录限流 + webhook compare_digest |
| WO-BUT-023 G2 DoD 批次 | ✅ | 门禁 exit 0 + HTTP 实跑通 + 部署前备份点 |
| WO-BUT-024 restart 验证 + 填 token | ✅ | G1 三条修复活体生效 + DESKPILOT_API_TOKEN/TASK_REPORT_TOKEN |

### Consent 判定收口
| 项 | 状态 | 说明 |
|---|---|---|
| WO-BUT-001 consent 判定 | ✅ | classify_answer 单点收口，删内联词表 |
| WO-BUT-002 四项补齐 | ✅ | UNKNOWN 重播上限 + 草稿 generation + 删除重复 dialog.py + 扩展回归 |
| WO-BUT-003 import 修复 | ✅ | ask.py 补 from homesdk.consent import |
| WO-BUT-013 consent 日志 | ✅ | YES/NO 分支加日志 + decider init 日志 |

### 跨仓断链
| 项 | 状态 | 说明 |
|---|---|---|
| WO-BUT-009 cross-repo bridge | ✅ | 四列表交付 |
| WO-BUT-020 断链修复 | ✅ | memory_agent 参数名 + af_bridge 鉴权+状态码 + getattr fallback |
| WO-ADM-001 R-32 熔断 | ✅ | MemoryAgentClient 连续失败 5 次→熔断 30s + is_healthy 健康位 |

### 功能
| 项 | 状态 | 说明 |
|---|---|---|
| WO-BUT-025 TTS 播放链路 | ✅ | BUTLER_SPEAK_TARGET 从 mqtt 改 tv（Node-RED 无订阅）+ MQTT 日志+回退 |
| WO-BUT-026 创建技能语音入口 | ✅ | simple_rules 匹配"创建技能"+ pending_skill_desc 状态路由 |
| 小爱设备控制双重播报修复 | ✅ | source=xiaoai 时设备控制跳过 TTS（小爱同学原生处理）+ 非小爱来源真正调 HA |

---

## 三、进行中

| 项 | 优先级 | 状态 | 说明 |
|---|---|---|---|
| M1-D3 真机同意冒烟 | P1 | ⏳ 等出资人在场 | 说"不同意"→日志拒绝出证 + 无执行动作 |
| WO-BUT-010 ALLOW_PASSWORD_AS_BEARER | P2 | ⏳ 等出资人批 | 读取链已列出，默认从 deps.py:64 变 true |
| WO-BUT-015 重复副本处置 | P3 | ⏳ 等 PM 批 | 两对 26MB 逐字节相同文件 |

---

## 四、待开发（v2.1 路线图）

### P0 — 阻塞性
| 项 | 说明 |
|---|---|
| 豆包 app webhook 修复 | 微信已恢复，豆包 app webhook 仍有问题 |
| 早报/晚报定时触发验证 | 写了但未实际跑过，需验证 cron 触发 |

### P1 — 核心体验
| 项 | 说明 |
|---|---|
| Koin 决策引擎落地 | morning_lidicn 场景验证：人脸识别→LLM 决策→电视控制 |
| 技能引擎 LLM 化 | 从"触发器+动作链"升级到"LLM 决策+确定性执行" |
| TV Pilot ask 接口 | 主动询问 + 提示音（交接单已发 TVPilot 开发者） |
| 小爱主动询问能力 | 复刻 NR 版小爱主动唤醒+播报+捕获回答到 butler |

### P2 — 稳定性
| 项 | 说明 |
|---|---|
| MQTT 周期性断连根因 | 约每 20 分钟一次 Normal disconnection |
| 语音路径端到端时延指标 | WO-BUT-005 记的债，下次同类病可能不进监控面 |
| iPad 海报墙图片加载 | iPhone 正常，iPad 加载不了 |

### P3 — 优化
| 项 | 说明 |
|---|---|
| TTS 音色优化 | 讯飞 TTS 速度快但声音机器，探索其他音色 |
| NowVoice 稳定性 | 登录限制 10 秒，自动获取 token |
| 技能平台 LLM 编写 | 让 LLM 可以编写技能（技能平台比 Action 更容易实现） |

---

## 五、跨项目依赖（需 ADM PM 协调）

| 项 | 依赖方 | 说明 |
|---|---|---|
| R-62 PATCH 白名单 | MA | MA `_butler_allowed` 需加 PATCH/PUT |
| R-49 MQTT QoS | MA | MA publish presence/device-health 需改 qos=1 |
| TV Pilot ask 接口 | TVPilot | 主动询问 + 提示音 |
| homesdk 变更 | MA + ADM PM | 任何 homesdk 变更需审批 |
| AF ask 桥联调 | AF | AF→语音确认问询链路，需 WO-BUT-013 先落地 |

---

## 六、本周进度（2026-09-22）

- ✅ WO-ADM-001 R-32 熔断 + 健康位（commit 9846924）
- ✅ 小爱设备控制双重播报修复（source=xiaoai 跳过 TTS + 非小爱真正调 HA）
- ✅ DB 路线图文件创建
- ⏳ M1-D3 真机同意冒烟（等出资人）

---

## 七、已知债

1. **语音路径缺端到端时延指标** — WO-BUT-005 记的债
2. **simple_rules 设备控制只匹配有限模式** — 需扩展覆盖更多设备/操作
3. **Node-RED unhealthy** — 两个 Node-RED 容器均 unhealthy，需排查
4. **早报/晚报未验证** — 定时任务写了但没跑过
