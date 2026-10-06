#!/usr/bin/env python3
"""修复 command_store._row_to_dict：id 字段改为 cmd_id（业务 ID），与 API 路径一致"""

path = '/vol1/1000/docker/doubao-butler/butler/store/command_store.py'
with open(path, 'r', encoding='utf-8') as f:
    code = f.read()

old = '''def _row_to_dict(row) -> dict:
    d = {}
    for k in row.keys():
        d[k] = row[k]
    if d.get("meta_json"):
        try:
            d["meta"] = json.loads(d["meta_json"])
        except Exception:
            d["meta"] = {}
    return d'''

new = '''def _row_to_dict(row) -> dict:
    d = {}
    for k in row.keys():
        d[k] = row[k]
    # id 统一用业务 ID（cmd_id），与 /api/commands/{cmd_id} 路径一致
    if d.get("cmd_id"):
        d["id"] = d["cmd_id"]
    if d.get("meta_json"):
        try:
            d["meta"] = json.loads(d["meta_json"])
        except Exception:
            d["meta"] = {}
    return d'''

if old in code:
    code = code.replace(old, new)
    print('_row_to_dict: id 改为 cmd_id ✅')
else:
    print('_row_to_dict: 未找到匹配')

with open(path, 'w', encoding='utf-8') as f:
    f.write(code)

# 验证
with open(path, 'r', encoding='utf-8') as f:
    c = f.read()
print('验证:', '✅' if 'd["id"] = d["cmd_id"]' in c else '❌')
print('完成！')
