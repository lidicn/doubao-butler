"""DB 侧房间/区域边界（DCD 20261104「房间定义统一到 HA 区域三仓契约」T1/T3/T5）。

口径：**canonical 键 = HA `area_id`**（pinyin，如 `shu_fang`）；`area_name`（中文）只是显示层。
真源在 HA，**解析机制在 homesdk.areas（0.3.3）**；本模块只提供 DB 自己的
「设备已绑的 area_id ↔ 中文显示名」这一层边界，喂给 `homesdk.areas`，不另造第四套房间表。

过渡（T5 双读）：DB 历史中文名（含与 HA 不一致的 `Kevin房间`→`fang_jian`、`Emily房间`→`wo_shi`）
作 aliases 回退；阶段 2 三仓统一收口后这些别名应清掉。Q4 甲：230 处中文串留显示层，只在边界映射。
"""
from __future__ import annotations

from butler.logging_setup import get_logger

logger = get_logger("butler.areas")

try:  # pragma: no cover - 依赖装配面
    from homesdk import areas as _hsa
except Exception:  # 0.3.3 尚未进镜像时降级，⛔ 让整个 app 起不来
    _hsa = None
    logger.warning("homesdk.areas 不可用（homesdk<0.3.3？），房间解析退化为本地映射；"
                   "镜像重建后自动切回 homesdk.areas（DCD 20261104 T7）")

# DB 历史中文显示名 → HA area_id。⛔ 新增中文房间名时先在这里对齐，再改 devices.json。
ROOM_TO_AREA_ID: dict[str, str] = {
    "客厅": "ke_ting",
    "书房": "shu_fang",
    "Kevin房间": "fang_jian",   # 与 HA 的「房间」不一致：DCD 契约 T3 要求对齐 fang_jian
    "Emily房间": "wo_shi",      # 与 HA 的「卧室」不一致：DCD 契约 T3 要求对齐 wo_shi
    "主卧室": "zhu_wo_shi",
    "主卧": "zhu_wo_shi",       # AF 旧 AREA_WORDS 的「主卧」＝HA 主卧室：过渡别名，阶段2清
    "主卧室浴室": "zhu_wo_shi_yu_shi",
    "卫生间": "wei_sheng_jian",
    "厨房": "chu_fang",
    "起居室": "qi_ju_shi",
    "洗衣房": "xi_yi_fang",
    "衣帽间": "yi_mao_jian",
    "杂物间": "za_wu_jian",
    "机房": "ji_fang",
}

# 旧中文名（与 HA area_name 不同形，纯 DB 历史）→ area_id：过渡别名，阶段 2 清。
LEGACY_ALIASES: dict[str, str] = {
    "Kevin房间": "fang_jian",
    "Emily房间": "wo_shi",
    "主卧": "zhu_wo_shi",
}

# HA area_name（中文）→ area_id。老 AF / 其他仓若仍发中文名，DB 侧双读兜住。
AREA_NAME_TO_AREA_ID: dict[str, str] = {
    "客厅": "ke_ting",
    "厨房": "chu_fang",
    "卧室": "wo_shi",
    "房间": "fang_jian",
    "起居室": "qi_ju_shi",
    "卫生间": "wei_sheng_jian",
    "洗衣房": "xi_yi_fang",
    "主卧室": "zhu_wo_shi",
    "书房": "shu_fang",
    "衣帽间": "yi_mao_jian",
    "杂物间": "za_wu_jian",
    "主卧室浴室": "zhu_wo_shi_yu_shi",
    "机房": "ji_fang",
}

_KNOWN_AREA_IDS = frozenset(ROOM_TO_AREA_ID.values()) | frozenset(AREA_NAME_TO_AREA_ID.values())


def resolve_room(room_text, mapping: dict[str, str] | None = None) -> str | None:
    """任意 room 文本 → HA `area_id`；解析不到返回 `None`。

    ★ `None` 是契约信号：调用方必须拒绝投递并打日志 `AREA_UNRESOLVED`，⛔ 静默 accepted
    （DCD 20261104 §2.3 硬判据）。

    `mapping` 通常传「本仓设备已绑的 area_id→显示名」，交给 `homesdk.areas.resolve`
    做别名/反查；未装配 homesdk.areas 时退回本地静态表。
    """
    text = "" if room_text is None else str(room_text).strip()
    if not text:
        return None
    if _hsa is not None and mapping:
        aid = _hsa.resolve(mapping, text, aliases=LEGACY_ALIASES)
        if aid:
            return aid
    if text in ROOM_TO_AREA_ID:
        return ROOM_TO_AREA_ID[text]
    if text in AREA_NAME_TO_AREA_ID:
        return AREA_NAME_TO_AREA_ID[text]
    if text in _KNOWN_AREA_IDS:
        return text
    return None


def display_room(area_id: str | None, mapping: dict[str, str] | None = None) -> str | None:
    """`area_id` → 本仓显示名（优先设备已绑的中文名，其次 HA area_name）。未知返回 None。"""
    if not area_id:
        return None
    if mapping and area_id in mapping:
        return mapping[area_id]
    return _reverse_lookup(area_id)


def _reverse_lookup(area_id: str) -> str | None:
    for name, aid in AREA_NAME_TO_AREA_ID.items():
        if aid == area_id:
            return name
    for name, aid in ROOM_TO_AREA_ID.items():
        if aid == area_id:
            return name
    return None
