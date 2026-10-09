#!/usr/bin/env python3
"""批86 腿②：把回归门里「跳过族」的钉值改成**从源码现读**的派生尺＋名册闭合判据。

为什么要有这件：`run_b85_regression_1009.sh` 的 OWN／B33／B36／OTHER 四枚 skip 期望值是手钉常数。
批87 解锁那 31 枚「ModuleNotFoundError」之后，那几档第一次真跑⇒方法级跳过第一次被执行⇒跳过行数
自然变多，钉值就成了台账 §101 的 B4 那一桶（在册尺常量漂＝尺的错，⛔ 生产红灯）。

判据形状（跑过一遍才定下来的）：⛔「跳过行数＝某枚数」当闸——那个数是**这一面的依赖状态**决定的
（缺哪个包、哪一档折叠成一条），换一面就漂；能静态判出的只有**声明名册**。所以本尺吃两样：
  名册（`--explain-from`）＝运行面每一行跳过都必须在**当前源码**里有来路，对不上⇒UNMATCHED＝该咬的；
  计数（`--key`）＝逐族声明数，只当对账视图⛔ 当判据（下面那些建模全是为它服务的）。
数法照 unittest 的收集规则建模（⛔ 拿「声明处个数」当「运行时行数」，两者在有继承时不等）：
  class  —— 类上的 `skip/skipIf/skipUnless`：一行＝该被收类**可执行的 test_\\* 方法**（含继承来的）；
  method —— 方法自己的装饰器或方法体里第一处 `skipTest`／`SkipTest`：一行＝每个真跑到它的被收类；
             同一个声明被两个被收类继承到＝运行时两行，本尺也跟着数两行；
  module —— 模块层 `raise SkipTest(...)`（含 `try: import … / except ImportError: raise …` 的 except 分支，
             只看 tree.body 直子会把这一族全读成「没声明」）：运行时整档一条，⛔ 当方法数算；
             同一档内的类级／方法级声明照数（声明是源码事实），只记 FOLD_CANDIDATES＝那一面可能折叠成一条；
             ⛔ 静态判得出折叠（取决于这一面缺哪个依赖），所以⛔ 从名册里删——删了就是把「有声明」读成「没声明」。
来路两条腿，任一命中即算有来路（一条断了另一条还在，⛔ 单腿定生死）：
  qid  —— 运行面括号里那枚限定名 `mod.Cls.test_x`（带文档串的格子它在上物理一行⇒要回溯前一行）；
  字面段—— 原因文本对得上某格声明的连续字面片段（按序、⛔ 全等）。整档跳过那格的 qid 是 loader
          自造的 `unittest.loader._SkippedTest`，只有这条腿认得它；反过来纯插值的声明
          （`f"{exc}"`＝一点字面都没有）只有 qid 那条腿认得，⛔ 拿空段去比＝恒真的尺。
只吃被收类（基类链能落到 TestCase／object 的）；Mixin（非 TestCase 子句）自己不跑，只通过子页数。
⛔ 猜基类：链上有取不到的基名就记 UNRESOLVED_BASE 并**点名**（派生值可能偏低，⛔ 当全集读）。
目录边界与收集器同形：根目录自己的 `test_*.py` 全吃，子目录要有 `__init__.py` 才下去
（`tests/contract/` 无 `__init__.py`⇒discover 收不到，那几枚由 scripts/contract_gate.sh 单跑）。

`--self-test`＝合成夹具校准，两侧都要过：应放行（八枚键的数逐一对上、名册收齐三桶＋继承那格、
合成运行面每一行都有来路）＋应咬住（注释与文档串里的同名字样⛔ 计数、被类装饰器盖住的方法体声明
⛔ 重复计数、Mixin⛔ 自己进名册、没声明过的键必须 0、造一行没来路的幽灵行必须落进 unmatched）。
⛔ 只跑真树——恒绿与恒红都不改变真树读数。
"""
import argparse
import ast
import os
import re
import sys
import tempfile

NOLIT = object()   # 声明在、原因字面量取不到；⛔ 与「没声明」（None）混成同一个值
KNOWN_CASE_BASES = {"TestCase", "unittest.TestCase", "AsyncTestCase",
                    "unittest.IsolatedAsyncioTestCase", "IsolatedAsyncioTestCase", "object"}


def _func_name(node):
    """基类名的点号形状（`unittest.TestCase`／`BaseFor`）；skip 判定⛔ 走这里（见 _skip_kind）。"""
    if isinstance(node, (ast.Attribute, ast.Name)):
        return _dotted(node)
    return ""


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return "%s.%s" % (_dotted(node.value), node.attr)
    return ""


# `%` 转换符（含 `%%` 本身）：格式化串就按这些位置切字面段。
_FORMAT_SPEC_RE = re.compile(r"%(?:%|[-+ #0]*[0-9*]*(?:\.[0-9*]+)?[hlL]?[diouxXeEfFgGcrsa])")


def _split_format(text):
    """`%` 格式化串按**转换符**切成字面段：`"…（%s）⇒ …" % exc` 的字面段是两段，⛔ 是含 `%s` 的整串。

    批42 现证：整串当一段⇒与运行时那一行永远不相等⇒「% 格式化」这一族整族落 unmatched。
    右侧实参的文本落在运行时那个洞里，位置由转换符决定⇒⛔ 跟着当后段比（顺序会错），只记「有洞」。
    """
    out, pos = [], 0
    for m in _FORMAT_SPEC_RE.finditer(text):
        out.append(text[pos:m.start()])
        pos = m.end()
    out.append(text[pos:])
    return [s for s in out if s]


def _literal_segments(node):
    """抠出**有序字面片段**，返回 (片段列表, 有插值洞否)。

    现树多数 skip 文案是 f-string／拼接（把异常种类插在中间），运行时整串与源码字面段永远不相等，
    所以匹配口径必须是「每段字面按序出现在那行里」，⛔ 拼成一整串再比（那比的是不存在的那串字）。
    """
    if isinstance(node, ast.Constant):
        return ([node.value] if isinstance(node.value, str) else []), not isinstance(node.value, str)
    if isinstance(node, ast.JoinedStr):
        segs, hole = [], False
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                segs.append(v.value)
            else:
                hole = True
        return segs, hole
    if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod)
            and isinstance(node.left, ast.Constant) and isinstance(node.left.value, str)):
        return _split_format(node.left.value), True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        lt, lh = _literal_segments(node.left)
        rt, rh = _literal_segments(node.right)
        return lt + rt, (lh or rh)
    return [], True


def _reason_from(call, arg_index):
    cand = None
    if len(call.args) > arg_index:
        cand = call.args[arg_index]
    for kw in call.keywords or []:
        if kw.arg == "reason":
            cand = kw.value
    if cand is None:
        return NOLIT, True
    segs, hole = _literal_segments(cand)
    return (segs, hole) if segs else (NOLIT, True)


SKIP_SHAPE = {"skip": "skip", "skipIf": "cond", "skipUnless": "cond",
              "SkipTest": "SkipTest", "skipTest": "skipTest"}
MODULE_HOLDERS = ("unittest", "builtins")


def _skip_kind(node):
    """skip 声明的形状：None／'skipTest'／'SkipTest'／'skip'／'cond'（skipIf／skipUnless）。

    三种写法都要认：`self.skipTest(...)`、裸名 `SkipTest(...)`、带模块的 `unittest.skipIf(...)`；
    ⛔ 用「末段名字」一刀切认（那会把别人家的 `foo.skipIf` 也算进来）。
    """
    if not isinstance(node, ast.Call):
        return None
    f = node.func
    if isinstance(f, ast.Name):
        return SKIP_SHAPE.get(f.id)
    if isinstance(f, ast.Attribute):
        holder = _dotted(f.value)
        if f.attr == "skipTest" and holder == "self":
            return "skipTest"
        if holder in MODULE_HOLDERS:
            return SKIP_SHAPE.get(f.attr)
    return None


def _reason_of(node, kind):
    return _reason_from(node, 1 if kind == "cond" else 0)


def _call_target(node):
    """调用目标的可比名：`foo(...)`／`mod.foo(...)` 一律取末段（跨模块辅助函数靠名字对上）。"""
    if not isinstance(node, ast.Call):
        return ""
    dotted = _dotted(node.func)
    return dotted.rsplit(".", 1)[-1] if dotted else ""


def _body_nodes(fn):
    """函数体内的语句（含 try／if／except 里的），但⛔ 下沉进嵌套 def／class。

    现树的 `_import_or_skip` 把 `raise SkipTest` 写在 except 分支里：只看 fn.body 直子读不到。
    """
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _module_dec_aliases(tree):
    """模块级别名装饰器：`needs_live = unittest.skipIf(条件, 原因)` ⇒ 后面 `@needs_live` 盖类／盖方法。

    现树（batch22）就是这么写的：形状判定若只认字面 `unittest.skipIf`，`@needs_live` 那一族全读成「没声明」。
    """
    out = {}
    for node in _module_level_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
        kind = _skip_kind(node.value)
        if kind not in ("skip", "cond"):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                out[tgt.id] = node.value
    return out


def _module_raisers(tree):
    """模块级「一调用就整格跳过」的辅助函数：函数体里 raise SkipTest，或调用另一枚已知的这类函数。

    现树（batch24 的 `_import_or_skip`）把声明写在辅助函数里，调用点分布在 setUp／setUpClass／test 体：
    只按「声明处的形状」数，这一族一行都进不了名册。原因字面量取被调那枚的（调用点自己没文案）。
    """
    funcs = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[node.name] = node
    out = {}
    grew = True
    while grew:
        grew = False
        for name, fn in sorted(funcs.items()):
            if name in out:
                continue
            for node in _body_nodes(fn):
                if isinstance(node, ast.Raise) and _skip_kind(node.exc) == "SkipTest":
                    out[name] = _reason_of(node.exc, "SkipTest")
                    grew = True
                    break
                target = _call_target(node)
                if target in out:
                    out[name] = out[target]
                    grew = True
                    break
    return out


def _applier_reason(fn):
    """函数体里写「`<自己的形参>.skipTest(…)`」⇒这枚是「把 case 递进去就跳」的辅助件（batch20 现证）。

    ⛔ 与 `_module_raisers` 混为一谈：那一族是 `raise SkipTest`，这一族是把**调用方的 self** 跳掉，
    调用点必须真把 self 传进来才算（`case.fail` 那一类⛔ 算跳过声明）。
    """
    a = fn.args
    params = set([x.arg for x in list(getattr(a, "posonlyargs", [])) + list(a.args) + list(a.kwonlyargs)])
    for extra in (a.vararg, a.kwarg):
        if extra is not None:
            params.add(extra.arg)
    for node in _body_nodes(fn):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Attribute) and f.attr == "skipTest"
                and isinstance(f.value, ast.Name) and f.value.id in params):
            return _reason_of(node, "skipTest")
    return None


def _module_appliers(tree):
    return dict((node.name, _applier_reason(node))
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and _applier_reason(node) is not None)


def _dec_decl(dec, aliases):
    """装饰器那一项落到哪枚声明：直接是 skip 调用，或者是指向 skip 调用的别名⇒(调用节点, 形状)。"""
    kind = _skip_kind(dec)
    if kind in ("skip", "cond"):
        return dec, kind
    if isinstance(dec, ast.Name) and dec.id in aliases:
        call = aliases[dec.id]
        return call, _skip_kind(call)
    return None, None


def _test_defs(classdef):
    return [n for n in classdef.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")]


def _class_skip_decorator(classdef, aliases=None):
    for dec in classdef.decorator_list:
        call, kind = _dec_decl(dec, aliases or {})
        if kind in ("skip", "cond"):
            return call, kind
    return None, None


def _method_skip(fn, aliases=None, helpers=None):
    """方法自己的装饰器优先；否则方法体里第一处 skipTest／SkipTest／调用已知「跳过源」辅助件。

    返回 (found, reason, hole, src)：src＝'dec'／'own'／'helper'，helper 那档是**推断**来的
    （名字对上就算，接线方式按形状核），名册因此偏大⇒闭合腿对它只放行不背书，
    逐格记进 roster_via_helper 点名。
    """
    for dec in fn.decorator_list:
        call, kind = _dec_decl(dec, aliases or {})
        if kind in ("skip", "cond"):
            reason, hole = _reason_of(call, kind)
            return True, reason, hole, "dec"
    for node in ast.walk(fn):
        kind = _skip_kind(node)
        if kind in ("skipTest", "SkipTest"):
            reason, hole = _reason_of(node, kind)
            return True, reason, hole, "own"
        target = _call_target(node)
        entry = helpers.get(target) if helpers else None
        if entry is not None and _helper_applies(node, entry[2]):
            return True, entry[0], entry[1], "helper"
    return False, None, None, None


class Index(object):
    """按「模块名→类名→类记录」建表，够把同仓继承链落地的 test_* 名字算全。"""

    def __init__(self):
        self.classes = {}      # (module, class) → dict(defs, own_test_names, bases, collected, skip_dec)
        self.files = 0
        self.notes = {"nonliteral": [], "unresolved_base": [], "nested_class": [],
                      "syntax_error": [], "module_no_init": [], "non_case_class": [],
                      "fold_candidates": [], "dup_qid": [], "roster_via_helper": [],
                      "raiser_name_clash": [],
                      "non_unittest_top_funcs": []}
        self.qids = {}         # 限定名 → 桶（method／class／module）＝闭合腿吃的名册

    def add_file(self, module, path, tree):
        self.files += 1
        top = [n for n in tree.body if isinstance(n, ast.ClassDef)]
        for cls in top:
            self.classes[(module, cls.name)] = {
                "node": cls, "path": path, "module": module,
                "defs": dict((f.name, f) for f in _test_defs(cls)),
                "bases": [_func_name(b) for b in cls.bases],
            }
        for cls in top:
            for nested in [n for n in cls.body if isinstance(n, ast.ClassDef)]:
                self.notes["nested_class"].append((path, "%s.%s" % (cls.name, nested.name)))

    def find_method(self, key, names, seen=None):
        """按 Python 名字解析的顺序找方法定义：本类优先，再顺着基类链（继承来的 setUp 也是这一族）。"""
        rec = self.classes.get(key)
        if rec is None:
            return None
        seen = seen or set()
        if key in seen:
            return None
        seen.add(key)
        for fn in rec["node"].body:
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name in names:
                return fn
        for b in rec["bases"]:
            sub = _resolve(self, rec["module"], b)
            if sub is not None:
                got = self.find_method(sub, names, set(seen))
                if got is not None:
                    return got
        return None

    def is_case(self, key, seen=None):
        rec = self.classes.get(key)
        if rec is None:
            return None
        seen = seen or set()
        if key in seen:
            return None
        seen.add(key)
        if not rec["bases"]:
            return False
        verdict = None
        for b in rec["bases"]:
            if b in KNOWN_CASE_BASES:
                if b != "object":
                    verdict = True
                continue
            sub = _resolve(self, rec["module"], b)
            if sub is None:
                self.notes["unresolved_base"].append((rec["path"], rec["node"].name, b))
                continue
            inner = self.is_case(sub, set(seen))
            if inner is True:
                verdict = True
        return verdict

    def exec_names(self, key):
        """被收类可执行的 test_* 名字（含继承，近的盖远的，与 Python 名字解析同形）。"""
        out = {}
        order = []

        def visit(k):
            rec = self.classes.get(k)
            if rec is None:
                return
            for b in rec["bases"]:
                sub = _resolve(self, rec["module"], b)
                if sub is not None:
                    visit(sub)
            for name in rec["defs"]:
                if name not in out:
                    out[name] = (k, rec["defs"][name])
                    order.append(name)
        visit(key)
        return [out[n] for n in order]


def _resolve(index, module, base):
    """基名解析：先同文件，再看带点号的 `mod.Cls`（按模块后缀或全等匹配）；落不到返回 None。"""
    if (module, base) in index.classes:
        return (module, base)
    if "." in base:
        mod, cls = base.rsplit(".", 1)
        if (mod, cls) in index.classes:
            return (mod, cls)
        for (m, c) in index.classes:
            if c == cls and (m.endswith("." + mod) or m == mod):
                return (m, c)
    return None


def collect(roots, prefix=""):
    """返回 (sites, index)。sites 是 (bucket, reason) 列表。

    `prefix`＝运行面的包前缀：`unittest discover -s tests -t .` 打出的限定名带 `tests.`，
    而 `--root tests` 数出来的模块名不带⇒两把尺的 qid 永不相交（10-09 实犯：by_qid 恒 0）。
    跑法是 `-s <root> -t .` 时，prefix 就填那个目录名。
    """
    index = Index()
    for root in roots:
        for dirpath, dirs, files in os.walk(root):
            dirs.sort()
            rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
            module_dir = "" if rel == "." else rel
            # 与收集器同形的边界：根目录自己的 test_*.py 全吃；子目录要 __init__.py 才进分母。
            collected_dir = (module_dir == "") or ("__init__.py" in files)
            if not collected_dir:
                index.notes["module_no_init"].append(dirpath)
            for f in sorted(files):
                if not (f.startswith("test_") and f.endswith(".py")):
                    continue
                if not collected_dir:
                    continue
                path = os.path.join(dirpath, f)
                module = (module_dir + "." if module_dir else "") + f[:-3]
                if prefix:
                    module = prefix + "." + module
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    src = fh.read()
                try:
                    tree = ast.parse(src, filename=path)
                except SyntaxError as e:
                    index.notes["syntax_error"].append((path, str(e)))
                    continue
                index.add_file(module, path, tree)
                _scan_module_top(index, tree, module, path)
    sites = _derive_sites(index)
    return sites, index


def _module_level_nodes(tree):
    """模块层语句（含 try／if／for 里面的），但⛔ 下沉进 def／class（那是方法级与类级）。

    现树的整档跳过大多写在模块顶层的 `try: import x / except ImportError: raise SkipTest(...)` 里，
    只看 tree.body 的直接子节点会把这一族全读成「没声明」。
    """
    stack = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _scan_module_top(index, tree, module, path):
    """模块顶层的三件事：整档 raise SkipTest、顶层 test_* 函数、别名装饰器＋「跳过源」辅助函数。"""
    index.modules = getattr(index, "modules", {})
    rec = index.modules.setdefault(module, {"module_raises": [], "top_funcs": []})
    for node in _module_level_nodes(tree):
        if isinstance(node, ast.Raise):
            kind = _skip_kind(node.exc)
            if kind == "SkipTest":
                reason, hole = _reason_of(node.exc, kind)
                rec["module_raises"].append((reason, hole))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            rec["top_funcs"].append(node.name)
            index.notes["non_unittest_top_funcs"].append((path, module, node.name))
    rec["dec_aliases"] = _module_dec_aliases(tree)
    rec["raiser_funcs"] = _module_raisers(tree)
    rec["applier_funcs"] = _module_appliers(tree)


SETUP_METHODS = ("setUp", "asyncSetUp")
CLASS_SETUP_METHODS = ("setUpClass",)


def _aliases(index, module):
    mods = getattr(index, "modules", {})
    return mods.get(module, {}).get("dec_aliases", {})


def _all_raisers(index):
    """跨档的「跳过源」辅助函数表：末段名字对上就算（⛔ 精确 import 解析）。

    因此名册对这一族**偏大**：同名的别处函数会被错认⇒每个由此进名册的格子都记进 roster_via_helper 点名，
    ⛔ 把它当「源码逐格声明」读。
    """
    out = {}
    for module, rec in sorted(getattr(index, "modules", {}).items()):
        for fname, val in sorted(rec.get("raiser_funcs", {}).items()):
            if fname in out:
                index.notes["raiser_name_clash"].append((module, fname))
                continue
            out[fname] = (val[0], val[1], "name")
    return out


def _all_appliers(index):
    """跨档的「递 case 进去就跳」辅助件：调用点必须把 self 当**实参**传进来才算（形参名⛔ 参与判定）。"""
    out = {}
    for module, rec in sorted(getattr(index, "modules", {}).items()):
        for fname, val in sorted(rec.get("applier_funcs", {}).items()):
            out.setdefault(fname, (val[0], val[1], "self_arg"))
    return out


def _class_skip_helpers(index, key):
    """类内辅助方法里写 `self.skipTest(...)`（batch32 的 `_handler_result`）：调用点只能是 `self.<name>()`。

    setUp／setUpClass／test_* 自己各有一条腿，⛔ 在这里再挂一遍（挂两遍＝同一格两条 site）。
    """
    rec = index.classes.get(key)
    if rec is None:
        return {}
    out = {}
    for fn in rec["node"].body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name.startswith("test") or fn.name in SETUP_METHODS or fn.name in CLASS_SETUP_METHODS:
            continue
        for node in _body_nodes(fn):
            if isinstance(node, ast.Call) and _skip_kind(node) == "skipTest":
                value, hole = _reason_of(node, "skipTest")
                out.setdefault(fn.name, (value, hole, "self_attr"))
                break
    return out


def _call_helpers(index, key, base):
    """一处方法能碰到的全部「跳过源」：跨档 raise 族＋跨档递 case 族＋本类 self 辅助方法族。"""
    helpers = dict(base)
    for name, val in sorted(_class_skip_helpers(index, key).items()):
        helpers.setdefault(name, val)
    return helpers


def _helper_applies(node, form):
    """调用点这一枚算不算「触发了那处声明」：三种形状各有各的接线方式，⛔ 一律按名字放行。"""
    if form == "name":
        return True
    if form == "self_arg":
        return any(isinstance(a, ast.Name) and a.id == "self" for a in node.args)
    if form == "self_attr":
        return isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
            and node.func.value.id == "self"
    return False


def _class_skip_decl(index, key, seen=None):
    """类级跳过声明：本类装饰器优先，否则顺基类链找——`__unittest_skip__` 是**类属性**，
    子类 getattr 读得到，所以父类被 `@skip` 盖住时子类的每一格都会各出一行。"""
    rec = index.classes.get(key)
    if rec is None:
        return None, None
    seen = seen or set()
    if key in seen:
        return None, None
    seen.add(key)
    call, kind = _class_skip_decorator(rec["node"], _aliases(index, rec["module"]))
    if call is not None:
        return call, kind
    for b in rec["bases"]:
        sub = _resolve(index, rec["module"], b)
        if sub is not None:
            call, kind = _class_skip_decl(index, sub, set(seen))
            if call is not None:
                return call, kind
    return None, None


def _derive_sites(index):
    sites = []
    modules = getattr(index, "modules", {})
    for module, rec in sorted(modules.items()):
        for value, hole in rec["module_raises"]:
            _push(index, sites, "module:" + module, module, "module", value, hole)
    folded_modules = set(m for m, rec in modules.items() if rec["module_raises"])
    base = dict(_all_raisers(index))
    for name, val in sorted(_all_appliers(index).items()):
        base.setdefault(name, val)
    for (module, cname), cls_rec in sorted(index.classes.items()):
        collected = index.is_case((module, cname))
        if collected is not True:
            if collected is None:
                index.notes["non_case_class"].append((cls_rec["path"], cname, "unresolved_or_nested"))
            continue
        key = (module, cname)
        helpers = _call_helpers(index, key, base)
        if module in folded_modules:
            # 同档另有模块级 SkipTest：那一格运行时可能整档折叠成一条⇒只登记，⛔ 从名册里删
            # （删了就等于「源码没声明」，而声明是事实；折叠⛔ 静态判得出，取决于这一面缺哪个依赖）
            index.notes["fold_candidates"].append((cls_rec["path"], cname))
        names = index.exec_names(key)
        qpref = "%s.%s" % (module, cname)
        dec, dkind = _class_skip_decl(index, key)
        if dec is not None:
            value, hole = _reason_of(dec, dkind)
            for _owner, _fn in names:
                _push(index, sites, cls_rec["path"],
                      "%s.%s" % (qpref, _fn.name), "class", value, hole)
            continue   # 类级跳过时方法体一行都不跑⇒②③④ 那几档的 qid 全被这里覆盖了
        setup_fn = index.find_method(key, SETUP_METHODS)
        if setup_fn is not None:
            found, value, hole, src = _method_skip(setup_fn, _aliases(index, module), helpers)
            if found:
                for _owner, _fn in names:
                    qid = "%s.%s" % (qpref, _fn.name)
                    if src == "helper":
                        index.notes["roster_via_helper"].append((cls_rec["path"], qid))
                    _push(index, sites, cls_rec["path"], qid, "class", value, hole)
        css_fn = index.find_method(key, CLASS_SETUP_METHODS)
        if css_fn is not None:
            found, value, hole, src = _method_skip(css_fn, _aliases(index, module), helpers)
            if found:
                if src == "helper":
                    index.notes["roster_via_helper"].append((cls_rec["path"], qpref))
                _push(index, sites, cls_rec["path"], qpref, "module", value, hole)
        for owner, fn in names:
            found, value, hole, src = _method_skip(fn, _aliases(index, owner[0]), helpers)
            if found:
                # qid 用**被收类**那一枚（运行面打的是实例所在的类名），⛔ 用方法定义处那个类：
                # MixIn 自己不跑，它只能通过子页出线——写成定义处的名字就是把名册挂到了不存在的格子上。
                qid = "%s.%s" % (qpref, fn.name)
                if src == "helper":
                    index.notes["roster_via_helper"].append((cls_rec["path"], qid))
                _push(index, sites, cls_rec["path"], qid, "method", value, hole)
    return sites


def _push(index, sites, path, qid, bucket, value, hole):
    """一格声明＝一条 site（qid＝运行面 `(...)` 里那枚限定名，闭合腿吃它）。"""
    if qid in index.qids:
        index.notes["dup_qid"].append((path, qid, bucket, index.qids[qid]))
    else:
        index.qids[qid] = bucket
    if value is NOLIT:
        index.notes["nonliteral"].append((path, qid, bucket))
        sites.append((bucket, qid, None, True))
    else:
        sites.append((bucket, qid, list(value), hole))


def _display(value):
    return "@NOLIT" if value is None else "".join(value)


def _segments_in(segs, text):
    """每段字面**按序**出现在那一行里⇒匹配。有插值洞的声明与运行时整串永不相等，只能这样比。

    ⛔ 全空段（`f"{exc}"` 这种一点字面都没有的声明）：那把尺对任何一行都返回真＝恒真的尺，
    与恒 0 同罪。这类声明只走 qid 名册那条腿。
    """
    lit = [s for s in segs if s]
    if not lit:
        return False
    pos = 0
    for seg in lit:
        i = text.find(seg, pos)
        if i < 0:
            return False
        pos = i + len(seg)
    return True


def match_sites(sites, text):
    """运行面那一行的原因文本↔源码声明：逐段按序命中⇒有来路；一段都对不上⇒没人声明的跳过（这才该咬）。"""
    return [(bucket, qid, _display(value)) for bucket, qid, value, _hole in sites
            if value and _segments_in(value, text)]


def extract_reason(line):
    """从 `-v` 输出的一行里取 `... skipped '…'` 的原因文本（取不到⇒None＝这行不是跳过行）。"""
    i = line.find("... skipped ")
    if i < 0:
        return None
    text = line[i + len("... skipped "):].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1]
    return text or None


def count_for_key(sites, key):
    """按「原因字面量含该键」计数（与尺子原先的 grep -F 同形：子串，⛔ 全等）。"""
    m = c = mo = 0
    for bucket, _qid, value, _hole in sites:
        if value is None or key not in _display(value):
            continue
        if bucket == "method":
            m += 1
        elif bucket == "class":
            c += 1
        else:
            mo += 1
    return m, c, mo


def grouped_reasons(sites):
    from collections import Counter
    ctr = Counter()
    for bucket, _qid, value, hole in sites:
        text = _display(value)[:70]
        ctr[(bucket, text + ("<…有插值洞⇒⛔ 与整串比" if hole and value is not None else ""))] += 1
    return ctr


_QID_RE = re.compile(r"\(([A-Za-z_][\w.]*)\)")


def extract_qid(line, prev_line=""):
    """运行面那一行的限定名：`(mod.Cls.test_x)` 里那枚。

    `unittest -v` 的 description 是 `str(test)` ＋换行＋docstring 首行，所以带文档串的格子
    **限定名在上物理一行、`... skipped` 在本行**⇒比先前的「同行才有 id」多一条回溯腿。
    两行都取不到⇒None（这行只能走原因文本那条腿）。
    """
    for text in (line, prev_line):
        m = _QID_RE.search(text)
        if m and "." in m.group(1):
            return m.group(1)
    return None


def explain_from(path, sites, index):
    """名册闭合：运行面每一行跳过都必须在**当前源码**里有来路（qid 命中名册，或原因字面段命中某格声明）。

    ⛔ 拿「跳过行总数＝某枚钉值」当判据：那个数是这一面的依赖状态决定的，换一面就漂（B4 那一桶）。
    该咬的是「运行面跳了、源码里没人声明」＝无来路的跳过。
    """
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    total = by_qid = by_text = unmatched = no_qid = 0
    bad = []
    for i, ln in enumerate(lines):
        reason = extract_reason(ln)
        if reason is None:
            continue
        total += 1
        qid = extract_qid(ln, lines[i - 1] if i else "")
        hits = match_sites(sites, reason)
        in_roster = bool(qid) and qid in index.qids
        if in_roster:
            by_qid += 1
        elif hits:
            by_text += 1
        else:
            unmatched += 1
            bad.append((i + 1, qid, reason[:90]))
        if not qid:
            no_qid += 1
        tag = "qid" if in_roster else ("text" if hits else "UNMATCHED")
        shown = hits[0][1] if hits else "-"
        print("EXPLAIN|line=%s|route=%s|qid=%s|site=%s|reason=%s"
              % (i + 1, tag, qid or "-", shown, reason[:60].replace("|", "/")))
    for line_no, qid, reason in bad:
        print("UNMATCHED|line=%s|qid=%s|reason=%s" % (line_no, qid or "-", reason.replace("|", "/")))
    print("EXPLAIN_SUM|skip_lines=%s|by_qid=%s|by_text=%s|unmatched=%s|no_qid=%s|roster=%s|sites=%s"
          % (total, by_qid, by_text, unmatched, no_qid, len(index.qids), len(sites)))
    stats = {"skip_lines": total, "by_qid": by_qid, "by_text": by_text,
             "unmatched": unmatched, "no_qid": no_qid}
    return (0 if (unmatched == 0 and total > 0) else 7), stats


# ---------------- 合成夹具：两份文件、七枚声明键＋一枚反对照＋四处同名字样干扰项 ----------------
# 文件一：类级／方法级形状，档内⛔ 模块级 raise（否则整档被抑制，那些格一行都不该数）。
_FIXTURE_MAIN = '''"""fixture：KEY_A 字样出现在文档串里，⛔ 被数进来。"""
import unittest

# 注释里也写 KEY_A，⛔ 被数进来


class MethodLevel(unittest.TestCase):
    """文档串里同样写 KEY_A。"""

    def test_one(self):
        self.skipTest("KEY_A 方法级一格")

    def test_two(self):
        self.skipTest("KEY_A 方法级第二格")

    def test_three(self):
        self.skipTest(None)


@unittest.skipUnless(False, "KEY_B 类级三格")
class ClassLevel(unittest.TestCase):
    def test_a(self):
        pass

    def test_b(self):
        pass

    def test_c(self):
        self.skipTest("KEY_A 已被类装饰器盖住，运行时到不了⇒⛔ 重复计数")


class BaseFor(unittest.TestCase):
    def test_base(self):
        pass


@unittest.skipIf(True, "KEY_D 带基类，继承来那格也要数")
class WithBase(BaseFor):
    def test_own(self):
        pass

    def test_own2(self):
        pass


class MixIn:
    def test_mixin(self):
        self.skipTest("KEY_E 只有被收的子类才真跑")


class ChildOfMixin(MixIn, unittest.TestCase):
    def test_own(self):
        pass


class FStringReason(unittest.TestCase):
    """文档串里写 KEY_F 是干扰项；真声明只有一处，且那处是拼接出来的。"""

    def test_f(self):
        try:
            value = 1 / 0
        except ZeroDivisionError as exc:
            self.skipTest("KEY_F 字面段在头 " + str(exc))
'''

# 文件二：模块级 try／except 里的整档跳过（现树最常见的形状），＋同档内一枚类级声明⇒照数并记 FOLD。
_FIXTURE_MODULE = '''"""fixture 二：模块级 raise 在 except 分支里；同档类级那枚运行时可能被折叠。"""
import unittest

try:
    import homesdk_absent_for_fixture  # noqa: F401
except ImportError as exc:
    raise unittest.SkipTest(f"KEY_C 模块级 try 里整档跳过：{exc}")


@unittest.skipIf(True, "KEY_H 同档另有整档跳过⇒运行时可能折叠成一条")
class InsideSkippedModule(unittest.TestCase):
    def test_h(self):
        pass
'''

# 文件三：现树真用过、AST 模型第一代漏掉的四种形状（逐枚都由 b86_leg2b 的 UNMATCHED 点名反推）：
#   KEY_I 别名装饰器 `needs_live = unittest.skipIf(...)` ⇒ `@needs_live` 盖类（batch22，且子类继承这层跳过）
#   KEY_J `setUp` 里 raise SkipTest⇒该组每格各一行（batch5／batch2 的 self.skipTest 同形）
#   KEY_K `setUpClass` 里 raise⇒整组折叠成一行，限定名只有类（batch24 DecisionRouteLegs 现证）
#   KEY_L 模块级辅助函数 `_import_or_skip` 里 raise，调用点在 setUp／在 test 体里（batch24／batch20）
_FIXTURE_SHAPES = '''"""fixture 三：别名装饰器／setUp／setUpClass／模块级辅助函数四种真实声明形状。"""
import unittest

IMPORT_ERROR = "No module named 'starlette'"


def _import_or_skip(dotted):
    """KEY_L 的声明体：运行面每个跑到它的格子各出一行。"""
    try:
        raise ImportError(dotted)
    except ImportError as exc:
        raise unittest.SkipTest("KEY_L 缺容器内依赖 " + dotted + "：" + repr(exc))


needs_live = unittest.skipIf(IMPORT_ERROR != "", "KEY_I 别名装饰器盖住的类级两格：" + IMPORT_ERROR)


@needs_live
class AliasLegs(unittest.TestCase):
    def test_i1(self):
        pass

    def test_i2(self):
        pass


class AliasChild(AliasLegs):
    """`__unittest_skip__` 是类属性⇒子类 getattr 也读到⇒这格也跳（继承来的三行）。"""

    def test_ic(self):
        pass


class SetUpLegs(unittest.TestCase):
    def setUp(self):
        try:
            import homesdk_absent_for_fixture_2
        except ImportError as exc:
            raise unittest.SkipTest("KEY_J setUp 里 raise⇒每格一行：" + repr(exc))

    def test_j1(self):
        pass

    def test_j2(self):
        pass


class SetUpClassLegs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raise unittest.SkipTest("KEY_K setUpClass 折叠成整组一行")

    def test_k1(self):
        pass

    def test_k2(self):
        pass


class HelperCallLegs(unittest.TestCase):
    def setUp(self):
        self.m = _import_or_skip("butler.absent")

    def test_l1(self):
        pass

    def test_l2(self):
        pass


class HelperInTestLegs(unittest.TestCase):
    def test_l3(self):
        _import_or_skip("butler.absent2")
'''

# 文件四：原因串是 `%` 格式化出来的（batch42 现证：`"…缺依赖（%s）⇒ …" % type(exc).__name__`）。
#   老模型把**含 `%s` 的整串**当一枚字面段⇒与运行时的整串永远不相等⇒那一族全落 unmatched。
#   这里⛔ 类（模块级 raise 会把同档类级那枚记成 FOLD，会脏掉 CAL_FOLD 那一格）。
_FIXTURE_FMT = '''"""fixture 四：`%` 格式化的原因串⇒字面段必须按转换符切开。"""
import unittest

try:
    import homesdk_absent_for_fixture_4  # noqa: F401
except Exception as exc:
    raise unittest.SkipTest("KEY_M 宿主侧缺依赖（%s）⇒ 本档只在容器内跑" % type(exc).__name__)
'''

# 文件五：声明写在「把 case／self 递进去就跳」的辅助件里（batch20 的 `_need_app(case)`／batch32 的 `_handler_result`）。
#   KEY_N 模块级函数体里 `case.skipTest(...)`，调用点在 setUp／在 test 体里；
#   KEY_O 类内辅助方法体里 `self.skipTest(...)`，调用点只能是 `self.<name>()`。
#   两枚反对照：`case.fail`（是失败⛔ 跳过）与 `other._guard()`（同名但⛔ 挂在 self 上）。
_FIXTURE_APPLY = '''"""fixture 五：跳过源是「递 case 进去就跳」的辅助件；调用点才是名册该收的格子。"""
import unittest

CASES = []


def _need_thing(case):
    if not CASES:
        case.skipTest("KEY_N 模块级辅助件里的声明（%s）⇒ 调用点每格各一行" % "空名单")
    return CASES


def _no_skip_here(case):
    """反对照：调 `case.fail`⇒是失败⛔ 跳过⇒挂它的格子必须⛔ 进名册。"""
    case.fail("KEY_Z9 这是失败是跳过")


class ApplierLegs(unittest.TestCase):
    def setUp(self):
        self.items = _need_thing(self)

    def test_n1(self):
        pass

    def test_n2(self):
        pass


class ApplierInTestLegs(unittest.TestCase):
    def test_n3(self):
        _need_thing(self)


class FailHelperLegs(unittest.TestCase):
    def test_n4(self):
        _no_skip_here(self)


class MethodApplierLegs(unittest.TestCase):
    def _guard(self):
        self.skipTest("KEY_O 类内辅助方法里的声明⇒调用它的那格一行")

    def test_o1(self):
        self._guard()

    def test_o2(self):
        pass

    def test_o3(self):
        other._guard()
'''

_FIXTURES = {"test_fixture.py": _FIXTURE_MAIN, "test_module_skip.py": _FIXTURE_MODULE,
             "test_shapes.py": _FIXTURE_SHAPES, "test_mfmt.py": _FIXTURE_FMT,
             "test_helpers.py": _FIXTURE_APPLY}

_EXPECT = {
    "KEY_A": (2, 0, 0),        # 方法级两格；注释／文档串⛔ 算；类装饰器盖住那格⛔ 再算
    "KEY_B": (0, 3, 0),        # 类级展开＝三个自己的 test_*
    "KEY_C": (0, 0, 1),        # 模块级只算一条，且它藏在 except 分支里（⛔ 只看 tree.body 直子）
    "KEY_D": (0, 3, 0),        # 自己的两格＋从 BaseFor 继承的那格＝运行时三行
    "KEY_E": (1, 0, 0),        # MixIn 自己不跑；只有 ChildOfMixin 跑到⇒一行
    "KEY_F": (1, 0, 0),        # f-string／拼接：字面段里含键⇒子串要命中（文档串那处同名⛔ 算）
    "KEY_H": (0, 1, 0),        # 同档另有模块级整档跳过⇒声明照数（它是源码事实），运行面可能折叠成一条⇒记 FOLD
    "KEY_I": (0, 5, 0),        # 别名装饰器＝类级：AliasLegs 两格＋子类继承的三格（`__unittest_skip__` 是类属性）
    "KEY_J": (0, 2, 0),        # setUp 里 raise⇒该组每格各一行（与类装饰器同形，落 class 桶）
    "KEY_K": (0, 0, 1),        # setUpClass 里 raise⇒整组折叠成一行，限定名只有类（落 module＝「一组一行」桶）
    "KEY_L": (1, 2, 0),        # 辅助函数里 raise：调用点在 test 体里＝method 一格，在 setUp 里＝class 两格
    "KEY_M": (0, 0, 1),        # `%` 格式化的整档跳过：切段后仍只一枚 module 声明
    "KEY_N": (1, 2, 0),        # 模块级「递 case 就跳」辅助件：test 体调用＝method 一格，setUp 调用＝class 两格
    "KEY_O": (1, 0, 0),        # 类内辅助方法里的 self.skipTest：只有 `self._guard()` 那一格算（`other._guard()`⛔ 算）
    "KEY_ABSENT": (0, 0, 0),   # 反对照：没声明过的键必须 0（恒 0 的尺与恒真的尺一样废）
}


# 合成运行面：每行都要有来路（三行走 qid 名册、模块级整档那行走原因字面段＝它的限定名是 loader
# 自造的 `unittest.loader._SkippedTest`⛔ 在名册里；setUpClass 折叠那行的限定名只有类）。
_CAL_RUNFACE = [
    "test_one (test_fixture.MethodLevel.test_one) ... skipped 'KEY_A 方法级一格'",
    "test_c (test_fixture.ClassLevel.test_c)",
    "KEY_B 类级三格。 ... skipped 'KEY_B 类级三格'",
    "test_f (test_fixture.FStringReason.test_f) ... skipped 'KEY_F 字面段在头 division by zero'",
    "test_module_skip (unittest.loader._SkippedTest) ... skipped "
    "'KEY_C 模块级 try 里整档跳过：No module named homesdk_absent_for_fixture'",
    "test_ic (test_shapes.AliasChild.test_ic) ... skipped "
    "'KEY_I 别名装饰器盖住的类级两格：No module named starlette'",
    "test_j1 (test_shapes.SetUpLegs.test_j1) ... skipped "
    "'KEY_J setUp 里 raise⇒每格一行：ImportError(No module named homesdk_absent_for_fixture_2)'",
    "SetUpClassLegs (test_shapes.SetUpClassLegs) ... skipped 'KEY_K setUpClass 折叠成整组一行'",
    "test_l1 (test_shapes.HelperCallLegs.test_l1) ... skipped "
    "'KEY_L 缺容器内依赖 butler.absent：ImportError(butler.absent)'",
    "test_mfmt (unittest.loader._SkippedTest) ... skipped "
    "'KEY_M 宿主侧缺依赖（ImportError）⇒ 本档只在容器内跑'",
    "test_n3 (test_helpers.ApplierInTestLegs.test_n3) ... skipped "
    "'KEY_N 模块级辅助件里的声明（空名单）⇒ 调用点每格各一行'",
    "test_o1 (test_helpers.MethodApplierLegs.test_o1) ... skipped "
    "'KEY_O 类内辅助方法里的声明⇒调用它的那格一行'",
]
# 幽灵行：限定名没声明过、原因文本也对不上任何字面段⇒必须落进 unmatched（这把尺该咬的地方）
_CAL_GHOST_LINE = ("test_ghost (test_fixture.MethodLevel.test_ghost) ... skipped "
                   "'ZZ 运行面自己冒出来、源码里没人声明的跳过'")
# 第二枚幽灵挂在**新形状那一族**上：证明「setUp／别名／辅助函数」把名册吹大之后，
# 同档里没声明过的格子照样落网（⛔ 只验第一族＝新分支没被咬过＝校准没覆盖）。
_CAL_GHOST_SHAPE = ("test_j9 (test_shapes.SetUpLegs.test_j9) ... skipped "
                    "'ZZ setUp 那一族里冒出来的第二枚幽灵'")
# 第三枚幽灵挂在**辅助件那一族**上：证明 KEY_N／KEY_O／`%` 切段把名册吹大之后，
# 同族里没声明过的格子照样落网（⛔ 只咬老族＝新分支没被验过＝校准没覆盖）。
_CAL_GHOST_APPLY = ("test_n9 (test_helpers.ApplierLegs.test_n9) ... skipped "
                    "'ZZ 辅助件那一族里冒出来的第三枚幽灵'")


def self_test():
    with tempfile.TemporaryDirectory() as d:
        for fname, src in _FIXTURES.items():
            with open(os.path.join(d, fname), "w", encoding="utf-8") as fh:
                fh.write(src)
        sites, index = collect([d])
        bad = 0
        for key, want in sorted(_EXPECT.items()):
            got = count_for_key(sites, key)
            ok = got == want
            print("CAL|%s|got=%s|want=%s|%s" % (key, got, want, "OK" if ok else "BAD"))
            if not ok:
                bad += 1
        named = sum(1 for p, n, b in index.notes["nonliteral"] if n.endswith("test_three"))
        print("CAL_NONLITERAL|total=%s|test_three_named=%s（want=1＝取不到字面量那格必须点名）"
              % (len(index.notes["nonliteral"]), named))
        if named != 1:
            bad += 1
        _HOLE_WANT = ["KEY_F 字面段在头 ", "KEY_C 模块级 try 里", "@NOLIT",
                      "KEY_I 别名装饰器盖住", "KEY_J setUp 里 ra", "KEY_L 缺容器内依赖",
                      "KEY_M 宿主侧缺依赖", "KEY_N 模块级辅助件"]
        holed = sorted(set(_display(r)[:12] for b, q, r, h in sites if h))
        print("CAL_HOLE|site_total=%s|distinct=%s|want=%s（want＝八族「带插值洞／取不到字面量」的声明各一枚；"
              "新形状没被跑过＝校准没覆盖）" % (sum(1 for _b, _q, _r, h in sites if h),
                                                holed, sorted(x[:12] for x in _HOLE_WANT)))
        if holed != sorted(x[:12] for x in _HOLE_WANT):
            bad += 1
        fold = list(index.notes["fold_candidates"])
        print("CAL_FOLD|total=%s|names=%s（want=1＝InsideSkippedModule＝同档另有整档跳过）"
              % (len(fold), [n for _p, n in fold]))
        if [n for _p, n in fold] != ["InsideSkippedModule"]:
            bad += 1
        # qid 名册：三种桶各点名一格，＋继承来的那格，＋Mixin 自己不进名册，＋四种新形状各自的落点
        want_in = ["test_fixture.MethodLevel.test_one", "test_fixture.ClassLevel.test_c",
                   "test_fixture.WithBase.test_base", "test_fixture.ChildOfMixin.test_mixin",
                   "test_module_skip.InsideSkippedModule.test_h", "test_module_skip",
                   "test_shapes.AliasLegs.test_i1", "test_shapes.AliasChild.test_ic",
                   "test_shapes.SetUpLegs.test_j1", "test_shapes.SetUpClassLegs",
                   "test_shapes.HelperCallLegs.test_l1", "test_shapes.HelperInTestLegs.test_l3",
                   "test_mfmt", "test_helpers.ApplierLegs.test_n1",
                   "test_helpers.ApplierInTestLegs.test_n3", "test_helpers.MethodApplierLegs.test_o1"]
        want_out = ["test_fixture.MixIn.test_mixin", "test_fixture.MethodLevel.test_ghost",
                    "test_fixture.ClassLevel", "test_shapes.AliasChild",
                    "test_shapes.SetUpLegs", "test_shapes.SetUpLegs.test_j9",
                    "test_helpers.FailHelperLegs.test_n4", "test_helpers.MethodApplierLegs.test_o2",
                    "test_helpers.MethodApplierLegs.test_o3", "test_helpers.ApplierLegs",
                    "test_helpers.MethodApplierLegs._guard", "test_helpers._need_thing"]
        miss = [q for q in want_in if q not in index.qids]
        extra = [q for q in want_out if q in index.qids]
        print("CAL_ROSTER|size=%s|missing=%s|leaked=%s（want＝missing 空＋leaked 空＝名册既收继承来的那格、"
              "也⛔ 收没跑的 MixIn 与没声明过的格子）" % (len(index.qids), miss, extra))
        if miss or extra:
            bad += 1
        m_ok = match_sites(sites, "KEY_F 字面段在头 division by zero")
        m_bad = match_sites(sites, "KEY_Z 运行面冒出来、源码里没人声明的跳过")
        print("CAL_MATCH_POSITIVE|hits=%s|%s（want=1＝带插值洞的声明要认得运行时整串）" % (len(m_ok), m_ok))
        print("CAL_MATCH_NEGATIVE|hits=%s（want=0＝没人声明的那行必须认不出；认得出＝这把尺恒真）" % len(m_bad))
        if len(m_ok) != 1 or len(m_bad) != 0:
            bad += 1
        r_ok = extract_reason("test_x (tests.t.T.test_x) ... skipped 'KEY_F 字面段在头 division by zero'")
        print("CAL_EXTRACT|got=%s（want 非空＝跳过行的取文腿在跑）" % (r_ok,))
        if not r_ok or match_sites(sites, r_ok).__len__() != 1:
            bad += 1
        # 闭合腿自己也要两侧校准：应放行（每一行都有来路）＋应咬住（造一行没来路的）
        pos = os.path.join(d, "runface_positive.txt")
        neg = os.path.join(d, "runface_negative.txt")
        with open(pos, "w", encoding="utf-8") as fh:
            fh.write("\n".join(_CAL_RUNFACE) + "\n")
        with open(neg, "w", encoding="utf-8") as fh:
            fh.write("\n".join(_CAL_RUNFACE + [_CAL_GHOST_LINE, _CAL_GHOST_SHAPE,
                                               _CAL_GHOST_APPLY]) + "\n")
        print("--- CAL_EXPLAIN_POSITIVE（want rc=0、unmatched=0）---")
        rc_pos, st_pos = explain_from(pos, sites, index)
        print("--- CAL_EXPLAIN_NEGATIVE（want rc=7、unmatched=3＝三枚造出来的幽灵都要落网，"
              "分别挂老形状／setUp 那一族／辅助件那一族）---")
        rc_neg, st_neg = explain_from(neg, sites, index)
        print("CAL_EXPLAIN_RC|pos=%s neg=%s|unmatched_pos=%s unmatched_neg=%s（want=0/7 与 0/3）"
              % (rc_pos, rc_neg, st_pos["unmatched"], st_neg["unmatched"]))
        if (rc_pos, rc_neg) != (0, 7) or st_pos["unmatched"] != 0 or st_neg["unmatched"] != 3:
            bad += 1
        print("CAL_FILES|%s（want=5）|TOTAL_SITES|method=%s class=%s module=%s" % (
            index.files,
            sum(1 for b, _q, _r, _h in sites if b == "method"),
            sum(1 for b, _q, _r, _h in sites if b == "class"),
            sum(1 for b, _q, _r, _h in sites if b == "module")))
        if index.files != 5:
            bad += 1
        print("CAL_UNRESOLVED_BASE|%s（want=0＝夹具里没有来历不明的基类）" % len(index.notes["unresolved_base"]))
        if index.notes["unresolved_base"]:
            bad += 1
            for item in index.notes["unresolved_base"][:6]:
                print("  UNRESOLVED|%s|%s|%s" % item)
        # 前缀腿：`-s tests -t .` 那面打的限定名带包前缀，名册若不带⇒qid 那条腿静默死（10-09 实犯 by_qid=0）。
        # 两侧都要验：带前缀的名册收得到带前缀的格，⛔ 同时把不带前缀的旧名也留着（那＝没换前缀，腿照样死）。
        _sites_p, index_p = collect([d], prefix="pkg")
        pref_in = "pkg.test_fixture.MethodLevel.test_one" in index_p.qids
        pref_old = "test_fixture.MethodLevel.test_one" in index_p.qids
        print("CAL_PREFIX|in=%s old_left=%s（want=True/False＝前缀必须整体换面，⛔ 两面混着挂）"
              % (pref_in, pref_old))
        if not pref_in or pref_old:
            bad += 1
        print("VERDICT|%s" % ("CALIBRATION_BITES" if bad == 0 else "CALIBRATION_DEAD(bad=%s)" % bad))
        return 0 if bad == 0 else 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", action="append", default=[], help="源码根（可多枚）")
    ap.add_argument("--module-prefix", default="",
                    help="运行面的包前缀（跑法是 `-s tests -t .` 就填 tests；⛔ 不填⇒名册与运行面的 qid 永不相交）")
    ap.add_argument("--key", action="append", default=[], help="原因子串键")
    ap.add_argument("--keys-file", help="一行一枚键（⛔ 走 shell 引号：CJK 与空格都在里面）")
    ap.add_argument("--emit-reasons", action="store_true", help="打印声明原因分布（与运行面逐族对账用）")
    ap.add_argument("--dump-sites", action="store_true", help="逐格打印派生到的声明（分桶＋取不到字面量的标记）")
    ap.add_argument("--explain-from", metavar="LOG", help="拿一份 `unittest -v` 输出做名册闭合：每行跳过都要有源码来路")
    ap.add_argument("--self-test", action="store_true", help="跑合成夹具校准")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if args.keys_file:
        with open(args.keys_file, "r", encoding="utf-8") as fh:
            args.key += [ln.rstrip("\n") for ln in fh if ln.rstrip("\n")]
    if not args.root:
        print("USAGE|需要 --root")
        return 2
    sites, index = collect(args.root, prefix=args.module_prefix)
    print("SCANNED_FILES|%s" % index.files)
    print("TOTAL_SITES|method=%s class=%s module=%s|roster=%s" % (
        sum(1 for b, _q, _r, _h in sites if b == "method"),
        sum(1 for b, _q, _r, _h in sites if b == "class"),
        sum(1 for b, _q, _r, _h in sites if b == "module"),
        len(index.qids)))
    for key in args.key:
        m, c, mo = count_for_key(sites, key)
        print("DECL|%s|method=%s|class_expanded=%s|module=%s|total=%s" % (key, m, c, mo, m + c + mo))
    if args.emit_reasons:
        for (bucket, reason), n in grouped_reasons(sites).most_common():
            print("REASON|%s|%s|%s" % (bucket, n, reason))
    if args.dump_sites:
        for bucket, qid, value, hole in sorted(sites, key=lambda s: (s[0], s[1])):
            text = "@NOLIT" if value is None else "".join(value)
            print("SITE|%s|%s|hole=%s|%s" % (bucket, qid, "Y" if hole else "N",
                                              text[:90].replace("|", "/")))
    if args.explain_from:
        print("--- 名册闭合（运行面 %s）---" % args.explain_from)
        rc, _stats = explain_from(args.explain_from, sites, index)
        if rc:
            print("VERDICT|CLOSURE_RED(rc=%s＝有跳过行在源码里没来路)" % rc)
        else:
            print("VERDICT|CLOSURE_GREEN（每一行跳过都对得上当前源码的声明）")
        return rc
    for tag in sorted(index.notes):
        items = index.notes[tag]
        if items:
            print("NOTE_%s|%s" % (tag.upper(), len(items)))
            for item in items[:60]:
                # item 有时是一枚字符串（module_no_init 存的就是目录名），逐字符 join 会把路径打碎
                text = item if isinstance(item, str) else "|".join(str(x) for x in item)
                print("  %s|%s" % (tag.upper(), text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
