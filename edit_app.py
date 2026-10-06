import re

with open(r'E:\NAS\doubao-butler\butler\static\js\app.js', 'r', encoding='utf-8') as f:
    content = f.read()

# 在 skills 后面添加 koin
old = '{ k: "skills", l: "技能", i: "🧩" },\n      { k: "commands"'
new = '{ k: "skills", l: "技能", i: "🧩" },\n      { k: "koin", l: "Koin智动", i: "🤖" },\n      { k: "commands"'

content = content.replace(old, new)

with open(r'E:\NAS\doubao-butler\butler\static\js\app.js', 'w', encoding='utf-8') as f:
    f.write(content)

print('done')
