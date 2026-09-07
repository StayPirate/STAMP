"""Unit tests for image-smoke fixture orchestration helpers."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Generator
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.image import conftest as image_conftest


def _run_report_hook(item: Any, report: Any) -> Any:
    hook_factory = cast(
        Callable[[Any, Any], Generator[None, Any, Any]],
        image_conftest.pytest_runtest_makereport,
    )
    hook = hook_factory(item, SimpleNamespace())
    next(hook)
    with pytest.raises(StopIteration) as exc_info:
        hook.send(report)
    return exc_info.value.value


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
        file_args=["-f", "docker-compose.smoke.yml"],
        project="sentinel-smoke",
        wait_for_ready=False,
        timeout=45.0,
    )

    assert result.returncode == 0
    assert calls[-1] == (
        [
            "docker",
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
        file_args=[],
        project="sentinel-smoke",
    )

    assert result.returncode == 1
    assert len(calls) == 1
    assert calls[0][0][-2:] == ["stop", "beat"]


@pytest.mark.unit
def test_capture_compose_diagnostics_collects_state_and_bounded_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], float]] = []
    results = [
        subprocess.CompletedProcess([], 0, stdout='{"State":"exited"}\n', stderr=""),
        subprocess.CompletedProcess([], 0, stdout="api-1 | shutdown\n", stderr=""),
    ]

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs["timeout"]))
        assert kwargs == {
            "capture_output": True,
            "text": True,
            "check": False,
            "timeout": image_conftest._DIAGNOSTIC_TIMEOUT,
        }
        return results.pop(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    # The removed legacy override must not change the fixed Docker invocation.
    monkeypatch.setenv("COMPOSE_CMD", "podman compose")
    monkeypatch.setenv("COMPOSE_FILES", "docker-compose.smoke.yml")
    monkeypatch.setenv("COMPOSE_PROJECT", "sentinel-smoke")

    sections = image_conftest._capture_compose_diagnostics("teardown")

    assert sections == [
        (
            "compose ps --all (teardown)",
            '{"State":"exited"}',
        ),
        (
            "compose logs (tail=100) (teardown)",
            "api-1 | shutdown",
        ),
    ]
    assert calls == [
        (
            [
                "docker",
                "compose",
                "-p",
                "sentinel-smoke",
                "-f",
                "docker-compose.smoke.yml",
                "ps",
                "--all",
                "--format",
                "json",
            ],
            15.0,
        ),
        (
            [
                "docker",
                "compose",
                "-p",
                "sentinel-smoke",
                "-f",
                "docker-compose.smoke.yml",
                "logs",
                "--timestamps",
                "--tail=100",
                "--no-color",
            ],
            15.0,
        ),
    ]


@pytest.mark.unit
@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
def test_report_hook_captures_diagnostics_for_every_failed_image_phase(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    expected_sections = [("state", "snapshot"), ("logs", "snapshot")]
    captured_phases: list[str] = []
    requested_markers: list[str] = []

    def fake_capture(current_phase: str) -> list[tuple[str, str]]:
        captured_phases.append(current_phase)
        return expected_sections

    def get_closest_marker(name: str) -> object:
        requested_markers.append(name)
        return object()

    monkeypatch.setattr(image_conftest, "_capture_compose_diagnostics", fake_capture)
    item = SimpleNamespace(get_closest_marker=get_closest_marker)
    report = SimpleNamespace(when=phase, failed=True, sections=[])

    result = _run_report_hook(item, report)

    assert result is report
    assert captured_phases == [phase]
    assert requested_markers == ["image"]
    assert report.sections == expected_sections


@pytest.mark.unit
@pytest.mark.parametrize(
    ("failed", "has_image_marker"),
    [(False, True), (True, False)],
)
def test_report_hook_skips_diagnostics_when_not_applicable(
    monkeypatch: pytest.MonkeyPatch,
    failed: bool,
    has_image_marker: bool,
) -> None:
    def fail_capture(phase: str) -> list[tuple[str, str]]:
        pytest.fail(f"unexpected diagnostic capture for {phase}")

    monkeypatch.setattr(image_conftest, "_capture_compose_diagnostics", fail_capture)
    marker = object() if has_image_marker else None
    item = SimpleNamespace(get_closest_marker=lambda name: marker)
    report = SimpleNamespace(when="call", failed=failed, sections=[])

    result = _run_report_hook(item, report)

    assert result is report
    assert report.sections == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "logs_failure",
    [OSError("compose missing"), subprocess.TimeoutExpired(["compose"], 15.0)],
)
def test_capture_compose_diagnostics_reports_each_command_failure(
    monkeypatch: pytest.MonkeyPatch,
    logs_failure: OSError | subprocess.TimeoutExpired,
) -> None:
    results: list[
        subprocess.CompletedProcess[str] | OSError | subprocess.TimeoutExpired
    ] = [
        subprocess.CompletedProcess([], 9, stdout="", stderr="state unavailable\n"),
        logs_failure,
    ]

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        result = results.pop(0)
        if isinstance(result, (OSError, subprocess.TimeoutExpired)):
            raise result
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)

    sections = image_conftest._capture_compose_diagnostics("setup")

    assert sections[0] == (
        "compose ps --all (setup)",
        "[diagnostic command exited 9]\nstate unavailable",
    )
    assert sections[1][0] == "compose logs (tail=100) (setup)"
    assert sections[1][1].startswith("[diagnostic command failed:")


@pytest.mark.unit
def test_capture_compose_diagnostics_labels_empty_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    sections = image_conftest._capture_compose_diagnostics("call")

    assert sections == [
        ("compose ps --all (call)", "[no output]"),
        ("compose logs (tail=100) (call)", "[no output]"),
    ]
