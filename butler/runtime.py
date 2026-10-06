"""运行时单例容器：lifespan 启动时装配，API/路由共享。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from butler.logging_setup import get_logger


@dataclass
class Runtime:
    settings = None
    state = None
    mqtt = None
    dialog = None
    tts = None
    memory = None
    llm = None
    tv = None
    tvpilot = None                  # TVPilot 工具层客户端（HTTP :8090，ReAct 电视手）
    deskpilot = None                # DeskPilot 工具层客户端（HTTP :8765，ReAct Windows 手）
    newapi = None                   # new-api 运维客户端（SQLite 直连，三表同步）
    docker = None                   # Docker 运维客户端（docker.sock，ps/restart/logs/compose）
    ha = None
    bark = None
    persona = None
    dedup = None
    wakeup = None
    runner = None                      # 技能调度器（skills.runner.SkillRunner）
    doubao = None                      # doubao2api 多模态客户端
    devices = None                    # 设备登记表（butler.devices.DeviceRegistry）
    roles = None                      # 角色登记表（butler.roles.store.RoleRegistry）
    role_state = None                  # 角色对话线程持久化（butler.roles.state.RoleConversationStore）
    locator = None                     # 成员定位（v0.4 大脑触发全屋找人）
    notifier = None                    # 豆包app主动推送（v0.5）
    notify_router = None               # 通知路由层：Bark/TTS/App 三通道路由（v2.5）
    inbox = None                       # §13.4 公共收件箱闸（butler.bus.inbox.InboxGate）
    adm_peers = None                   # adm/* 对端在线状态缓存（memory-agent/autoforge）
    trigger_engine = None              # trigger 规则层（v0.1）
    decision = None                    # 决策层引擎（v0.9）
    scheduler = None                   # APScheduler（提醒/心跳）
    scheduler_job_failures = None      # V37：注册失败的作业 id；None＝还没跑过注册，[]＝全成就
    skill_creator = None               # 技能生成器（v1.2 对话式创建）
    quarantine_mgr = None              # 技能隔离管理器（v1.3 自动隔离）
    skill_version_mgr = None           # 技能版本管理器（v1.4）
    sandbox_mgr = None                 # 技能沙箱管理器（v1.5）
    conflict_detector = None           # 冲突检测器（v1.6）
    template_mgr = None                # 技能模板管理器（v1.7）
    perf_monitor = None                 # 性能监控器（v1.8）
    collab_mgr = None                   # 多Agent协作管理器（v1.9）
    self_evolution = None               # 自进化引擎（v2.0）
    presence_engine = None              # 多源融合定位引擎（v1.5）
    presence_store = None               # 位置历史存储（v1.5）
    _sched = None
    sse_subscribers: set = field(default_factory=set)
    sse_dropped: int = 0                 # SSE 广播丢件计数（表行 21 后半：⛔ 无声丢弃不可查）


_JOB_LOG = get_logger("butler.scheduler.jobs")


def safe_add_job(sched, failures, *args, **kwargs):
    """V37（10-02 稳定性报告 P0-2 残半）：逐作业隔离——一枚 `add_job` 抛错只少它自己。

    改前 19 枚注册全在同一枚外层 try 里，前面一抛就把其后作业整批打掉，而 `rt.scheduler`
    照样 start()。失败的 id 记进 `failures`（面板腿 `_scheduler_up` 读它），日志固定标记
    `SCHED_JOB_REGISTER_FAILED`——没标记就等于没这条腿。
    """
    job_id = str(kwargs.get("id") or "<no-id>")
    try:
        sched.add_job(*args, **kwargs)
        return True
    except Exception as exc:
        failures.append(job_id)
        _JOB_LOG.error("SCHED_JOB_REGISTER_FAILED id=%s err=%s: %s",
                       job_id, type(exc).__name__, exc)
        return False


_RT = Runtime()


def get_runtime() -> Runtime:
    return _RT
