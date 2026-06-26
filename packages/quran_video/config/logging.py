from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

SECRET_ENV_NAMES = {
    "QF_CLIENT_SECRET",
    "YOUTUBE_CLIENT_SECRET",
    "YOUTUBE_REFRESH_TOKEN",
    "TELEGRAM_BOT_TOKEN",
}

TOKEN_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"Basic\s+[A-Za-z0-9+/=-]+", re.IGNORECASE),
    re.compile(r"(bot)[0-9]{4,}:[A-Za-z0-9_-]+"),
    re.compile(r"(refresh_token=)[^&\s]+", re.IGNORECASE),
]


def secret_values_from_env() -> list[str]:
    values: list[str] = []
    for name in SECRET_ENV_NAMES:
        value = os.getenv(name)
        if value:
            values.append(value)
    return values


def redact_secret_text(message: str, extra_values: list[str] | None = None) -> str:
    redacted = message
    for value in [*secret_values_from_env(), *(extra_values or [])]:
        if value:
            redacted = redacted.replace(value, "[REDACTED]")
    for pattern in TOKEN_PATTERNS:
        redacted = pattern.sub(
            lambda match: match.group(1) + "[REDACTED]" if match.groups() else "[REDACTED]",
            redacted,
        )
    return redacted


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "component": getattr(record, "component", record.name),
            "job_id": getattr(record, "job_id", None),
            "phase": getattr(record, "phase", None),
            "progress": getattr(record, "progress", None),
            "event": getattr(record, "event", record.getMessage()),
            "elapsed": getattr(record, "elapsed", None),
            "message": redact_secret_text(record.getMessage()),
        }
        if record.exc_info:
            payload["exception"] = redact_secret_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
