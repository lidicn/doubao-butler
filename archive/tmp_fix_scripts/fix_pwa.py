#!/usr/bin/env python3
"""修复 PWA 移动端底部导航（11 Tab 挤爆）+ 添加版本号显示"""
import sys

html_path = '/vol1/1000/docker/doubao-butler/butler/static/index.html'
with open(html_path, 'r', encoding='utf-8') as f:
    html = f.read()

# === 1. 底部导航改用 mobileNav ===
# 定位："手机底部导航"注释之后的第一个 $store.app.nav
marker = '手机底部导航'
idx = html.find(marker)
if idx < 0:
    print('ERROR: 未找到手机底部导航注释')
    sys.exit(1)

# 在 marker 之后找第一个 $store.app.nav
nav_str = '$store.app.nav'
nav_idx = html.find(nav_str, idx)
if nav_idx < 0:
    print('ERROR: 未找到底部导航的 nav 引用')
    sys.exit(1)

# 替换为 mobileNav || nav（只替换这一处）
html = html[:nav_idx] + '$store.app.mobileNav || $store.app.nav' + html[nav_idx + len(nav_str):]
print('底部导航改用 mobileNav ✅')

# === 2. 设置页面添加版本号 ===
settings_str = "view==='settings'"
sidx = html.find(settings_str)
if sidx < 0:
    print('ERROR: 未找到设置页面')
    sys.exit(1)

# 找到 settings section 的开始标签结束位置（> 字符）
tag_end = html.find('>', sidx)
if tag_end < 0:
    print('ERROR: 未找到设置页面标签结束')
    sys.exit(1)

version_card = '''
        <div class="glass p-4 mb-4">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <span class="text-lg">ℹ️</span>
              <span class="font-medium">版本信息</span>
            </div>
            <span class="text-xs px-2 py-1 rounded-full bg-amber-500/20 text-amber-400 font-mono" x-text="$store.app.APP_VERSION || 'v1.2.0'"></span>
          </div>
          <div class="mt-2 text-xs text-[var(--txt-2)]">豆包管家 · 自研轻量 ReAct Agent · TVPilot/DeskPilot 生态链</div>
        </div>'''

html = html[:tag_end + 1] + version_card + html[tag_end + 1:]
print('设置页面添加版本号 ✅')

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(html)

# 验证
with open(html_path, 'r', encoding='utf-8') as f:
    ht = f.read()
print()
print('=== 验证 ===')
print('底部 mobileNav:', '✅' if 'mobileNav ||' in ht else '❌')
print('版本号卡片:', '✅' if 'APP_VERSION ||' in ht else '❌')
print('文件大小:', len(ht), 'bytes')
print('完成！')
