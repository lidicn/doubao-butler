"""Cron Task 执行引擎（v1.9 Koin Action）。

负责：
1. 调用预注册的 API（彩云天气等）
2. JSONPath 提取字段，条件判断
3. 满足条件时执行动作（TTS / Bark / 微信推送）
4. 执行日志记录
5. 定时调度（APScheduler）
6. API 注册信息持久化
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from butler.logging_setup import get_logger, warn_throttled

logger = get_logger("butler.cron_task")


class CronTaskExecutor:
    """Cron Task 执行器。"""
    
    def __init__(self, settings, ha=None, xiaomi=None, bark=None, wechat=None, scheduler=None):
        self.s = settings
        self.ha = ha
        self.xiaomi = xiaomi
        self.bark = bark
        self.wechat = wechat
        self.scheduler = scheduler
        self._api_registry: dict[str, dict] = {}  # api_id -> {url, headers, timeout}
        self._secrets: dict[str, str] = {}  # secret_name -> value（Secret Vault）
        self._execution_log: list[dict] = []  # 最近执行记录（内存缓存）
        self._max_log_size = 1000  # 磁盘保留上限
        self._scheduled_jobs: dict[str, str] = {}  # skill_id -> job_id
        self._failure_counts: dict[str, int] = {}  # skill_id -> 连续失败次数
        self._failure_alerted: set[str] = set()  # 已告警过的任务（避免重复告警）
        self._pending_bark_tasks: set = set()  # Bark 告警任务的强引用（完成即摘除）
        self._api_last_call: dict[str, float] = {}  # api_id -> 上次调用时间戳（节流）
        self._api_min_interval = 10.0  # 同一 API 最小调用间隔（秒）
        self._tts_locks: dict[str, asyncio.Semaphore] = {}  # device_id -> TTS 串行锁
        self._data_dir = Path(getattr(settings, "data_dir", "data")) / "cron_task"
        self._apis_file = self._data_dir / "apis.json"
        self._secrets_file = self._data_dir / "secrets.json"
        self._log_file = self._data_dir / "execution_logs.jsonl"
        self._load_apis()
        self._load_secrets()
        self._load_logs()
    
    def _load_apis(self):
        """从磁盘加载 API 注册信息。"""
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            if self._apis_file.exists():
                data = json.loads(self._apis_file.read_text(encoding="utf-8"))
                self._api_registry = data.get("apis", {})
                logger.info("cron_task APIs loaded: %d", len(self._api_registry))
        except Exception as e:
            logger.warning("failed to load cron_task APIs: %s", e)
            self._api_registry = {}
    
    def _save_apis(self):
        """保存 API 注册信息到磁盘。"""
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            data = {"apis": self._api_registry, "updated_at": datetime.now().isoformat()}
            self._apis_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("failed to save cron_task APIs: %s", e)

    def _load_logs(self):
        """从磁盘加载执行日志（JSONL）。"""
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            if self._log_file.exists():
                lines = self._log_file.read_text(encoding="utf-8").strip().splitlines()
                self._execution_log = []
                for line in lines:
                    if line.strip():
                        try:
                            self._execution_log.append(json.loads(line))
                        except json.JSONDecodeError:
                            warn_throttled(logger, "cron.log_line", "cron 执行日志有一行解析不了（该行跳过，其余照旧）")
                            continue
                logger.info("cron_task execution logs loaded: %d", len(self._execution_log))
        except Exception as e:
            logger.warning("failed to load cron_task logs: %s", e)
            self._execution_log = []

    def _append_log(self, entry: dict):
        """追加一条日志到磁盘（JSONL）。"""
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            # 超过上限时裁剪
            if len(self._execution_log) > self._max_log_size:
                self._execution_log = self._execution_log[-self._max_log_size:]
                self._rewrite_logs()
        except Exception as e:
            logger.warning("failed to append cron_task log: %s", e)

    def _rewrite_logs(self):
        """重写日志文件（裁剪后）。"""
        try:
            with open(self._log_file, "w", encoding="utf-8") as f:
                for entry in self._execution_log:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("failed to rewrite cron_task logs: %s", e)
    
    def register_api(self, api_id: str, url: str, headers: dict | None = None,
                     timeout: int = 10, secret: str = "", secret_name: str = "token",
                     method: str = "GET", body: str = ""):
        """注册 API。URL 中可用 {secret:xxx} 占位符引用密钥。"""
        # 如果传了 secret，存入 Vault
        if secret:
            self._secrets[secret_name] = secret
            self._save_secrets()
        self._api_registry[api_id] = {
            "url": url,
            "headers": headers or {},
            "timeout": timeout,
            "method": method.upper(),
            "body": body,
        }
        self._save_apis()
        logger.info("API registered: %s (%s)", api_id, method.upper())

    def unregister_api(self, api_id: str):
        """注销 API。"""
        if api_id in self._api_registry:
            del self._api_registry[api_id]
            self._save_apis()
            logger.info("API unregistered: %s", api_id)

    def _load_secrets(self):
        """从磁盘加载 Secret Vault。"""
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            if self._secrets_file.exists():
                self._secrets = json.loads(self._secrets_file.read_text(encoding="utf-8"))
                logger.info("cron_task secrets loaded: %d keys", len(self._secrets))
        except Exception as e:
            logger.warning("failed to load secrets: %s", e)
            self._secrets = {}

    def _save_secrets(self):
        """保存 Secret Vault 到磁盘。"""
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            self._secrets_file.write_text(
                json.dumps(self._secrets, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.warning("failed to save secrets: %s", e)

    def _resolve_url(self, url: str) -> str:
        """把 URL 中的 {secret:xxx} 替换为实际密钥值。"""
        import re
        def replacer(m):
            name = m.group(1)
            return self._secrets.get(name, m.group(0))
        return re.sub(r"\{secret:([^}]+)\}", replacer, url)

    def test_api(self, api_id: str) -> dict:
        """直接测试 API 连通性（不经过条件/动作）。同步方法：事件循环内的调用方须自行
        to_thread 包裹（唯一调用方 cron_task_routes 已这么做），否则 asyncio.run 当场报错，
        好过起线程池兜底把调用方一起拖停。"""
        return asyncio.run(self._test_api_async(api_id))

    async def _test_api_async(self, api_id: str) -> dict:
        """异步测试 API 连通性。"""
        api_config = self._api_registry.get(api_id)
        if not api_config:
            return {"ok": False, "error": f"API {api_id} 未注册"}
        try:
            resolved_url = self._resolve_url(api_config["url"])
            method = (api_config.get("method") or "GET").upper()
            body_raw = api_config.get("body", "")
            if isinstance(body_raw, dict):
                body_raw = json.dumps(body_raw)
            resolved_body = self._resolve_url(body_raw) if body_raw else None
            async with httpx.AsyncClient(timeout=api_config["timeout"]) as client:
                if method == "POST":
                    req_headers = dict(api_config["headers"])
                    if resolved_body and "content-type" not in {k.lower() for k in req_headers}:
                        req_headers["Content-Type"] = "application/json"
                    resp = await client.post(resolved_url, headers=req_headers, content=resolved_body)
                else:
                    resp = await client.get(resolved_url, headers=api_config["headers"])
            return {
                "ok": resp.status_code == 200,
                "http_status": resp.status_code,
                "body_preview": resp.text[:500],
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_apis(self) -> list[dict]:
        """列出所有已注册 API（密钥脱敏）。"""
        result = []
        for k, v in self._api_registry.items():
            entry = {"id": k}
            for kk, vv in v.items():
                if kk == "headers" and isinstance(vv, dict):
                    # 脱敏 headers 中的 Authorization
                    safe_headers = {}
                    for hk, hv in vv.items():
                        if "auth" in hk.lower() or "token" in hk.lower():
                            safe_headers[hk] = "***"
                        else:
                            safe_headers[hk] = hv
                    entry["headers"] = safe_headers
                else:
                    entry[kk] = vv
            result.append(entry)
        return result
    
    async def _emit_tv_notify(self, out_config: dict, message: str) -> str:
        """tv_notify 出口（DCD 2026-10-02 §二 A）：插活井，⛔ 死井。

        旧实现直发那条 0 订阅方的死井主题（sdd 前缀，DCD 裁定不登记、改插活井）却日志写
        `tv_notify: ok`＝对自己撒谎。
        现走 butler/notify 路由 → TVClient.notify() → `tv/livingroom/cmd/notify`（CAM 订 cmd/#）。
        载荷以 api/notify_routes.py 那份为准（同一形状，⛔ 另定 CAM 专用最小载荷）。
        返回值必须跟着通道回执：失败/未就绪里⛔ 不许出现 ": ok"。
        """
        from butler.notify.singleton import get_router
        from butler.notify.router import ALLOWED_TYPES

        router = get_router()
        if router is None:
            return "tv_notify: notify router not ready"

        member = str(out_config.get("member", "")).strip()
        ntype = str(out_config.get("notify_type", "info")).strip().lower()
        if ntype not in ALLOWED_TYPES:
            ntype = "info"
        important = bool(out_config.get("important", False))
        duration = int(out_config.get("duration", 8000)) if not important else 0
        base_url = str(getattr(self.s, "base_url", "") or "").rstrip("/")
        payload = {
            "title": str(out_config.get("title", "豆包管家")),
            "content": message,
            "type": ntype,
            "duration": duration,
            "important": important,
            "tts_url": str(out_config.get("tts_url", "")),
            "tts_volume": int(out_config.get("volume", 80)),
            "pause_media": bool(out_config.get("pause_media", False)),
            "avatar_url": "%s/avatars/%s.png" % (base_url, member or "doubao"),
        }
        try:
            res = await router.notify("tv", message, tv_payload=payload)
        except Exception as e:
            logger.warning("cron tv_notify raised: %s", e)
            return "tv_notify: failed - %s" % e
        cr = res.results.get("tv") if getattr(res, "results", None) else None
        if cr is None:
            return "tv_notify: no receipt from tv channel"
        if getattr(cr, "ok", False):
            return "tv_notify: ok"
        return "tv_notify: failed - %s" % (cr.error or "unknown")

    async def execute_task(self, skill: dict) -> dict:
        """执行一个 cron_task 技能。
        
        Returns:
            {
                "ok": bool,
                "status": "success" | "failed" | "skipped",
                "reason": str,
                "http_status": int,
                "condition_result": bool,
                "action_result": str,
            }
        """
        skill_id = skill.get("id", "unknown")
        brain = skill.get("brain", {})
        output = skill.get("output", [])
        
        logger.info("Executing cron_task: %s", skill_id)
        
        result = {
            "ok": True,
            "status": "success",
            "reason": "",
            "http_status": 0,
            "condition_result": False,
            "action_result": "",
            "executed_at": datetime.now().isoformat(),
        }
        
        # 1. 调用 API
        api_id = brain.get("api_id")
        if not api_id:
            result["ok"] = False
            result["status"] = "failed"
            result["reason"] = "api_id 未配置"
            self._log_execution(skill_id, result)
            return result
        
        api_config = self._api_registry.get(api_id)
        if not api_config:
            result["ok"] = False
            result["status"] = "failed"
            result["reason"] = f"API {api_id} 未注册"
            self._log_execution(skill_id, result)
            return result
        
        # 执行 HTTP 请求（单次，无重试，重试由上层调度器控制）
        # API 节流：同一 api_id 10s 内最多 1 次
        now = time.time()
        last_call = self._api_last_call.get(api_id, 0)
        if now - last_call < self._api_min_interval:
            result["status"] = "skipped"
            result["reason"] = f"API 节流：{api_id} 距上次调用不足 {self._api_min_interval:.0f}s"
            logger.info("Cron task %s throttled: %s", skill_id, result["reason"])
            self._log_execution(skill_id, result)
            return result
        self._api_last_call[api_id] = now

        try:
            resolved_url = self._resolve_url(api_config["url"])
            method = (api_config.get("method") or "GET").upper()
            body_template = api_config.get("body", "")
            async with httpx.AsyncClient(timeout=api_config["timeout"]) as client:
                if method == "POST":
                    body_raw = api_config.get("body", "")
                    if isinstance(body_raw, dict):
                        body_raw = json.dumps(body_raw)
                    resolved_body = self._resolve_url(body_raw) if body_raw else None
                    # 自动补 Content-Type（如果用户没设）
                    req_headers = dict(api_config["headers"])
                    if resolved_body and "content-type" not in {k.lower() for k in req_headers}:
                        req_headers["Content-Type"] = "application/json"
                    resp = await client.post(resolved_url, headers=req_headers,
                                            content=resolved_body)
                else:
                    resp = await client.get(resolved_url, headers=api_config["headers"])
                result["http_status"] = resp.status_code
                if resp.status_code != 200:
                    result["ok"] = False
                    result["status"] = "failed"
                    result["reason"] = f"HTTP {resp.status_code}"
                    self._log_execution(skill_id, result)
                    return result
                data = resp.json()
        except Exception as e:
            result["ok"] = False
            result["status"] = "failed"
            result["reason"] = f"HTTP 请求失败: {str(e)}"
            self._log_execution(skill_id, result)
            return result
        
        # 2. 条件判断
        condition = brain.get("condition", {})

        # 时区感知模式：hour_local（按本地小时匹配，不需要猜数组下标）
        if condition.get("type") == "hour_local":
            condition_result = self._eval_hour_local(data, condition)
        else:
            jsonpath = condition.get("jsonpath", "")
            comparator = condition.get("comparator", "exists")
            expected_value = condition.get("value")
            extracted = self._extract_jsonpath(data, jsonpath)
            condition_result = self._evaluate_condition(extracted, comparator, expected_value)

        result["condition_result"] = condition_result
        
        if not condition_result:
            result["status"] = "skipped"
            result["reason"] = "条件不满足"
            logger.info("Cron task %s skipped: condition not met", skill_id)
            self._log_execution(skill_id, result)
            return result
        
        # 3. 执行动作
        message_template = brain.get("message_template", "")
        message = self._render_template(message_template, data)
        
        action_results = []
        for out_config in output:
            out_type = out_config.get("type", "")
            try:
                if out_type == "xiaomi_speak":
                    room = out_config.get("room", "客厅")
                    role_id = out_config.get("role", "butler")
                    device_id = out_config.get("device", "")
                    # TTS 设备级串行：同一设备的播报排队，不重叠
                    lock_key = device_id or f"room:{room}"
                    if lock_key not in self._tts_locks:
                        self._tts_locks[lock_key] = asyncio.Semaphore(1)
                    async with self._tts_locks[lock_key]:
                        # 调用 dialog 的 speak_as_role 功能，指定角色和房间
                        from butler.runtime import get_runtime
                        rt = get_runtime()
                        if rt.dialog and rt.roles:
                            role = rt.roles.get(role_id)
                            if role:
                                # 如果指定了 device_id，直接用该设备
                                if device_id:
                                    device = rt.devices.get(device_id)
                                    if device:
                                        await rt.dialog._emit_devices(message, [device], role)
                                        action_results.append(f"xiaomi_speak@{device_id}: ok")
                                    else:
                                        action_results.append(f"xiaomi_speak@{device_id}: device not found")
                                else:
                                    # 否则按房间解析
                                    await rt.dialog.speak_as_role(role, message, room, member="系统")
                                    action_results.append(f"xiaomi_speak@{room}: ok")
                            else:
                                action_results.append(f"xiaomi_speak@{room}: role {role_id} not found")
                        else:
                            action_results.append(f"xiaomi_speak@{room}: dialog or roles not ready")
                elif out_type == "bark":
                    if self.bark:
                        b_title = out_config.get("title", "豆包管家")
                        await self.bark.push(message, title=b_title, priority="info")
                        action_results.append("bark: ok")
                    else:
                        action_results.append("bark: not configured")
                elif out_type == "tv_notify":
                    action_results.append(await self._emit_tv_notify(out_config, message))
            except Exception as e:
                action_results.append(f"{out_type}: failed - {str(e)}")
        
        result["action_result"] = "; ".join(action_results)
        self._log_execution(skill_id, result)
        logger.info("Cron task %s completed: %s", skill_id, result["status"])
        return result
    
    def _extract_jsonpath(self, data: dict, path: str) -> Any:
        """简单 JSONPath 提取（只支持 $.a.b.c 格式）。"""
        if not path:
            return None
        # 去掉 $.
        if path.startswith("$."):
            path = path[2:]
        parts = path.split(".")
        current = data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list):
                # 数组索引
                try:
                    idx = int(part)
                    current = current[idx] if idx < len(current) else None
                except ValueError:
                    return None
            else:
                return None
        return current
    
    def _evaluate_condition(self, actual: Any, comparator: str, expected: Any = None) -> bool:
        """条件判断。"""
        if comparator == "exists":
            return actual is not None
        elif comparator == "gt":
            try:
                return float(actual) > float(expected)
            except (TypeError, ValueError):
                return False
        elif comparator == "lt":
            try:
                return float(actual) < float(expected)
            except (TypeError, ValueError):
                return False
        elif comparator == "eq":
            return str(actual) == str(expected)
        elif comparator == "contains":
            return str(expected) in str(actual)
        return False

    def _eval_hour_local(self, data: dict, condition: dict) -> bool:
        """时区感知条件：按本地小时匹配降水概率。

        condition 格式：
        {
          "type": "hour_local",
          "hours": [12, 18, 20],       # 要检查的本地小时
          "threshold": 50,             # 概率阈值（百分比）
          "any": true                  # true=任一小时超过即触发，false=全部超过才触发
        }
        """
        import datetime as dt
        try:
            hourly = (data.get("result") or {}).get("hourly") or {}
            precip = hourly.get("precipitation") or []
            if not precip:
                return False

            target_hours = condition.get("hours", [])
            threshold = condition.get("threshold", 50)
            any_mode = condition.get("any", True)

            # 深圳 UTC+8
            tz_offset = dt.timedelta(hours=8)
            now_local = dt.datetime.now() + tz_offset  # 粗略：API 返回的时间戳转本地

            results = []
            for target_h in target_hours:
                found = False
                for slot in precip:
                    # slot 有 datetime 字段（UTC ISO）
                    slot_dt_str = slot.get("datetime", "")
                    if not slot_dt_str:
                        continue
                    try:
                        slot_dt = dt.datetime.fromisoformat(slot_dt_str.replace("Z", "+00:00"))
                        slot_local_hour = (slot_dt.hour + 8) % 24  # UTC+8
                    except Exception:
                        warn_throttled(logger, "cron.precip_slot", "cron 降水时段 datetime 解析不了（该 slot 跳过）")
                        continue
                    if slot_local_hour == target_h:
                        prob = slot.get("probability", 0) or 0
                        results.append(prob > threshold)
                        found = True
                        break
                if not found:
                    results.append(False)

            if any_mode:
                return any(results)
            return all(results)
        except Exception as e:
            logger.warning("hour_local eval failed: %s", e)
            return False
    
    def _record_failure_pattern(self, skill_id: str, result: dict):
        """记录失败模式到文件（供自进化分析）。"""
        try:
            pattern_file = self._data_dir / "failure_patterns.jsonl"
            entry = {
                "time": datetime.now().isoformat(),
                "skill_id": skill_id,
                "reason": result.get("reason", ""),
                "http_status": result.get("http_status", 0),
                "condition_result": result.get("condition_result"),
                "repair_suggestion": self._suggest_fix(skill_id, result.get("reason", "")),
            }
            with open(pattern_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug("failed to record failure pattern: %s", e)

    def _suggest_fix(self, skill_id: str, reason: str) -> str:
        """根据失败原因生成修复建议（规则-based，不调 LLM）。"""
        suggestions = {
            "API": "检查 API 注册是否有效，token 是否过期",
            "HTTP 401": "API 密钥已过期，需重新注册",
            "HTTP 403": "API 权限不足，检查密钥",
            "HTTP 404": "API 地址有误，检查 URL",
            "HTTP 500": "API 服务器故障，稍后重试",
            "timeout": "API 响应超时，增加 timeout 或检查网络",
            "JSONPath": "条件路径有误，检查 JSONPath 表达式",
            "hour_local": "小时数据不足，检查预报是否覆盖目标时间",
            "节流": "调用过于频繁，这是正常保护",
        }
        for keyword, suggestion in suggestions.items():
            if keyword.lower() in reason.lower():
                return suggestion
        return "检查技能配置或联系管理员"

    def get_failure_patterns(self, skill_id: str | None = None, limit: int = 50) -> list[dict]:
        """获取失败模式记录。"""
        patterns = []
        pattern_file = self._data_dir / "failure_patterns.jsonl"
        if not pattern_file.exists():
            return patterns
        try:
            lines = pattern_file.read_text(encoding="utf-8").strip().splitlines()
            for line in lines[-limit:]:
                if line.strip():
                    entry = json.loads(line)
                    if skill_id is None or entry.get("skill_id") == skill_id:
                        patterns.append(entry)
        except Exception as e:
            # P2-9 ②类（批42 组8）：try 包住整段读＋循环⇒一枚坏行抹掉其后全部条目，⛔ 再无声
            logger.warning("cron_task failure_patterns read aborted [%s] kept=%d: %s", skill_id, len(patterns), e)
        return patterns

    def _render_template(self, template: str, data: dict) -> str:
        """简单模板渲染（支持 {{$.a.b.c}} 格式）。"""
        if not template:
            return ""
        import re
        def replacer(match):
            path = match.group(1)
            value = self._extract_jsonpath(data, path)
            return str(value) if value is not None else ""
        return re.sub(r"\{\{([^}]+)\}\}", replacer, template)
    
    def _log_execution(self, skill_id: str, result: dict):
        """记录执行日志（内存 + 磁盘持久化），并跟踪连续失败。"""
        entry = {"skill_id": skill_id, **result}
        self._execution_log.append(entry)
        self._append_log(entry)

        # 连续失败检测
        status = result.get("status", "")
        if status == "failed":
            self._failure_counts[skill_id] = self._failure_counts.get(skill_id, 0) + 1
            count = self._failure_counts[skill_id]
            # 记录失败模式（供自进化分析）
            self._record_failure_pattern(skill_id, result)
            # 连续 3 次失败 → Bark 告警（只告警一次）
            if count >= 3 and skill_id not in self._failure_alerted:
                self._failure_alerted.add(skill_id)
                reason = result.get("reason", "未知")
                logger.error("cron task %s failed %d times, sending bark alert", skill_id, count)
                # 生成修复建议
                suggestion = self._suggest_fix(skill_id, reason)
                if self.bark:
                    self._spawn_bark_alert(
                        f"「{skill_id}」连续失败 {count} 次：{reason}\n建议：{suggestion}")
        elif status == "success":
            # 成功执行 → 重置失败计数和告警标记
            if skill_id in self._failure_counts:
                del self._failure_counts[skill_id]
            self._failure_alerted.discard(skill_id)
    
    def _spawn_bark_alert(self, message: str):
        """告警任务必须挂在强引用集合上——返回值一丢，任务可能在发出前就被 GC（同批18 P0-7）。"""
        task = asyncio.create_task(self.bark.push(message, title="Koin 任务告警", priority="warning"))
        self._pending_bark_tasks.add(task)
        task.add_done_callback(self._bark_alert_done)
    
    def _bark_alert_done(self, task):
        self._pending_bark_tasks.discard(task)
        if task.cancelled():
            logger.error("CRON_BARK 告警任务被取消，未送达")
            return
        exc = task.exception()
        if exc is not None:
            logger.error("CRON_BARK 告警发送失败：%r", exc, exc_info=exc)
    
    def _log_job_outcome(self, skill_id: str, future):
        """取回跨线程派发的 Future：协程炸了必须落流水，否则失败计数不涨、Bark 链路也不触发。"""
        try:
            exc = future.exception()
        except BaseException as e:
            logger.error("CRON_JOB_EXCEPTION skill=%s Future 取回本身失败：%r",
                         skill_id, e, exc_info=True)
            self._log_execution(skill_id, self._job_failure_entry("future 取回失败: %r" % e))
            return
        if exc is None:
            return
        # exc_info 要给异常本体：done-callback 里没有「正在处理」的异常，True 会取到 (None,None,None)
        logger.error("CRON_JOB_EXCEPTION skill=%s 定时任务协程抛出，已取回落账：%r",
                     skill_id, exc, exc_info=exc)
        self._log_execution(skill_id, self._job_failure_entry(
            "定时任务协程抛出 %s: %s" % (type(exc).__name__, exc)))
    
    def _job_failure_entry(self, reason: str) -> dict:
        return {"ok": False, "status": "failed", "reason": reason, "http_status": 0,
                "condition_result": False, "action_result": "",
                "executed_at": datetime.now().isoformat()}
    
    def get_execution_log(self, skill_id: str | None = None, limit: int = 20) -> list[dict]:
        """获取执行日志。"""
        if skill_id:
            entries = [e for e in self._execution_log if e.get("skill_id") == skill_id]
        else:
            entries = self._execution_log
        return entries[-limit:]

    def get_task_health(self) -> list[dict]:
        """所有任务的健康度汇总（从执行日志统计，不依赖调度器注册）。"""
        # 收集所有出现过的 skill_id（scheduled_jobs + 执行日志）
        all_skill_ids = set(self._scheduled_jobs.keys())
        for entry in self._execution_log:
            sid = entry.get("skill_id")
            if sid:
                all_skill_ids.add(sid)

        health = []
        for skill_id in sorted(all_skill_ids):
            recent = [e for e in self._execution_log if e.get("skill_id") == skill_id]
            last = recent[-1] if recent else None
            total = len(recent)
            failures = sum(1 for e in recent if e.get("status") == "failed")
            success_rate = round((total - failures) / total * 100, 1) if total > 0 else None
            health.append({
                "skill_id": skill_id,
                "job_id": self._scheduled_jobs.get(skill_id),
                "total_runs": total,
                "consecutive_failures": self._failure_counts.get(skill_id, 0),
                "success_rate": success_rate,
                "last_status": last.get("status") if last else "never_run",
                "last_run": last.get("executed_at") if last else None,
                "last_reason": last.get("reason") if last else None,
            })
        return health
    
    # ── 调度器集成 ──────────────────────────────────────
    
    def schedule_task(self, skill: dict) -> bool:
        """把一个 cron_task 技能添加到调度器。"""
        if not self.scheduler:
            logger.warning("scheduler not initialized, cannot schedule task: %s", skill.get("id"))
            return False
        
        skill_id = skill.get("id", "")
        trigger = skill.get("trigger", {})
        cron_expr = trigger.get("cron", "")
        
        if not cron_expr:
            logger.warning("task %s has no cron expression, skipping", skill_id)
            return False
        
        # 如果已经调度过，先移除旧的
        if skill_id in self._scheduled_jobs:
            self.unschedule_task(skill_id)
        
        # 解析 cron 表达式（简单版：分 时 日 月 周）
        parts = cron_expr.split()
        if len(parts) != 5:
            logger.warning("invalid cron expression for %s: %s", skill_id, cron_expr)
            return False
        
        minute, hour, day, month, day_of_week = parts
        
        # 创建 job
        async def job_func():
            await self.execute_task(skill)
        
        # R2-02 fix: 捕获主事件循环（不能在线程池里调 get_event_loop）
        try:
            _main_loop = asyncio.get_running_loop()
        except RuntimeError:
            # 非事件循环线程调进来：原写法直接抛出去被上层咽掉，cron job 悄悄没登记
            logger.error("CRON_SCHEDULE_NO_LOOP skill=%s schedule_task 不在事件循环线程里，cron 未登记",
                         skill_id)
            return False
        def sync_job():
            try:
                future = asyncio.run_coroutine_threadsafe(job_func(), _main_loop)
            except Exception as e:
                logger.error("CRON_DISPATCH_ERROR skill=%s 派发进事件循环当场失败，本轮任务没跑：%r",
                             skill_id, e, exc_info=True)
                return
            future.add_done_callback(lambda f: self._log_job_outcome(skill_id, f))
        
        try:
            job = self.scheduler.add_job(
                sync_job,
                "cron",
                id=f"cron_task_{skill_id}",
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=day_of_week,
                replace_existing=True,
            )
            self._scheduled_jobs[skill_id] = job.id
            logger.info("cron task scheduled: %s (cron=%s)", skill_id, cron_expr)
            return True
        except Exception as e:
            logger.error("failed to schedule cron task %s: %s", skill_id, e)
            return False
    
    def unschedule_task(self, skill_id: str) -> bool:
        """从调度器移除一个 cron_task 技能。"""
        if not self.scheduler:
            return False
        
        job_id = self._scheduled_jobs.get(skill_id)
        if not job_id:
            return False
        
        try:
            self.scheduler.remove_job(job_id)
            del self._scheduled_jobs[skill_id]
            logger.info("cron task unscheduled: %s", skill_id)
            return True
        except Exception as e:
            logger.warning("failed to unschedule cron task %s: %s", skill_id, e)
            return False
    
    def init_from_store(self, skill_store):
        """从技能存储初始化所有启用的 cron_task 技能。"""
        if not skill_store:
            return
        
        count = 0
        for skill in skill_store._index.values():
            brain = skill.get("brain", {})
            # S-14：schema.py 自陈 brain.type / brain.engine 两种写法都算 cron_task，
            # creator.py 造的 http_condition 型只带 engine ⇒ 只认 type 会整类不排程
            is_cron_task = (brain.get("type") == "cron_task"
                            or brain.get("engine") == "cron_task")
            if is_cron_task and skill.get("status") == "enabled":
                if self.schedule_task(skill):
                    count += 1
        
        logger.info("cron tasks initialized: %d", count)
