"""API 公共依赖：响应约定、WebUI 登录鉴权。"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse

from butler.config import get_settings
from butler.logging_setup import get_logger
from butler.runtime import get_runtime

logger = get_logger("butler.auth")

# ── WO-ME-207：会话存储 ────────────────────────────────────────────────
# 改前三处缺陷：
#   1) token = sha256(用户 + 会话密钥 + 当秒时间戳)。会话密钥又由登录口令派生，
#      而本实例的口令只有 5 位 ⇒ 把「登录用户 × 7 天内的某一秒」跑 60 万次哈希，
#      就能撞中一条仍然活着的会话（TTL 7 天）。全程不发登录请求，
#      所以 WO-BUT-022 R-36 的登录锁定完全碰不到这条路。
#   2) 会话只在内存 ⇒ 容器一重启全员掉线。WO-ME-206 把浏览器改骑会话 cookie 之后，
#      这条从"不方便"升级成我引入的回归。
#   3) 只在命中时清理，条目只增不减；且"口令当 Bearer"那条兼容通道不过 /login 锁定，
#      等于留了一个不受限的 200/401 判分器。
# 改后：token 由 CSPRNG 生成；盘上只落 sha256(token) -> 过期时间（读到文件也拿不到可用会话）；
#      写入即清扫、条目有上限；Bearer 失败按来源计数刹车。
_SESSION_TTL = 7 * 24 * 3600  # 7 天过期
_SESSIONS_FILE = Path("/app/data/sessions.json")
_SESSIONS_MAX = 200
_active_sessions: dict[str, float] = {}  # sha256(token) -> 过期时间戳

# Bearer 侧刹车：同一来源 60s 内错 20 次即暂时拒判（正确凭证永不计数，正常调用方不受影响）
_BEARER_WINDOW = 60.0
_BEARER_MAX_FAIL = 20
_bearer_failures: dict[str, list[float]] = {}


def _session_hash(tok: str) -> str:
    return hashlib.sha256(tok.encode("utf-8")).hexdigest()


def _sessions_sweep(now: float) -> None:
    for k in [k for k, exp in _active_sessions.items() if exp <= now]:
        _active_sessions.pop(k, None)


def _sessions_save() -> None:
    try:
        _SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _SESSIONS_FILE.with_name("sessions.json.tmp")
        tmp.write_text(json.dumps(_active_sessions), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, _SESSIONS_FILE)
    except OSError as exc:  # 卷不可写时退回纯内存：行为等于改前，不会放行失败
        logger.warning("WO-ME-207 会话落盘失败，本轮退回内存会话：%s", type(exc).__name__)


def _sessions_load() -> None:
    try:
        raw = json.loads(_SESSIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return  # 没有 / 坏损 = 没有活会话：只会要求重新登录，绝不会放行
    now = time.time()
    if isinstance(raw, dict):
        _active_sessions.update({
            k: float(v)
            for k, v in raw.items()
            if isinstance(k, str) and isinstance(v, (int, float)) and float(v) > now
        })
    _sessions_sweep(now)


def make_session() -> str:
    """发一条会话。token 不可预测，且落盘，所以重启不再掉线。"""
    tok = secrets.token_urlsafe(32)
    now = time.time()
    _active_sessions[_session_hash(tok)] = now + _SESSION_TTL
    _sessions_sweep(now)
    overflow = len(_active_sessions) - _SESSIONS_MAX
    if overflow > 0:
        for k in sorted(_active_sessions, key=lambda t: _active_sessions[t])[:overflow]:
            _active_sessions.pop(k, None)
    _sessions_save()
    return tok


def session_alive(tok: str) -> bool:
    exp = _active_sessions.get(_session_hash(tok))
    if exp is None:
        return False
    if exp <= time.time():
        _active_sessions.pop(_session_hash(tok), None)
        _sessions_save()
        return False
    return True


def revoke_session(tok: str) -> bool:
    if _active_sessions.pop(_session_hash(tok), None) is None:
        return False
    _sessions_save()
    return True


def bearer_locked(request: Request) -> bool:
    """该来源是否已被 Bearer 失败次数刷进临时拒绝。命中时顺便记账并清旧账。"""
    ip, _, _ = _req_shape(request)
    now = time.time()
    fails = [t for t in _bearer_failures.get(ip, []) if now - t < _BEARER_WINDOW]
    if fails:
        _bearer_failures[ip] = fails
    else:
        _bearer_failures.pop(ip, None)
    if len(fails) < _BEARER_MAX_FAIL:
        return False
    if _audit_should_log(f"bearer_locked|{ip}"):
        logger.warning("WO-ME-207 Bearer 凭证错误过多，暂时拒判该来源：来源=%s 次数=%d", ip, len(fails))
    return True


def note_bearer_failure(request: Request) -> None:
    ip, _, _ = _req_shape(request)
    if len(_bearer_failures) > _AUDIT_CAP:  # 这张表也不许变成第二个"永不清理"
        now = time.time()
        for stale in [k for k, ts in _bearer_failures.items() if now - max(ts) >= _BEARER_WINDOW]:
            _bearer_failures.pop(stale, None)
    _bearer_failures.setdefault(ip, []).append(time.time())


_sessions_load()

# WO-BUT-022 R-36：登录限流 —— 5 次失败后锁定 15 分钟
_login_failures: dict[str, list[float]] = {}  # client_ip -> [失败时间戳]
_LOGIN_MAX_FAIL = 5
_LOGIN_LOCK_SECONDS = 15 * 60

# ── WO-ME-201：内部调用方的具名服务钥匙 ────────────────────────────────
# 格式：BUTLER_SERVICE_TOKENS="name1=tok1,name2=tok2"（逗号分隔，等号分左右）
# 读环境变量在 import 期，改值需重启容器。空值 = 服务钥匙功能关闭，行为与改前一致。
# 目的：让内部调用方逐步从"拿登录口令当 Bearer"迁到各自的具名钥匙，
#      第二步（ALLOW_PASSWORD_AS_BEARER=false）要等本单记账名单清零才允许出。
def _load_service_tokens() -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in os.environ.get("BUTLER_SERVICE_TOKENS", "").split(","):
        name, sep, tok = pair.partition("=")
        name, tok = name.strip(), tok.strip()
        if sep and name and tok:
            out[name] = tok
    return out


_SERVICE_TOKENS = _load_service_tokens()

# 记账节流：同一 (来源, 路径) 每 300s 最多一条，避免刷日志
_AUDIT_TTL = 300.0
_AUDIT_CAP = 512
_audit_seen: dict[str, float] = {}


def _audit_should_log(key: str) -> bool:
    now = time.time()
    if len(_audit_seen) > _AUDIT_CAP:  # 不让这张表变成第二个"永不清理"的会话表
        for stale in [k for k, t in _audit_seen.items() if now - t >= _AUDIT_TTL]:
            _audit_seen.pop(stale, None)
        if len(_audit_seen) > _AUDIT_CAP:
            return False
    last = _audit_seen.get(key, 0.0)
    if now - last < _AUDIT_TTL:
        return False
    _audit_seen[key] = now
    return True


def _req_shape(request: Request) -> tuple[str, str, str]:
    ip = getattr(getattr(request, "client", None), "host", "") or "unknown"
    path = str(getattr(getattr(request, "url", None), "path", "") or "")
    ua = request.headers.get("User-Agent", "")[:80] if hasattr(request, "headers") else ""
    return ip, path, ua

def _note_compat(kind: str, ip: str, path: str) -> None:
    """WO-ME-220：把兼容通道调用记进跨重启账本。

    记不上账不许影响鉴权结果，但也不许静默——本仓正为「吞异常且不落一字」开单修理（WO-ME-213）。
    """
    try:
        from butler.store.db import note_compat
        note_compat(kind, ip, path)
    except Exception as e:
        logger.warning("WO-ME-220 兼容账本写入失败（不影响鉴权结果）：kind=%s 原因=%s", kind, e)


def _note_password_bearer(request: Request) -> None:
    ip, path, ua = _req_shape(request)
    if _audit_should_log(f"pw|{ip}|{path}"):
        # 字段名刻意避开 password=/token=/key= 形状：日志脱敏器会把那种形状的整行吃掉
        logger.warning("WO-ME-201 内部调用用登录口令当凭证，待迁移具名钥匙：来源=%s 接口=%s 代理=%s",
                       ip, path, ua)
        _note_compat("pw_bearer", ip, path)  # WO-ME-220：账要跨重启活著，日志不算账本


def _note_service_call(name: str, request: Request) -> None:
    ip, path, _ = _req_shape(request)
    if _audit_should_log(f"st|{name}|{path}"):
        logger.info("WO-ME-201 具名钥匙通过：钥匙名=%s 来源=%s 接口=%s", name, ip, path)
        _note_compat("service_token", ip, path)  # WO-ME-220：同上，且与 pw_bearer 分桶


def ok(data=None, **kw) -> JSONResponse:
    body: dict = {"ok": True}
    if data is not None:
        body["data"] = data
    body.update(kw)
    return JSONResponse(body)


def err(message: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=code)




def auth_enabled() -> bool:
    return bool(get_settings().web_user)


def auth_disabled_allowed() -> bool:
    """WO-ME-204：未配置 BUTLER_WEB_USER 时是否仍放行全站。默认 False = 拒绝。"""
    return bool(getattr(get_settings(), "allow_no_auth", False))


def check_auth(request: Request) -> bool:
    if not auth_enabled():
        if auth_disabled_allowed():
            if _audit_should_log("no_auth_on"):
                logger.error("WO-ME-204 全站以「无鉴权」模式运行（调试开关已显式打开），任何来源皆为管理员")
            return True
        if _audit_should_log("no_auth_off"):
            logger.error("WO-ME-204 未配置 BUTLER_WEB_USER，全站鉴权按默认拒绝（原行为是静默放行，属 fail-open）")
        return False
    # cookie 会话（WO-ME-207：过期与落盘统一交给存储层）
    cookie_tok = request.cookies.get("butler_auth", "")
    if cookie_tok and session_alive(cookie_tok):
        return True
    ah = request.headers.get("Authorization", "")
    if not ah.startswith("Bearer "):
        return False
    tok = ah[7:].strip()
    if not tok:
        return False
    if bearer_locked(request):  # WO-ME-207：先刹车，再谈凭证
        return False
    # WO-ME-201：具名服务钥匙，命中即放行并记一笔（不打印凭证本身）
    for name, expected in _SERVICE_TOKENS.items():
        if hmac.compare_digest(tok, expected):
            _note_service_call(name, request)
            return True
    # 兼容 API key / web_password 作为 Bearer（便于 Node-RED / mi-gpt 直接调用）
    # P0-8：可通过 ALLOW_PASSWORD_AS_BEARER=false 禁用
    # WO-ME-201：本分支每次命中都会进账，等名单清零后出第二步工单关闸
    # 比较改常数时间，与 WO-BUT-022 R-52 的 webhook 比较口径一致
    allow_pw_bearer = os.environ.get("ALLOW_PASSWORD_AS_BEARER", "false").lower() in ("true", "1", "yes", "on")
    if allow_pw_bearer and hmac.compare_digest(tok, get_settings().web_password or ""):
        _note_password_bearer(request)
        return True
    note_bearer_failure(request)  # WO-ME-207
    return False


def is_login_locked(client_ip: str) -> bool:
    """WO-BUT-022 R-36：检查该 IP 是否在登录锁定中（5 次失败后 15 分钟）。"""
    import time
    now = time.time()
    fails = _login_failures.get(client_ip, [])
    fails = [t for t in fails if now - t < _LOGIN_LOCK_SECONDS]
    return len(fails) >= _LOGIN_MAX_FAIL


def login(user: str, password: str, client_ip: str = "unknown") -> str | None:
    """登录验证，带 WO-BUT-022 R-36 限流：5 次失败锁定 15 分钟。
    锁定时仍返回 None（由调用方先用 is_login_locked 判断返回 429）。"""
    import time
    s = get_settings()
    now = time.time()
    fails = _login_failures.get(client_ip, [])
    fails = [t for t in fails if now - t < _LOGIN_LOCK_SECONDS]
    if len(fails) >= _LOGIN_MAX_FAIL:
        return None  # 锁定中
    if not auth_enabled():
        if not auth_disabled_allowed():
            return None
        return make_session()
    if hmac.compare_digest(str(user), str(s.web_user)) and hmac.compare_digest(str(password), str(s.web_password)):
        _login_failures.pop(client_ip, None)
        return make_session()
    fails.append(now)
    _login_failures[client_ip] = fails
    return None


def logout(request: Request) -> bool:
    """登出：移除会话 token。返回是否成功。"""
    tok = request.cookies.get("butler_auth", "")
    return bool(tok) and revoke_session(tok)


def guard(request: Request):
    """无鉴权返回 err(...)。配合路由使用。"""
    if not check_auth(request):
        return err("未登录", 401)
    return None


# WO-ME-215：任务板上报钥匙（TASK_REPORT_TOKEN）的失败刹车。
# 刻意不复用 WO-ME-207 的 _bearer_failures 桶：那条桶锁的是"该 IP 的一切 Bearer 内部调用"，
# 与任务板共用会让一台误报的 TP 主机顺手把自己 IP 上的其它内部调用一起锁死。
_report_failures: dict[str, list[float]] = {}
_REPORT_MAX_FAIL = 5
_REPORT_WINDOW = 300.0


def report_locked(request) -> bool:
    """该来源在 _REPORT_WINDOW 内失败次数已达 _REPORT_MAX_FAIL。顺带清过期账。"""
    ip, _, _ = _req_shape(request)
    now = time.time()
    fails = [t for t in _report_failures.get(ip, []) if now - t < _REPORT_WINDOW]
    if fails:
        _report_failures[ip] = fails
    else:
        _report_failures.pop(ip, None)
    return len(fails) >= _REPORT_MAX_FAIL


def note_report_failure(request) -> None:
    """记一次失败并落一行留痕（同来源 300s 内最多一行，防刷日志）。不记钥匙本身。"""
    ip, path, _ = _req_shape(request)
    if len(_report_failures) > _AUDIT_CAP:
        now = time.time()
        for stale in [k for k, ts in _report_failures.items() if now - max(ts) >= _REPORT_WINDOW]:
            _report_failures.pop(stale, None)
    _report_failures.setdefault(ip, []).append(time.time())
    if _audit_should_log(f"rf|{ip}|{path}"):
        logger.warning("WO-ME-215 上报钥匙校验失败：来源=%s 接口=%s 窗口内累计=%d/%d 次",
                       ip, path, len(_report_failures.get(ip, [])), _REPORT_MAX_FAIL)
