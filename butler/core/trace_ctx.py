"""全链路 trace 上下文（v2.6#2）。

叶子模块：只依赖 contextvars，不回指 agent/dialog/logging，任何层都能 import。
butler.core.agent 里的 _current_trace_id 就是这里那个对象，两处不是两份变量。
"""
import contextvars

current_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_trace_id", default="")
