"""门禁③：每仓 `import` 冒烟。

存在的理由：doubao-butler 那条「import 即崩」的结论最后被证明是我读错了副本，但**这个检查该有**——
半途重构留下的死引用（`butler/tts/manager.py` 一类）在单测里不一定会被碰到，而容器起不来是最贵的一种失败。

它只证明「顶层 import 打得开」，不证明「服务能起」。所以本工具的输出刻意写成 `import-smoke`，
工单口径里明确要求再补 `docker compose ps` + 一次 HTTP 验证，不许用本条代替那一层。

**必须在部署镜像里跑**：在开发机解释器上跑，第三方依赖没装齐会整片报红，
那类失败与本门禁想抓的东西无关。`--python` 用来指向容器内解释器。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from .config import GateConfig
from .scan import RULE_ERROR, Violation

TIMEOUT_S: int = 60

_MISSING_MODULE = re.compile(r"No module named '([^']+)'")


def _module_paths(missing: str, repo_root: Path, config: GateConfig) -> list[Path]:
    """缺失模块在磁盘上的可能落点。导入根可能是仓库根（doubao-butler），也可能是 src/（其余三家）。"""
    dotted = Path(*missing.split("."))
    return [repo_root / dotted.with_suffix(".py"), repo_root / dotted] + [
        repo_root / r / dotted.with_suffix(".py")
        for r in config.source_roots
    ] + [repo_root / r / dotted for r in config.source_roots]


def _diagnose(module: str, detail: str, repo_root: Path, config: GateConfig) -> str:
    """把 `ModuleNotFoundError` 分成两类，别让读报告的人靠猜。

    第三方依赖缺失 = 多半是**本机解释器没装齐**；本仓自身模块缺失 = 真死引用，
    **或者**本地副本不完整（doubao-butler 的 `butler/tts/nowvoice_tts.py` 就只存在于 NAS 线上副本）。
    """
    m = _MISSING_MODULE.search(detail)
    if not m:
        return ""
    missing = m.group(1)
    if missing.split(".")[0] != module.split(".")[0]:
        return f"（第三方依赖 `{missing}` 未安装：请用 `--python` 指向部署镜像解释器，本条不代表代码有错）"
    exists = any(p.exists() for p in _module_paths(missing, repo_root, config))
    kind = "文件在本副本中存在，可能是它的依赖缺失或顶层有副作用" if exists else "本副本中无此文件，先确认线上副本是否完整"
    return f"（本仓模块 `{missing}` 导入失败：{kind}）"


def smoke_import(
    repo_root: Path, module: str, config: GateConfig, *, python: str | None = None
) -> Violation | None:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    roots = os.pathsep.join([str(repo_root / r) for r in config.source_roots] + ([existing] if existing else []))
    env["PYTHONPATH"] = roots
    try:
        proc = subprocess.run(
            [python or sys.executable, "-c", f"import {module}"],
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return Violation("import-smoke", ".", 0, module, f"import {module} 超时（>{TIMEOUT_S}s）", RULE_ERROR)
    if proc.returncode == 0:
        return None
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()
    detail = tail[-1] if tail else f"exit={proc.returncode}"
    message = f"import {module} 失败：{detail}{_diagnose(module, detail, repo_root, config)}"
    return Violation("import-smoke", ".", 0, module, message, RULE_ERROR)


def run_smoke(repo_root: Path, config: GateConfig, *, python: str | None = None) -> list[Violation]:
    return [
        v for module in config.smoke_targets if (v := smoke_import(repo_root, module, config, python=python))
    ]
