"""日志初始化：统一格式、级别、敏感字段掩码。

绝不打印 token / 密码 / Cookie / 人脸图像 base64。高频事件（人脸、传感器）由调用方节流。

WO-DB-102 修复：脱敏从 Filter.filter()（改格式化前的模板串，会吃掉 %s 占位符导致整行丢失）
改为自定义 Formatter.format()（格式化之后再遮返回值，不碰 record.msg / record.args）。
"""
from __future__ import annotations

import logging
import re
import sys
import time

_SENSITIVE_KEYS = (
    "password", "passwd", "token", "secret", "cookie", "authorization",
    "api_key", "apikey", "key", "mqtt_password", "ha_token", "web_password",
)
_SENSITIVE_RE = re.compile(r"(" + "|".join(_SENSITIVE_KEYS) + r")\s*[:=]\s*['\"]?[^\s'\"]+", re.IGNORECASE)


class MaskingFilter(logging.Filter):
    """仅处理 dict 形式的 args（遮值不遮模板）。字符串模板的脱敏移到 MaskingFormatter。"""
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, dict):
            record.args = {k: ("***" if k.lower() in _SENSITIVE_KEYS else v) for k, v in record.args.items()}
        return True


def _active_trace_id() -> str:
    """当前上下文里的 trace_id；不在链上时为空串。

    不缓存导入结果：首次调用可能发生在 butler 包尚未就绪时。
    """
    try:
        from butler.core.trace_ctx import current_trace_id
        return current_trace_id.get() or ""
    except Exception:
        return ""


class MaskingFormatter(logging.Formatter):
    """格式化之后再对完整字符串做脱敏，不碰 record.msg / record.args，避免吃掉 %s 占位符。"""
    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if _SENSITIVE_RE.search(text):
            text = _SENSITIVE_RE.sub(r"\1=***", text)
        # v2.6#2：反查"这句话是谁触发播的"靠行尾这一个 id，不靠每个调用点自觉
        trace = _active_trace_id()
        return f"{text} trace={trace}" if trace else text


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        MaskingFormatter(
            fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler.addFilter(MaskingFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # 降噪第三方
    logging.getLogger("paho").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# ── 批28：调用方节流的本体（第六轮 P1-23「except → continue 把失败吞成无声」）──
DEFAULT_THROTTLE_WINDOW = 300.0  # 秒：同一个 key 在窗内只留第一条

_throttle_state: dict = {}  # key -> {"last": 上次放行时刻, "suppressed": 窗内被压掉的条数}


def _now() -> float:
    """节流用的钟。单独一个函数＝验收能注入假钟，⛔ 让测试睡满一个窗口。"""
    return time.monotonic()


def warn_throttled(logger, key: str, msg: str, *args,
                   window: float = DEFAULT_THROTTLE_WINDOW, exc: bool = True) -> None:
    """高频失败的 WARNING 留痕：窗内只发第一条，压掉的条数由出窗那条报「抑制 N 条」。

    给 `except Exception: continue` 那一类站点用——既要「这件事出过」看得见，
    又⛔ 把 900 轮的轮询变成 900 行日志（09-22 加日志加出 10,713 行/天那次）。
    必须在 except 块里调：traceback 从 sys.exc_info() 取，出块就没了。
    """
    now = _now()
    st = _throttle_state.get(key)
    if st is not None and now - st["last"] < window:
        st["suppressed"] += 1
        return
    suppressed = st["suppressed"] if st is not None else 0
    _throttle_state[key] = {"last": now, "suppressed": 0}
    text = "%s（抑制 %d 条）" % (msg, suppressed) if suppressed else msg
    ei = sys.exc_info()
    logger.warning(text, *args, exc_info=ei if (exc and ei[0] is not None) else False)
