#!/usr/bin/env python3
"""Update push_briefing: TV first, fallback to xiaomi"""

filepath = '/vol1/1000/docker/doubao-butler/butler/core/briefing.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

old_code = '''async def push_briefing(rt, briefing_type: str = "morning"):
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

    # 2. TTS 播报：TV 优先，TV 关了才用小爱
    try:
        ha = getattr(rt, "ha", None)
        tv_on = False
        if ha:
            try:
                # 检查客厅电视是否打开
                states = await ha.get_states()
                for s in states:
                    eid = s.get("entity_id", "")
                    if eid.startswith("media_player.") and "tv" in eid.lower():
                        if s.get("state") == "on":
                            tv_on = True
                            logger.info("briefing: TV is on (%s)", eid)
                            break
            except Exception as e:
                logger.warning("briefing: check TV state failed: %s", e)

        dialog = getattr(rt, "dialog", None)
        if dialog:
            role = rt.roles.get("butler") if hasattr(rt, "roles") else None
            if role:
                if tv_on:
                    # TV 打开了，通过 TV TTS 播放
                    logger.info("briefing: broadcasting via TV")
                    await dialog.speak_as_role(
                        role, text,
                        room="客厅",
                        member="大佬",
                    )
                else:
                    # TV 没开，用客厅小爱音箱
                    logger.info("briefing: TV off, broadcasting via xiaomi")
                    await dialog.speak_as_role(
                        role, text,
                        room="客厅",
                        member="大佬",
                    )
    except Exception as e:
        logger.warning("briefing TTS failed: %s", e)'''

if old_code in content:
    content = content.replace(old_code, new_code)
    print("Replaced!")
else:
    print("Old code not found!")

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)
