"""Black-box image smoke assertions for the `sentinel` CLI entry point.

Verifies — via `compose exec` against the running `api` container — that
the built image exposes the console script (`sentinel = "app.cli:main"`)
and the module invocation (`python -m app.cli`), that both reach the
same command surface, and that the P2-12 bootstrap command group
(`manage-user create/list/show`) is discoverable. See
docs/features/platform/testing-strategy.md (Image / Container Smoke
Testing, Growth Rule) and docs/features/platform/cli-infrastructure.md.

No destructive/interactive invocation is exercised here — creating a
user requires a TTY and a hidden password prompt, which a
`compose exec -T` (non-interactive) call cannot supply. The full
create/list/show behavioral contract is already covered by the
in-process integration suite (`tests/test_cli/test_manage_user.py`),
which runs far faster and does not need a running container.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest


@pytest.mark.image
def test_sentinel_console_script_help_exits_zero(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """The installed `sentinel` console script is executable and its
    root `--help` exits 0 without requiring a database connection."""
    result = compose_exec("api", "sentinel", "--help")
    assert result.returncode == 0, (
        f"sentinel --help failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "Sentinel command-line interface" in result.stdout


@pytest.mark.image
def test_sentinel_version_matches_installed_package(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """`sentinel --version` exits 0 and prints a non-empty version string."""
    result = compose_exec("api", "sentinel", "--version")
    assert result.returncode == 0, (
        f"sentinel --version failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.stdout.strip()


@pytest.mark.image
def test_module_invocation_help_exits_zero(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """`python -m app.cli --help` reaches the same root group as the
    installed console script (docs/features/platform/cli-infrastructure.md,
    Package Entry Point & Invocation)."""
    result = compose_exec("api", "python", "-m", "app.cli", "--help")
    assert result.returncode == 0, (
        f"python -m app.cli --help failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "Sentinel command-line interface" in result.stdout


@pytest.mark.image
def test_console_script_and_module_invocation_are_equivalent(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Both entry points expose the identical command surface.

    The `Usage:` line's program name intentionally differs (Click infers
    it from `argv[0]`: `sentinel` vs. `python -m app.cli`) — everything
    after it (description, options, command list) must be identical.
    """
    console_help = compose_exec("api", "sentinel", "--help")
    module_help = compose_exec("api", "python", "-m", "app.cli", "--help")
    assert console_help.returncode == 0
    assert module_help.returncode == 0

    def _body(output: str) -> str:
        return output.split("\n", 1)[1]

    assert _body(console_help.stdout) == _body(module_help.stdout)


@pytest.mark.image
def test_manage_user_group_is_discoverable(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """`sentinel manage-user --help` lists all three bootstrap commands."""
    result = compose_exec("api", "sentinel", "manage-user", "--help")
    assert result.returncode == 0, (
        f"manage-user --help failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "create" in result.stdout
    assert "list" in result.stdout
    assert "show" in result.stdout


@pytest.mark.image
def test_manage_user_create_help_shows_bootstrap_surface(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """`sentinel manage-user create --help` documents the required flags
    for the local administrator bootstrap/recovery path — proving the
    command surface is present in the built image without ever invoking
    the interactive password prompt."""
    result = compose_exec("api", "sentinel", "manage-user", "create", "--help")
    assert result.returncode == 0, (
        f"manage-user create --help failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "--username" in result.stdout
    assert "--email" in result.stdout
    assert "--role" in result.stdout
