"""Structured logging configuration.

Configures `structlog` on top of the stdlib `logging` module so that
Sentinel's own code and third-party libraries (uvicorn, SQLAlchemy,
httpx, Celery) emit through the same pipeline, format, and output
stream. See `docs/features/platform/logging.md` for the full
specification this module implements.

This module governs the long-running runtime processes (API server,
Celery worker, git worker, Beat, IBS consumer). CLI (Click) processes
use `configure_cli_logging()` instead — see its docstring.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, TextIO

import structlog

if TYPE_CHECKING:
    from app.config import Settings

# Third-party loggers captured via the stdlib bridge. Explicitly reset
# to NOTSET + propagate=True so they defer to the root logger's level
# and handler uniformly — no per-logger overrides, no conditional pins.
_THIRD_PARTY_LOGGERS = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "sqlalchemy.engine",
    "httpx",
    "httpcore",
    "celery",
)

# Shared processors applied to every log record regardless of origin:
# both structlog-originated calls (via `structlog.configure`) and
# stdlib-originated records captured by the bridge (via
# `foreign_pre_chain`). NOTE: `structlog.stdlib.filter_by_level` is
# deliberately NOT included here — it assumes a bound structlog logger
# instance and raises AttributeError when run against foreign (stdlib)
# records, where `logger` is None. Level filtering is instead performed
# by the stdlib logger's own `isEnabledFor()` check.
_TIMESTAMPER = structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp")

_SHARED_PROCESSORS: list[structlog.types.Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    _TIMESTAMPER,
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
]


def _bind_app_name(app_name: str) -> structlog.types.Processor:
    """Build a processor that stamps every event with the `app` field."""

    def processor(
        logger: object, method_name: str, event_dict: structlog.types.EventDict
    ) -> structlog.types.EventDict:
        event_dict["app"] = app_name
        return event_dict

    return processor


def resolve_log_format(log_format: str, *, stream: TextIO | None = None) -> str:
    """Resolve the effective log output format.

    `auto` resolves to `console` when the target stream is a TTY and to
    `json` otherwise. Explicit `json` or `console` values are returned
    unchanged. `log_format` is expected to already be validated/
    normalized (lowercase) by `Settings`.
    """
    if log_format != "auto":
        return log_format
    target = stream if stream is not None else sys.stdout
    return "console" if target.isatty() else "json"


def configure_logging(settings: Settings, *, stream: TextIO | None = None) -> None:
    """Configure the structlog + stdlib logging pipeline.

    Idempotent: safe to call multiple times — each call fully replaces
    the previous root logger handler/level and structlog configuration
    rather than layering on top of it (relevant for test invocations
    and any future re-configuration need).

    `stream` defaults to `sys.stdout` and is exposed only for testing
    (capturing output without relying on global stdout redirection).
    """
    target_stream = stream if stream is not None else sys.stdout
    log_format = resolve_log_format(settings.log_format, stream=target_stream)
    level: int = getattr(logging, settings.log_level)

    app_name_processor = _bind_app_name(settings.app_name)
    processors = [*_SHARED_PROCESSORS, app_name_processor]

    structlog.configure(
        processors=[
            *processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: structlog.types.Processor
    if log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=target_stream.isatty())

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=processors,
    )

    handler = logging.StreamHandler(target_stream)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    for name in _THIRD_PARTY_LOGGERS:
        third_party_logger = logging.getLogger(name)
        third_party_logger.handlers.clear()
        third_party_logger.setLevel(logging.NOTSET)
        third_party_logger.propagate = True


def configure_cli_logging() -> None:
    """Configure minimal logging for CLI (Click) processes.

    Per `docs/features/platform/logging.md` (Scope of this pipeline),
    CLI processes do not use the full structlog pipeline. When CLI code
    invokes shared service/utility code that logs via
    `structlog.get_logger()`, this configuration routes that output
    through stdlib logging to stderr, in plain text (not JSON, not
    colorized), at WARNING level or above — so DEBUG/INFO messages from
    service code do not pollute CLI output. stdout remains reserved
    exclusively for the CLI Output Contract.

    Correlation IDs are not bound in CLI processes and are therefore
    omitted from any log records emitted this way.
    """
    processors = [
        *_SHARED_PROCESSORS,
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ]

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        foreign_pre_chain=_SHARED_PROCESSORS,
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.WARNING)
