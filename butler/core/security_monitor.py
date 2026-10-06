"""安全监控：确定性规则，不依赖 LLM。

监控项：
1. 门窗未关提醒（离家时）
2. 异常能耗提醒（空调开了但没人）
3. 长时间无人活动提醒
"""
from __future__ import annotations

import time

from butler.logging_setup import get_logger

logger = get_logger("butler.core.security_monitor")


class SecurityMonitor:
    """安全监控引擎。"""

    def __init__(self, runtime):
        self.rt = runtime
        self._last_alert: dict[str, float] = {}
        self._alert_cooldown = 3600  # 1小时冷却
        self._last_check_result: list[dict] = []  # 最近一次 check() 结果缓存（只读快照）
        self._last_check_time: float = 0.0         # 最近一次 check() 时间戳

    def check(self) -> list[dict]:
        """运行所有安全检查，返回告警列表。"""
        alerts = []

        # 1. 检查门窗是否打开（夜间）
        door_alerts = self._check_doors_open()
        alerts.extend(door_alerts)

        # 2. 检查空调是否在无人时运行
        ac_alerts = self._check_ac_unoccupied()
        alerts.extend(ac_alerts)

        # 写入只读快照（供 GET 端点读取，避免端点直接调 check() 推进冷却）
        self._last_check_result = alerts
        self._last_check_time = time.time()
        return alerts

    def _check_doors_open(self) -> list[dict]:
        """检查夜间门窗是否打开。"""
        alerts = []
        es = getattr(self.rt, "event_stream", None)
        if not es:
            return alerts

        state = es.get_current_state()
        now = time.time()
        import datetime
        hour = datetime.datetime.fromtimestamp(now).hour

        # 只在夜间 23:00-06:00 检查
        if not (hour >= 23 or hour < 6):
            return alerts

        for eid, evt in state.items():
            if "door" in eid.lower() and evt["state"] == "on":
                alert_key = f"door_open_{eid}"
                if now - self._last_alert.get(alert_key, 0) > self._alert_cooldown:
                    self._last_alert[alert_key] = now
                    alerts.append({
                        "type": "door_open",
                        "entity": eid,
                        "message": f"夜间门仍打开：{eid}",
                        "severity": "warning",
                    })
                    logger.warning("security alert: door open at night: %s", eid)

        return alerts

    def _check_ac_unoccupied(self) -> list[dict]:
        """检查空调是否在无人时运行。"""
        alerts = []
        es = getattr(self.rt, "event_stream", None)
        if not es:
            return alerts

        state = es.get_current_state()

        # 检查是否有人（人体传感器）
        anyone_home = False
        for eid, evt in state.items():
            if "motion" in eid.lower() or "occupancy" in eid.lower():
                if evt["state"] == "on":
                    anyone_home = True
                    break

        if anyone_home:
            return alerts

        # 没人在家，检查空调是否开着
        now = time.time()
        for eid, evt in state.items():
            if "climate" in eid.lower() and evt["state"] in ("heat", "cool", "auto", "dry", "fan_only"):
                alert_key = f"ac_unoccupied_{eid}"
                if now - self._last_alert.get(alert_key, 0) > self._alert_cooldown:
                    self._last_alert[alert_key] = now
                    alerts.append({
                        "type": "ac_unoccupied",
                        "entity": eid,
                        "message": f"无人在家但空调运行：{eid}",
                        "severity": "info",
                    })
                    logger.info("security alert: ac running unoccupied: %s", eid)

        return alerts


async def push_alert(rt, alert: dict) -> bool:
    """把一枚安全告警推给 Bark（审计 MA-01）。返回值＝是否真送达。

    Bark 那个方法的返回值就是回执：没配 url、被风控 drop／merge、HTTP≠200 都返回 False，
    它自己不会抛 ⇒ ⛔ 拿「没抛异常」当「已送达」＝把安全告警咽回去。
    """
    message = str((alert or {}).get("message", "")).strip()
    if not message:
        logger.error("SECURITY_PUSH_FAILED reason=empty_message alert=%r", alert)
        return False
    bark = getattr(rt, "bark", None)
    if bark is None:
        logger.error("SECURITY_PUSH_FAILED reason=no_bark_client（安全告警到不了手机）")
        return False
    try:
        sent = await bark.push(message, title="安全提醒")
    except Exception as e:
        logger.error("SECURITY_PUSH_FAILED reason=raised err=%r", e)
        return False
    if not sent:
        logger.error("SECURITY_PUSH_FAILED reason=not_sent（Bark 回执 False：无 url／被风控丢弃或合并／HTTP≠200）")
        return False
    return True
