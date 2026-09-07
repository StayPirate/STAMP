"""Regression tests for the Docker-only image-smoke runner."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT_DIR / "scripts" / "image-smoke.sh"

DOCKER_STUB = """\
#!/usr/bin/env bash
set -euo pipefail
printf 'docker %s\n' "$*" >> "${IMAGE_SMOKE_STUB_LOG}"

if [[ "${1:-}" == "version" ]]; then
    if [[ "${DOCKER_VERSION_EXIT:-0}" != "0" ]]; then
        exit "${DOCKER_VERSION_EXIT}"
    fi
    printf '%s\n' "${DOCKER_SERVER_IDENTITY:-Docker Engine - Community|Engine}"
    exit 0
fi

if [[ "${1:-}" == "compose" && "${2:-}" == "version" ]]; then
    if [[ "${COMPOSE_VERSION_EXIT:-0}" != "0" ]]; then
        exit "${COMPOSE_VERSION_EXIT}"
    fi
    printf '%s\n' "${DETECTED_COMPOSE_VERSION:-2.7.0}"
    exit 0
fi

if [[ "${1:-}" == "compose" && "$*" == *" build" ]]; then
    exit "${COMPOSE_BUILD_EXIT:-0}"
fi

if [[ "${1:-}" == "compose" && "$*" == *" up -d --wait" ]]; then
    exit "${COMPOSE_UP_EXIT:-0}"
fi
"""

UV_STUB = """\
#!/usr/bin/env bash
set -euo pipefail
printf 'uv %s\n' "$*" >> "${IMAGE_SMOKE_STUB_LOG}"
exit "${UV_EXIT:-0}"
"""

PODMAN_STUB = """\
#!/usr/bin/env bash
set -euo pipefail
printf 'podman %s\n' "$*" >> "${IMAGE_SMOKE_STUB_LOG}"
exit 0
"""


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _prepare_bin(tmp_path: Path, *, docker: bool = True, podman: bool = False) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "bash").symlink_to("/usr/bin/bash")
    (bin_dir / "dirname").symlink_to("/usr/bin/dirname")
    if docker:
        _write_executable(bin_dir / "docker", DOCKER_STUB)
    if podman:
        _write_executable(bin_dir / "podman", PODMAN_STUB)
    _write_executable(bin_dir / "uv", UV_STUB)
    return bin_dir


def _run_script(
    tmp_path: Path,
    *args: str,
    docker: bool = True,
    podman: bool = False,
    env_overrides: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = _prepare_bin(tmp_path, docker=docker, podman=podman)
    log_path = tmp_path / "stub.log"
    log_path.write_text("", encoding="utf-8")
    env = os.environ | {
        "PATH": str(bin_dir),
        "IMAGE_SMOKE_STUB_LOG": str(log_path),
    }
    env.update(env_overrides or {})

    result = subprocess.run(
        ["/usr/bin/bash", str(SCRIPT_PATH), *args],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, log_path.read_text(encoding="utf-8").splitlines()


@pytest.mark.unit
def test_runner_rejects_podman_when_docker_cli_is_missing(tmp_path: Path) -> None:
    result, calls = _run_script(tmp_path, docker=False, podman=True)

    assert result.returncode != 0
    assert "Error: Docker CLI not found." in result.stderr
    assert calls == []


@pytest.mark.unit
def test_runner_rejects_unreachable_docker_engine(tmp_path: Path) -> None:
    result, calls = _run_script(
        tmp_path,
        env_overrides={"DOCKER_VERSION_EXIT": "1"},
    )

    assert result.returncode != 0
    assert "Error: Docker Engine is unreachable." in result.stderr
    assert calls == [
        "docker version --format "
        "{{.Server.Platform.Name}}|{{(index .Server.Components 0).Name}}"
    ]


@pytest.mark.unit
def test_runner_rejects_podman_api_behind_docker_cli(tmp_path: Path) -> None:
    result, calls = _run_script(
        tmp_path,
        env_overrides={
            "DOCKER_SERVER_IDENTITY": (
                "linux/amd64/arch-unknown|Podman Engine|Conmon|"
                "OCI Runtime (crun)|Engine"
            )
        },
    )

    assert result.returncode != 0
    assert "unsupported container server 'linux/amd64/arch-unknown|Podman Engine" in (
        result.stderr
    )
    assert "Podman compatibility endpoints are not supported" in result.stderr
    assert calls == [
        "docker version --format "
        "{{.Server.Platform.Name}}|{{(index .Server.Components 0).Name}}"
    ]


@pytest.mark.unit
def test_runner_rejects_missing_compose_plugin(tmp_path: Path) -> None:
    result, calls = _run_script(
        tmp_path,
        env_overrides={"COMPOSE_VERSION_EXIT": "1"},
    )

    assert result.returncode != 0
    assert "Error: Docker Compose CLI plugin not found." in result.stderr
    assert calls[-1] == "docker compose version --short"
    assert not any(" up " in call or call.endswith(" build") for call in calls)


@pytest.mark.unit
@pytest.mark.parametrize("compose_version", ["2.6.1", "1.29.2"])
def test_runner_rejects_unsupported_compose_version(
    tmp_path: Path, compose_version: str
) -> None:
    result, calls = _run_script(
        tmp_path,
        env_overrides={"DETECTED_COMPOSE_VERSION": compose_version},
    )

    assert result.returncode != 0
    assert f"Docker Compose {compose_version} is unsupported" in result.stderr
    assert calls[-1] == "docker compose version --short"


@pytest.mark.unit
def test_runner_rejects_unparseable_compose_version(tmp_path: Path) -> None:
    result, calls = _run_script(
        tmp_path,
        env_overrides={"DETECTED_COMPOSE_VERSION": "Docker Compose version two"},
    )

    assert result.returncode != 0
    assert "unable to parse Docker Compose version" in result.stderr
    assert calls[-1] == "docker compose version --short"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("server_identity", "compose_version"),
    [
        ("Docker Engine - Community", "2.7.0"),
        ("|Engine", "2.7.0"),
        ("Docker Engine - Community", "v2.7.0"),
        ("Docker Desktop 4.50.0", "2.40.3-desktop.1"),
        ("Docker Engine - Community", "3.0.0"),
    ],
)
def test_runner_accepts_supported_compose_versions(
    tmp_path: Path, server_identity: str, compose_version: str
) -> None:
    result, calls = _run_script(
        tmp_path,
        "--no-build",
        env_overrides={
            "DOCKER_SERVER_IDENTITY": server_identity,
            "DETECTED_COMPOSE_VERSION": compose_version,
        },
    )

    assert result.returncode == 0
    assert f"[image-smoke] Using: docker compose {compose_version}" in result.stdout
    assert not any(call.endswith(" build") for call in calls)
    assert any(call.endswith(" up -d --wait") for call in calls)
    assert calls[-1].endswith(" down -v --remove-orphans")


@pytest.mark.unit
def test_runner_builds_before_starting_and_runs_pytest(tmp_path: Path) -> None:
    result, calls = _run_script(tmp_path)

    assert result.returncode == 0
    build_index = next(i for i, call in enumerate(calls) if call.endswith(" build"))
    up_index = next(i for i, call in enumerate(calls) if call.endswith(" up -d --wait"))
    pytest_index = next(i for i, call in enumerate(calls) if call.startswith("uv "))
    down_index = next(
        i for i, call in enumerate(calls) if call.endswith(" down -v --remove-orphans")
    )
    assert build_index < up_index < pytest_index < down_index
    assert calls[pytest_index] == "uv run pytest -m image tests/image/"


@pytest.mark.unit
def test_runner_propagates_pytest_failure_and_tears_down(tmp_path: Path) -> None:
    result, calls = _run_script(
        tmp_path,
        "--no-build",
        env_overrides={"UV_EXIT": "7", "COMPOSE_CMD": "podman compose"},
    )

    assert result.returncode == 7
    assert calls[-1].endswith(" down -v --remove-orphans")
    assert all(not call.startswith("podman ") for call in calls)


@pytest.mark.unit
def test_runner_tears_down_after_build_failure(tmp_path: Path) -> None:
    result, calls = _run_script(
        tmp_path,
        env_overrides={"COMPOSE_BUILD_EXIT": "9"},
    )

    assert result.returncode == 9
    assert any(call.endswith(" build") for call in calls)
    assert calls[-1].endswith(" down -v --remove-orphans")
    assert not any(call.endswith(" up -d --wait") for call in calls)


@pytest.mark.unit
def test_runner_tears_down_after_stack_start_failure(tmp_path: Path) -> None:
    result, calls = _run_script(
        tmp_path,
        env_overrides={"COMPOSE_UP_EXIT": "8"},
    )

    assert result.returncode == 8
    assert any(call.endswith(" build") for call in calls)
    assert any(call.endswith(" up -d --wait") for call in calls)
    assert calls[-1].endswith(" down -v --remove-orphans")
    assert not any(call.startswith("uv ") for call in calls)


@pytest.mark.unit
def test_runner_rejects_unknown_argument_before_preflight(tmp_path: Path) -> None:
    result, calls = _run_script(tmp_path, "--unknown")

    assert result.returncode != 0
    assert "Error: unknown argument '--unknown'" in result.stderr
    assert calls == []
