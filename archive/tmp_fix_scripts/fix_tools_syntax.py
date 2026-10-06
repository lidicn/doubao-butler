with open("/app/butler/core/tools.py", "r", encoding="utf-8") as f:
    content = f.read()

# 修复：create_vibe_decision 的 required 行后缺少闭合括号
old = '''                "required": ["question", "options"],
    {
        "type": "function",
        "function": {
            "name": "analyze_screenshot",'''

new = '''                "required": ["question", "options"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_screenshot",'''

if old in content:
    content = content.replace(old, new, 1)
    with open("/app/butler/core/tools.py", "w", encoding="utf-8") as f:
        f.write(content)
    print("FIXED: create_vibe_decision closing braces restored")
else:
    print("pattern not found, checking if already fixed...")
    # 检查是否已经有正确的闭合
    if '"required": ["question", "options"],\n            },\n        },\n    },' in content:
        print("already fixed")
    else:
        print("ERROR: cannot find pattern to fix")
