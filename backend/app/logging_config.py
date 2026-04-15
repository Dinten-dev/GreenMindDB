"""Structured logging configuration for the GreenMind backend.

Usage:
    from app.logging_config import get_logger
    logger = get_logger(__name__)
    logger.info("Sensor data received", extra={"sensor_id": "abc", "count": 42})
"""

import logging
import sys
from datetime import UTC, datetime


class StructuredFormatter(logging.Formatter):
    """JSON-like structured log formatter.

    Produces log lines in key=value format for easy parsing:
        timestamp=2024-01-15T10:30:00Z level=INFO logger=app.routers.ingest msg="Data ingested"
    """

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat()
        level = record.levelname
        logger_name = record.name
        message = record.getMessage()

        parts = [
            f"timestamp={timestamp}",
            f"level={level}",
            f"logger={logger_name}",
            f'msg="{message}"',
        ]

        # Append extra fields (skip standard LogRecord attributes)
        standard_attrs = {
            "name",
            "msg",
            "args",
            "created",
            "relativeCreated",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "pathname",
            "filename",
            "module",
            "levelno",
            "levelname",
            "msecs",
            "processName",
            "process",
            "threadName",
            "thread",
            "taskName",
            "message",
        }
        for key, value in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                parts.append(f"{key}={value}")

        return " ".join(parts)


def setup_logging(log_level: str = "INFO") -> None:
    """Configure structured logging for the entire application.

    Call this once at application startup (in main.py).
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    # Quiet down noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a named logger. Use __name__ as the argument.

    Example:
        logger = get_logger(__name__)
        logger.info("Processing complete", extra={"duration_ms": 42})
    """
    return logging.getLogger(name)
