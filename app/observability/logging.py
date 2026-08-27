"""结构化日志配置。"""

from __future__ import annotations

import logging

import structlog


def configure_logging(*, log_level: str) -> None:
    """按应用配置初始化标准库日志和 Structlog。"""
    # 保留宿主进程已有的日志处理器，避免嵌入式运行时重复配置。
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(message)s",
    )

    structlog.configure(
        processors=[
            # 将请求中绑定的 request_id、路径等上下文字段写入每条日志。
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
