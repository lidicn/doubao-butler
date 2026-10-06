#!/usr/bin/env python3
"""Add TTS broadcast to push_briefing"""

filepath = '/vol1/1000/docker/doubao-butler/butler/core/briefing.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

old_code = '''async def push_briefing(rt, briefing_type: str = "morning"):
    text = await generate_briefing(rt, briefing_type)
    bark = getattr(rt, "bark", None)
    if bark and text:
        title = "☀️" if briefing_type == "morning" else "🌙"
        await bark.send(title, text)
        logger.info("briefing pushed: %s (%d chars)", briefing_type, len(text))'''

new_code = '''async def push_briefing(rt, briefing_type: str = "morning"):
    text = await generate_briefing(rt, briefing_type)
    if not text:
        return

    # 1. Bark 文字推送
    bark = getattr(rt, "bark", None)
    if bark:
        title = "☀️" if briefing_type == "morning" else "🌙"
        await bark.send(title, text)
        logger.info("briefing bark pushed: %s (%d chars)", briefing_type, len(text))

    # 2. TTS 播报到客厅小爱音箱
    try:
        dialog = getattr(rt, "dialog", None)
        if dialog:
            role = rt.roles.get("butler") if hasattr(rt, "roles") else None
            if role:
                await dialog.speak_as_role(
                    role, text,
                    room="客厅",
                    member="大佬",
                )
                logger.info("briefing TTS broadcasted to living room")
    except Exception as e:
        logger.warning("briefing TTS failed: %s", e)'''

if old_code in content:
    content = content.replace(old_code, new_code)
    print("Replaced!")
else:
    print("Old code not found!")

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)
