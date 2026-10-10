"""数据根目录的单点惰性解析（审计 D5「配置面不一致」的收敛尺子）。

为什么要有它：`Settings.data_dir` 读 `DATA_DIR`，但 5 枚模块级常量把 `/app/data` 烤死在 import 期
（`aliases.ALIAS_FILE`／`fast_routes.ROUTES_FILE`／`deps._SESSIONS_FILE`／`push_routes.SKILL_DIR`
／`self_evolve.DB_PATH`，外加 `doubao_webhook` 那份 `role_state.json`）。用户按文档把 `DATA_DIR`
指到 NAS 挂载点后，一部分模块写挂载点、另一部分读写容器内置路径 ⇒ **同一个逻辑文件变成两个物理文件**。
最重的一枚是 webhook：它读 `/app/data/role_state.json`，而 `roles/state.py` 与 `memory/feeder.py`
写的是 `DATA_DIR` 那份 ⇒ 反向查角色恒为 None ⇒ 手机端消息除一个硬编码 CID 外全部被静默丢弃
（失败方向反直觉：不崩溃，消息只是不见了）。

为什么**惰性**（每次调用现取）：模块级常量在 import 期求值，而 `get_settings()` 在 import 期调用
有循环导入风险（config 反过来要读路径）；惰性还保证测试里换掉 settings 后立刻拿到新值。

口径：`settings.data_dir` → 环境变量 `DATA_DIR` → `/app/data`。最后一档就是容器现网的原值，
本模块⛔ 改它——compose 没设 `DATA_DIR` 时行为与今天逐字节相同。
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ROOT = "/app/data"


def data_root() -> Path:
    """当前进程该用的数据根目录。每次现取，⛔ 缓存成模块级常量。"""
    root = ""
    try:
        from butler.config import get_settings
        root = getattr(get_settings(), "data_dir", "") or ""
    except Exception:
        # get_settings() 本身可能因启动硬门而抛——那不等于「没有根目录」，回 env 那一档再判
        root = ""
    if not root:
        root = os.getenv("DATA_DIR", "") or DEFAULT_ROOT
    return Path(root)


def data_file(*parts: str) -> Path:
    """根目录下的一个数据文件／目录（`data_file("skills", "user")` 那种多层写法）。"""
    return data_root().joinpath(*parts)
