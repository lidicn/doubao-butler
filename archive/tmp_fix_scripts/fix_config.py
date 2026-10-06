#!/usr/bin/env python3
"""Add nowvoice config to settings"""

filepath = '/vol1/1000/docker/doubao-butler/butler/config.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. 添加字段
old_field = '''    tts_primary: str = "edge-tts"
    tts_edge_voice: str = "zh-CN-XiaoxiaoNeural"'''

new_field = '''    tts_primary: str = "edge-tts"
    tts_edge_voice: str = "zh-CN-XiaoxiaoNeural"
    nowvoice_token: str = ""
    nowvoice_voice: str = "afeb4759"  # Xiaochen'''

content = content.replace(old_field, new_field)

# 2. 添加 load 方法
old_load = '''            tts_primary=_env("TTS_PRIMARY", "edge-tts"),
            tts_edge_voice=_env("TTS_EDGE_VOICE", "zh-CN-XiaoxiaoNeural"),'''

new_load = '''            tts_primary=_env("TTS_PRIMARY", "edge-tts"),
            tts_edge_voice=_env("TTS_EDGE_VOICE", "zh-CN-XiaoxiaoNeural"),
            nowvoice_token=_env("NOWVOICE_TOKEN", ""),
            nowvoice_voice=_env("NOWVOICE_VOICE", "afeb4759"),'''

content = content.replace(old_load, new_load)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print("Updated!")
