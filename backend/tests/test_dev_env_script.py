"""Regression tests for the Docker-only development environment runner."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT_DIR / "scripts" / "dev-env.sh"

DOCKER_STUB = """\
#!/usr/bin/env bash
set -euo pipefail
printf 'docker %s\n' "$*" >> "${DEV_ENV_STUB_LOG}"

if [[ "${1:-}" == "version" && "$*" == *"Platform.Name"* ]]; then
    printf '%s\n' "${DOCKER_SERVER_IDENTITY:-|Engine}"
    exit "${DOCKER_IDENTITY_EXIT:-0}"
fi

if [[ "${1:-}" == "version" && "$*" == *"Server.Version"* ]]; then
    printf '%s\n' "${DOCKER_SERVER_VERSION-24.0.0}"
    exit "${DOCKER_VERSION_EXIT:-0}"
fi

if [[ "${1:-}" == "compose" && "${2:-}" == "version" ]]; then
    printf '%s\n' "${DETECTED_COMPOSE_VERSION:-2.7.0}"
    exit "${COMPOSE_VERSION_EXIT:-0}"
fi

if [[ "${1:-}" == "compose" ]]; then
    exit "${COMPOSE_COMMAND_EXIT:-0}"
fi
"""


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run_script(
    tmp_path: Path,
    command: str,
    *,
    docker: bool = True,
    env_overrides: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "bash").symlink_to("/usr/bin/bash")
    (bin_dir / "dirname").symlink_to("/usr/bin/dirname")
    if docker:
        _write_executable(bin_dir / "docker", DOCKER_STUB)

    log_path = tmp_path / "stub.log"
    log_path.write_text("", encoding="utf-8")
    env = os.environ | {
        "PATH": str(bin_dir),
        "DEV_ENV_STUB_LOG": str(log_path),
        "DOCKER_SERVER_IDENTITY": "|Engine",
    }
    env.update(env_overrides or {})

    result = subprocess.run(
        ["/usr/bin/bash", str(SCRIPT_PATH), command],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, log_path.read_text(encoding="utf-8").splitlines()


@pytest.mark.unit
def test_help_does_not_require_docker(tmp_path: Path) -> None:
    result, calls = _run_script(tmp_path, "help", docker=False)

    assert result.returncode == 0
    assert "Sentinel Development Environment Manager" in result.stdout
    assert calls == []


@pytest.mark.unit
def test_runner_requires_docker_cli(tmp_path: Path) -> None:
    result, calls = _run_script(tmp_path, "status", docker=False)

    assert result.returncode != 0
    assert "Docker CLI not found" in result.stderr
    assert calls == []


@pytest.mark.unit
def test_runner_rejects_unreachable_docker_engine(tmp_path: Path) -> None:
    result, calls = _run_script(
        tmp_path,
        "status",
        env_overrides={"DOCKER_IDENTITY_EXIT": "1"},
    )

    assert result.returncode != 0
    assert "Docker Engine is unreachable" in result.stderr
    assert len(calls) == 1


@pytest.mark.unit
def test_runner_rejects_non_docker_server(tmp_path: Path) -> None:
    result, calls = _run_script(
        tmp_path,
        "status",
        env_overrides={"DOCKER_SERVER_IDENTITY": "Podman Engine"},
    )

    assert result.returncode != 0
    assert "Unsupported container server 'Podman Engine'" in result.stderr
    assert len(calls) == 1


@pytest.mark.unit
def test_runner_rejects_empty_docker_version(tmp_path: Path) -> None:
    result, calls = _run_script(
        tmp_path,
        "status",
        env_overrides={"DOCKER_SERVER_VERSION": ""},
    )

    assert result.returncode != 0
    assert "Docker Engine returned an empty version" in result.stderr
    assert len(calls) == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    ("env_overrides", "diagnostic"),
    [
        ({"DETECTED_COMPOSE_VERSION": "2.6.9"}, "Docker Compose 2.6.9"),
        (
            {"DETECTED_COMPOSE_VERSION": "not-a-version"},
            "Unable to parse Docker Compose version",
        ),
        ({"COMPOSE_VERSION_EXIT": "1"}, "Compose CLI plugin not found"),
    ],
)
def test_runner_rejects_unsupported_toolchain(
    tmp_path: Path,
    env_overrides: dict[str, str],
    diagnostic: str,
) -> None:
    result, calls = _run_script(
        tmp_path,
        "status",
        env_overrides=env_overrides,
    )

    assert result.returncode != 0
    assert diagnostic in result.stderr
    assert not any(" compose -f " in call for call in calls)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("docker_version", "compose_version"),
    [("1.0.0", "2.7.0"), ("99.1.0", "99.1.0")],
)
def test_runner_accepts_compose_minimum_and_future_versions(
    tmp_path: Path,
    docker_version: str,
    compose_version: str,
) -> None:
    result, calls = _run_script(
        tmp_path,
        "status",
        env_overrides={
            "DOCKER_SERVER_VERSION": docker_version,
            "DETECTED_COMPOSE_VERSION": compose_version,
            "COMPOSE_CMD": "unsupported compose override",
        },
    )

    assert result.returncode == 0
    assert f"Docker Engine: {docker_version}" in result.stdout
    assert f"Docker Compose: {compose_version}" in result.stdout
    compose_suffix = (
        "compose -f " + str(ROOT_DIR / "docker-compose.yml") + " -p sentinel ps"
    )
    assert calls[-1].endswith(compose_suffix)
    assert all(not call.startswith("unsupported ") for call in calls)


@pytest.mark.unit
def test_runner_propagates_compose_failure(tmp_path: Path) -> None:
    result, calls = _run_script(
        tmp_path,
        "down",
        env_overrides={"COMPOSE_COMMAND_EXIT": "7"},
    )

    assert result.returncode == 7
    assert calls[-1].endswith(" -p sentinel down")
