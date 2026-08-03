"""Celery application factory and bootstrap.

Constructs the single `Celery` application object shared by every
Celery-based runtime process (worker, Beat, IBS RabbitMQ consumer — see
`docs/deployment.md`, Container Images). Validates the mandatory
startup contracts defined in
`docs/features/platform/fetcher-infrastructure.md` (Startup
Validation): UTC timezone and RedBeat distributed lock enabled, both
via `validate_celery_config()`. The no-result-backend contract is
fixed by construction (`result_backend=None` is hard-coded, with no
corresponding `Settings` field to misconfigure) rather than actively
validated.

Also wires:
- Structured logging (`app.core.logging.configure_logging`) into
  Celery's own logging setup via the `setup_logging` signal, so Celery
  does not install its own handlers/levels on top of the
  structlog/stdlib pipeline (see `docs/features/platform/logging.md`,
  Integration with Third-Party Loggers).
- Task correlation binding (`celery_task_id`) via the
  `task_prerun`/`task_postrun` signals (see
  `docs/features/platform/logging.md`, Correlation IDs).

Scope: this module intentionally does NOT define any Celery task,
fetcher registry, Beat schedule, or reconciliation logic — all deferred
to Phase 3 (see issue #27, P1-06).
"""

from __future__ import annotations

from typing import Any

import structlog
from celery import Celery
from celery.signals import setup_logging, task_postrun, task_prerun

from app.config import Settings, settings
from app.core.logging import configure_logging

# Fixed application-level tuning (not environment-configurable — see
# docs/features/platform/fetcher-infrastructure.md, Redbeat Configuration
# and docs/configuration.md, Beat Tick Interval).
_BEAT_MAX_LOOP_INTERVAL = 60
_REDBEAT_LOCK_KEY = "redbeat::lock"
# Derived as `beat_max_loop_interval * 5`, matching redbeat's own default
# derivation — see docs/features/platform/fetcher-infrastructure.md
# (Redbeat Configuration, Derived values).
_REDBEAT_LOCK_TIMEOUT = _BEAT_MAX_LOOP_INTERVAL * 5

# Correlation ContextVar names cleared unconditionally at task_prerun —
# see docs/features/platform/logging.md (Reset requirement).
_CORRELATION_KEYS = ("request_id", "celery_task_id", "fetcher_run_id")


def validate_celery_config(app: Celery) -> None:
    """Validate the mandatory startup contracts on a constructed app.

    Raises `RuntimeError` with a fatal, operator-facing message if
    either the UTC timezone contract or the redbeat distributed lock
    contract is violated. See
    `docs/features/platform/fetcher-infrastructure.md` (Startup
    Validation) for the exact conditions and message text this
    function implements verbatim.
    """
    timezone = app.conf.timezone
    enable_utc = app.conf.enable_utc
    if timezone != "UTC" or enable_utc is not True:
        msg = (
            f"FATAL: Celery timezone must be UTC. Current value: "
            f"timezone={timezone}, enable_utc={enable_utc}. All fetcher "
            f"schedules assume UTC — see docs/conventions.md."
        )
        raise RuntimeError(msg)

    lock_key = app.conf.get("redbeat_lock_key")
    lock_timeout = app.conf.get("redbeat_lock_timeout")
    if not lock_key or not lock_timeout:
        msg = (
            f"FATAL: Redbeat distributed lock must be enabled. Current "
            f"value: redbeat_lock_key={lock_key}, "
            f"redbeat_lock_timeout={lock_timeout}. The lock is required "
            f"for automatic recovery from Redis data loss — see "
            f"docs/features/platform/fetcher-infrastructure.md (Runtime: "
            f"Redis Data Loss)."
        )
        raise RuntimeError(msg)


def create_celery_app(app_settings: Settings) -> Celery:
    """Build and validate the Sentinel Celery application.

    No fetcher task, registry, or Beat schedule is registered here —
    deferred to Phase 3 (`run_fetcher`, `FETCHER_REGISTRY` discovery).
    """
    app = Celery("sentinel")
    app.conf.update(
        broker_url=app_settings.celery_broker_url,
        result_backend=None,
        task_ignore_result=True,
        timezone=app_settings.celery_timezone,
        enable_utc=app_settings.celery_enable_utc,
        worker_hijack_root_logger=False,
        beat_scheduler="redbeat.RedBeatScheduler",
        beat_max_loop_interval=_BEAT_MAX_LOOP_INTERVAL,
        redbeat_lock_key=_REDBEAT_LOCK_KEY,
        redbeat_lock_timeout=_REDBEAT_LOCK_TIMEOUT,
    )
    validate_celery_config(app)
    return app


def _install_structured_logging(**_kwargs: Any) -> None:
    """Replace Celery's own logging setup with Sentinel's pipeline.

    Connecting a receiver to `setup_logging` makes Celery skip its
    entire built-in `setup_logging_subsystem` branch (see
    `celery.app.log.Logging.setup_logging_subsystem` — it only runs
    when there are no receivers). `worker_hijack_root_logger=False`
    alone is insufficient: Celery still reconfigures the root logger's
    *level* even when hijacking is disabled, which would silently
    override `LOG_LEVEL`. See
    docs/features/platform/logging.md (Integration with Third-Party
    Loggers) for the corrected contract this receiver implements.
    """
    configure_logging(settings)


def _bind_task_correlation(task_id: str | None = None, **_kwargs: Any) -> None:
    """Bind `celery_task_id` for the duration of task execution.

    Unconditionally clears all three correlation ContextVars first —
    see docs/features/platform/logging.md (Reset requirement) — so a
    stale value from a previous task cannot leak into this one in
    reused prefork worker processes, even if `task_postrun` was skipped
    for the previous task (hard time limit kill, unhandled exception in
    the signal handler itself).
    """
    structlog.contextvars.unbind_contextvars(*_CORRELATION_KEYS)
    structlog.contextvars.bind_contextvars(celery_task_id=task_id)


def _unbind_task_correlation(**_kwargs: Any) -> None:
    """Reset `celery_task_id` at the end of task execution.

    Defense-in-depth cleanup — the unconditional clear in
    `_bind_task_correlation` is the primary guarantee (see
    docs/features/platform/logging.md, Reset requirement).
    """
    structlog.contextvars.unbind_contextvars(*_CORRELATION_KEYS)


# Connected via explicit `.connect(...)` calls (rather than the
# `@signal.connect(...)` decorator form) because `celery.signals` ships
# no type stubs (see the mypy override for `celery.*` in
# pyproject.toml): the decorator form makes mypy infer these functions
# as untyped, which strict mode rejects. Explicit connection keeps the
# handlers themselves fully typed.
setup_logging.connect(
    _install_structured_logging, dispatch_uid="sentinel.celery_app.setup_logging"
)
task_prerun.connect(
    _bind_task_correlation, dispatch_uid="sentinel.celery_app.task_prerun"
)
task_postrun.connect(
    _unbind_task_correlation, dispatch_uid="sentinel.celery_app.task_postrun"
)


celery_app = create_celery_app(settings)
