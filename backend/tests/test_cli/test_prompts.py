"""Tests for `app.cli._prompts` (Interactive Input Helpers).

See docs/features/platform/cli-infrastructure.md (Interactive Input
Helpers): these are Category B helpers — no side effects beyond terminal
I/O, no exceptions of their own. Tests exercise them directly, using
Click's `CliRunner.isolation()` to supply scripted stdin for the
prompt-based helper.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from app.cli._prompts import is_interactive_terminal, prompt_password_with_confirmation


@pytest.mark.unit
def test_is_interactive_terminal_reflects_stdin_isatty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert is_interactive_terminal() is True

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert is_interactive_terminal() is False


@pytest.mark.unit
def test_prompt_password_with_confirmation_matching_returns_password() -> None:
    runner = CliRunner()
    with runner.isolation(input="a-very-strong-password-1\na-very-strong-password-1\n"):
        result = prompt_password_with_confirmation()

    assert result == "a-very-strong-password-1"


@pytest.mark.unit
def test_prompt_password_with_confirmation_mismatch_returns_none() -> None:
    runner = CliRunner()
    with runner.isolation(input="a-very-strong-password-1\na-different-password-2\n"):
        result = prompt_password_with_confirmation()

    assert result is None


@pytest.mark.unit
def test_prompt_password_with_confirmation_never_echoes_to_output() -> None:
    runner = CliRunner()
    with runner.isolation(
        input="a-very-strong-password-1\na-very-strong-password-1\n"
    ) as outstreams:
        prompt_password_with_confirmation()
        output = outstreams[0].getvalue().decode()

    assert "a-very-strong-password-1" not in output
