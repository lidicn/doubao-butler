"""homesdk：ADM 生态共享安全地基。

当前只有一个模块：`consent`（同意判定）。它不是服务、不占端口、不联网——被 import 时使用。
"""

from homesdk.consent import NO, UNKNOWN, YES, allows_execution, classify_answer

__all__ = ["NO", "UNKNOWN", "YES", "allows_execution", "classify_answer"]
__version__ = "0.1.1"
