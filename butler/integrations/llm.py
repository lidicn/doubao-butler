"""LLM 客户端：支持双后端（new-api / doubao2api），均为 OpenAI 兼容。

- new-api（默认）：deepseek 原生 function calling，可靠，工具调用首选
- doubao2api：推断式 function calling（服务端合成 tool_calls），出现在豆包app，
  适合需要对话展示的场景；参数提取可靠性低于 new-api，需配合 doubao2api 侧优化

- chat()：简单对话（无工具）
- chat_with_tools()：可观察 ReAct 工具循环（Thought→Action→Observation→Adjust），
  支持轨迹落库、步数/时间预算、逃生回调、结构化错误码解析。
多模态（vision/image/music）不走本客户端，见 integrations/doubao.py。
"""
from __future__ import annotations

import json
import asyncio
import random
import time
from typing import Any, Callable

import httpx

from butler.config import Settings
from butler.logging_setup import get_logger

logger = get_logger("butler.llm")

# 可退避重试的状态码：429 限流 + 5xx 瞬态故障
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

# 已知工具错误码（用于结构化解析与轨迹标记）
_KNOWN_ERROR_CODES = frozenset({
    "invalid_parameter", "operation_timeout", "session_required",
    "app_not_running", "app_not_responding", "wrong_foreground",
    "tv_unreachable", "mytv_api_error", "not_implemented",
    "network_error", "empty_movie_name",
})


class LLMClient:
    def __init__(self, settings: Settings):
        self.s = settings

    def _backoff(self, attempt: int) -> float:
        """指数退避 + 抖动：base * 2^attempt * U(0.5,1.5)。"""
        base = max(0.01, self.s.llm_retry_backoff)
        return base * (2 ** attempt) * random.uniform(0.5, 1.5)

    def _endpoint(self, backend: str) -> tuple[str, str, str]:
        """返回 (url, api_key, model)。"""
        if backend == "doubao2api":
            return (
                self.s.doubao_api_url,
                self.s.doubao_api_key,
                self.s.doubao_model,
            )
        return (
            f"{self.s.new_api_url.rstrip('/')}/chat/completions",
            self.s.new_api_key,
            self.s.new_api_model,
        )

    async def _raw(self, messages, *, tools=None, temperature: float = 0.85,
                   max_tokens: int = 2000, backend: str = "new_api"):
        url, api_key, model = self._endpoint(backend)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        t0 = time.time()
        max_retries = self.s.llm_retry_max
        attempt = 0
        while True:
            try:
                async with httpx.AsyncClient(timeout=self.s.llm_timeout) as c:
                    r = await c.post(url, headers=headers, json=payload)
                if r.status_code in _RETRYABLE_STATUSES and attempt < max_retries:
                    wait = self._backoff(attempt)
                    logger.warning("%s %s status=%d attempt=%d/%d retry in %.1fs",
                                   backend, model, r.status_code, attempt + 1, max_retries, wait)
                    await asyncio.sleep(wait)
                    attempt += 1
                    continue
                r.raise_for_status()
                data = r.json()
                logger.info("%s call %dms", backend, int((time.time() - t0) * 1000))
                return data
            except (httpx.TransportError, httpx.TimeoutException) as e:
                # 网络/连接瞬态故障同样退避（防御：不让 ReAct 闭环因一次抖动断开）
                if attempt < max_retries:
                    wait = self._backoff(attempt)
                    logger.warning("%s %s transport error %r attempt=%d/%d retry in %.1fs",
                                   backend, model, e, attempt + 1, max_retries, wait)
                    await asyncio.sleep(wait)
                    attempt += 1
                    continue
                raise

    async def chat(self, system: str, messages: list[dict], *, max_tokens: int = 400,
                   temperature: float = 0.9, backend: str = "new_api"):
        """简单对话（无工具）。返回 (回复文本, 耗时ms)。"""
        full = [{"role": "system", "content": system}] + list(messages)
        data = await self._raw(full, temperature=temperature, max_tokens=max_tokens, backend=backend)
        text = data["choices"][0]["message"]["content"].strip()
        return text, 0

    async def chat_with_tools(
        self,
        system: str,
        messages: list[dict],
        tool_schemas: list[dict],
        tool_executor,
        *,
        max_iter: int = 8,
        time_budget: float = 60.0,
        temperature: float = 0.3,
        max_tokens: int = 2000,
        backend: str = "new_api",
        tracer=None,
        escape_fn: Callable[[str], str] | None = None,
        observe_fn: Callable[[str, dict, str], str | None] | None = None,
        tool_sleep: float = 1.0,
    ) -> str:
        """可观察 ReAct 工具循环：返回最终回复文本。

        tool_executor(name: str, args: dict) -> str
        tracer: AgentTracer 实例（可选），记录 thought/action/observation/final
        escape_fn(reason) -> str：超时/超步数时的逃生回调，返回最终回复
        observe_fn(tool, args, result) -> str|None：工具调用后的观察器，
            返回额外验证文本（如前台包名校验），None 表示无需观察
        tool_sleep：每次工具调用后的限流等待（秒），防御免费模型 429

        backend="doubao2api" 时走推断式 function calling（出现在豆包app），
        但参数提取可靠性低于 new-api，建议用于低风险场景或配合 doubao2api 侧优化。
        """
        full = [{"role": "system", "content": system}] + list(messages)
        t_start = time.time()

        def _timeout() -> bool:
            return (time.time() - t_start) > time_budget

        def _escape(reason: str) -> str:
            if tracer:
                tracer.escape(reason)
            if escape_fn:
                try:
                    return escape_fn(reason)
                except Exception as e:
                    logger.warning("escape_fn failed: %s", e)
            return f"（{reason}，已停止）"

        for i in range(max_iter):
            # 时间预算检查（在 LLM 调用前）
            if _timeout():
                reason = f"超时（>{time_budget:.0f}s）"
                logger.warning("chat_with_tools %s", reason)
                if tracer:
                    tracer.finish("timeout", reason)
                return _escape(reason)

            data = await self._raw(full, tools=tool_schemas, temperature=temperature,
                                   max_tokens=max_tokens, backend=backend)
            if tracer:
                tracer.llm_calls += 1
            msg = data["choices"][0]["message"]
            has_tc = bool(msg.get("tool_calls"))
            content = (msg.get("content") or "").strip()
            logger.info("chat_with_tools iter%d/%d backend=%s tool_calls=%s content=%s",
                        i+1, max_iter, backend, has_tc, content[:80])

            if not has_tc:
                if not content:
                    logger.warning("llm empty content (reasoning model? max_tokens=%d)", max_tokens)
                if tracer:
                    tracer.thought(content)
                    tracer.final(content)
                    tracer.finish("ok")
                return content

            # Thought：LLM 的中间推理文本
            if content and tracer:
                tracer.thought(content)

            # 把 assistant 消息（含 tool_calls）原样回填
            asst = {"role": "assistant", "content": content}
            tcs = []
            for tc in msg["tool_calls"]:
                tcs.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]},
                })
            asst["tool_calls"] = tcs
            full.append(asst)

            for tc in msg["tool_calls"]:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except Exception:
                    args = {}
                # doubao2api 推断式 tool_calls 可能 arguments 为空，记录日志便于排查
                if backend == "doubao2api" and not args:
                    logger.warning("doubao2api tool_calls empty args: %s", name)

                t_tool = time.time()
                result = await tool_executor(name, args)
                cost_ms = int((time.time() - t_tool) * 1000)
                logger.info("tool executed: %s(%s) -> %s",
                            name, json.dumps(args, ensure_ascii=False)[:120], str(result)[:120])

                # 结构化结果解析：提取 error_code
                error_code = _extract_error_code(result)
                if tracer:
                    tracer.action(name, args, cost_ms)
                    tracer.observation(name, result, error_code, cost_ms)

                # 观察器：工具调用后验证（如前台包名/频道）
                obs_extra = None
                if observe_fn:
                    try:
                        obs = observe_fn(name, args, str(result))
                        # 支持 async observe_fn（需要调 HTTP 的场景）
                        if asyncio.iscoroutine(obs):
                            obs_extra = await obs
                        else:
                            obs_extra = obs
                    except Exception as e:
                        logger.warning("observe_fn failed for %s: %s", name, e)
                tool_content = str(result)[:4000]
                if obs_extra:
                    tool_content += f"\n\n【观察验证】{obs_extra}"
                    if tracer:
                        tracer.observation(f"{name}:observe", obs_extra, "", 0)

                full.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_content,
                })
                await asyncio.sleep(tool_sleep)  # 避免免费模型 429 限流

        # 超出步数上限
        reason = f"超出步数上限（{max_iter}步）"
        logger.warning("chat_with_tools %s", reason)
        if tracer:
            tracer.finish("max_iter", reason)
        return _escape(reason)


def _extract_error_code(result: Any) -> str:
    """从工具执行结果中提取结构化错误码。

    优先级：1) dict 含 error_code 字段；2) JSON 字符串含 error_code；
    3) 字符串中匹配已知错误码关键词。无错误返回空串。
    """
    if isinstance(result, dict):
        ec = result.get("error_code") or result.get("error") or ""
        if ec and ec in _KNOWN_ERROR_CODES:
            return str(ec)
        if not result.get("ok", True):
            return str(ec) or "unknown_error"
        return ""
    if isinstance(result, str):
        s = result.strip()
        # 尝试 JSON 解析
        if s.startswith("{"):
            try:
                obj = json.loads(s)
                if isinstance(obj, dict):
                    ec = obj.get("error_code") or ""
                    if ec:
                        return str(ec)
                    if obj.get("ok") is False:
                        return str(obj.get("error") or "unknown_error")
            except Exception:
                pass
        # 字符串匹配已知错误码
        for code in _KNOWN_ERROR_CODES:
            if code in s:
                return code
    return ""
