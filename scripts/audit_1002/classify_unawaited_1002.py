"""未 await 普查在册 30 枚：逐枚点开＋会咬的门（审计 2026-10-02 第六批）。

普查尺 `probe_unawaited_coro_calls.py` 自己写明：它按**末段名**捞候选，同名同步体会混进来，
判定权在点开它的人。本脚本把那次点开**工具化**：先把每枚的料摆出来（包围函数／接收者写法／
包装链／返回值去向），再把接收者解析到具体的类，最后给每枚一个 verdict。

verdict 五种，其中两种算红：
  SYNC_OK          解析到本仓的 `def`（同步体）⇒ 不是缺陷
  EXTERNAL_SYNC    解析到外部库，按登记过的同步性放行（来源见下）
  WRAPPED          已被 await / create_task / ensure_future / run_* / gather / wait_for / shield 包着
  ASYNC_UNAWAITED  解析到 `async def` 且没人接 ⇒ **真缺陷**（红）
  UNKNOWN          解析不出来 ⇒ **也算红**：「我看不出」⛔ 记成「没问题」

认得的绑定形状（超出的一律 UNKNOWN，⛔ 猜）：
  R1 `x = Cls(...)`                     局部构造
  R2 `x = f()` 取 f 的返回注解           `conn = get_conn()` → `-> sqlite3.Connection`
  R3 `self.a = <带注解的形参>`           `def __init__(self, tv: TVClient): self.tv = tv`
  R4 `obj.a = <R1|R2>`（全树扫）        `rt.mqtt = mqtt` + `mqtt = MQTTClient(...)`
  R5 模块名.函数                        `write_failures.record(...)`
  R6 链式外部返回                       `c = conn.cursor()` → `sqlite3.Cursor`
  R7 端口缺方法时的兜底：扫全树 `*.a =` 收集**真能接这枚方法**的候选类；
     候选齐一色（全同步／全异步）才下判定，混色或空 ⇒ UNKNOWN
  R8 `x = getattr(obj, "a", 默认)`      字面量属性名才认（`getattr(rt, "ilink_bridge", None)`）
  R9 `a, b, c = f()`                    进 f 的 `return (…)` 按位置取那一元的类
     ——R6/R7/R8/R9 都只走**字面量**能确定的形状；动态拼接的属性名一律 UNKNOWN

EXTERNAL_SYNC 三族的来源（⛔ 凭常识放行）：
  sqlite3 —— `butler/store/db.py:35 def get_conn() -> sqlite3.Connection`（stdlib 同步 API）
  subprocess.run —— stdlib 同步
  APScheduler —— 容器内现读：`inspect.iscoroutinefunction(AsyncIOScheduler.start)=False`、
    `.shutdown=False`（`/usr/local/lib/python3.11/site-packages/apscheduler/schedulers/asyncio.py`，
    BaseScheduler 两枚同读 False）⇒ 这条只在**容器那棵树**成立，换版本要重读。

用法：
  python3 scripts/audit_1002/classify_unawaited_1002.py [<仓根>]
  python3 scripts/audit_1002/classify_unawaited_1002.py --gate [<仓根>]   # 有红则 rc=1
验收在 `tests/test_audit_1002_batch6_gate.py`。
"""
from __future__ import annotations

import ast
import pathlib
import re
import shutil
import sys

PKG = "butler"

# 批5 之后普查在册的 30 枚（原样抄自台账 §6；行号漂了我自己报 DRIFT，⛔ 手改名单）
SITES = [
    ("api/agent_routes.py", 159, "delete_alias"),
    ("api/cron_task_routes.py", 56, "register_api"),
    ("api/cron_task_routes.py", 70, "unregister_api"),
    ("api/doubao_webhook.py", 760, "execute"),
    ("app.py", 267, "run"),
    ("app.py", 464, "start"),
    ("app.py", 539, "start"),
    ("app.py", 740, "start"),
    ("app.py", 755, "start"),
    ("app.py", 763, "start"),
    ("app.py", 781, "stop"),
    ("app.py", 784, "stop"),
    ("app.py", 788, "shutdown"),
    ("core/dialog.py", 518, "notify"),
    ("core/event_stream.py", 243, "execute"),
    ("core/event_stream.py", 251, "record"),
    ("core/perception_learn.py", 192, "execute"),
    ("core/perception_learn.py", 198, "execute"),
    ("core/perception_learn.py", 213, "execute"),
    ("integrations/ilink/api.py", 120, "stop"),
    ("integrations/ilink/api.py", 138, "start"),
    ("integrations/ilink/api.py", 153, "stop"),
    # 302→309→320：批7（2026-10-02）给 TVPopupPort 补 notify 声明把这段推移了 7 行；
    # 批10（裁②，同日）给 _to_bark 加 bark 闸门又推 11 行。两次锚腿都当场判 UNKNOWN/DRIFT
    # 逼重看，重看点开结果仍是 sync 的 TVClient.notify（判定不变）。
    ("notify/router.py", 320, "notify"),
    ("proactive/engine.py", 301, "execute"),
    ("proactive/engine.py", 360, "execute"),
    # 411→424：批10（裁②）在 run() 里加模式闸门 +13 行，_push_tv 内的 rt.tv.notify 随之推移。
    ("skills/runner.py", 424, "notify"),
    ("tools/registry.py", 1074, "notify"),
    ("tools/schedule.py", 105, "notify"),
    ("triggers/engine.py", 175, "record"),
    ("triggers/engine.py", 434, "record"),
]

WRAPPERS = ("create_task", "ensure_future", "run_coroutine_threadsafe",
            "run_until_complete", "gather", "wait_for", "shield")

EXTERNAL: dict[str, dict[str, str]] = {
    "sqlite3.Connection": {"execute": "sync", "executemany": "sync", "cursor": "sync",
                           "commit": "sync", "close": "sync", "executescript": "sync"},
    "sqlite3.Cursor": {"execute": "sync", "executemany": "sync",
                       "fetchone": "sync", "fetchall": "sync", "close": "sync"},
    "subprocess": {"run": "sync", "Popen": "sync", "check_output": "sync"},
    "AsyncIOScheduler": {"start": "sync", "shutdown": "sync", "add_job": "sync",
                         "remove_job": "sync", "get_job": "sync"},
}
EXTERNAL_RETURN: dict[tuple[str, str], str] = {
    ("sqlite3.Connection", "cursor"): "sqlite3.Cursor",
}


# ------------------------------------------------------------------ AST 基底

def link_parents(tree):
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child.parent = node
    return tree


def enclosing(node, kinds):
    cur = getattr(node, "parent", None)
    while cur is not None:
        if isinstance(cur, kinds):
            return cur
        cur = getattr(cur, "parent", None)
    return None


def func_of(node):
    return enclosing(node, (ast.FunctionDef, ast.AsyncFunctionDef))


def class_of(node):
    return enclosing(node, (ast.ClassDef,))


def callee_leaf(func):
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def chain(node):
    """`rt.mqtt.publish` → ["rt","mqtt","publish"]；不是纯名字链 ⇒ None。"""
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return list(reversed(parts))
    return None


def wrapper_on_chain(node):
    cur = getattr(node, "parent", None)
    hops = 0
    while cur is not None and hops < 12:
        if isinstance(cur, ast.Await):
            return "await"
        if isinstance(cur, (ast.AsyncFor, ast.AsyncWith)):
            return "async-context"
        if isinstance(cur, ast.Call) and callee_leaf(cur.func) in WRAPPERS:
            return callee_leaf(cur.func)
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return ""
        cur = getattr(cur, "parent", None)
        hops += 1
    return ""


def ann_name(ann):
    if isinstance(ann, ast.Name):
        return ann.id
    if isinstance(ann, ast.Attribute):
        c = chain(ann)
        return ".".join(c) if c else None
    if isinstance(ann, ast.Subscript):
        return ann_name(ann.value)
    if isinstance(ann, ast.BinOp):          # `Port | None` ⇒ 取第一个点得出名的分支
        return ann_name(ann.left) or ann_name(ann.right)
    return None


class Tree:
    """一棵包目录：文件 AST、类索引、函数索引、模块点号索引、每文件 import 表。"""

    def __init__(self, pkg: pathlib.Path):
        self.pkg = pathlib.Path(pkg)
        self.trees: dict[str, ast.Module] = {}
        self.classes: dict[str, list[tuple[str, ast.ClassDef]]] = {}
        self.funcs: dict[str, list[tuple[str, ast.AST]]] = {}
        self.mods: dict[str, str] = {}
        self._imports: dict[str, dict[str, str]] = {}
        self._load()

    def _load(self):
        for path in sorted(self.pkg.rglob("*.py")):
            rel = str(path.relative_to(self.pkg)).replace("\\", "/")
            try:
                tree = link_parents(ast.parse(path.read_text(encoding="utf-8")))
            except (SyntaxError, UnicodeDecodeError) as exc:
                print(f"PARSE-SKIP {rel} {exc}")
                continue
            self.trees[rel] = tree
            pkgname = self.pkg.name
            dotted = pkgname if rel == "__init__.py" else pkgname + "." + rel[:-3].replace("/", ".")
            self.mods[dotted] = rel
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    self.classes.setdefault(node.name, []).append((rel, node))
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.funcs.setdefault(node.name, []).append((rel, node))
            imp = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for al in node.names:
                        imp[al.asname or al.name.split(".")[0]] = al.name
                elif isinstance(node, ast.ImportFrom):
                    for al in node.names:
                        imp[al.asname or al.name] = ((node.module or "") + "." + al.name)
            self._imports[rel] = imp

    def imports(self, rel):
        return self._imports.get(rel, {})

    def is_internal_module(self, dotted):
        return dotted in self.mods

    def module_file(self, dotted):
        return self.mods.get(dotted)


class Idx:
    """`a, b, c = f()` 里某一元的绑定：RHS 是那次的右值，i 是位置。"""

    def __init__(self, rhs, i):
        self.rhs = rhs
        self.i = i


def scope_assigns(fn, cls):
    """作用域内 {目标: RHS | Idx(RHS, 位置)}；`self.x` 记成 "self.x"。"""
    out = {}
    root = fn if fn is not None else cls
    if root is None:
        return out
    for node in ast.walk(root):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    out.setdefault(tgt.id, node.value)
                elif isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name) \
                        and tgt.value.id == "self":
                    out.setdefault("self." + tgt.attr, node.value)
                elif isinstance(tgt, ast.Tuple):
                    for i, elt in enumerate(tgt.elts):
                        if isinstance(elt, ast.Name):
                            out.setdefault(elt.id, Idx(node.value, i))
    if cls is not None:
        for node in cls.body:
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id not in out:
                        out[tgt.id] = node.value
    return out


def param_ann(fn, name):
    if fn is None:
        return None
    for a in fn.args.args + fn.args.posonlyargs + fn.args.kwonlyargs:
        if a.arg == name and a.annotation is not None:
            return ann_name(a.annotation)
    return None


# ------------------------------------------------------------------ 接收者解析

def return_ann_of(fname, tree: Tree):
    """`def get_conn() -> sqlite3.Connection` ⇒ 只认**同步 def** 的返回注解。"""
    for _, node in tree.funcs.get(fname, []):
        if isinstance(node, ast.FunctionDef) and node.returns is not None:
            nm = ann_name(node.returns)
            if nm:
                return nm
    return None


def resolve_class(expr, tree: Tree, rel, fn, cls, depth=0):
    """表达式 → 类名（本仓类名／`sqlite3.Connection`／`AsyncIOScheduler` 这样的叶子名）。"""
    if expr is None or depth > 4:
        return None
    if isinstance(expr, ast.Call):
        if isinstance(expr.func, ast.Name) and expr.func.id == "getattr" and len(expr.args) >= 2:
            key = expr.args[1]                     # R8：getattr(rt, "ilink_bridge", None)
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                owner = resolve_class(expr.args[0], tree, rel, fn, cls, depth + 1)
                return _attr_owner(key.value, tree, owner)
        head = callee_leaf(expr.func)
        if isinstance(expr.func, ast.Name):
            if head in tree.classes:
                return head
            ann = return_ann_of(head, tree)
            if ann:
                return ann
            return head if head[:1].isupper() else None
        owner = resolve_class(expr.func.value, tree, rel, fn, cls, depth + 1)
        meth = expr.func.attr if isinstance(expr.func, ast.Attribute) else ""
        ret = EXTERNAL_RETURN.get((owner, meth)) if owner else None
        if ret:
            return ret
        if owner and _method_of(owner, meth, tree):        # obj.make() → 那方法的返回注解
            _, mnode = _method_of(owner, meth, tree)
            return ann_name(mnode.returns) if mnode.returns else None
        return None
    if isinstance(expr, ast.Name):
        got = param_ann(fn, expr.id)
        if got:
            return got
        own = scope_assigns(fn, cls)
        rhs = own.get(expr.id)
        if isinstance(rhs, Idx):
            return _tuple_element_class(rhs, tree, rel, fn, cls, depth)
        if rhs is not None and rhs is not expr:
            return resolve_class(rhs, tree, rel, fn, cls, depth + 1)
        return return_ann_of(expr.id, tree)
    if isinstance(expr, ast.Attribute):
        parts = chain(expr)
        if parts:
            return _resolve_parts(parts, tree, rel, fn, cls, depth)
        if isinstance(expr.value, ast.Call):        # get_runtime().cron_task_executor
            owner = resolve_class(expr.value, tree, rel, fn, cls, depth + 1)
            return _attr_owner(expr.attr, tree, owner)
        return None
    return None


def _tuple_element_class(idx: Idx, tree: Tree, rel, fn, cls, depth):
    """R9：`a, b, c = f()` ⇒ 进 f 的 `return (…)` 按位置取那一元的类。"""
    if not isinstance(idx.rhs, ast.Call) or depth > 4:
        return None
    fname = callee_leaf(idx.rhs.func)
    for rel2, fnode in tree.funcs.get(fname, []):
        for ret in ast.walk(fnode):
            if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Tuple):
                if idx.i < len(ret.value.elts):
                    return resolve_class(ret.value.elts[idx.i], tree, rel2,
                                         fnode, class_of(fnode), depth + 1)
    return None


def _resolve_parts(parts, tree: Tree, rel, fn, cls, depth):
    root, rest = parts[0], parts[1:]
    if root == "self":
        owner = cls.name if cls is not None else None
        if not rest:
            return owner
        return _walk(owner, rest, tree, depth) if owner else None
    owner = resolve_class(ast.Name(id=root), tree, rel, fn, cls, depth + 1)
    if owner is None:
        dotted = tree.imports(rel).get(root)
        if dotted and not tree.is_internal_module(dotted):
            owner = root                       # 外部模块名（subprocess…）
    if not rest:
        return owner or (root if tree.imports(rel).get(root) else None)
    if owner is None:
        owner = _attr_owner(root, tree, None)
    return _walk(owner, rest, tree, depth) if owner else None


def _walk(owner, attrs, tree: Tree, depth):
    cur = owner
    for a in attrs:
        if cur is None:
            return None
        cur = _attr_owner(a, tree, cur)
    return cur


def _attr_class_in(cls, attr, tree: Tree, rel, depth):
    for node in ast.walk(cls):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Attribute) and tgt.attr == attr:
                got = resolve_class(node.value, tree, rel, func_of(node), class_of(node) or cls,
                                    depth + 1)
                if got:
                    return got
    return None


def _attr_bindings(tree: Tree, attr, depth=1):
    """全树扫 `*.attr = RHS`（含 self.attr 与 rt.attr）⇒ RHS 能解析成的类集合。"""
    found = set()
    for rel2, tr in tree.trees.items():
        for node in ast.walk(tr):
            if not isinstance(node, ast.Assign):
                continue
            for tgt in node.targets:
                if isinstance(tgt, ast.Attribute) and tgt.attr == attr:
                    got = resolve_class(node.value, tree, rel2, func_of(node),
                                        class_of(node), depth)
                    if got:
                        found.add(got)
    return found


_attr_cache: dict[str, set] = {}


def _attr_owner(attr, tree: Tree, owner_cls):
    """`owner_cls.attr` 的类：先在 owner_cls 里找 `self.attr=`／`attr=`，再全树扫 `*.attr=`。"""
    if owner_cls:
        for rel2, cls in tree.classes.get(owner_cls, []):
            got = _attr_class_in(cls, attr, tree, rel2, 0)
            if got:
                return got
    if attr not in _attr_cache:
        _attr_cache[attr] = _attr_bindings(tree, attr)
    found = _attr_cache[attr]
    return next(iter(found)) if len(found) == 1 else None


def _method_of(owner, name, tree: Tree):
    if not owner or "." in owner or owner in EXTERNAL:
        return None
    for rel2, cls in tree.classes.get(owner, []):
        for item in cls.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name:
                return rel2, item
    return None


def _candidates_for_attr(attr, name, tree: Tree):
    """R7：全树 `*.attr =` 的绑定里，留下**真能接 name() 这枚方法**的候选类。"""
    return {c for c in _attr_bindings(tree, attr) if _method_of(c, name, tree)}


def _module_func(tree: Tree, rel, parts, name):
    """R5：`write_failures.record(...)`，write_failures 是本文件 import 的内部模块。"""
    if len(parts) != 2:
        return None
    dotted = tree.imports(rel).get(parts[0])
    target = tree.module_file(dotted) if dotted else None
    if not target:
        return None
    tr = tree.trees.get(target)
    if tr is None:
        return None
    for item in tr.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name:
            return target, item
    return None


# ------------------------------------------------------------------ 单枚判定

def site_call(tree: Tree, rel, lineno, name):
    tr = tree.trees.get(rel)
    if tr is None:
        return None
    for node in ast.walk(tr):
        if not isinstance(node, ast.Call):
            continue
        lines = {node.lineno}
        if isinstance(node.func, ast.Attribute):
            lines.add(node.func.lineno)
        if callee_leaf(node.func) != name or lineno not in lines:
            continue
        return node
    return None


def verdict(site, name, v, where="", why=""):
    return {"site": site, "name": name, "verdict": v, "where": where, "why": why}


def classify(tree: Tree, rel, lineno, name):
    site = f"{rel}:{lineno}"
    call = site_call(tree, rel, lineno, name)
    if call is None:
        return verdict(site, name, "UNKNOWN", "", "DRIFT：这一行 AST 里没有该名字的 Call")
    wrap = wrapper_on_chain(call)
    if wrap:
        return verdict(site, name, "WRAPPED", wrap, "已被异步原语包着")
    fn, cls = func_of(call), class_of(call)
    full = chain(call.func)
    if full is None:
        return verdict(site, name, "UNKNOWN", ast.unparse(call.func)[:40], "调用目标不是名字链")

    if len(full) == 1:                                  # 裸函数 name(...)
        node = None
        for rel2, n in tree.funcs.get(name, []):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node = (rel2, n)
                break
        if node is None:
            return verdict(site, name, "UNKNOWN", name, "找不到同名 def")
        kind = "async" if isinstance(node[1], ast.AsyncFunctionDef) else "sync"
        return verdict(site, name,
                       "ASYNC_UNAWAITED" if kind == "async" else "SYNC_OK",
                       f"{node[0]}:{node[1].lineno}", f"模块级 def 是 {kind}")

    if mf := _module_func(tree, rel, full, name):
        kind = "async" if isinstance(mf[1], ast.AsyncFunctionDef) else "sync"
        return verdict(site, name,
                       "ASYNC_UNAWAITED" if kind == "async" else "SYNC_OK",
                       f"{mf[0]}:{mf[1].lineno}", "模块函数")

    owner = _resolve_parts(full[:-1], tree, rel, fn, cls, 0)
    node = _method_of(owner, name, tree) if owner else None
    if node:
        kind = "async" if isinstance(node[1], ast.AsyncFunctionDef) else "sync"
        discarded = isinstance(getattr(call, "parent", None), ast.Expr)
        if kind == "async" and discarded:
            return verdict(site, name, "ASYNC_UNAWAITED", f"{node[0]}:{node[1].lineno}",
                           "协程对象创建后丢弃")
        return verdict(site, name, "SYNC_OK" if kind == "sync" else "WRAPPED",
                       f"{node[0]}:{node[1].lineno} {kind}",
                       "同步体" if kind == "sync" else "协程有人接（存变量/传参）")
    if owner and EXTERNAL.get(owner, {}).get(name) == "sync":
        return verdict(site, name, "EXTERNAL_SYNC", f"{owner}.{name}",
                       "外部库同步 API（来源见文件头）")
    if not node and len(full) >= 2:                     # R7 兜底：按属性名找真能接的候选类
        cands = _candidates_for_attr(full[-2], name, tree)
        kinds = set()
        for c in cands:
            mnode = _method_of(c, name, tree)[1]
            kinds.add("async" if isinstance(mnode, ast.AsyncFunctionDef) else "sync")
        if kinds == {"sync"}:
            return verdict(site, name, "SYNC_OK", ",".join(sorted(cands)),
                           f"声明类型缺这枚方法；按 `*.{full[-2]} =` 的实绑定候选判同步")
        if kinds == {"async"}:
            return verdict(site, name, "ASYNC_UNAWAITED", ",".join(sorted(cands)), "候选全异步")
        return verdict(site, name, "UNKNOWN", f"{owner or '?'}.{name}",
                       f"候选类 {sorted(cands)} 混色" if cands else
                       f"解析到 `{owner or '?'}.{name}`：无该类方法，也没扫到能接它的属性绑定")
    return verdict(site, name, "UNKNOWN", f"{owner or '?'}.{name}", "解析不出接收者")


# ------------------------------------------------------------------ 对外接口

def gate(pkg, sites):
    tree = pkg if isinstance(pkg, Tree) else Tree(pathlib.Path(pkg))
    rows = []
    for rel, lineno, name in sites:
        row = classify(tree, rel, lineno, name)
        row["recv"] = f"{rel}:{lineno} {name}"
        rows.append(row)
    return rows


def verdict_counts(rows):
    out = {}
    for r in rows:
        out[r["verdict"]] = out.get(r["verdict"], 0) + 1
    return out


def def_at(path: pathlib.Path, name: str, cls: str = ""):
    """锚点尺：`path` 里（可挂在 `cls` 下的）那枚 def ⇒ kind + lineno。"""
    tr = link_parents(ast.parse(pathlib.Path(path).read_text(encoding="utf-8")))
    if cls:
        for node in ast.walk(tr):
            if isinstance(node, ast.ClassDef) and node.name == cls:
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name:
                        return {"kind": "async" if isinstance(item, ast.AsyncFunctionDef) else "sync",
                                "lineno": item.lineno}
        return None
    for item in tr.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name:
            return {"kind": "async" if isinstance(item, ast.AsyncFunctionDef) else "sync",
                    "lineno": item.lineno}
    return None


# ------------------------------------------------------------------ 负控夹具

_FIX = {
    "pkg/__init__.py": "",
    "pkg/svc.py": (
        "class Bridge:\n"
        "    def start(self) -> None:\n"
        "        pass\n\n"
        "    def stop(self) -> None:\n"
        "        pass\n\n\n"
        "class Worker:\n"
        "    async def start(self) -> None:\n"
        "        pass\n\n"
        "    async def run(self) -> None:\n"
        "        pass\n"
    ),
    "pkg/main.py": (
        "import asyncio\n"
        "from pkg.svc import Bridge, Worker\n\n\n"
        "async def lifespan():\n"
        "    bridge = Bridge()\n"
        "    worker = Worker()\n"
        "    bridge.start()                          # S1:SYNC_OK:start\n"
        "    worker.start()                          # S2:ASYNC_UNAWAITED:start\n"
        "    await worker.run()                      # S3:WRAPPED:run\n"
        "    asyncio.create_task(worker.start())     # S4:WRAPPED:start\n"
    ),
}

_SITE_RE = re.compile(r"# (S\d+):([A-Z_]+):(\w+)")


def fixture_sites(text):
    """夹具行号**现取**（`# S1:期望:方法名`）⇒ 夹具挪行不会把负控变成假测。"""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        m = _SITE_RE.search(line)
        if m:
            out.append({"id": m.group(1), "expect": m.group(2), "name": m.group(3),
                        "site": f"main.py:{i}"})
    return out


def self_check(tmp) -> None:
    """负控：合成包那几枚必须逐枚点名为夹具里写的期望；错任意一枚 ⇒ raise。"""
    tmp = pathlib.Path(tmp)
    pkg = tmp / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    for rel, text in _FIX.items():
        (tmp / rel).write_text(text, encoding="utf-8")
    want = fixture_sites(_FIX["pkg/main.py"])
    if len(want) < 4:
        raise AssertionError(f"SELFTEST|夹具点位只取到 {len(want)} 枚，期望 ≥4")
    rows = gate(pkg, [(w["site"].split(":")[0], int(w["site"].split(":")[1]), w["name"])
                      for w in want])
    got = {r["site"]: r["verdict"] for r in rows}
    bad = [f"{w['id']}@{w['site']}: 期望 {w['expect']} 实得 {got.get(w['site'], '<缺>')}"
           for w in want if got.get(w["site"]) != w["expect"]]
    if len(rows) != len(want):
        bad.append(f"枚数 {len(rows)} != {len(want)}")
    if bad:
        raise AssertionError("SELFTEST|" + "; ".join(bad))


RED = ("ASYNC_UNAWAITED", "UNKNOWN")


def main(argv):
    gate_flag = "--gate" in argv
    pos = [a for a in argv[1:] if not a.startswith("--")]
    base = pathlib.Path(pos[0]) if pos else pathlib.Path(".")
    pkg = base / PKG
    if not pkg.is_dir():
        print(f"NO-TREE {pkg}")
        return 1
    rows = gate(Tree(pkg), SITES)
    for r in rows:
        print(f"{r['site']} [{r['name']}] {r['verdict']:<15} where={r['where']} :: {r['why']}")
    counts = verdict_counts(rows)
    red = sum(counts.get(k, 0) for k in RED)
    tree_count = len([p for p in pkg.rglob("*.py")])
    print("TOTAL|" + " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
          + f" sites={len(SITES)} rows={len(rows)} py_files={tree_count} red={red}")
    scratch = base / ".audit_selfcheck"
    try:
        self_check(scratch)
        print("SELFTEST|PASS")
    except AssertionError as exc:
        print(str(exc))
        red += 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return 1 if (gate_flag and red) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
