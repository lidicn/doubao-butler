"""技能引擎协议数据结构（runner 与引擎共用，独立模块避免循环导入）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillContext:
    """传给引擎的一次运行上下文。rt 为全局 Runtime（引擎按需取服务）。"""

    skill: dict                       # 规范化技能定义
    source: str = "api"               # api | mqtt | test
    dry_run: bool = False             # 试跑：执行感知与组装，但不推送输出
    payload: dict = field(default_factory=dict)   # 触发方附带参数
    rt: Any = None                    # 全局 Runtime
    now: str = ""
    role: Any = None                  # 解析出的角色（butler.roles.store.Role），用于音色/设备/对话线程


@dataclass
class SkillResult:
    """引擎运行结果。text 为最终口播文本；meta 透传给 API/编排层。"""

    ok: bool
    text: str = ""
    error: str = ""
    status: str = "ok"                # ok | tv_offline | dedup | error ...
    meta: dict = field(default_factory=dict)
