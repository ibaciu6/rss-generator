from __future__ import annotations

import logging

import structlog


def configure_logging(level: int = logging.INFO) -> None:
    """
    Configure structured logging for the application.
    """
    logging.basicConfig(
        level=level,
        format="%(message)s",
    )

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """
    Get a structured logger instance.
    """
    return structlog.get_logger(name)


