"""设备登记表：房间 → 设备（tv / xiaomi）。

支撑两大能力：
- 「全屋任意小爱音箱」：每个 xiaomi 设备绑定一个 HA notify/实体，播报时按设备分发文本。
- 角色设备路由：角色只在其 output_devices 列表里的设备出声（如晓月仅客厅小爱PRO右）。

设备 JSON 存 data/devices.json（首次启动播种默认值，之后以文件为准，WebUI 可改）。
设备类型：
- tv：经 TVClient.play_url 播放 TTS 音频 URL（保留角色 edge-tts 音色）。
- xiaomi：经 HAClient.notify_message 推文本到指定 ha_entity（小爱用自己的声音播报）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from butler.config import Settings
from butler.core.atomic_json import read_json_object, write_json_atomic
from butler.logging_setup import get_logger
from butler.param import soft_flag

logger = get_logger("butler.devices")


def _resolve_area(room: str, mapping: dict[str, str] | None = None) -> str:
    """显示名 / area_id → HA area_id（DCD 20261104 契约；解析不到返回空串，不抛穿）。"""
    try:
        from butler.areas import resolve_room
    except Exception:  # pragma: no cover - 依赖装配面
        return ""
    return resolve_room(room, mapping) or ""


@dataclass
class Device:
    id: str
    type: str                     # "tv" | "xiaomi"
    room: str = ""               # 房间显示名（中文）；"*" 表示任意。⛔ 跨仓键是 area_id
    area_id: str = ""            # HA 区域 id（pinyin，如 shu_fang）——跨仓 canonical 键（DCD 20261104）
    ha_entity: str = ""          # xiaomi：HA notify/实体 id（notify_text 模式用）
    mqtt: str = ""               # tv：可选 MQTT 主题覆盖（默认用全局 PUB_TV_TTS）
    channel: str = ""            # 预留：左右声道（部分小爱支持）
    voice_override: str = ""      # 可选：该设备覆盖角色音色
    enabled: bool = True
    # ---- Phase 6：双通道发声 + 任意小爱登记 ----
    play_mode: str = "notify_text"   # "notify_text"(小爱自带TTS, 无循环) | "tts_speak"(edge-tts url, 角色音色)
    ha_tts_entity: str = ""          # tts_speak 模式：HA 的 tts.* 实体（如 tts.edgetts_zh_cn_xiaoxiaoneural）
    ha_player_entity: str = ""       # tts_speak 模式：目标 media_player.* 实体（用于 play_media + 关循环）
    ha_sensor: str = ""              # 该音箱的 conversation 传感器实体（NR 耳朵→房间映射用）
    # tts_speak 直连小爱云端的方式：
    #   "url"   -> player_play_url（一次性投射不循环，适用于 LX06 等大多数音箱）
    #   "music" -> player_play_music（标准音乐 payload，适用于红米触屏 X08A 等只认此接口的固件；
    #              此类固件播单条会单曲循环，发完需补 player_set_loop 0 关闭循环）
    xiaomi_play: str = "url"
    wake_button: str = ""          # HA button.xiaomi_xxx_wake_up，用于打断小爱内置TTS

    def to_dict(self) -> dict:
        return {
            "id": self.id, "type": self.type, "room": self.room,
            "area_id": self.area_id,
            "ha_entity": self.ha_entity, "mqtt": self.mqtt, "channel": self.channel,
            "voice_override": self.voice_override, "enabled": self.enabled,
            "play_mode": self.play_mode, "ha_tts_entity": self.ha_tts_entity,
            "ha_player_entity": self.ha_player_entity, "ha_sensor": self.ha_sensor,
            "xiaomi_play": self.xiaomi_play,
            "wake_button": self.wake_button,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Device":
        return cls(
            id=str(d.get("id", "")), type=str(d.get("type", "xiaomi")),
            room=str(d.get("room", "")), area_id=str(d.get("area_id", "")),
            ha_entity=str(d.get("ha_entity", "")),
            mqtt=str(d.get("mqtt", "")), channel=str(d.get("channel", "")),
            voice_override=str(d.get("voice_override", "")),
            enabled=soft_flag(d.get("enabled"), "enabled", default=True),
            play_mode=str(d.get("play_mode", "notify_text")),
            ha_tts_entity=str(d.get("ha_tts_entity", "")),
            ha_player_entity=str(d.get("ha_player_entity", "")),
            ha_sensor=str(d.get("ha_sensor", "")),
            xiaomi_play=str(d.get("xiaomi_play", "url")),
            wake_button=str(d.get("wake_button", "")),
        )


class DeviceRegistry:
    def __init__(self, settings: Settings):
        self.s = settings
        self.path = Path(settings.data_dir) / "devices.json"
        self.devices: dict[str, Device] = {}
        # 批71 D3：「读不出来」≠「没有数据」——坏档兜底出厂种子后这份内存态不完整，
        # 写腿（save/upsert/delete）拿它整份覆盖＝用户自加的设备永久找不回。拒写并留痕。
        self.unreadable = False

    # ---- 种子（首次启动播种默认设备，已存在则不覆盖用户编辑）----

    def _seed(self) -> dict[str, Device]:
        primary_xiao = self.s.xiaomi_notify_entity
        edgetts = "tts.edgetts_zh_cn_xiaoxiaoneural"
        devices: dict[str, Device] = {
            "tv_living": Device("tv_living", "tv", room="客厅", mqtt="", channel=""),
            "xiao_living": Device(
                "xiao_living", "xiaomi", room="客厅",
                ha_entity="notify.xiaomi_cn_266168894_lx06_execute_text_directive_a_5_5",
                channel="left", play_mode="tts_speak", ha_tts_entity=edgetts,
                ha_player_entity="media_player.xiaomi_lx06_a137_play_control",
                ha_sensor="sensor.xiaomi_lx06_a137_conversation",
                wake_button="button.xiaomi_lx06_a137_wake_up",
            ),
            "xiao_living_right": Device(
                "xiao_living_right", "xiaomi", room="客厅",
                ha_entity="notify.xiaomi_cn_266158096_lx06_execute_text_directive_a_5_5",
                channel="right", play_mode="tts_speak", ha_tts_entity=edgetts,
                ha_player_entity="media_player.xiaomi_lx06_7709_play_control",
                ha_sensor="sensor.xiaomi_lx06_7709_conversation",
                wake_button="button.xiaomi_lx06_7709_wake_up",
            ),
            "xiao_study": Device(
                "xiao_study", "xiaomi", room="书房",
                ha_entity="notify.xiaomi_cn_330794773_x08a_execute_text_directive_a_5_4",
                channel="", play_mode="tts_speak", ha_tts_entity=edgetts,
                ha_player_entity="media_player.xiaomi_x08a_1648_play_control",
                ha_sensor="sensor.xiaomi_x08a_1648_conversation",
                wake_button="button.xiaomi_x08a_1648_wake_up",
            ),
            "xiao_kevin": Device(
                "xiao_kevin", "xiaomi", room="Kevin房间",
                ha_entity="notify.xiaomi_cn_795540700_l06a_execute_text_directive_a_5_5",
                channel="", play_mode="tts_speak", ha_tts_entity=edgetts,
                ha_player_entity="media_player.xiaomi_l06a_42e5_play_control",
                ha_sensor="sensor.xiaomi_l06a_42e5_conversation",
                wake_button="button.xiaomi_l06a_42e5_wake_up",
            ),
            "xiao_emily": Device(
                "xiao_emily", "xiaomi", room="Emily房间",
                ha_entity="notify.xiaomi_cn_795535333_l06a_execute_text_directive_a_5_5",
                channel="", play_mode="tts_speak", ha_tts_entity=edgetts,
                ha_player_entity="media_player.xiaomi_l06a_2dee_play_control",
                ha_sensor="sensor.xiaomi_l06a_2dee_conversation",
                wake_button="button.xiaomi_l06a_2dee_wake_up",
            ),
            "xiao_touch8": Device(
                "xiao_touch8", "xiaomi", room="主卧室",
                ha_entity="notify.xiaomi_cn_1108723976_l17a_execute_text_directive_a_7_4",
                channel="", play_mode="tts_speak", ha_tts_entity=edgetts,
                ha_player_entity="media_player.xiaomi_l17a_60b3_play_control",
                ha_sensor="sensor.xiaomi_l17a_60b3_conversation",
                wake_button="button.xiaomi_l17a_60b3_wake_up",
            ),
            "xiao_master_bath": Device(
                "xiao_master_bath", "xiaomi", room="主卧室浴室",
                ha_entity="notify.xiaomi_cn_83458853_s12_execute_text_directive_a_5_5",
                channel="", play_mode="tts_speak", ha_tts_entity=edgetts,
                ha_player_entity="media_player.xiaomi_s12_10ca_play_control",
                ha_sensor="sensor.xiaomi_s12_10ca_conversation",
                wake_button="button.xiaomi_s12_10ca_wake_up",
            ),
            "xiao_toilet": Device(
                "xiao_toilet", "xiaomi", room="卫生间",
                ha_entity="notify.xiaomi_cn_83350876_s12_execute_text_directive_a_5_5",
                channel="", play_mode="tts_speak", ha_tts_entity=edgetts,
                ha_player_entity="media_player.xiaomi_s12_b693_play_control",
                ha_sensor="sensor.xiaomi_s12_b693_conversation",
                wake_button="button.xiaomi_s12_b693_wake_up",
            ),
        }
        # DCD 20261104 T3/Q4甲：种子设备统一按显示名对齐到 HA area_id（只切键，不改显示名）
        for dev in devices.values():
            if not dev.area_id and dev.room:
                dev.area_id = _resolve_area(dev.room)
        return devices

    def load(self) -> None:
        seed = self._seed()
        # 判定口径走批70b 收敛尺子 read_json_object（⛔ 第 6 份自家 try/except）：
        # 缺档＝真没有（unreadable=False，首次播种照写）；存在但读不出＝现场还在盘上（拒写那半在 save()）。
        raw, self.unreadable = read_json_object(self.path)
        if self.unreadable:
            # 读侧兜底出厂种子本身是对的（批17 口径：⛔ 抛穿成 500），本批⛔ 动那半
            logger.warning("devices.json unreadable, falling back to seed devices: %s", self.path)
        for did, d in raw.items():
            if isinstance(d, dict):
                dev = Device.from_dict({**d, "id": did})
                if not dev.area_id and dev.room:
                    # 老 devices.json 无 area_id：按显示名就地对齐到 HA area_id（双读过渡）
                    dev.area_id = _resolve_area(dev.room) or ""
                seed[did] = dev
        self.devices = seed
        # 首次播种后落盘，便于 WebUI 直接编辑（缺档＝真没有，⛔ 一并拒掉，否则新装环境建不起表）
        if not self.path.exists():
            self.save()
        elif any(not (isinstance(raw.get(k), dict) and raw[k].get("area_id")) for k in raw):
            # 有老档缺 area_id：补齐一次落盘（只加键，不改用户其它字段；坏档拒写那半在 save()）
            self.save()

    def save(self) -> bool:
        """整份落盘。坏档时拒绝对覆盖、盘上一个字节不动；返回是否真正保存（调用方据此如实回报）。"""
        if self.unreadable:
            logger.error("devices.json unreadable, refusing full overwrite that would replace "
                         "the user's file with the seed/incomplete in-memory table: %s", self.path)
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {d.id: d.to_dict() for d in self.devices.values()}
            # 表行 45 P1-13：就地 write_text 崩在半路＝半截 devices.json，下次 load() 静默回退出厂种子
            write_json_atomic(self.path, data)
            return True
        except Exception as e:
            logger.warning("save devices failed: %s", e)
            return False

    # ---- 查询 ----

    def all(self) -> list[Device]:
        return list(self.devices.values())

    def get(self, dev_id: str) -> Device | None:
        return self.devices.get(dev_id)

    def _canonical_room(self, room: str | None) -> str | None:
        """入站 room（area_id / HA area_name / 旧中文名）→ 登记表显示房间名（边界归一）。

        DCD 20261104 T1/T5：canonical 键是 area_id；这里只做「边界归一」，
        既有按 dev.room 精确比对与空 room 留痕口径一字不动。
        """
        if room in (None, "", "*"):
            return room
        mapping = {d.area_id: d.room for d in self.devices.values() if d.area_id and d.room}
        aid = _resolve_area(room, mapping) if mapping else _resolve_area(room)
        if aid and mapping and aid in mapping:
            return mapping[aid]
        return room

    def resolve(self, device_ids: list[str], room: str | None = None) -> list[Device]:
        """返回角色在指定房间可用的设备（按 output_devices 过滤）。

        批200 Q3（DCD 20261011 七问裁定 乙）——把原先挤在同一句里的口径**升格为判据**：
        `room` 为 `None`／空串／`'*'` ＝**不按房间过滤，返回该角色全部可用台数**；
        ⛔ 把空 room 读成「没设备」而拒绝（AF ask 桥那类天生不带 room 的问句会整条哑掉）。
        这一档另配一行台数留痕（`room-unfiltered`），供装机窗回读「不带房间时到底给了几台」。
        """
        room = self._canonical_room(room)
        unfiltered = not room or room == "*"
        out: list[Device] = []
        for did in device_ids or []:
            dev = self.devices.get(did)
            if dev is None or not dev.enabled:
                if did:
                    logger.warning("device %s not found/disabled, skip", did)
                continue
            if room and room != "*" and dev.room and dev.room != "*" and dev.room != room:
                continue
            out.append(dev)
        if unfiltered:
            logger.info("device resolve room-unfiltered: incoming=%d devices=%d",
                        len(device_ids or []), len(out))
        return out

    # ---- 编辑（WebUI）----

    def upsert(self, d: dict) -> Device:
        dev = Device.from_dict(d)
        if not dev.id:
            raise ValueError("device id required")
        if dev.type not in ("tv", "xiaomi"):
            raise ValueError("device type must be tv|xiaomi")
        self.devices[dev.id] = dev
        self.save()
        return dev

    def delete(self, dev_id: str) -> bool:
        if dev_id in self.devices:
            del self.devices[dev_id]
            self.save()
            return True
        return False
