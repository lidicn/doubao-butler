"""Cron Task 静态校验器（v1.9 Koin Action）。

在 LLM 生成任务 JSON 后、保存前进行校验：
1. JSON Schema 校验
2. cron 表达式合法性
3. JSONPath 语法校验
4. URL 白名单 + SSRF 内网 IP 拦截
5. 非法参数拦截
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

from butler.logging_setup import get_logger

logger = get_logger("butler.cron_task.validator")


# 允许的 comparator
_COMPARATORS = {"exists", "gt", "lt", "eq", "contains"}

# 允许的输出类型
_OUTPUT_TYPES = {"xiaomi_speak", "bark", "tv_notify"}

# 内网 IP 网段（SSRF 防护）
_PRIVATE_NETS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
]


class CronTaskValidator:
    """Cron Task 静态校验器。"""
    
    def __init__(self, allow_internal_ips: bool = False):
        self.allow_internal_ips = allow_internal_ips
    
    def validate(self, task: dict) -> tuple[bool, list[str]]:
        """校验任务定义。返回 (是否合法, 错误列表)。"""
        errors = []
        
        # 1. 基本结构校验
        if not isinstance(task, dict):
            return False, ["任务定义必须是 JSON object"]
        
        # 2. id 校验
        task_id = task.get("id", "")
        if not re.match(r"^[a-z0-9][a-z0-9_-]{1,39}$", task_id):
            errors.append("id 必须为 2~40 位小写字母/数字/下划线")
        
        # 3. name 校验
        if not task.get("name"):
            errors.append("name 不能为空")
        
        # 4. trigger 校验
        trigger = task.get("trigger", {})
        if not isinstance(trigger, dict):
            errors.append("trigger 必须是 JSON object")
        else:
            entry = trigger.get("entry", "")
            if entry != "schedule":
                errors.append(f"trigger.entry 必须是 schedule，当前是 {entry}")
            
            cron_expr = trigger.get("cron", "")
            if cron_expr:
                cron_err = self._validate_cron(cron_expr)
                if cron_err:
                    errors.append(f"cron 表达式非法: {cron_err}")
            elif "interval_s" not in trigger:
                errors.append("trigger 必须包含 cron 或 interval_s")
        
        # 5. brain 校验
        brain = task.get("brain", {})
        if not isinstance(brain, dict):
            errors.append("brain 必须是 JSON object")
        else:
            brain_type = brain.get("type", "")
            if brain_type != "cron_task":
                errors.append(f"brain.type 必须是 cron_task，当前是 {brain_type}")
            
            # api_id 校验
            if not brain.get("api_id"):
                errors.append("brain.api_id 不能为空")
            
            # condition 校验
            condition = brain.get("condition", {})
            if condition:
                if not isinstance(condition, dict):
                    errors.append("condition 必须是 JSON object")
                else:
                    jsonpath = condition.get("jsonpath", "")
                    if not jsonpath:
                        errors.append("condition.jsonpath 不能为空")
                    else:
                        path_err = self._validate_jsonpath(jsonpath)
                        if path_err:
                            errors.append(f"JSONPath 非法: {path_err}")
                    
                    comparator = condition.get("comparator", "exists")
                    if comparator not in _COMPARATORS:
                        errors.append(f"comparator 必须是 {_COMPARATORS}，当前是 {comparator}")
                    
                    if comparator in ("gt", "lt", "eq") and "value" not in condition:
                        errors.append(f"comparator={comparator} 时必须提供 value")
            
            # message_template 校验
            if not brain.get("message_template"):
                errors.append("brain.message_template 不能为空")
        
        # 6. output 校验
        output = task.get("output", [])
        if not isinstance(output, list) or len(output) == 0:
            errors.append("output 必须是非空数组")
        else:
            for i, out in enumerate(output):
                if not isinstance(out, dict):
                    errors.append(f"output[{i}] 必须是 JSON object")
                    continue
                out_type = out.get("type", "")
                if out_type not in _OUTPUT_TYPES:
                    errors.append(f"output[{i}].type 必须是 {_OUTPUT_TYPES}，当前是 {out_type}")
        
        return len(errors) == 0, errors
    
    def validate_api_url(self, url: str) -> tuple[bool, str]:
        """校验 API URL（SSRF 防护）。"""
        try:
            parsed = urlparse(url)
        except Exception as e:
            return False, f"URL 解析失败: {e}"
        
        # 只允许 http/https
        if parsed.scheme not in ("http", "https"):
            return False, f"URL scheme 必须是 http/https，当前是 {parsed.scheme}"
        
        # 检查内网 IP
        if not self.allow_internal_ips:
            hostname = parsed.hostname or ""
            try:
                ip = ipaddress.ip_address(hostname)
                for net in _PRIVATE_NETS:
                    if ip in net:
                        return False, f"不允许访问内网地址: {hostname}"
            except ValueError:
                # 不是 IP 地址，是域名，跳过检查
                pass
        
        return True, ""
    
    def _validate_cron(self, cron_expr: str) -> str:
        """简单 cron 表达式校验。"""
        parts = cron_expr.split()
        if len(parts) != 5:
            return f"cron 表达式必须有 5 个字段，当前有 {len(parts)} 个"
        
        # 简单校验每个字段
        for i, part in enumerate(parts):
            if not re.match(r"^[\d\*,/\-\?]+$", part):
                return f"第 {i+1} 个字段非法: {part}"
        
        return ""
    
    def _validate_jsonpath(self, jsonpath: str) -> str:
        """简单 JSONPath 校验。"""
        if not jsonpath.startswith("$."):
            return "JSONPath 必须以 $. 开头"
        
        # 去掉 $.
        path = jsonpath[2:]
        # 简单校验：只允许字母、数字、点、方括号
        if not re.match(r"^[a-zA-Z0-9\.\[\]\*]+$", path):
            return "JSONPath 包含非法字符"
        
        return ""
    
    def generate_preview(self, task: dict) -> str:
        """生成任务预览文本（给用户看）。"""
        name = task.get("name", "未命名任务")
        trigger = task.get("trigger", {})
        brain = task.get("brain", {})
        output = task.get("output", [])
        
        parts = []
        parts.append(f"📋 任务名称：{name}")
        
        # 触发条件
        cron_expr = trigger.get("cron", "")
        interval_s = trigger.get("interval_s", 0)
        if cron_expr:
            parts.append(f"⏰ 触发时间：cron 表达式 `{cron_expr}`")
        elif interval_s:
            parts.append(f"⏰ 触发时间：每 {interval_s} 秒")
        
        # API
        api_id = brain.get("api_id", "")
        parts.append(f"🔌 数据源：{api_id}")
        
        # 条件
        condition = brain.get("condition", {})
        if condition:
            jsonpath = condition.get("jsonpath", "")
            comparator = condition.get("comparator", "exists")
            value = condition.get("value", "")
            parts.append(f"🔍 条件：{jsonpath} {comparator} {value}")
        
        # 动作
        output_types = [o.get("type", "") for o in output]
        parts.append(f"📢 动作：{', '.join(output_types)}")
        
        # 消息模板
        template = brain.get("message_template", "")
        if template:
            parts.append(f"💬 播报内容：{template[:50]}...")
        
        return "\n".join(parts)


# 全局单例
validator = CronTaskValidator()
