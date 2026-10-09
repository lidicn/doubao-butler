"""TTS Speaker 适配器：把新的 TTSQueue 接到现有的 TTSManager 上。

实现 TTSSpeaker 协议，让队列知道怎么播。
"""
from __future__ import annotations

from typing import Any

from butler.logging_setup import get_logger
from butler.tts.manager import TTSManager
from butler.tts.queue import TTSItem

logger = get_logger("butler.tts.adapter")

# 房间 → ha_player_entity 的**回退表**（第五轮 P0-8：devices.json 里 7 间房各有真音箱，
# 这张只认其中 3 间，`卧室`／`房间` 两键在登记表里根本不存在＝臆造）。真源＝设备登记表，
# 见 TTSManagerSpeaker._player_entity；本表只在登记表没命中时兜底。
ROOM_TO_PLAYER_ENTITY = {
    "书房": "media_player.xiaomi_x08a_1648_play_control",
    "客厅": "media_player.xiaomi_lx06_a137_play_control",
    "主卧室": "media_player.xiaomi_l17a_60b3_play_control",
    "卧室": "media_player.xiaomi_l06a_2dee_play_control",
    "房间": "media_player.xiaomi_l06a_42e5_play_control",
}


class TTSManagerSpeaker:
    """TTSSpeaker 协议实现：调 TTSManager 合成并播放。"""

    def __init__(self, manager: TTSManager, devices: Any = None):
        self.manager = manager
        # 房间→播放实体的真源＝设备登记表（devices.json，WebUI 可改）。没注入则取 runtime 上装配的
        # 那枚；启动期尚未装配（None）⇒ 走 ROOM_TO_PLAYER_ENTITY 回退，行为与改前一致。
        self.devices = devices

    def _registry(self) -> Any:
        if self.devices is not None:
            return self.devices
        from butler.runtime import get_runtime
        return get_runtime().devices

    def _player_entity(self, room: str) -> str:
        """先问登记表（enabled 的 xiaomi 且有 ha_player_entity，同房间取顺序里第一只），再退回硬编码表。"""
        reg = self._registry()
        if reg is not None:
            for dev in reg.all():
                if (dev.enabled and dev.type == "xiaomi" and dev.room == room
                        and dev.ha_player_entity):
                    return dev.ha_player_entity
        return ROOM_TO_PLAYER_ENTITY.get(room, "")

    async def speak(self, item: TTSItem) -> bool:
        """播一条：合成 + 播放到指定设备。

        格14（R1 20260930）：这里是「队列 → 嘴」的唯一接缝，把 ``item.trace_id`` 交给
        ``TTSManager.speak`` 并声明 ``via="queue"``——``TTSItem.trace_id`` 从入队（tts/helper.py）
        到播报之间此前全仓 0 个读取点，投出去的一句话追不回是哪次唤醒／哪条问询播的。
        """
        device_id = item.device_id

        # room → ha_player_entity → xiaoai_id 解析
        if not device_id and item.room:
            player_entity = self._player_entity(item.room)
            if player_entity and self.manager.ha is not None:
                try:
                    xiaoai_id = await self.manager.ha.get_xiaoai_id(player_entity)
                    if xiaoai_id:
                        device_id = xiaoai_id
                        logger.debug("room '%s' -> %s -> xiaoai_id %s",
                                   item.room, player_entity, xiaoai_id)
                    else:
                        logger.warning("get_xiaoai_id returned empty for %s", player_entity)
                except Exception as e:
                    logger.warning("get_xiaoai_id failed for %s: %s", player_entity, e)

        if not device_id:
            logger.warning("no device resolved for room '%s', skipping playback", item.room)
            # 协议（tts/queue.py TTSSpeaker）写明「必须返回 bool」；裸 return 是 None。
            # 唯一调用点 queue.py play_one 走 bool(...)，None 与 False 同值 ⇒ 行为不变。
            return False

        try:
            await self.manager.speak(
                item.text,
                voice=item.voice,
                device_id=device_id,
                member=item.member,
                backend=item.backend,
                speed=item.speed,
                trace_id=item.trace_id,
                via="queue",
                # DCD 裁定②：ALERT 豁免要活着走到这张嘴——item.priority 在这条接缝上此前被丢掉。
                priority=item.priority,
            )
            return True
        except Exception as e:
            logger.warning("TTSQueue speaker failed: %s | %.30s", e, item.text)
            raise

    async def stop(self, device_id: str | None = None) -> None:
        """停止播放（HA 层面）。"""
        if self.manager.ha is not None and device_id:
            try:
                await self.manager.ha.stop(device_id)
            except Exception as e:
                logger.warning("TTSQueue stop failed: %s", e)
