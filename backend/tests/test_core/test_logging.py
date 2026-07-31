"""Tests for structured logging configuration (backend/app/core/logging.py).

See docs/features/platform/logging.md for the full specification.
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from app.config import Settings
from app.core.logging import (
    _THIRD_PARTY_LOGGERS,
    configure_cli_logging,
    configure_logging,
    resolve_log_format,
)


def _settings(**overrides) -> Settings:
    """Build a Settings instance for logging tests, bypassing env/file
    sources entirely so tests are hermetic."""
    defaults = {
        "jwt_secret_key": "a" * 32,
        "app_name": "sentinel-test",
        "log_level": "INFO",
        "log_format": "json",
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


@pytest.fixture(autouse=True)
def _reset_logging_state():
    """Save/restore global logging state around every test.

    `configure_logging`/`configure_cli_logging` mutate the root logger
    and third-party logger objects, and reconfigure structlog globally.
    Without this fixture, tests would leak state into each other and
    into the rest of the test suite (which relies on `caplog` and the
    module-level `configure_logging(settings)` call already performed
    at `app.main` import time in conftest.py).
    """
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level

    original_third_party = {
        name: (logging.getLogger(name).level, logging.getLogger(name).propagate)
        for name in _THIRD_PARTY_LOGGERS
    }

    yield

    root_logger.handlers.clear()
    root_logger.handlers.extend(original_handlers)
    root_logger.setLevel(original_level)

    for name, (level, propagate) in original_third_party.items():
        logging.getLogger(name).setLevel(level)
        logging.getLogger(name).propagate = propagate


class _FakeStream(io.StringIO):
    """A StringIO that can simulate being a TTY or not."""

    def __init__(self, *, isatty: bool = False) -> None:
        super().__init__()
        self._isatty = isatty

    def isatty(self) -> bool:
        return self._isatty


@pytest.mark.unit
class TestResolveLogFormat:
    """Pure function: auto-detection and explicit override behavior."""

    def test_auto_resolves_to_console_when_tty(self):
        stream = _FakeStream(isatty=True)
        assert resolve_log_format("auto", stream=stream) == "console"

    def test_auto_resolves_to_json_when_not_tty(self):
        stream = _FakeStream(isatty=False)
        assert resolve_log_format("auto", stream=stream) == "json"

    def test_explicit_json_overrides_tty_stream(self):
        stream = _FakeStream(isatty=True)
        assert resolve_log_format("json", stream=stream) == "json"

    def test_explicit_console_overrides_non_tty_stream(self):
        stream = _FakeStream(isatty=False)
        assert resolve_log_format("console", stream=stream) == "console"


@pytest.mark.unit
class TestConfigureLoggingJsonSchema:
    """JSON renderer output matches the Standard Log Record Schema."""

    def test_json_record_has_required_fields(self):
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

    def test_level_values_are_lowercase(self):
        stream = _FakeStream(isatty=False)
        settings = _settings(log_level="DEBUG")
        configure_logging(settings, stream=stream)

        import structlog

        log = structlog.get_logger("app.test.level")
        log.warning("warn_event")

        record = json.loads(stream.getvalue().strip())
        assert record["level"] == "warning"

    def test_correlation_fields_omitted_when_not_bound(self):
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

    def test_correlation_field_present_when_bound(self):
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

    def test_exception_field_rendered_on_exception_log(self):
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

    def test_console_output_is_not_json(self):
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

    def test_debug_below_configured_level_is_suppressed(self):
        stream = _FakeStream(isatty=False)
        settings = _settings(log_level="INFO")
        configure_logging(settings, stream=stream)

        import structlog

        log = structlog.get_logger("app.test.suppressed")
        log.debug("should_not_appear")

        assert stream.getvalue() == ""

    def test_level_at_or_above_configured_level_is_emitted(self):
        stream = _FakeStream(isatty=False)
        settings = _settings(log_level="WARNING")
        configure_logging(settings, stream=stream)

        import structlog

        log = structlog.get_logger("app.test.emitted")
        log.warning("should_appear")

        assert "should_appear" in stream.getvalue()

    def test_third_party_loggers_deferred_to_root(self):
        stream = _FakeStream(isatty=False)
        settings = _settings(log_level="ERROR")
        configure_logging(settings, stream=stream)

        for name in _THIRD_PARTY_LOGGERS:
            logger = logging.getLogger(name)
            assert logger.level == logging.NOTSET
            assert logger.propagate is True

    def test_sqlalchemy_parent_logger_does_not_cap_engine_level(self):
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

    def test_third_party_logger_captured_in_same_pipeline(self):
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

    def test_second_call_replaces_handlers(self):
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

    def test_routes_to_stderr_not_stdout(self, capsys):
        configure_cli_logging()

        logging.getLogger("app.test.cli").warning("cli_warning")

        captured = capsys.readouterr()
        assert captured.out == ""
        assert "cli_warning" in captured.err

    def test_info_below_warning_is_suppressed(self, capsys):
        configure_cli_logging()

        logging.getLogger("app.test.cli.info").info("cli_info_should_not_appear")

        captured = capsys.readouterr()
        assert captured.err == ""

    def test_output_is_plain_text_not_json(self, capsys):
        configure_cli_logging()

        logging.getLogger("app.test.cli.plain").warning("cli_plain_event")

        captured = capsys.readouterr()
        with pytest.raises(json.JSONDecodeError):
            json.loads(captured.err.strip())
        assert "cli_plain_event" in captured.err
