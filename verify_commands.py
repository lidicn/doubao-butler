import pathlib
h = pathlib.Path('/app/butler/static/index.html').read_text()
j = pathlib.Path('/app/butler/static/js/app.js').read_text()
print('index.html commands section:', "view==='commands'" in h)
print('index.html 创建指令表单:', 'newCmdText' in h)
print('index.html 详情弹窗:', 'commandDetail' in h)
print('app.js nav commands:', "k: 'commands'" in j)
print('app.js loadCommands:', 'async loadCommands' in j)
print('app.js createCommand:', 'async createCommand' in j)
print('app.js cancelCommand:', 'async cancelCommand' in j)
mobile_section = j.split('mobileNav')[1].split(']')[0] if 'mobileNav' in j else ''
print('app.js mobileNav commands:', 'commands' in mobile_section)
print('app.js APP_VERSION:', 'APP_VERSION' in j)
