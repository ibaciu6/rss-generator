from __future__ import annotations

import logging
import re

import structlog

# A config URL may hold a secret the engine only resolves at request time
# (see core/engine.py). The generation log is published as a public CI
# artifact, so mask the value once here rather than at every call site.
_API_KEY_RE = re.compile(r"(api_key=)[^&\s\"']+")


def _redact_api_keys(
    _logger: object, _method_name: str, event_dict: dict[str, object]
) -> dict[str, object]:
    """Replace any ``api_key=...`` value in a string field with a placeholder."""
    for key, value in event_dict.items():
        if isinstance(value, str):
            event_dict[key] = _API_KEY_RE.sub(r"\1[REDACTED]", value)
    return event_dict


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
            _redact_api_keys,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """
    Get a structured logger instance.
    """
    return structlog.get_logger(name)


