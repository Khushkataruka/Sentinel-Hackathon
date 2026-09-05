"""Structured logging. Every service calls configure_logging() once at start."""

from __future__ import annotations

import logging
import sys

import structlog
from sentinel.core.config import settings

_configured = False

#: Libraries whose DEBUG output is traffic, not information. asyncio announces
#: its event loop implementation; httpcore narrates each connection in eleven
#: lines; asyncpg and PIL are similar.
NOISY_LOGGERS = (
    "asyncio",
    "httpcore",
    "httpx",
    "hpack",
    "urllib3",
    "asyncpg",
    "PIL",
)


def configure_logging(service: str) -> None:
    global _configured
    if _configured:
        return

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    # Third-party loggers are pinned, not left on the root level. At DEBUG,
    # httpcore traces every TLS handshake and every response header of every
    # catalogue poll -- dozens of lines that bury the one ingest line you
    # turned DEBUG on to read. Our own loggers stay at whatever was asked for.
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service)
    _configured = True


def get_logger(name: str):
    return structlog.get_logger(name)
