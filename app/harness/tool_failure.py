"""工具调用失败的分类与重试判定。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.tools.registry import ToolExecutionError


@dataclass(frozen=True)
class ToolFailure:
    """描述一次工具调用失败是否允许自动重试。"""

    retryable: bool
    category: str
    message: str


class ToolFailureClassifier(Protocol):
    """为 Harness 提供可替换的工具错误分类策略。"""

    def classify(self, error: Exception) -> ToolFailure:
        """根据异常类型返回重试决策。"""


class DefaultToolFailureClassifier:
    """仅允许明确的临时传输故障进入工具重试路径。"""

    def classify(self, error: Exception) -> ToolFailure:
        """将工具执行异常分类为可恢复或不可恢复故障。"""
        error_text = str(error)[:4_000]

        # 权限错误继承自 OSError，必须优先判断。
        if isinstance(error, PermissionError):
            return ToolFailure(
                retryable=False,
                category="authorization_error",
                message=error_text,
            )

        # 注册、参数和调用约束错误不会通过重复执行自动恢复。
        if isinstance(error, (ToolExecutionError, ValueError, TypeError, AssertionError)):
            return ToolFailure(
                retryable=False,
                category="invalid_tool_request",
                message=error_text,
            )

        # 网络、超时和底层 I/O 故障可在预算内进行重试。
        if isinstance(error, (TimeoutError, ConnectionError, OSError)):
            return ToolFailure(
                retryable=True,
                category="transient_transport_error",
                message=error_text,
            )

        # 未识别错误默认不重试，避免放大副作用或掩盖程序缺陷。
        return ToolFailure(
            retryable=False,
            category="unclassified_tool_error",
            message=error_text,
        )
