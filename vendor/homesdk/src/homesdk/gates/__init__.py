"""三条质量门禁：`except: pass` 禁令 / `ok←True` 字面量 AST / 每仓 import 冒烟。

用法：

    python -m homesdk.gates /path/to/repo            # 只报新增，存量走基线
    python -m homesdk.gates /path/to/repo --update-baseline
    python -m homesdk.gates /path/to/repo --no-baseline   # 全量口径（首次评估用）

退出码：0 干净 / 1 有未获批的违规 / 2 配置或路径错误。

**基线的语义是「只准减少不准增加」**：`.gates-baseline.txt` 里列出的指纹放行，
新出现的指纹一律红；同时报告「基线里已消失的指纹」，逼着提交者把修好的条目删掉——
净减少原则由机器强制，不靠人记。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import BASELINE_NAME, CONFIG_NAME, GateConfig, iter_python_files
from .scan import RULE_ERROR, RULE_WARN, Violation, scan_file
from .smoke import run_smoke

__all__ = [
    "CONFIG_NAME",
    "BASELINE_NAME",
    "GateConfig",
    "Report",
    "Violation",
    "scan_repo",
    "stale_judgeable",
]


@dataclass(frozen=True)
class Report:
    active: tuple[Violation, ...]      #: 需要处理（未被基线吸收）
    baselined: tuple[Violation, ...]   #: 被基线吸收的存量
    stale: tuple[str, ...]             #: 基线里已不再命中的指纹 —— 应该删掉
    counts: tuple[tuple[str, int], ...]  #: 按规则的计数（含 baselined）

    @property
    def failed(self) -> bool:
        """门禁红：有未获批的违规，**或有已修完却没删掉的基线条目**。

        后半个条件是为了让「基线只准减少」真的被强制——不然基线文件会越积越肥，
        里面全是早已不存在的指纹，没人有动力去清。
        """
        return bool(self.active) or bool(self.stale)

    @property
    def error_count(self) -> int:
        return sum(1 for v in self.active if v.level == RULE_ERROR)

    @property
    def warn_count(self) -> int:
        return sum(1 for v in self.active if v.level == RULE_WARN)


def stale_judgeable(fp: str, config: GateConfig, *, with_smoke: bool, scan_ast: bool) -> bool:
    """这条基线指纹本轮**有没有被真正检查过**？没检查过就不许判它过期。

    漏了这一步的话：`--no-smoke` 会把基线里的 import-smoke 条目报成过期（假红），
    而只扫 `src/` 的那次运行会把 `scripts/` 的条目报成过期——
    更糟的是随后一次 `--update-baseline` 会把没扫那一桶整批抹掉，等于放行。
    """
    parts = fp.split("#")
    if len(parts) < 3:
        return False
    path, rule = parts[0], parts[1]
    if rule == "import-smoke":
        return with_smoke
    if not scan_ast:
        return False
    return any(path == r or path.startswith(r.rstrip("/") + "/") for r in config.source_roots)


def scan_repo(
    repo_root: Path,
    config: GateConfig,
    *,
    use_baseline: bool = True,
    with_smoke: bool = True,
    scan_ast: bool = True,
    python: str | None = None,
) -> Report:
    found: list[Violation] = []
    if scan_ast:
        for path in iter_python_files(repo_root, config):
            found.extend(scan_file(path, repo_root, config))
    if with_smoke:
        found.extend(run_smoke(repo_root, config, python=python))

    baseline = config.baseline if use_baseline else frozenset()
    seen = {v.fingerprint for v in found}
    active = tuple(v for v in found if v.fingerprint not in baseline)
    baselined = tuple(v for v in found if v.fingerprint in baseline)
    stale = tuple(sorted(
        fp for fp in baseline - seen
        if stale_judgeable(fp, config, with_smoke=with_smoke, scan_ast=scan_ast)
    ))

    tally: dict[str, int] = {}
    for v in found:
        tally[v.rule] = tally.get(v.rule, 0) + 1
    return Report(tuple(active), baselined, stale, tuple(sorted(tally.items())))
