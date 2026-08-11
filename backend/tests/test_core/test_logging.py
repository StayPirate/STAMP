"""Tests for structured logging configuration (backend/app/core/logging.py).

See docs/features/platform/logging.md for the full specification.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest
import structlog

from app.config import Settings
from app.core.logging import (
    _THIRD_PARTY_LOGGERS,
    configure_cli_logging,
    configure_logging,
    resolve_log_format,
)


def _settings(**overrides: str) -> Settings:
    """Build a Settings instance for logging tests, bypassing env/file
    sources entirely so tests are hermetic."""
    defaults = {
        "jwt_secret_key": "a" * 32,
        "app_name": "sentinel-test",
        "log_level": "INFO",
        "log_format": "json",
    }
    defaults.update(overrides)
    # pydantic-settings' BaseSettings.__init__ exposes many strictly-typed
    # special init kwargs (_cli_settings_source, etc.) beyond the model's
    # own fields, so a **dict[str, str] spread cannot be matched precisely
    # against every possible parameter slot.
    return Settings(_env_file=None, **defaults)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _reset_logging_state() -> Iterator[None]:
    """Save/restore global logging state around every test.

    `configure_logging`/`configure_cli_logging` mutate the root logger
    and third-party logger objects, and reconfigure structlog globally.
    Without this fixture, tests would leak state into each other and
    into the rest of the test suite (which relies on `caplog` and the
    module-level `configure_logging(settings)` call already performed
    at `app.main` import time in conftest.py).

    Restores, in addition to root logger handlers/level: third-party
    logger **handlers** (not just level/propagate — `configure_logging`
    clears them on every call) and the global structlog configuration
    (`structlog.configure(...)` mutates process-wide state that
    `structlog.get_config()`/`structlog.configure(**config)` can
    snapshot and replay exactly).
    """
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level

    original_third_party = {
        name: (
            logging.getLogger(name).level,
            logging.getLogger(name).propagate,
            list(logging.getLogger(name).handlers),
        )
        for name in _THIRD_PARTY_LOGGERS
    }

    original_structlog_config = structlog.get_config()

    yield

    root_logger.handlers.clear()
    root_logger.handlers.extend(original_handlers)
    root_logger.setLevel(original_level)

    for name, (level, propagate, handlers) in original_third_party.items():
        logger = logging.getLogger(name)
        logger.setLevel(level)
        logger.propagate = propagate
        logger.handlers.clear()
        logger.handlers.extend(handlers)

    structlog.configure(**original_structlog_config)


class _FakeStream(io.StringIO):
    """A StringIO that can simulate being a TTY or not."""

    def __init__(self, *, isatty: bool = False) -> None:
        super().__init__()
        self._isatty = isatty

    def isatty(self) -> bool:
        return self._isatty


@pytest.mark.unit
class TestResetLoggingStateFixture:
    """Regression coverage for the module's own `_reset_logging_state`
    autouse fixture. Exercises the fixture's generator function
    directly (bypassing pytest's fixture-resolution machinery) so the
    save/mutate/restore sequence can be asserted within a single test,
    proving the two behaviors this fixture is responsible for beyond
    root logger handlers/level: restoring third-party logger handlers
    and the global structlog configuration, both of which
    `configure_logging()` mutates on every call."""

    def test_restores_third_party_logger_handlers(self) -> None:
        third_party_name = _THIRD_PARTY_LOGGERS[0]
        third_party_logger = logging.getLogger(third_party_name)
        pre_existing_handler = logging.NullHandler()
        third_party_logger.addHandler(pre_existing_handler)
        try:
            fixture_gen = _reset_logging_state._fixture_function()
            next(fixture_gen)  # enter: snapshot taken with the handler present

            configure_logging(_settings(), stream=_FakeStream())
            assert pre_existing_handler not in third_party_logger.handlers

            with pytest.raises(StopIteration):
                next(fixture_gen)  # exit: restore

            assert pre_existing_handler in third_party_logger.handlers
        finally:
            third_party_logger.removeHandler(pre_existing_handler)

    def test_restores_structlog_global_configuration(self) -> None:
        original_processors = structlog.get_config()["processors"]

        fixture_gen = _reset_logging_state._fixture_function()
        next(fixture_gen)  # enter: snapshot taken

        configure_logging(_settings(), stream=_FakeStream())
        assert structlog.get_config()["processors"] != original_processors

        with pytest.raises(StopIteration):
            next(fixture_gen)  # exit: restore

        assert structlog.get_config()["processors"] == original_processors


@pytest.mark.unit
class TestResolveLogFormat:
    """Pure function: auto-detection and explicit override behavior."""

    def test_auto_resolves_to_console_when_tty(self) -> None:
        stream = _FakeStream(isatty=True)
        assert resolve_log_format("auto", stream=stream) == "console"

    def test_auto_resolves_to_json_when_not_tty(self) -> None:
        stream = _FakeStream(isatty=False)
        assert resolve_log_format("auto", stream=stream) == "json"

    def test_explicit_json_overrides_tty_stream(self) -> None:
        stream = _FakeStream(isatty=True)
        assert resolve_log_format("json", stream=stream) == "json"

    def test_explicit_console_overrides_non_tty_stream(self) -> None:
        stream = _FakeStream(isatty=False)
        assert resolve_log_format("console", stream=stream) == "console"


@pytest.mark.unit
class TestConfigureLoggingJsonSchema:
    """JSON renderer output matches the Standard Log Record Schema."""

    def test_json_record_has_required_fields(self) -> None:
        stream = _FakeStream(isatty=False)
        settings = _settings(log_format="json", app_name="sentinel-schema-test")
        configure_logging(settings, stream=stream)

        import structlog

        log = structlog.get_logger("app.test.schema")
        log.info("schema_test_event")

        line = stream.getvalue().strip()
        record = json.loads(line)
        assert record["event"] == "schema_test_event"
        assert record["level"] == "info"
        assert record["logger"] == "app.test.schema"
        assert record["app"] == "sentinel-schema-test"
        assert record["timestamp"].endswith("Z")

    def test_level_values_are_lowercase(self) -> None:
        stream = _FakeStream(isatty=False)
        settings = _settings(log_level="DEBUG")
        configure_logging(settings, stream=stream)

        import structlog

        log = structlog.get_logger("app.test.level")
        log.warning("warn_event")

        record = json.loads(stream.getvalue().strip())
        assert record["level"] == "warning"

    def test_correlation_fields_omitted_when_not_bound(self) -> None:
        stream = _FakeStream(isatty=False)
        settings = _settings()
        configure_logging(settings, stream=stream)

        import structlog

        log = structlog.get_logger("app.test.correlation")
        log.info("no_correlation_event")

        record = json.loads(stream.getvalue().strip())
        assert "request_id" not in record
        assert "celery_task_id" not in record
        assert "fetcher_run_id" not in record

    def test_correlation_field_present_when_bound(self) -> None:
        stream = _FakeStream(isatty=False)
        settings = _settings()
        configure_logging(settings, stream=stream)

        import structlog

        tokens = structlog.contextvars.bind_contextvars(request_id="req-123")
        try:
            log = structlog.get_logger("app.test.bound")
            log.info("bound_event")
        finally:
            structlog.contextvars.reset_contextvars(**tokens)

        record = json.loads(stream.getvalue().strip())
        assert record["request_id"] == "req-123"

    def test_exception_field_rendered_on_exception_log(self) -> None:
        stream = _FakeStream(isatty=False)
        settings = _settings()
        configure_logging(settings, stream=stream)

        import structlog

        log = structlog.get_logger("app.test.exc")
        try:
            raise ValueError("boom")
        except ValueError:
            log.exception("operation_failed")

        record = json.loads(stream.getvalue().strip())
        assert "exception" in record
        assert "ValueError: boom" in record["exception"]
        assert record["level"] == "error"


@pytest.mark.unit
class TestConfigureLoggingConsole:
    """Console renderer produces human-readable, non-JSON output."""

    def test_console_output_is_not_json(self) -> None:
        stream = _FakeStream(isatty=False)
        settings = _settings(log_format="console")
        configure_logging(settings, stream=stream)

        import structlog

        log = structlog.get_logger("app.test.console")
        log.info("console_event")

        line = stream.getvalue().strip()
        with pytest.raises(json.JSONDecodeError):
            json.loads(line)
        assert "console_event" in line


@pytest.mark.unit
class TestConfigureLoggingLevel:
    """LOG_LEVEL controls all loggers uniformly; no per-logger pins."""

    def test_debug_below_configured_level_is_suppressed(self) -> None:
        stream = _FakeStream(isatty=False)
        settings = _settings(log_level="INFO")
        configure_logging(settings, stream=stream)

        import structlog

        log = structlog.get_logger("app.test.suppressed")
        log.debug("should_not_appear")

        assert stream.getvalue() == ""

    def test_level_at_or_above_configured_level_is_emitted(self) -> None:
        stream = _FakeStream(isatty=False)
        settings = _settings(log_level="WARNING")
        configure_logging(settings, stream=stream)

        import structlog

        log = structlog.get_logger("app.test.emitted")
        log.warning("should_appear")

        assert "should_appear" in stream.getvalue()

    def test_third_party_loggers_deferred_to_root(self) -> None:
        stream = _FakeStream(isatty=False)
        settings = _settings(log_level="ERROR")
        configure_logging(settings, stream=stream)

        for name in _THIRD_PARTY_LOGGERS:
            logger = logging.getLogger(name)
            assert logger.level == logging.NOTSET
            assert logger.propagate is True

    def test_sqlalchemy_parent_logger_does_not_cap_engine_level(self) -> None:
        """SQLAlchemy pins the parent "sqlalchemy" logger to WARNING at
        import time; it must also be reset so it doesn't cap the
        effective level of "sqlalchemy.engine" below the configured
        LOG_LEVEL (see docs/features/platform/logging.md, third-party
        logger integration)."""
        stream = _FakeStream(isatty=False)
        settings = _settings(log_level="DEBUG")
        configure_logging(settings, stream=stream)

        assert (
            logging.getLogger("sqlalchemy.engine").getEffectiveLevel() == logging.DEBUG
        )

    def test_third_party_logger_captured_in_same_pipeline(self) -> None:
        stream = _FakeStream(isatty=False)
        settings = _settings(
            log_level="INFO", log_format="json", app_name="sentinel-third-party"
        )
        configure_logging(settings, stream=stream)

        stdlib_logger = logging.getLogger("uvicorn.access")
        stdlib_logger.info("GET / HTTP/1.1 200")

        record = json.loads(stream.getvalue().strip())
        assert record["logger"] == "uvicorn.access"
        assert record["event"] == "GET / HTTP/1.1 200"
        assert record["level"] == "info"
        assert record["app"] == "sentinel-third-party"
        assert record["timestamp"].endswith("Z")


@pytest.mark.unit
class TestConfigureLoggingIdempotency:
    """Calling configure_logging multiple times must not duplicate output."""

    def test_second_call_replaces_handlers(self) -> None:
        stream1 = _FakeStream(isatty=False)
        stream2 = _FakeStream(isatty=False)
        settings = _settings()

        configure_logging(settings, stream=stream1)
        configure_logging(settings, stream=stream2)

        import structlog

        log = structlog.get_logger("app.test.idempotent")
        log.info("only_once")

        assert stream1.getvalue() == ""
        lines = [ln for ln in stream2.getvalue().splitlines() if ln.strip()]
        assert len(lines) == 1


@pytest.mark.unit
class TestConfigureCliLogging:
    """CLI logging: stderr, plain text, WARNING threshold, no correlation."""

    def test_routes_to_stderr_not_stdout(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_cli_logging()

        logging.getLogger("app.test.cli").warning("cli_warning")

        captured = capsys.readouterr()
        assert captured.out == ""
        assert "cli_warning" in captured.err

    def test_info_below_warning_is_suppressed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_cli_logging()

        logging.getLogger("app.test.cli.info").info("cli_info_should_not_appear")

        captured = capsys.readouterr()
        assert captured.err == ""

    def test_output_is_plain_text_not_json(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_cli_logging()

        logging.getLogger("app.test.cli.plain").warning("cli_plain_event")

        captured = capsys.readouterr()
        with pytest.raises(json.JSONDecodeError):
            json.loads(captured.err.strip())
        assert "cli_plain_event" in captured.err
