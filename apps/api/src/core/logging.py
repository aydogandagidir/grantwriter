"""Structured JSON logging.

A hand-rolled :class:`logging.Formatter` so we add no new dependency. Emits
one JSON object per record with a stable schema: ``timestamp`` (ISO-8601 UTC),
``level``, ``logger``, ``message``, plus any ``extra=`` fields. ``SecretStr``
values are masked before serialization to prevent leaks via
``logger.info(extra=settings.model_dump())``.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from pydantic import SecretStr

_RESERVED_LOGRECORD_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "asctime",
        "message",
        "taskName",
    }
)


def _safe(value: Any) -> Any:
    """Recursively mask ``SecretStr`` values; return everything else as-is.

    We intentionally do not stringify other types — ``json.dumps`` with
    ``default=str`` handles unknown objects below.
    """

    if isinstance(value, SecretStr):
        return "***"
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_safe(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    """Format ``LogRecord`` instances as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key in _RESERVED_LOGRECORD_KEYS or key.startswith("_"):
                continue
            payload[key] = _safe(value)

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Install a single stdout JSON handler at ``level`` on the root logger.

    Idempotent: existing handlers are removed before installing the new one,
    so calling this twice (e.g. once at app boot, once in tests) is safe.
    """

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
