#!/usr/bin/env python3
"""Add NOWVOICE_TOKEN to docker-compose"""

filepath = '/vol1/1000/docker/doubao-butler/docker-compose.yml'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''      - TTS_CACHE_ENABLED=true
      # 对话/唤醒（非敏感）'''

new = '''      - TTS_CACHE_ENABLED=true
      - NOWVOICE_TOKEN=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1dWlkIjoiZTQ3MTllYWEtZTI0OC00MjQzLWJjOWMtNmRiYjU0NWFhN2YxIiwic2Vzc2lvbiI6IjUzODQxMWNiLWFhMzgtNDQ4Mi05ZTJlLTNmNzFlMjdiMTMxYSIsImV4cCI6MjEwNDY2ODI2MH0.FkR-rxprbguGY1Tm5ce9_bRoPJ_A33T-x0p0_gp4-H8
      - NOWVOICE_VOICE=afeb4759
      # 对话/唤醒（非敏感）'''

if old in content:
    content = content.replace(old, new)
    print("Replaced!")
else:
    print("Old code not found!")

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)
