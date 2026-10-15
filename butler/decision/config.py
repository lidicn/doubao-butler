"""决策层配置：data/decision.json（首次播种默认值，之后以文件为准）。"""
from __future__ import annotations

import json
from pathlib import Path

from butler.config import Settings
from butler.logging_setup import get_logger
from butler.param import ParamError, to_bool, to_float, to_int

logger = get_logger("butler.decision.config")

# 默认配置（首次播种）
DEFAULT_CONFIG: dict = {
    "enabled": True,
    "interval_minutes": 10,        # 心跳周期
    "night_start": 23,             # 夜间静默开始（只允许 notify）
    "night_end": 7,                # 夜间静默结束
    "confidence_threshold": 0.6,   # 置信度阈值：低于则拒绝执行
    "cooldown_minutes": 30,        # 同类行动冷却
    "devices": {
        "客厅灯": "switch.0x00158d0001fd43d1_switch",
        "主卧室空调": "climate.lumi_mcn02_5e7c_air_conditioner",
        "书房电脑": "switch.5ce50c972e97_outlet",
        "米家新风空调": "climate.xiaomi_cn_533439795_mt0",
    },
    "rooms": ["客厅", "主卧室", "书房", "Kevin房间", "Emily房间"],
}


class DecisionConfig:
    def __init__(self, data_dir: str):
        self.path = Path(data_dir) / "decision.json"
        self.cfg: dict = {}
        self.load()

    def load(self) -> None:
        cfg = dict(DEFAULT_CONFIG)
        cfg["devices"] = dict(DEFAULT_CONFIG["devices"])
        cfg["rooms"] = list(DEFAULT_CONFIG["rooms"])
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    if isinstance(raw.get("devices"), dict):
                        cfg["devices"] = raw["devices"]
                    if isinstance(raw.get("rooms"), list):
                        cfg["rooms"] = raw["rooms"]
                    for k in ("enabled", "interval_minutes", "night_start", "night_end",
                              "confidence_threshold", "cooldown_minutes"):
                        if k in raw:
                            cfg[k] = raw[k]
            except Exception as e:
                logger.warning("decision.json parse failed, use default: %s", e)
        self.cfg = cfg
        # 首次播种落盘
        if not self.path.exists():
            self.save()

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("save decision.json failed: %s", e)

    # ---- 读取 ----
    # 配置侧一律「坏值记一行 + 按文档默认值走」：这些属性在调度器注册块里被读，
    # DB99 的原始形态就是一枚 `interval_minutes: "abc"` 让整块注册中断（⛔ 抛给调用方）。

    def _num(self, name: str, default, *, integer: bool = True, minimum=None, maximum=None):
        conv = to_int if integer else to_float
        try:
            return conv(self.cfg.get(name), name, default=default, minimum=minimum, maximum=maximum)
        except ParamError as exc:
            logger.warning("decision.json 字段不可解，按默认值走：%s（默认=%s）", exc, default)
            return default

    def _flag(self, name: str, default: bool) -> bool:
        try:
            return to_bool(self.cfg.get(name), name, default=default)
        except ParamError as exc:
            logger.warning("decision.json 布尔字段不可解，按默认值走：%s（默认=%s）", exc, default)
            return default

    @property
    def enabled(self) -> bool:
        return self._flag("enabled", True)

    @property
    def interval_minutes(self) -> int:
        return max(1, self._num("interval_minutes", 10))

    @property
    def night_start(self) -> int:
        return self._num("night_start", 23, minimum=0, maximum=23)

    @property
    def night_end(self) -> int:
        return self._num("night_end", 7, minimum=0, maximum=23)

    @property
    def confidence_threshold(self) -> float:
        return self._num("confidence_threshold", 0.6, integer=False, minimum=0.0, maximum=1.0)

    @property
    def cooldown_minutes(self) -> int:
        return max(1, self._num("cooldown_minutes", 30))

    @property
    def devices(self) -> dict[str, str]:
        return dict(self.cfg.get("devices") or {})

    @property
    def rooms(self) -> list[str]:
        return list(self.cfg.get("rooms") or [])

    def to_dict(self) -> dict:
        return dict(self.cfg)
