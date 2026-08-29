"""模型调用失败的分类与重试判定。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ModelFailure:
    """描述一次模型调用失败是否允许自动重试。"""

    retryable: bool
    category: str
    message: str


class ModelFailureClassifier(Protocol):
    """为 Harness 提供可替换的模型错误分类策略。"""

    def classify(self, error: Exception) -> ModelFailure:
        """根据异常类型返回重试决策。"""


class DefaultModelFailureClassifier:
    """仅允许临时传输故障和空结构化响应进入自动重试路径。"""

    def classify(self, error: Exception) -> ModelFailure:
        """将常见异常归为临时故障或不可恢复故障。"""
        error_text = str(error)[:4_000]

        # 权限错误继承自 OSError，必须在通用 OSError 判断之前拦截。
        if isinstance(error, PermissionError):
            return ModelFailure(
                retryable=False,
                category="authorization_error",
                message=error_text,
            )

        # 请求超时、网络连接失败和底层 I/O 故障通常可通过退避恢复。
        if isinstance(error, (TimeoutError, ConnectionError, OSError)):
            return ModelFailure(
                retryable=True,
                category="transient_transport_error",
                message=error_text,
            )

        # 兼容端点偶发返回空 parsed 字段；不涉及工具执行，可安全重试一次。
        if error_text == "structured action response did not contain a parsed action":
            return ModelFailure(
                retryable=True,
                category="empty_structured_output",
                message=error_text,
            )

        # 参数、结构化输出和程序断言错误不应重复请求模型。
        if isinstance(error, (ValueError, TypeError, AssertionError)):
            return ModelFailure(
                retryable=False,
                category="invalid_model_response",
                message=error_text,
            )

        # 未识别异常默认不重试，避免隐藏程序错误或扩大外部调用。
        return ModelFailure(
            retryable=False,
            category="unclassified_model_error",
            message=error_text,
        )
