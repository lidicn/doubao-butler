#!/usr/bin/env python3
"""Rewrite nowvoice_client.py to use httpx instead of requests"""

filepath = '/vol1/1000/docker/doubao-butler/butler/tts/nowvoice_client.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# 替换 import
content = content.replace('import requests', 'import httpx')

# 替换 API 调用
content = content.replace(
    '''        resp = requests.post(
            f"{self.api_base}{path}",
            data=encrypted_body,
            headers=headers,
            timeout=timeout
        )''',
    '''        resp = httpx.post(
            f"{self.api_base}{path}",
            content=encrypted_body,
            headers=headers,
            timeout=timeout
        )'''
)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print("Updated to use httpx!")
