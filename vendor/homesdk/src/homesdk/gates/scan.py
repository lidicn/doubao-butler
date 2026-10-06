"""三条门禁的 AST 扫描。

规则集（与工单口径一一对应）：

| rule | 判什么 | 默认级别 |
|---|---|---|
| `except-pass-bare` | 裸 `except:` + 空体 | error（任何文件） |
| `except-pass-broad` | `except Exception/BaseException:` + 空体 | 关键模块 error，其余 warn |
| `fake-ok-const` | 返回/构造的字面量 dict 里 `"ok"/"success"/"passed": True` | 写入路径 error，其余 warn |
| `swallow-and-claim-ok` | **同一函数内**既有空体 except 又有 `ok←True` | error（任何文件） |
| `local-consent-table` | 仓内自行定义同意词表（`_YES_WORDS` 等） | error，需配置显式开启 |
| `inline-consent-list` | 内联词表 `any(w in text for w in [ … ])`（butler P0-12 的形状） | error，需配置显式开启 |

`swallow-and-claim-ok` 是本套门禁存在的理由：它正是「失败被咽下 + 对外宣称成功」的机器可读形状——
autoflow 的 `rolled_back: (snap is not None)`、AutoForge 的 `simulate().ok` 恒真、MA 的 `except: pass` 三例，
都落在这条规则上。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from .config import CONSENT_TABLES, GateConfig, matches_glob
from ..consent import CONSENT_VOCAB

OK_KEYS: frozenset[str] = frozenset({"ok", "success", "passed"})
BROAD_NAMES: frozenset[str] = frozenset({"Exception", "BaseException"})
RULE_ERROR: str = "error"
RULE_WARN: str = "warn"


@dataclass(frozen=True)
class Violation:
    """一条违规。`fingerprint` 刻意不含行号——改一行不该让基线失效。

    `tag` 用来区分「同一函数里的两处同类违规」（如模块级 `_YES_WORDS` 与 `_NO_WORDS`）：
    不带 tag 时它们指纹相同，基线一行就能同时放过两条，净减少也就没人看得见了。
    """

    rule: str
    rel_path: str
    line: int
    qualname: str
    message: str
    level: str
    tag: str = ""

    @property
    def fingerprint(self) -> str:
        base = f"{self.rel_path}#{self.rule}#{self.qualname}"
        return f"{base}#{self.tag}" if self.tag else base

    def render(self) -> str:
        loc = f"{self.rel_path}:{self.line}"
        return f"{self.level.upper():5} {self.rule:24} {loc:60} {self.qualname}  {self.message}"


def _is_trivial_suppression(handler: ast.ExceptHandler) -> bool:
    """ except 体只有 `pass`（或字符串字面量 + pass）。任何真实处理都不算。"""
    body = [s for s in handler.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))]
    return bool(body) and all(isinstance(s, ast.Pass) for s in body)


def _handler_kind(handler: ast.ExceptHandler) -> str | None:
    if handler.type is None:
        return "bare"
    node = handler.type
    names = node.elts if isinstance(node, ast.Tuple) else [node]
    for item in names:
        if isinstance(item, ast.Name) and item.id in BROAD_NAMES:
            return "broad"
        if isinstance(item, ast.Attribute) and item.attr in BROAD_NAMES:
            return "broad"
    return None


class _Visitor(ast.NodeVisitor):
    def __init__(self, rel_path: str, config: GateConfig) -> None:
        self.rel_path = rel_path
        self.config = config
        self.scope: list[str] = []
        self.violations: list[Violation] = []
        #: qualname -> 是否出现过空体 except（给 swallow-and-claim-ok 用）
        self.suppressed: set[str] = set()
        self.is_critical = matches_glob(rel_path, config.critical_globs)
        self.is_write_path = matches_glob(rel_path, config.write_path_globs)

    # ── 作用域 ────────────────────────────────────────────────────────
    def _visit_scoped(self, node: ast.AST, name: str) -> None:
        self.scope.append(name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scoped(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scoped(node, node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scoped(node, node.name)

    @property
    def qualname(self) -> str:
        return ".".join(self.scope) or "<module>"

    # ── 规则① except 咽下 ─────────────────────────────────────────────
    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        kind = _handler_kind(node)
        if kind and _is_trivial_suppression(node):
            if kind == "bare":
                rule, level, msg = "except-pass-bare", RULE_ERROR, "裸 except 咽下一切异常且不做处理"
            elif self.is_critical:
                rule, level, msg = "except-pass-broad", RULE_ERROR, "关键模块（鉴权/回滚/闸门/快照）咽下 Exception"
            else:
                rule, level, msg = "except-pass-broad", RULE_WARN, "咽下 Exception 且不做处理"
            self.violations.append(Violation(rule, self.rel_path, node.lineno, self.qualname, msg, level))
            self.suppressed.add(".".join(self.scope))
        self.generic_visit(node)

    # ── 规则② 假成功字面量 ────────────────────────────────────────────
    def visit_Dict(self, node: ast.Dict) -> None:
        for key, value in zip(node.keys, node.values, strict=False):
            if not (isinstance(key, ast.Constant) and key.value in OK_KEYS):
                continue
            if isinstance(value, ast.Constant) and value.value is True:
                level = RULE_ERROR if self.is_write_path else RULE_WARN
                where = ".".join(self.scope)
                if where in self.suppressed or any(s and where.startswith(s) for s in self.suppressed):
                    self.violations.append(
                        Violation(
                            "swallow-and-claim-ok", self.rel_path, node.lineno, self.qualname,
                            f"同一函数内既咽下异常又返回 {key.value}=True —— 谎报成功", RULE_ERROR,
                        )
                    )
                else:
                    self.violations.append(
                        Violation(
                            "fake-ok-const", self.rel_path, node.lineno, self.qualname,
                            f"字面量 {key.value}=True，不来自任何实际校验", level,
                        )
                    )
        self.generic_visit(node)

    # ── 规则③（G3 加严）同意词表只准有一处 ────────────────────────────
    def visit_Assign(self, node: ast.Assign) -> None:
        if self.config.forbid_local_consent_tables:
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in CONSENT_TABLES:
                    self.violations.append(
                        Violation(
                            "local-consent-table", self.rel_path, node.lineno, self.qualname,
                            f"仓内定义 `{target.id}` —— 同意判定必须来自 homesdk.consent", RULE_ERROR,
                            tag=target.id,
                        )
                    )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """内联词表：`any(w in text for w in ["确认","好的",…])`。

        doubao-butler 的 P0-12 就是这个形状——**没有命名常量，所以 `visit_Assign` 抓不到**。
        G3 只查具名词表的话，一家可以把词表全改成内联字面量来「合规」。
        """
        if self.config.forbid_local_consent_tables and isinstance(node.func, ast.Name) and node.func.id in {"any", "all"}:
            hits = _inline_word_list(node)
            if hits:
                self.violations.append(
                    Violation(
                        "inline-consent-list", self.rel_path, node.lineno, self.qualname,
                        f"内联同意词表（命中 {len(hits)} 个判定词：{'、'.join(hits[:4])}）"
                        " —— 同意/取消判定必须来自 homesdk.consent",
                        RULE_ERROR,
                        tag="、".join(hits[:3]),
                    )
                )
        self.generic_visit(node)


def _inline_word_list(node: ast.Call) -> list[str]:
    """`any(x in <文本> for x in [字面量…] )` → 返回字面量表，否则 []。

    刻意保守：≥4 个、每个 ≤6 字符的字符串字面量，左操作数必须是推导变量，
    右操作数必须引用一个名字（真实文本）。普通业务枚举（`if ext in (".py",".md")`）不报。
    """
    if not node.args:
        return []
    arg = node.args[0]
    if not (isinstance(arg, ast.GeneratorExp) and arg.generators):
        return []
    gen = arg.generators[0]
    if not isinstance(gen.target, ast.Name) or not isinstance(gen.iter, (ast.List, ast.Tuple)):
        return []
    words = [e.value for e in gen.iter.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    if len(words) != len(gen.iter.elts) or not all(0 < len(w) <= 6 for w in words):
        return []
    elt = arg.elt
    if not (isinstance(elt, ast.Compare) and len(elt.ops) == 1 and isinstance(elt.ops[0], ast.In)):
        return []
    if not (isinstance(elt.left, ast.Name) and elt.left.id == gen.target.id):
        return []
    if not _ref_names(elt.comparators[0]):
        return []
    # 只抓「同意/否决」词汇，不抓设备指令词表：后者不是本规则要治的病。
    # 门槛定在 2 个精确命中的词汇——`["好","行"]` 这种短表也逃不掉。
    hits = [w for w in words if w in CONSENT_VOCAB]
    return hits if len(hits) >= 2 else []


def _ref_names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _suppress_scopes(tree: ast.AST) -> set[str]:
    """先扫一遍，收集「出现过空体 except」的作用域。

    必须两趟：单趟按源码序走，`return {"ok": True}` 写在 except 之前的函数就漏判了。
    """
    parents = {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and _handler_kind(node) and _is_trivial_suppression(node):
            chain: list[str] = []
            ancestor = parents.get(id(node))
            while ancestor is not None:
                if isinstance(ancestor, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    chain.append(ancestor.name)
                ancestor = parents.get(id(ancestor))
            out.add(".".join(reversed(chain)))
    return out


def scan_source(rel_path: str, source: str, config: GateConfig) -> list[Violation]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:  # 语法错本身就让 import 冒烟红，这里只报一条
        return [Violation("syntax-error", rel_path, exc.lineno or 0, "<module>", str(exc.text or "").strip(), RULE_ERROR)]
    visitor = _Visitor(rel_path, config)
    visitor.suppressed |= _suppress_scopes(tree)
    visitor.visit(tree)
    return visitor.violations


def scan_file(path: Path, repo_root: Path, config: GateConfig) -> list[Violation]:
    rel = path.relative_to(repo_root).as_posix()
    # utf-8-sig：Python 的导入器会自行吞掉 BOM，`ast.parse` 不会。
    # 实测 doubao-butler 的 `roles/store.py`、`tts/edge_tts.py` 带 BOM——用 utf-8 读会报出两条假 syntax-error。
    return scan_source(rel, path.read_text(encoding="utf-8-sig", errors="replace"), config)
