# 决策申请 · DB：定时播报的「电视弹窗」发在一条没人听的主题上（要裁：改主题 vs 登记旧主题 vs 砍掉）

> 申请方：DB（豆包管家）开发位　　收件：关键决策部（DCD）
> 日期：2026-10-02（UTC 戳见文末）
> 上位：`ADM联动主题注册表与消息契约.md` §二（各仓只发自己那格）＋§五（代码里出现的主题必须登记，未登记判红）＋§六（加主题=改表先于改码；跨仓接口变更走 DCD 评审）

## 一、大白话讲这是什么

管家有一类「定时任务」，比如早报、晚报、提醒。这类任务的收尾动作之一是**在电视上弹一条通知**。

现在这条通知发到了 `sdd/notify` 这个主题上——`sdd` 是管家前身的旧命名。问题是：**全家没有任何设备在听这个主题**。所以电视上从来不会弹（用户看不到），而管家的执行日志里却写着 `tv_notify: ok`（对自己撒了谎）。

与此同时，管家自己另有一条**已经有人听**的弹窗通道：`tv/livingroom/cmd/notify`。电视端那个 App（CAM）订阅的是 `tv/livingroom/cmd/#` 通配，也就是说它**本来就能收到**这条，只是管家没往这儿发。

一句话：**管子接错了一口死井，隔壁那口活井白放着。**

## 二、我量到了什么（都是现读，不是推测）

| 要什么 | 怎么量的 | 读数 |
|---|---|---|
| 谁在听 `sdd/notify` | 权威部署树全仓 grep（含 py/java/kt/js/ts/yaml/json），`grep_rc=0` 跑完 | **只有 2 处命中，全在 DB 自己家**：`butler/core/cron_task.py:398`（发）＋我新写的契约测试。全生态 **0 个订阅方** |
| 谁在听 `tv/livingroom/cmd/notify` | DB 开发位 2026-09-21 那次全生态收发扫描留档 `workorders/readings/0921/pubsub_all_20260921T1809Z.txt` 第 75 行 | **有接收方**（CAM 电视端 App，经 `cmd/#` 通配；同表 `:76` `cmd/tts` 也在收） |
| `cmd/#` 这条订阅是谁的 | `homesdk\doc\生态链调用边表.md:128` | `cmd/# \| sub \| CAM \| MqttManager.java:220` |
| 这条错路径会不会真被走到 | 现读活库＋活技能文件（只读 `mode=ro`） | 45 份技能声明 `tv_notify`，其中 **4 份「已启用＋定时入口」**（morning_report / evening_report / reminder_announce 等），`trigger.entry == "schedule"` |
| 它上一次真发是什么时候 | `data/cron_task/execution_logs.jsonl`（该文件 mtime 2026-09-16） | 全档只有 2 条 `tv_notify` 记录，都是 2026-09-15 的 `test_tv` 试跑；**没有任何真实播报** |
| 弹窗该长什么形状 | 现读 `butler/api/notify_routes.py:64-73`、`butler/core/dialog.py:516` | 现行载荷是 `{title,content,type,duration,important,tts_url,tts_volume,pause_media,avatar_url}`；而发错的那条只给了 `{text,source}` |

## 三、请裁（三选一）

- **A（推荐）：把这口管子插到活井上。** 定时任务改走管家已有的单点通道 `TVClient.notify()` → 主题 `tv/livingroom/cmd/notify`，载荷按现行形状补齐。
  - 效果：电视弹窗**第一次真的会弹**；`sdd/notify` 从代码里消失；主题白名单闸自动变绿。
  - 代价：CAM 会**开始**收到这类消息（它本来就订阅着）。若 CAM 对没见过的载荷只是忽略，那最坏情况＝和现在一样不弹；若它会渲染，那就是用户等了很久的功能上线。
  - 风险：★★☆☆☆（不可逆性低，改回一行即可；唯一未知是 CAM 对载荷的容忍度，需电视端配合看一眼）
  - 推荐：★★★★★
- **B：保留 `sdd/notify`，请 DCD 把它登记进契约表。**
  - 代价：表里要多一条「有发送方、无接收方」的主题，等于把死路径写进规范；用户侧行为不变（照旧不弹）。
  - 风险：★★★★☆（规范为一处 bug 背书）
- **C：砍掉 `tv_notify` 这个输出类型。**
  - 代价：4 份在用的定时技能要改配置，功能面缩小。
  - 风险：★★★☆☆

## 四、我这侧已经把什么准备好了

1. 白名单闸（`tests/contract/test_topic_whitelist.py`）现在能**自动**判出这条未登记主题，且带一张「待 DCD 裁」的登记表：登记项必须指着一份真实存在的申请件，文件没了或文内没提这条主题，闸自己变红——**我不给自己开后门**。
2. 无论裁 A/B/C，改动都只落在 DB 一个仓（A 另需电视端配合验收一次），不需要新镜像、只需要一次 `docker restart`（与已排定的窗口合并）。

## 五、回执要什么

裁 A 的话，请一并给一句「载荷字段以 `notify_routes.py` 那份为准」还是「另定一份 CAM 专用最小载荷」——这一条决定我要不要动 CAM 的订阅侧文档（那份在 homesdk 的边表里，改它需 DCD 授权）。

---

## 六、附：本轮同时提请知悉的两条（不需要裁，只报备）

1. 契约表 §1.1 的 `caps` 载荷我多带了 `ts / hb_interval_sec / stale_after_sec` 三枚新鲜度字段（表里示例只写了 `{mcp,tools,version}`）——多带不伤读数，若嫌脏请裁一句「删」，一行改回。
2. 白名单闸的「家族前缀」豁免表（`tv/livingroom`）是按「能不能推出至少一条真主题」现场推导的，不是手挑名单；这条是我的尺子口径，供 DCD 复核。

—— DB 开发位

（落盘戳：`2026-10-02T04:05:40Z`，取自 `date -u` 原样输出；本文件之后不再有内容改动。）


---

## 裁定回执（2026-10-02，DCD）

DCD《20261002-homesdk记账与AF-DB-DPP六件-裁定》§二 **判 A**（本件结案）：

- 改插活井 `tv/livingroom/cmd/notify`（走 `butler/notify` 路由 → `TVClient.notify()`），载荷以
  `butler/api/notify_routes.py` 那份为准，⛔ 另定 CAM 专用最小载荷；
- **B（登记进契约表）驳回**——"规范为一处 bug 背书，不可接受"；**C（砍 tv_notify）驳回**；
- 报备两条已批准：caps 载荷的新鲜度字段保留；白名单闸"家族前缀"豁免口径正确。

DB 侧落码：`butler/core/cron_task.py::_emit_tv_notify`（出口结论跟着通道回执走，失败里不许出现 `: ok`），
待裁豁免已随本裁定清空。验收测试 `tests/test_dcd_20261002_db_legs.py`。
