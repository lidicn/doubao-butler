"""`python -m homesdk.gates` 命令行入口。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import BASELINE_NAME, CONFIG_NAME, GateConfig
from .scan import RULE_ERROR
from . import Report, scan_repo, stale_judgeable


def _write_baseline(
    repo_root: Path,
    report: Report,
    config: GateConfig,
    *,
    with_smoke: bool,
    scan_ast: bool,
) -> int:
    """基线按「严重度 + 规则 + 路径」排序，方便人读，也方便 diff 看出净减少。

    **本轮没检查过的那一桶原样保留**：`--no-smoke` 更新基线时若把 import-smoke 条目
    一起清掉，下一次全量跑就没人记得那里曾拦过什么。
    """
    untouched = {
        fp for fp in config.baseline
        if not stale_judgeable(fp, config, with_smoke=with_smoke, scan_ast=scan_ast)
    }
    by_fp = {v.fingerprint: v for v in (*report.active, *report.baselined)}
    all_violations = sorted(
        by_fp.values(),
        key=lambda v: (v.level != RULE_ERROR, v.rule, v.rel_path, v.qualname),
    )
    lines = [
        "# homesdk 质量门禁基线：只准减少，不准新增。",
        "# 指纹 = 相对路径#规则#函数限定名（刻意不含行号，改一行不该让基线失效）。",
        f"# 由 `python -m homesdk.gates {repo_root.name} --update-baseline` 生成。",
    ]
    lines += [v.fingerprint for v in all_violations]
    lines += sorted(untouched)
    (repo_root / BASELINE_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(all_violations) + len(untouched)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="homesdk.gates", description="ADM 生态三条质量门禁")
    parser.add_argument("repo", nargs="?", default=".", help="仓库根目录")
    parser.add_argument("--config", default=None, help=f"配置文件路径（默认 <repo>/{CONFIG_NAME}）")
    parser.add_argument("--update-baseline", action="store_true", help="把当前全部违规写进基线")
    parser.add_argument("--no-baseline", action="store_true", help="忽略基线，报全量（首次评估用）")
    parser.add_argument("--no-smoke", action="store_true", help="跳过 import 冒烟（子进程较慢）")
    parser.add_argument("--only-smoke", action="store_true", help="只做 import 冒烟，不跑 AST 规则")
    parser.add_argument(
        "--python",
        default=None,
        help="冒烟用的解释器。默认当前解释器，但**部署口径应指向容器内的 python**，否则第三方依赖没装齐会整片假红。",
    )
    parser.add_argument("--quiet", action="store_true", help="只输出汇总行")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo).resolve()
    if not repo_root.is_dir():
        print(f"仓库目录不存在：{repo_root}", file=sys.stderr)
        return 2
    try:
        config = GateConfig.load_file(Path(args.config)) if args.config else GateConfig.load(repo_root)
    except Exception as exc:  # 配置错必须显式红，不能悄悄放过
        print(f"配置读取失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.only_smoke and args.no_smoke:
        print("--only-smoke 与 --no-smoke 同时给出，没有可跑的检查。", file=sys.stderr)
        return 2
    if args.only_smoke and args.update_baseline:
        # 局部扫描重写基线 = 把没扫的那一桶整批抹掉，等于放行。禁止。
        print("--only-smoke 不许配 --update-baseline：那会把 AST 条目从基线里清空。", file=sys.stderr)
        return 2

    report = scan_repo(
        repo_root,
        config,
        use_baseline=not args.no_baseline,
        with_smoke=not args.no_smoke or args.only_smoke,
        scan_ast=not args.only_smoke,
        python=args.python,
    )

    if not args.quiet:
        for v in report.active:
            print(v.render())
        if report.baselined and not args.no_baseline:
            print(f"\n（另有 {len(report.baselined)} 条存量违规被基线吸收，只准减少不准增加）")
        for fp in report.stale:
            print(f"STALE 基线条目已不再命中，请从 {BASELINE_NAME} 删除：{fp}")

    summary = " | ".join(f"{rule}={n}" for rule, n in report.counts) or "无违规"
    print(
        f"扫描完成：{repo_root.name}  新增/未获批 {len(report.active)} 条"
        f"（error {report.error_count} / warn {report.warn_count}）"
        f"，基线内存量 {len(report.baselined)} 条，过期基线条目 {len(report.stale)} 条"
    )
    print(f"计数：{summary}")

    if args.update_baseline:
        total = _write_baseline(
            repo_root,
            report,
            config,
            with_smoke=not args.no_smoke or args.only_smoke,
            scan_ast=not args.only_smoke,
        )
        print(f"已写入 {BASELINE_NAME}：{total} 条")
        return 0

    if report.stale:
        print("失败：基线只准减少。请删除已不再命中的条目后重跑。", file=sys.stderr)
    return 1 if (report.failed or report.stale) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
