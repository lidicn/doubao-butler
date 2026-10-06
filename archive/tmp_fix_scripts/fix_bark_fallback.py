#!/usr/bin/env python3
"""Fix bark fallback when triggered from xiaomi speaker"""

filepath = '/vol1/1000/docker/doubao-butler/butler/core/dialog.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

old_code = '''        if not devices:
            logger.warning("role %s 在房间 %s 无可出声设备，Bark 兜底", role.id, room)
            if self.bark:
                await self.bark.push(text, title=f"{role.name} · {member}")
            return {"spoken": False, "fallback": "bark"}'''

new_code = '''        if not devices:
            # 如果是通过小爱音箱触发的对话（source_device 存在），找不到设备时不应该 bark 兜底
            # 因为用户就在小爱旁边，小爱已经回复了，不需要再通过 bark 推送
            if source_device:
                logger.info("role %s 在房间 %s 无可出声设备（来自小爱音箱 %s），静默不兜底", role.id, room, source_device)
                return {"spoken": False, "fallback": "silent"}
            logger.warning("role %s 在房间 %s 无可出声设备，Bark 兜底", role.id, room)
            if self.bark:
                await self.bark.push(text, title=f"{role.name} · {member}")
            return {"spoken": False, "fallback": "bark"}'''

if old_code in content:
    content = content.replace(old_code, new_code)
    print("Replaced!")
else:
    print("Old code not found!")

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)
