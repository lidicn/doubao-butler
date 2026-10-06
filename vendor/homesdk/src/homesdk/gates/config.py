"""门禁配置：路径口径、严重度分级、基线。

设计约束（和 homesdk.consent 同源）：**一份定义，多处引用**。
四仓各自的 lint 配置如果各写一份，就会长成第五份 `_LINT_BLOCK_RULES`。
所以这里只有一个配置文件名（`.gates.toml`）、一个加载函数、一份默认口径。
"""

from __future__ import annotations

import fnmatch
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_NAME: str = ".gates.toml"
BASELINE_NAME: str = ".gates-baseline.txt"

#: 永远不扫的目录名（审计口径的同一份排除表）
SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "tmp",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        "graphify-out",
        "site-packages",
    }
)

#: 目录名前缀命中即跳过（`src_backup_before_xxx`、`backup_20260901`、`tmp2`）
SKIP_DIR_PREFIXES: tuple[str, ...] = ("src_backup", "backup", "tmp_", ".venv", "outputs")

#: 文件名 glob 命中即跳过
SKIP_FILE_GLOBS: tuple[str, ...] = ("*.bak*", "*_backup*", "*.orig", "*.rej", "#*#")

#: 「同一份判定不允许出现第二处」的词表变量名（G3 加严项，见 rules.CONSENT_TABLES）
CONSENT_TABLES: tuple[str, ...] = ("_YES_WORDS", "_NO_WORDS", "_AFFIRM_WORDS", "_NEG_WORDS")


@dataclass(frozen=True)
class GateConfig:
    """一个仓库的门禁口径。缺省值即生态推荐值。"""

    #: 源码根（相对仓库根）。只有落在这些目录下的 .py 才被扫。
    source_roots: tuple[str, ...] = ("src",)
    #: 冒烟 import 的顶层包名。
    smoke_targets: tuple[str, ...] = ()
    #: 写入路径模块（glob，相对仓库根）——`ok←True` 在这些文件里是硬错误。
    write_path_globs: tuple[str, ...] = ()
    #: 关键模块（回滚/快照/鉴权/闸门）——`except Exception: pass` 在这些都是硬错误。
    critical_globs: tuple[str, ...] = ()
    #: 允许整体跳过的相对路径 glob。
    extra_skip_globs: tuple[str, ...] = ()
    #: G3 加严项：禁止仓内自行定义同意词表。三家还没切到 homesdk 前先关着，切完必须打开。
    forbid_local_consent_tables: bool = False
    #: 基线指纹（存量违规，只放行不新增）。
    baseline: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def load(cls, repo_root: Path) -> GateConfig:
        return cls._build(repo_root / CONFIG_NAME, repo_root / BASELINE_NAME)

    @classmethod
    def load_file(cls, cfg_path: Path, baseline_path: Path | None = None) -> GateConfig:
        """显式指定配置文件（基线默认取同目录下的 `.gates-baseline.txt`）。"""
        return cls._build(cfg_path, baseline_path or cfg_path.parent / BASELINE_NAME)

    @classmethod
    def _build(cls, cfg_path: Path, base_path: Path) -> GateConfig:
        raw: dict[str, Any] = {}
        if cfg_path.is_file():
            raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
        baseline = frozenset(
            line.strip()
            for line in base_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ) if base_path.is_file() else frozenset()
        return cls(
            source_roots=tuple(raw.get("source_roots") or ("src",)),
            smoke_targets=tuple(raw.get("smoke_targets") or ()),
            write_path_globs=tuple(raw.get("write_path_globs") or ()),
            critical_globs=tuple(raw.get("critical_globs") or ()),
            extra_skip_globs=tuple(raw.get("extra_skip_globs") or ()),
            forbid_local_consent_tables=bool(raw.get("forbid_local_consent_tables", False)),
            baseline=baseline,
        )


def matches_glob(rel_path: str, globs: tuple[str, ...]) -> bool:
    """glob 语义：既支持 `*/x.py` 也支持裸目录名 `autoforge/af_live.py` 前缀命中。"""
    norm = rel_path.replace("\\", "/")
    for pattern in globs:
        p = pattern.replace("\\", "/")
        if fnmatch.fnmatch(norm, p) or fnmatch.fnmatch(norm, f"*{p}") or norm.startswith(p.rstrip("/*") + "/"):
            return True
    return False


def should_skip(rel_path: str, extra_skip_globs: tuple[str, ...] = ()) -> bool:
    """路径是否排除在统计外。与审计取证口径同一份表。"""
    parts = [p for p in rel_path.replace("\\", "/").split("/") if p]
    name = parts[-1] if parts else ""
    for part in parts[:-1]:
        if part in SKIP_DIR_NAMES or part.startswith(SKIP_DIR_PREFIXES):
            return True
        # `autoflow_gateway.bak-20260806-203201/` 这类「目录名带 .bak」的备份树
        if any(fnmatch.fnmatch(part, g) for g in SKIP_FILE_GLOBS):
            return True
    if any(fnmatch.fnmatch(name, g) for g in SKIP_FILE_GLOBS):
        return True
    if matches_glob(rel_path, extra_skip_globs):
        return True
    return False


def iter_python_files(repo_root: Path, config: GateConfig) -> list[Path]:
    """按 `source_roots` 收集待扫文件，排序保证输出可重放。"""
    found: list[Path] = []
    for root in config.source_roots:
        base = repo_root / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            rel = path.relative_to(repo_root).as_posix()
            if should_skip(rel, config.extra_skip_globs):
                continue
            found.append(path)
    return sorted(found)
