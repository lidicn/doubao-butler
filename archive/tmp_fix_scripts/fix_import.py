with open("/app/butler/api/skill_routes.py", "r", encoding="utf-8") as f:
    content = f.read()

old = """    # 2. 文件系统草稿（status=draft 的技能文件，含 trace_to_skill_draft 生成的）
    try:
        s = get_settings()"""

new = """    # 2. 文件系统草稿（status=draft 的技能文件，含 trace_to_skill_draft 生成的）
    import os as _os
    import json as _json
    try:
        s = get_settings()"""

if old in content:
    content = content.replace(old, new, 1)
    # 同时把函数内的 os. 替换为 _os.，json. 替换为 _json.
    # 只替换文件扫描块内的（从 import os 到 return 之前）
    # 简单方式：全局替换，但只在这个函数内
    # 用更精确的方式：替换 skills_dir 和 with open 中的 os/json
    content = content.replace("skills_dir = os.path.join", "skills_dir = _os.path.join")
    content = content.replace("os.walk(skills_dir)", "_os.walk(skills_dir)")
    content = content.replace("os.path.join(root, fname)", "_os.path.join(root, fname)")
    content = content.replace("json.load(f)", "_json.load(f)")
    with open("/app/butler/api/skill_routes.py", "w", encoding="utf-8") as f:
        f.write(content)
    print("FIXED: added import os, json and replaced references")
else:
    print("pattern not found")
