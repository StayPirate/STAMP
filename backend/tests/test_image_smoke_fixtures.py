"""Unit tests for image-smoke fixture orchestration helpers."""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest

from tests.image import conftest as image_conftest


@pytest.fixture
def compose_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    list[tuple[list[str], float]],
    Callable[[int], subprocess.CompletedProcess[str]],
]:
    calls: list[tuple[list[str], float]] = []
    return_codes: list[int] = []

    def queue_result(returncode: int) -> subprocess.CompletedProcess[str]:
        return_codes.append(returncode)
        return subprocess.CompletedProcess([], returncode)

    def fake_run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, timeout))
        return subprocess.CompletedProcess(cmd, return_codes.pop(0))

    monkeypatch.setattr(image_conftest, "_run_compose_bounded", fake_run)
    return calls, queue_result


@pytest.mark.unit
def test_restart_compose_service_waits_for_service_readiness(
    compose_calls: tuple[
        list[tuple[list[str], float]],
        Callable[[int], subprocess.CompletedProcess[str]],
    ],
) -> None:
    calls, queue_result = compose_calls
    queue_result(0)
    queue_result(0)

    result = image_conftest._restart_compose_service(
        "worker",
        compose_cmd=["docker", "compose"],
        file_args=["-f", "docker-compose.smoke.yml"],
        project="sentinel-smoke",
        timeout=60.0,
    )

    assert result.returncode == 0
    assert calls == [
        (
            [
                "docker",
                "compose",
                "-p",
                "sentinel-smoke",
                "-f",
                "docker-compose.smoke.yml",
                "stop",
                "worker",
            ],
            60.0,
        ),
        (
            [
                "docker",
                "compose",
                "-p",
                "sentinel-smoke",
                "-f",
                "docker-compose.smoke.yml",
                "up",
                "-d",
                "--wait",
                "worker",
            ],
            60.0,
        ),
    ]


@pytest.mark.unit
def test_restart_compose_service_can_skip_readiness_wait(
    compose_calls: tuple[
        list[tuple[list[str], float]],
        Callable[[int], subprocess.CompletedProcess[str]],
    ],
) -> None:
    calls, queue_result = compose_calls
    queue_result(0)
    queue_result(0)

    result = image_conftest._restart_compose_service(
        "api",
        compose_cmd=["podman", "compose"],
        file_args=["-f", "docker-compose.smoke.yml"],
        project="sentinel-smoke",
        wait_for_ready=False,
        timeout=45.0,
    )

    assert result.returncode == 0
    assert calls[-1] == (
        [
            "podman",
            "compose",
            "-p",
            "sentinel-smoke",
            "-f",
            "docker-compose.smoke.yml",
            "up",
            "-d",
            "api",
        ],
        45.0,
    )


@pytest.mark.unit
def test_restart_compose_service_propagates_readiness_failure(
    compose_calls: tuple[
        list[tuple[list[str], float]],
        Callable[[int], subprocess.CompletedProcess[str]],
    ],
) -> None:
    calls, queue_result = compose_calls
    queue_result(0)
    queue_result(1)

    result = image_conftest._restart_compose_service(
        "api",
        compose_cmd=["docker", "compose"],
        file_args=[],
        project="sentinel-smoke",
    )

    assert result.returncode == 1
    assert result.args == calls[-1][0]
    assert calls[-1][0][-4:] == ["up", "-d", "--wait", "api"]


@pytest.mark.unit
def test_restart_compose_service_returns_stop_failure_without_starting(
    compose_calls: tuple[
        list[tuple[list[str], float]],
        Callable[[int], subprocess.CompletedProcess[str]],
    ],
) -> None:
    calls, queue_result = compose_calls
    queue_result(1)

    result = image_conftest._restart_compose_service(
        "beat",
        compose_cmd=["docker", "compose"],
        file_args=[],
        project="sentinel-smoke",
    )

    assert result.returncode == 1
    assert len(calls) == 1
    assert calls[0][0][-2:] == ["stop", "beat"]
