#!/usr/bin/env python3
"""Fix HA fallback: use xiaomi direct stop"""

filepath = '/vol1/1000/docker/doubao-butler/butler/api/tts_routes.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# 修复第一个 HA 兜底（xiaoai_id not found）
old1 = '''            # 兜底：HA media_player 路径
            await rt.ha.call_service("media_player", "volume_mute",
                                      {"entity_id": player_entity, "is_volume_muted": False})
            play_result = await rt.ha.tts_play_url(res.public_url, player_entity)
            # 播放完后自动停止（用 HA media_stop）
            estimated_sec = max(2.0, len(text) / 4.0 + 1.0)
            async def _stop_after():
                await asyncio.sleep(estimated_sec)
                try:
                    await rt.ha.call_service("media_player", "media_stop",
                                              {"entity_id": player_entity})
                except Exception as e:
                    logger.warning("HA media_stop failed: %s", e)
            asyncio.create_task(_stop_after())
            return {"spoken": play_result.startswith("ok"), "device": dev.id, "room": dev.room,
                    "channel": "ha_play_media_fallback", "url": res.public_url, "provider": res.provider}'''

new1 = '''            # 兜底：HA media_player 路径
            await rt.ha.call_service("media_player", "volume_mute",
                                      {"entity_id": player_entity, "is_volume_muted": False})
            play_result = await rt.ha.tts_play_url(res.public_url, player_entity)
            # 播放完后自动停止（用小米直连 stop）
            estimated_sec = max(2.0, len(text) / 4.0 + 1.0)
            # 硬编码 xiaoai_id（从文档里取）
            xiaoai_id = getattr(dev, 'xiaoai_id', None) or 'ac77f44b-3b0a-44dc-bef7-1363e33b9ef4'
            try:
                rt.ha.schedule_xiaomi_stop(xiaoai_id, estimated_sec)
            except Exception as e:
                logger.warning("schedule stop failed: %s", e)
            return {"spoken": play_result.startswith("ok"), "device": dev.id, "room": dev.room,
                    "channel": "ha_play_media_fallback", "url": res.public_url, "provider": res.provider}'''

content = content.replace(old1, new1)

# 修复第二个 HA 兜底（xiaomi direct token expired）
old2 = '''            # 兜底：HA media_player 路径
            await rt.ha.call_service("media_player", "volume_mute",
                                      {"entity_id": player_entity, "is_volume_muted": False})
            fb_result = await rt.ha.tts_play_url(res.public_url, player_entity)
            # 播放完后自动停止（用 HA media_stop）
            estimated_sec = max(2.0, len(text) / 4.0 + 1.0)
            async def _stop_after():
                await asyncio.sleep(estimated_sec)
                try:
                    await rt.ha.call_service("media_player", "media_stop",
                                              {"entity_id": player_entity})
                except Exception as e:
                    logger.warning("HA media_stop failed: %s", e)
            asyncio.create_task(_stop_after())
            return {"spoken": fb_result.startswith("ok"), "device": dev.id, "room": dev.room,
                    "channel": "ha_play_media_fallback", "url": res.public_url,
                    "provider": res.provider, "voice": voice,
                    "note": "xiaomi direct token expired, using HA fallback"}'''

new2 = '''            # 兜底：HA media_player 路径
            await rt.ha.call_service("media_player", "volume_mute",
                                      {"entity_id": player_entity, "is_volume_muted": False})
            fb_result = await rt.ha.tts_play_url(res.public_url, player_entity)
            # 播放完后自动停止（用小米直连 stop）
            estimated_sec = max(2.0, len(text) / 4.0 + 1.0)
            # 硬编码 xiaoai_id（从文档里取）
            xiaoai_id = getattr(dev, 'xiaoai_id', None) or 'ac77f44b-3b0a-44dc-bef7-1363e33b9ef4'
            try:
                rt.ha.schedule_xiaomi_stop(xiaoai_id, estimated_sec)
            except Exception as e:
                logger.warning("schedule stop failed: %s", e)
            return {"spoken": fb_result.startswith("ok"), "device": dev.id, "room": dev.room,
                    "channel": "ha_play_media_fallback", "url": res.public_url,
                    "provider": res.provider, "voice": voice,
                    "note": "xiaomi direct token expired, using HA fallback"}'''

content = content.replace(old2, new2)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print("Fixed!")
