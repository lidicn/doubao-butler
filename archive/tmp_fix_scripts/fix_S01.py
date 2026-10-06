#!/usr/bin/env python3
"""Fix S-01: Add authentication to webhook endpoint"""

filepath = '/vol1/1000/docker/doubao-butler/butler/api/doubao_webhook.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# 在 _webhook_handler 函数开头添加认证检查
old_handler = '''async def _webhook_handler(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    result = await handle_webhook(payload)
    status = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=status)'''

new_handler = '''async def _webhook_handler(request: Request):
    # S-01 修复：添加 webhook token 认证
    from butler.config import get_settings
    s = get_settings()
    expected_token = getattr(s, "webhook_token", "") or ""

    if expected_token:
        # 如果配置了 webhook_token，必须验证
        request_token = request.headers.get("X-Webhook-Token", "")
        if request_token != expected_token:
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    result = await handle_webhook(payload)
    status = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=status)'''

content = content.replace(old_handler, new_handler)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print("S-01 Fixed!")
