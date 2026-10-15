"""入参解析共用尺子（批221）。

落点依据：`doc/审计报告/DB_第五期_33轮审计汇总报告_20261015.md` §八-③——
公共实现建在 `api/deps.py` 时，`bus`／`core`／`skills` 层反向依赖不到，约 30 处站点够不着，
故本模块放在 `butler/` 根层，任何一层都能引。

三族形态在这一把尺子里各自有明确判据（⛔ 三条族共用一个「兜底值」）：
  ① `LIMIT -1` 在 SQLite 里是「无上限」⇒ 负数必须判失败（`minimum` 必给）。
  ② 负数时间窗让 `cutoff = now - (-1)` 落到未来 ⇒ 窗口类参数 `minimum=1`。
  ③ `d.get(k, D)` 的默认值在显式 `null` 时不生效 ⇒ 本模块把「缺键」与「显式 null」并成一形。
"""
from __future__ import annotations

from typing import Any

from butler.logging_setup import get_logger

logger = get_logger("butler.param")

__all__ = [
    "ParamError",
    "to_int",
    "to_float",
    "to_bool",
    "soft_num",
    "soft_flag",
    "body_num",
    "body_bool",
    "query_num",
    "query_bool",
    "path_num",
]

_REQUIRED = object()

_TRUE_WORDS = {"1", "true", "yes", "y", "on"}
_FALSE_WORDS = {"0", "false", "no", "n", "off", ""}


class ParamError(ValueError):
    """入参不合法。继承 `ValueError`：既有 `except ValueError` 的站点仍能接住。

    消息只点名参数与失败原因，⛔ 回显原值（调用方常把它直接写进响应或日志）。
    """

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason
        super().__init__("参数 %s 非法：%s" % (name, reason))


def _is_absent(value: Any) -> bool:
    """缺键、显式 null、空串／纯空白一律算「没给」。"""
    if value is None:
        return True
    return isinstance(value, str) and not value.strip()


def to_int(value: Any, name: str, *, default: Any = _REQUIRED,
           minimum: int | None = None, maximum: int | None = None) -> Any:
    return _to_number(value, name, default=default, minimum=minimum, maximum=maximum, integer=True)


def to_float(value: Any, name: str, *, default: Any = _REQUIRED,
             minimum: float | None = None, maximum: float | None = None) -> Any:
    return _to_number(value, name, default=default, minimum=minimum, maximum=maximum, integer=False)


def _to_number(value: Any, name: str, *, default: Any, minimum, maximum, integer: bool) -> Any:
    if _is_absent(value):
        if default is _REQUIRED:
            raise ParamError(name, "缺少必填值")
        return default
    # bool 是 int 的子类，但 `{"limit": true}` 不是调用方想要的数字。
    if isinstance(value, bool):
        raise ParamError(name, "不是数字")
    try:
        num = int(value) if integer else float(value)
    except (TypeError, ValueError):
        raise ParamError(name, "不是%s" % ("整数" if integer else "数字")) from None
    if integer and isinstance(value, float) and not value.is_integer():
        raise ParamError(name, "不是整数")
    if minimum is not None and num < minimum:
        raise ParamError(name, "不得小于 %s" % minimum)
    if maximum is not None and num > maximum:
        raise ParamError(name, "不得大于 %s" % maximum)
    return num


def to_bool(value: Any, name: str, *, default: Any = _REQUIRED) -> Any:
    """把 `null`／`"false"`／`0`／`"off"` 各按其本义读，⛔ 靠 `bool(...)` 的 truthiness 猜。

    `bool("false") is True` 正是 DB139 那条：「不要覆盖静默」被读成「要」。
    """
    if _is_absent(value):
        if default is _REQUIRED:
            raise ParamError(name, "缺少必填值")
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
    raise ParamError(name, "不是布尔值")


def soft_num(value: Any, name: str, *, default: Any, minimum=None, maximum=None,
             integer: bool = True) -> Any:
    """载荷／配置取数字：不可解就记一行日志按默认值走。

    分工——HTTP 侧抛 `ParamError` 换 400（调用方能改），MQTT 载荷／磁盘配置抛出去只会打断
    消费链与调度器注册，所以只能软着陆＋留痕。日志只点参数名与失败原因，⛔ 回显原值。
    """
    conv = to_int if integer else to_float
    try:
        return conv(value, name, default=default, minimum=minimum, maximum=maximum)
    except ParamError as exc:
        logger.warning("%s 不可解，按默认值走：%s（默认=%s）", name, exc.reason, default)
        return default


def soft_flag(value: Any, name: str, *, default: Any) -> Any:
    """`soft_num` 的布尔版，语义同上（DB139 那条 `bool("false") is True` 的宽形制）。"""
    try:
        return to_bool(value, name, default=default)
    except ParamError as exc:
        logger.warning("%s 不可解，按默认值走：%s（默认=%s）", name, exc.reason, default)
        return default


def body_num(body: Any, key: str, *, default: Any = _REQUIRED, minimum=None, maximum=None,
             required: bool = False, integer: bool = True) -> Any:
    """从已解析的请求体里取数字。`required=True` 时缺键即失败（返回类型仍按 `default` 走）。"""
    value = body.get(key) if isinstance(body, dict) else None
    if required and _is_absent(value):
        raise ParamError(key, "缺少必填值")
    to_num = to_int if integer else to_float
    return to_num(value, key, default=default, minimum=minimum, maximum=maximum)


def body_bool(body: Any, key: str, *, default: Any = _REQUIRED) -> Any:
    value = body.get(key) if isinstance(body, dict) else None
    return to_bool(value, key, default=default)


def query_num(request: Any, key: str, *, default: Any = _REQUIRED, minimum=None, maximum=None,
              integer: bool = True) -> Any:
    raw = request.query_params.get(key) if hasattr(request, "query_params") else None
    to_num = to_int if integer else to_float
    return to_num(raw, key, default=default, minimum=minimum, maximum=maximum)


def query_bool(request: Any, key: str, *, default: Any = _REQUIRED) -> Any:
    raw = request.query_params.get(key) if hasattr(request, "query_params") else None
    return to_bool(raw, key, default=default)


def path_num(request: Any, key: str, *, default: Any = _REQUIRED, minimum=None, maximum=None,
             integer: bool = True) -> Any:
    """路径段取数字。

    改前形制 `int(request.path_params.get("id", "0"))` 有两层病：非数字直接抛 ⇒ 冒 500；
    默认 "0" 静默落进 `WHERE id = 0` ⇒ 查不到却回 200，与「记录不存在」同形（DB138）。
    """
    raw = request.path_params.get(key) if hasattr(request, "path_params") else None
    to_num = to_int if integer else to_float
    return to_num(raw, key, default=default, minimum=minimum, maximum=maximum)
