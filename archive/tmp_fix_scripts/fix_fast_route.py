#!/usr/bin/env python3
"""Fix fast device control: skip generic device names without room context"""

filepath = '/vol1/1000/docker/doubao-butler/butler/core/agent.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

old_code = '''        # 设备控制兜底：打开/关闭/开灯/关灯 直接执行
        t = text.strip()
        if any(t.startswith(p) for p in ("打开", "关闭", "开灯", "关灯", "打开灯", "关闭灯")):
            # 提取设备名（去掉动词前缀）
            for prefix in ("打开", "关闭", "开灯", "关灯", "打开灯", "关闭灯"):
                if t.startswith(prefix):
                    device_name = t[len(prefix):].strip()
                    if device_name:
                        service = "turn_on" if "打开" in prefix or "开灯" in prefix else "turn_off"
                        logger.info("fast device control: %s -> %s", device_name, service)
                        # 不传 domain，让 _resolve_entity 跨域匹配
                        return await dispatch_tool("control_device", {
                            "entity_id": device_name,
                            "service": service,
                        }, self)'''

new_code = '''        # 设备控制兜底：打开/关闭/开灯/关灯 直接执行
        t = text.strip()
        if any(t.startswith(p) for p in ("打开", "关闭", "开灯", "关灯", "打开灯", "关闭灯")):
            # 提取设备名（去掉动词前缀）
            for prefix in ("打开", "关闭", "开灯", "关灯", "打开灯", "关闭灯"):
                if t.startswith(prefix):
                    device_name = t[len(prefix):].strip()
                    if device_name:
                        # 通用设备名（无房间/位置上下文）不执行快速路由，避免误匹配
                        # 例如"打开空调"在 Kevin 房间应该控制 Kevin 房间的空调，
                        # 而不是默认匹配到书房的空调伴侣
                        GENERIC_NAMES = {"空调", "灯", "风扇", "窗帘", "电视", "空气净化器", "加湿器"}
                        if device_name in GENERIC_NAMES:
                            logger.info("fast device control skipped (generic name): %s", device_name)
                            return None
                        service = "turn_on" if "打开" in prefix or "开灯" in prefix else "turn_off"
                        logger.info("fast device control: %s -> %s", device_name, service)
                        # 不传 domain，让 _resolve_entity 跨域匹配
                        return await dispatch_tool("control_device", {
                            "entity_id": device_name,
                            "service": service,
                        }, self)'''

if old_code in content:
    content = content.replace(old_code, new_code)
    print("Replaced!")
else:
    print("Old code not found!")

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)
