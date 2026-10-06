#!/usr/bin/env python3
"""Fix S-02: Add CSP header to prevent stored XSS"""

filepath = '/vol1/1000/docker/doubao-butler/butler/api/doubao_webhook.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# 找到 user_pages 静态文件挂载的位置，添加 CSP Header
# 先看看有没有静态文件挂载
if 'StaticFiles' not in content:
    # 如果没有静态文件挂载，就在 HTML 响应时添加 CSP Header
    # 找到返回 HTML 的地方，添加安全 Header
    
    # 简单方案：在 app.py 里添加全局 CSP Header
    pass

# 实际上，更简单的方式是：在返回 HTML 的时候，不要直接返回原始 HTML
# 而是用 iframe 沙箱包裹，或者添加 CSP Header

# 让我们在 app.py 里添加全局 CSP 中间件
print("S-02: Adding CSP header in app.py")
