"""Tests for the shared CLI root group, bootstrap, and exception mapper.

See docs/features/platform/cli-infrastructure.md (Root Command Group &
Bootstrap, Error Handling & Exit Code Mapping, Signal Handling) for the
authoritative contract exercised here.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from importlib.metadata import version as get_version
from pathlib import Path

import click
import pytest
from click.testing import CliRunner, Result
from redis.exceptions import RedisError
from sqlalchemy.exc import OperationalError

import app.cli.manage_user as manage_user_module
from app.cli import cli, main
from app.cli._runtime import _load_settings, bootstrap
from app.core.exceptions import ServiceError

_BACKEND_DIR = Path(__file__).resolve().parents[2]


def _invoke(args: list[str], input: str | None = None) -> Result:
    """Invoke the raw `cli` group with `standalone_mode=False`, mirroring
    exactly how production's `main()` invokes it — Click's own
    standalone-mode safety nets (e.g. converting `KeyboardInterrupt` to
    `Aborted!`/exit 1) must not mask the behavior under test."""
    return CliRunner().invoke(cli, args, input=input, standalone_mode=False)


# ---------------------------------------------------------------------------
# Eager --help / --version: no bootstrap, no Settings, no DB
# ---------------------------------------------------------------------------


def _forbid_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any call to `bootstrap()` fail the test loudly."""

    def _fail() -> None:
        raise AssertionError("bootstrap() must not run for --help/--version")

    monkeypatch.setattr(manage_user_module, "bootstrap", _fail)


@pytest.mark.unit
def test_root_help_exits_zero_without_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_bootstrap(monkeypatch)
    result = _invoke(["--help"])
    assert result.exit_code == 0
    assert "Sentinel command-line interface" in result.output


@pytest.mark.unit
def test_root_version_exits_zero_without_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_bootstrap(monkeypatch)
    result = _invoke(["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == get_version("sentinel")


@pytest.mark.unit
def test_group_help_exits_zero_without_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_bootstrap(monkeypatch)
    result = _invoke(["manage-user", "--help"])
    assert result.exit_code == 0
    assert "create" in result.output
    assert "list" in result.output
    assert "show" in result.output


@pytest.mark.unit
@pytest.mark.parametrize("command", ["create", "list", "show"])
def test_command_help_exits_zero_without_bootstrap(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    _forbid_bootstrap(monkeypatch)
    result = _invoke(["manage-user", command, "--help"])
    assert result.exit_code == 0
    assert "Traceback" not in result.output


@pytest.mark.unit
def test_root_missing_command_exits_one() -> None:
    result = _invoke([])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Module invocation parity (`python -m app.cli`)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_module_invocation_delegates_to_same_main() -> None:
    import app.cli.__main__ as dunder_main

    assert dunder_main.main is main


# ---------------------------------------------------------------------------
# bootstrap(): fail-fast Settings load
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_bootstrap_exits_2_when_settings_fail(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _raise() -> None:
        raise ValueError("Invalid JWT_SECRET_KEY: must be at least 32 characters")

    monkeypatch.setattr("app.cli._runtime._load_settings", _raise)

    with pytest.raises(SystemExit) as exc_info:
        bootstrap()

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.err.strip() == (
        "Error: Invalid JWT_SECRET_KEY: must be at least 32 characters"
    )
    assert "Traceback" not in captured.err


@pytest.mark.unit
def test_bootstrap_succeeds_when_settings_valid() -> None:
    # Exercises the real (already-valid, per conftest.py) Settings load —
    # must not raise or print anything.
    bootstrap()
    _load_settings()  # importing again is a cheap no-op (cached module)


@pytest.mark.unit
def test_get_session_factory_returns_production_factory() -> None:
    from app.cli._runtime import get_session_factory
    from app.database import async_session_factory

    assert get_session_factory() is async_session_factory


# ---------------------------------------------------------------------------
# main(): shared exception-to-exit-code mapper
# ---------------------------------------------------------------------------


def _invoke_main(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> int:
    """Call the real `main()` wrapper with `sys.argv` patched, returning
    the process exit code it raises via `SystemExit`."""
    monkeypatch.setattr(sys, "argv", ["sentinel", *args])
    with pytest.raises(SystemExit) as exc_info:
        main()
    code = exc_info.value.code
    assert isinstance(code, int)
    return code


@pytest.mark.unit
def test_main_maps_click_usage_error_to_exit_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Missing required --username/--email triggers Click's own
    # MissingParameter (a UsageError/ClickException subclass) before the
    # command body ever runs.
    code = _invoke_main(monkeypatch, ["manage-user", "create"])
    assert code == 1
    captured = capsys.readouterr()
    assert "Usage:" in captured.err


@pytest.mark.unit
def test_main_maps_click_abort_to_exit_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    @cli.command("_raises-abort")
    def _raises_abort() -> None:
        raise click.Abort()

    try:
        code = _invoke_main(monkeypatch, ["_raises-abort"])
        assert code == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == "Aborted."
    finally:
        del cli.commands["_raises-abort"]


@pytest.mark.unit
def test_main_maps_service_error_to_exit_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    @cli.command("_raises-service-error")
    def _raises_service_error() -> None:
        raise ServiceError("something went wrong")

    try:
        code = _invoke_main(monkeypatch, ["_raises-service-error"])
        assert code == 1
        captured = capsys.readouterr()
        assert captured.err.strip() == "Error: something went wrong"
    finally:
        del cli.commands["_raises-service-error"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc",
    [
        OperationalError("SELECT 1", {}, Exception("connection refused")),
        RedisError("connection refused"),
    ],
)
def test_main_maps_connection_errors_to_exit_two(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], exc: Exception
) -> None:
    @cli.command("_raises-connection-error")
    def _raises_connection_error() -> None:
        raise exc

    try:
        code = _invoke_main(monkeypatch, ["_raises-connection-error"])
        assert code == 2
        captured = capsys.readouterr()
        assert captured.err.startswith("Error: ")
    finally:
        del cli.commands["_raises-connection-error"]


@pytest.mark.unit
def test_main_maps_unhandled_exception_to_exit_two_and_logs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    @cli.command("_raises-unexpected")
    def _raises_unexpected() -> None:
        raise RuntimeError("boom")

    try:
        code = _invoke_main(monkeypatch, ["_raises-unexpected"])
        assert code == 2
        captured = capsys.readouterr()
        assert captured.err.strip() == "Error: boom"
    finally:
        del cli.commands["_raises-unexpected"]


@pytest.mark.unit
def test_main_maps_unhandled_exception_with_empty_message_to_class_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _EmptyMessageError(Exception):
        def __str__(self) -> str:
            return ""

    @cli.command("_raises-empty-message")
    def _raises_empty_message() -> None:
        raise _EmptyMessageError()

    try:
        code = _invoke_main(monkeypatch, ["_raises-empty-message"])
        assert code == 2
        captured = capsys.readouterr()
        assert captured.err.strip() == "Error: _EmptyMessageError"
    finally:
        del cli.commands["_raises-empty-message"]


# ---------------------------------------------------------------------------
# Signal handling: SIGINT -> 130, SIGTERM -> 143
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    ("signum", "expected_exit"),
    [(signal.SIGINT, 130), (signal.SIGTERM, 143)],
)
def test_signal_produces_documented_exit_code(signum: int, expected_exit: int) -> None:
    """A long-running command reacts to SIGINT/SIGTERM with exit 130/143.

    Uses `manage-user list` (read-only) against the real dev-env
    PostgreSQL as an observable, slow-enough-to-signal invocation. If no
    PostgreSQL is reachable the process exits 2 before the signal is
    delivered, which would falsify `expected_exit` and fail the
    assertion below rather than silently pass.
    """
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.cli", "manage-user", "list"],
        cwd=str(_BACKEND_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(0.5)  # let the process install its handlers and start
        proc.send_signal(signum)
        exit_code = proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    assert exit_code == expected_exit
