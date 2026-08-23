"""Black-box image smoke assertions for the `sentinel` CLI entry point.

Verifies — via `compose exec` against the running `api` container — that
the built image exposes the console script (`sentinel = "app.cli:main"`)
and the module invocation (`python -m app.cli`), that both reach the
same command surface, and that the P2-12 bootstrap command group
(`manage-user create/list/show`) plus the P2-13 remaining local identity
commands (`manage-user set-password/unlock`, `api-key list/revoke`) are
discoverable, along with the read-only `sentinel fetcher list/config`
diagnostic commands. See docs/features/platform/testing-strategy.md
(Image / Container Smoke Testing, Growth Rule) and
docs/features/platform/cli-infrastructure.md.

No destructive/interactive invocation is exercised here — creating a
user or resetting a password requires a TTY and a hidden password
prompt, which a `compose exec -T` (non-interactive) call cannot supply.
The full create/list/show/set-password/unlock/api-key behavioral
contract is already covered by the in-process integration suite
(`tests/test_cli/test_manage_user.py`, `tests/test_cli/test_api_key.py`),
which runs far faster and does not need a running container. This suite
proves command discovery and representative non-destructive paths
(unknown-user/unknown-key/unknown-fetcher errors, non-TTY rejection,
and a real `fetcher list`/`fetcher config` execution) against the
actual built artifact. The exhaustive `fetcher list`/`fetcher config`
rendering contract is covered by
`tests/test_cli/test_fetcher.py`.
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
    """`sentinel manage-user --help` lists all five subcommands."""
    result = compose_exec("api", "sentinel", "manage-user", "--help")
    assert result.returncode == 0, (
        f"manage-user --help failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "create" in result.stdout
    assert "list" in result.stdout
    assert "show" in result.stdout
    assert "set-password" in result.stdout
    assert "unlock" in result.stdout


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


@pytest.mark.image
def test_manage_user_set_password_help_shows_username_option(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = compose_exec("api", "sentinel", "manage-user", "set-password", "--help")
    assert result.returncode == 0, (
        f"manage-user set-password --help failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "--username" in result.stdout
    # No password option exists — the password is always collected via
    # an interactive hidden prompt, never as a command-line argument.
    assert "--password" not in result.stdout


@pytest.mark.image
def test_manage_user_set_password_non_tty_rejected(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """`compose exec -T` has no TTY attached to stdin, so the command
    must reject the invocation before ever reaching the hidden password
    prompt — proving the built image enforces this guard without
    performing any mutation."""
    result = compose_exec(
        "api", "sentinel", "manage-user", "set-password", "--username", "jdoe"
    )
    assert result.returncode == 1, (
        f"expected exit 1 (non-TTY), got rc={result.returncode}: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "interactive terminal" in result.stderr


@pytest.mark.image
def test_manage_user_unlock_help_shows_username_option(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = compose_exec("api", "sentinel", "manage-user", "unlock", "--help")
    assert result.returncode == 0, (
        f"manage-user unlock --help failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "--username" in result.stdout


@pytest.mark.image
def test_manage_user_unlock_unknown_user_exits_one(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """A username certain not to exist proves the command reaches the
    real database and reports the not-found error, with no destructive
    effect (there is nothing to unlock)."""
    result = compose_exec(
        "api",
        "sentinel",
        "manage-user",
        "unlock",
        "--username",
        "image-smoke-nonexistent-user",
    )
    assert result.returncode == 1, (
        f"expected exit 1 (user not found), got rc={result.returncode}: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "not found" in result.stderr


@pytest.mark.image
def test_api_key_group_is_discoverable(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """`sentinel api-key --help` lists `list` and `revoke` only — no
    `create` command exists (API keys are self-service only)."""
    result = compose_exec("api", "sentinel", "api-key", "--help")
    assert result.returncode == 0, (
        f"api-key --help failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "list" in result.stdout
    assert "revoke" in result.stdout
    assert "create" not in result.stdout


@pytest.mark.image
def test_api_key_list_help_shows_username_option(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = compose_exec("api", "sentinel", "api-key", "list", "--help")
    assert result.returncode == 0, (
        f"api-key list --help failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "--username" in result.stdout


@pytest.mark.image
def test_api_key_list_unknown_user_exits_one(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = compose_exec(
        "api",
        "sentinel",
        "api-key",
        "list",
        "--username",
        "image-smoke-nonexistent-user",
    )
    assert result.returncode == 1, (
        f"expected exit 1 (user not found), got rc={result.returncode}: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "not found" in result.stderr


@pytest.mark.image
def test_api_key_revoke_help_shows_key_id_option(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = compose_exec("api", "sentinel", "api-key", "revoke", "--help")
    assert result.returncode == 0, (
        f"api-key revoke --help failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "--key-id" in result.stdout
    assert "--username" not in result.stdout


@pytest.mark.image
def test_api_key_revoke_missing_key_exits_one(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """A syntactically valid but certainly-nonexistent UUID proves the
    command reaches the real database without performing any mutation."""
    result = compose_exec(
        "api",
        "sentinel",
        "api-key",
        "revoke",
        "--key-id",
        "00000000-0000-0000-0000-000000000000",
    )
    assert result.returncode == 1, (
        f"expected exit 1 (key not found), got rc={result.returncode}: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "not found" in result.stderr


# ---------------------------------------------------------------------------
# `sentinel fetcher` (P3-10 — read-only diagnostic CLI)
# ---------------------------------------------------------------------------

_FETCHER_CLI_SEED_NAME = "image_smoke_cli_fetcher"

# The shipped image's `FETCHER_REGISTRY` has no production fetcher yet
# (see docs/features/platform/fetcher-infrastructure.md, Fetcher
# Registry) — mirrors `tests/image/test_fetchers_api.py`'s rationale for
# arranging a deregistered `FetcherConfig` row directly to get a
# realistic, non-empty rendering for both commands.
_FETCHER_CLI_ARRANGE_SCRIPT = r"""
import asyncio

from app.database import async_session_factory
from app.models.fetcher_config import FetcherConfig

async def main():
    async with async_session_factory() as db:
        db.add(
            FetcherConfig(
                fetcher_name="image_smoke_cli_fetcher",
                schedule_override="0 5 * * *",
                custom_settings={"raw_setting": "raw_value"},
            )
        )
        await db.commit()

asyncio.run(main())
"""

_FETCHER_CLI_CLEANUP_SCRIPT = r"""
import asyncio

from sqlalchemy import delete

from app.database import async_session_factory
from app.models.fetcher_config import FetcherConfig

async def main():
    async with async_session_factory() as db:
        await db.execute(
            delete(FetcherConfig).where(
                FetcherConfig.fetcher_name == "image_smoke_cli_fetcher"
            )
        )
        await db.commit()

asyncio.run(main())
"""


@pytest.mark.image
def test_fetcher_group_is_discoverable(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """`sentinel fetcher --help` lists both read-only subcommands."""
    result = compose_exec("api", "sentinel", "fetcher", "--help")
    assert result.returncode == 0, (
        f"fetcher --help failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "list" in result.stdout
    assert "config" in result.stdout


@pytest.mark.image
def test_fetcher_list_help_exits_zero(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = compose_exec("api", "sentinel", "fetcher", "list", "--help")
    assert result.returncode == 0, (
        f"fetcher list --help failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


@pytest.mark.image
def test_fetcher_config_help_shows_name_argument(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = compose_exec("api", "sentinel", "fetcher", "config", "--help")
    assert result.returncode == 0, (
        f"fetcher config --help failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "NAME" in result.stdout


@pytest.mark.image
def test_fetcher_config_unknown_fetcher_exits_one(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """A fetcher name certain not to exist proves the command reaches
    the real database and reports the not-found error, with no
    destructive effect (there is nothing to read)."""
    result = compose_exec(
        "api", "sentinel", "fetcher", "config", "image-smoke-nonexistent-fetcher"
    )
    assert result.returncode == 1, (
        f"expected exit 1 (fetcher not found), got rc={result.returncode}: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "not found" in result.stderr


@pytest.mark.image
def test_fetcher_list_and_config_real_execution(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Representative non-destructive execution path for both `fetcher
    list` and `fetcher config <name>` against the real database, per
    docs/features/platform/testing-strategy.md (Image / Container Smoke
    Testing, Growth Rule)."""
    arrange = compose_exec("api", "python", "-c", _FETCHER_CLI_ARRANGE_SCRIPT)
    assert arrange.returncode == 0, (
        f"fetcher CLI smoke arrange failed "
        f"(stdout={arrange.stdout!r}, stderr={arrange.stderr!r})"
    )
    try:
        list_result = compose_exec("api", "sentinel", "fetcher", "list")
        assert list_result.returncode == 0, (
            f"fetcher list failed (rc={list_result.returncode}): "
            f"stdout={list_result.stdout!r} stderr={list_result.stderr!r}"
        )
        assert "Deregistered (historical data only):" in list_result.stdout
        assert _FETCHER_CLI_SEED_NAME in list_result.stdout

        config_result = compose_exec(
            "api", "sentinel", "fetcher", "config", _FETCHER_CLI_SEED_NAME
        )
        assert config_result.returncode == 0, (
            f"fetcher config failed (rc={config_result.returncode}): "
            f"stdout={config_result.stdout!r} stderr={config_result.stderr!r}"
        )
        assert (
            f"Fetcher: {_FETCHER_CLI_SEED_NAME} (deregistered)" in config_result.stdout
        )
        assert "Schedule override: 0 5 * * *" in config_result.stdout
        assert "raw_setting = raw_value" in config_result.stdout
    finally:
        cleanup = compose_exec("api", "python", "-c", _FETCHER_CLI_CLEANUP_SCRIPT)
        assert cleanup.returncode == 0, (
            f"fetcher CLI smoke cleanup failed "
            f"(stdout={cleanup.stdout!r}, stderr={cleanup.stderr!r})"
        )
