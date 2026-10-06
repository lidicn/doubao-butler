#!/usr/bin/env python3
"""Fix briefing: add user message"""

filepath = '/vol1/1000/docker/doubao-butler/butler/core/briefing.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

old_code = '''    llm = getattr(rt, "llm", None)
    if not llm:
        return "LLM 未就绪。"
    try:
        text, _ = await llm.chat(system, [], max_tokens=500, temperature=0.8)
        return (text or "").strip()'''

new_code = '''    llm = getattr(rt, "llm", None)
    if not llm:
        return "LLM 未就绪。"
    try:
        # new-api 要求至少有一个 user message
        text, _ = await llm.chat(system, [{"role": "user", "content": "请开始播报"}], max_tokens=500, temperature=0.8)
        return (text or "").strip()'''

if old_code in content:
    content = content.replace(old_code, new_code)
    print("Replaced!")
else:
    print("Old code not found!")

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)
