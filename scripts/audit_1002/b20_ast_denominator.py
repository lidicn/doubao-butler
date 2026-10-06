#!/usr/bin/env python3
# 批20 分母尺（唯一一份定义）：宿主跑尺件与容器跑尺件都调这一枚，打印必须逐字节相同。
# 为什么要抽出来：两枚跑尺件各自抄了一份 AST heredoc，一枚用 ast.walk（吃得到方法里嵌套的
# ClassDef，本档里就有 CallbackAPIVersion），一枚只看 t.body ⇒ 同一个标签 ast_classes 打印出
# 11 与 12 两个数。读数对不上时人只会怀疑码，不会怀疑尺 ⇒ 尺只留一份。
# 三个数必须能互相勾稽：ast_total == 容器里的 Ran；不等＝有名用例既没跑也没红。
import ast
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
tree = ast.parse(path.read_text(encoding="utf-8"))

funcs = (ast.FunctionDef, ast.AsyncFunctionDef)
top_classes, all_classes, methods, module_level = [], [], [], []
per_class = []

for node in tree.body:
    if isinstance(node, ast.ClassDef):
        top_classes.append(node.name)
        n = 0
        for m in node.body:
            if isinstance(m, funcs) and m.name.startswith("test_"):
                methods.append("%s.%s" % (node.name, m.name))
                n += 1
        per_class.append("%s=%d" % (node.name, n))
    elif isinstance(node, funcs) and node.name.startswith("test_"):
        module_level.append(node.name)

for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef):
        all_classes.append(node.name)

print("ast_classes=%d ast_classes_nested=%d ast_methods=%d ast_module_level=%d ast_total=%d"
      % (len(top_classes), len(all_classes) - len(top_classes), len(methods),
         len(module_level), len(methods) + len(module_level)))
print("ast_per_class=" + ",".join(per_class))
print("ast_roster=" + ",".join(methods)
      + ("|" + ",".join(module_level) if module_level else ""))
if module_level:
    print("注意 模块级 test_ 函数在场：discover 不 import pytest 时会静默收下＝零执行不报错")
