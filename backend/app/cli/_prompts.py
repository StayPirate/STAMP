"""Shared interactive input helpers for CLI commands.

See `docs/features/platform/cli-infrastructure.md` (Interactive Input
Helpers). These are Category B (no side effects beyond terminal I/O)
utility functions: each signals its outcome via a return value, never by
raising or printing — the calling command owns its own exact error
message, output channel, and exit code.
"""

from __future__ import annotations

import sys

import click


def is_interactive_terminal() -> bool:
    """Return whether stdin is attached to an interactive terminal.

    A thin, patchable wrapper around `sys.stdin.isatty()` — tests
    monkeypatch this function directly rather than reaching into Click's
    own internals to simulate a TTY or non-TTY invocation.
    """
    return sys.stdin.isatty()


def prompt_password_with_confirmation(
    prompt: str = "Password", confirm_prompt: str = "Confirm password"
) -> str | None:
    """Prompt twice via hidden (non-echoed) input and compare the entries.

    Returns the entered password when both entries match, or `None` when
    they differ. Never prints an error itself — the calling command
    prints its own exact mismatch message and chooses its own exit code.
    """
    password = click.prompt(prompt, hide_input=True)
    confirmation = click.prompt(confirm_prompt, hide_input=True)
    if password != confirmation:
        return None
    return str(password)
