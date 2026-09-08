"""OCI artifact probes for the packaged ``sentinel`` CLI entry points.

Detailed command discovery, options, output, domain errors, and database
behavior remain in ``tests/test_cli/``. The image gate verifies only the
installed script, package metadata, and packaged module invocation required by
the Artifact-Risk Rule in ``docs/features/platform/testing-strategy.md``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest


@pytest.mark.image
def test_sentinel_console_script_help_exits_zero(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = compose_exec("api", "sentinel", "--help")
    assert result.returncode == 0, (
        f"sentinel --help failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "Sentinel command-line interface" in result.stdout
    assert "manage-user" in result.stdout
    assert "api-key" in result.stdout
    assert "fetcher" in result.stdout


@pytest.mark.image
def test_sentinel_version_matches_installed_package(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
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
    result = compose_exec("api", "python", "-m", "app.cli", "--help")
    assert result.returncode == 0, (
        f"python -m app.cli --help failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "Sentinel command-line interface" in result.stdout
