"""Unit tests for image-smoke fixture orchestration helpers."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.image import conftest as image_conftest

_IMAGE_ID = f"sha256:{'a' * 64}"


def _context(tmp_path: Path, *, project: str = "sentinel-smoke-123") -> Any:
    compose_file = tmp_path / "compose.yml"
    compose_file.touch()
    registry = tmp_path / "projects"
    registry.touch()
    return image_conftest.ImageSmokeContext(
        compose_files=(compose_file,),
        project=project,
        image_id=_IMAGE_ID,
        base_url="http://127.0.0.1:49152",
        project_registry=registry,
    )


def _set_valid_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> dict[str, str]:
    first_file = tmp_path / "compose.yml"
    second_file = tmp_path / "override.yml"
    registry = tmp_path / "projects"
    first_file.touch()
    second_file.touch()
    registry.touch()
    values = {
        "COMPOSE_PROJECT": "sentinel-smoke-123",
        "COMPOSE_FILES": f"{first_file}:{second_file}",
        "IMAGE_SMOKE_IMAGE_ID": _IMAGE_ID,
        "IMAGE_SMOKE_BASE_URL": "http://127.0.0.1:49152",
        "IMAGE_SMOKE_PROJECT_REGISTRY": str(registry),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


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
    Callable[..., subprocess.CompletedProcess[str]],
]:
    calls: list[tuple[list[str], float]] = []
    results: list[subprocess.CompletedProcess[str]] = []

    def queue_result(
        returncode: int,
        *,
        stdout: str = "",
        stderr: str = "",
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.CompletedProcess([], returncode, stdout, stderr)
        results.append(result)
        return result

    def fake_run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, timeout))
        result = (
            results.pop(0) if results else subprocess.CompletedProcess(cmd, 0, "", "")
        )
        return subprocess.CompletedProcess(
            cmd, result.returncode, result.stdout, result.stderr
        )

    monkeypatch.setattr(image_conftest, "_run_compose_bounded", fake_run)
    return calls, queue_result


@pytest.mark.unit
def test_resolve_image_smoke_context_requires_every_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    values = _set_valid_environment(monkeypatch, tmp_path)

    for name in values:
        monkeypatch.delenv(name)
        with pytest.raises(pytest.UsageError, match=f"{name} is required"):
            image_conftest._resolve_image_smoke_context()
        monkeypatch.setenv(name, values[name])


@pytest.mark.unit
def test_resolve_image_smoke_context_returns_validated_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    values = _set_valid_environment(monkeypatch, tmp_path)

    context = image_conftest._resolve_image_smoke_context()

    assert context.project == values["COMPOSE_PROJECT"]
    assert context.image_id == values["IMAGE_SMOKE_IMAGE_ID"]
    assert context.base_url == values["IMAGE_SMOKE_BASE_URL"]
    assert context.compose_files == tuple(
        Path(value) for value in values["COMPOSE_FILES"].split(":")
    )
    assert context.project_registry == Path(values["IMAGE_SMOKE_PROJECT_REGISTRY"])
    assert context.file_args == [
        "-f",
        str(context.compose_files[0]),
        "-f",
        str(context.compose_files[1]),
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "project",
    ["Sentinel", "-sentinel", "sentinel.smoke", "sentinel smoke", "sentinel\nother"],
)
def test_resolve_image_smoke_context_rejects_invalid_compose_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, project: str
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("COMPOSE_PROJECT", project)

    with pytest.raises(pytest.UsageError, match="COMPOSE_PROJECT must start"):
        image_conftest._resolve_image_smoke_context()


@pytest.mark.unit
@pytest.mark.parametrize("compose_files", ["", ":", "missing.yml", "one.yml:"])
def test_resolve_image_smoke_context_rejects_invalid_compose_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, compose_files: str
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("COMPOSE_FILES", compose_files)

    with pytest.raises(pytest.UsageError, match="COMPOSE_FILES"):
        image_conftest._resolve_image_smoke_context()


@pytest.mark.unit
@pytest.mark.parametrize(
    "image_id",
    ["", "a" * 64, f"sha256:{'A' * 64}", f"sha256:{'a' * 63}", f"sha512:{'a' * 64}"],
)
def test_resolve_image_smoke_context_rejects_invalid_image_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, image_id: str
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("IMAGE_SMOKE_IMAGE_ID", image_id)

    with pytest.raises(pytest.UsageError, match="IMAGE_SMOKE_IMAGE_ID"):
        image_conftest._resolve_image_smoke_context()


@pytest.mark.unit
@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "https://127.0.0.1:49152",
        "http://localhost:49152",
        "http://127.0.0.1",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
        "http://user@127.0.0.1:49152",
        "http://127.0.0.1:49152/",
        "http://127.0.0.1:49152/health",
        "http://127.0.0.1:49152?",
        "http://127.0.0.1:49152?ready=true",
        "http://127.0.0.1:49152#",
        "http://127.0.0.1:49152#fragment",
    ],
)
def test_resolve_image_smoke_context_rejects_invalid_base_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, base_url: str
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("IMAGE_SMOKE_BASE_URL", base_url)

    with pytest.raises(pytest.UsageError, match="IMAGE_SMOKE_BASE_URL"):
        image_conftest._resolve_image_smoke_context()


@pytest.mark.unit
@pytest.mark.parametrize("registry", ["relative", "/missing/registry", ""])
def test_resolve_image_smoke_context_rejects_invalid_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, registry: str
) -> None:
    _set_valid_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("IMAGE_SMOKE_PROJECT_REGISTRY", registry)

    with pytest.raises(pytest.UsageError, match="IMAGE_SMOKE_PROJECT_REGISTRY"):
        image_conftest._resolve_image_smoke_context()


@pytest.mark.unit
def test_image_setup_validates_only_image_marked_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        image_conftest,
        "_resolve_image_smoke_context",
        lambda: calls.append("resolved"),
    )

    image_conftest.pytest_runtest_setup(
        cast(pytest.Item, SimpleNamespace(get_closest_marker=lambda name: None))
    )
    image_conftest.pytest_runtest_setup(
        cast(pytest.Item, SimpleNamespace(get_closest_marker=lambda name: object()))
    )

    assert calls == ["resolved"]


@pytest.mark.unit
def test_exec_propagates_the_same_project_and_files(
    tmp_path: Path,
    compose_calls: tuple[
        list[tuple[list[str], float]],
        Callable[..., subprocess.CompletedProcess[str]],
    ],
) -> None:
    calls, _ = compose_calls
    context = _context(tmp_path)
    compose_exec = cast(Any, image_conftest.compose_exec).__wrapped__(context)

    compose_exec("api", "true", env={"CHECK": "one"}, timeout=11.0)

    prefix = [
        "docker",
        "compose",
        "-p",
        context.project,
        "-f",
        str(context.compose_files[0]),
    ]
    assert calls == [
        ([*prefix, "exec", "-T", "-e", "CHECK=one", "api", "true"], 11.0),
    ]


@pytest.mark.unit
def test_disposable_exec_and_state_use_exact_project(
    tmp_path: Path,
    compose_calls: tuple[
        list[tuple[list[str], float]],
        Callable[..., subprocess.CompletedProcess[str]],
    ],
) -> None:
    calls, _ = compose_calls
    context = _context(tmp_path, project="sentinel-smoke-isolated-abcd1234")
    stack = image_conftest.IsolatedComposeStack(context, tmp_path / "override.yml")

    stack.exec("worker", "python", "-V", timeout=12.0)
    stack.service_state("worker")

    assert calls == [
        ([*context.common_args, "exec", "-T", "worker", "python", "-V"], 12.0),
        (
            [
                *context.common_args,
                "ps",
                "--all",
                "--format",
                "{{.State}}|{{.ExitCode}}",
                "worker",
            ],
            10.0,
        ),
    ]


@pytest.mark.unit
def test_capture_diagnostics_includes_metadata_and_complete_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = _context(tmp_path, project="sentinel-smoke-disposable")
    calls: list[list[str]] = []
    results = [
        '{"State":"exited"}\n',
        "api-1 | 2026-09-07T12:00:00Z stopped\n",
        '{"Service":"migrate","ExitCode":1}\n',
        f'{{"ID":"{_IMAGE_ID}"}}\n',
    ]

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        assert kwargs["timeout"] == image_conftest._DIAGNOSTIC_TIMEOUT
        return subprocess.CompletedProcess(command, 0, results.pop(0), "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    sections = image_conftest._capture_compose_diagnostics("call", context)

    assert sections[0] == (
        "image smoke metadata (call)",
        f"project={context.project}\n"
        f"candidate_image_id={_IMAGE_ID}\n"
        "base_url=http://127.0.0.1:49152\n"
        f"compose_files={context.compose_files[0]}",
    )
    assert [title for title, _ in sections[1:]] == [
        f"compose ps --all (call, project={context.project})",
        f"compose logs (tail=100) (call, project={context.project})",
        f"migration exit evidence (call, project={context.project})",
        f"container image IDs (call, project={context.project})",
    ]
    assert calls[0][-4:] == ["ps", "--all", "--format", "json"]
    assert calls[1][-5:] == ["logs", "--timestamps", "--tail", "100", "--no-color"]
    assert calls[2][-5:] == ["ps", "--all", "--format", "json", "migrate"]
    assert calls[3][-3:] == ["images", "--format", "json"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "failure",
    [OSError("compose missing"), subprocess.TimeoutExpired(["compose"], 15.0)],
)
def test_capture_diagnostics_reports_failures_without_raising(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: BaseException
) -> None:
    context = _context(tmp_path)
    results: list[subprocess.CompletedProcess[str] | BaseException] = [
        subprocess.CompletedProcess([], 9, "", "state unavailable\n"),
        failure,
        subprocess.CompletedProcess([], 0, "", ""),
        subprocess.CompletedProcess([], 0, "", ""),
    ]

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        result = results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)

    sections = image_conftest._capture_compose_diagnostics("setup", context)

    assert sections[1][1] == "[diagnostic command exited 9]\nstate unavailable"
    assert sections[2][1].startswith("[diagnostic command failed:")
    assert sections[3][1] == "[no output]"


@pytest.mark.unit
@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
def test_report_hook_captures_primary_diagnostics_for_failed_image_phase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, phase: str
) -> None:
    context = _context(tmp_path)
    expected = [("state", "snapshot")]
    monkeypatch.setattr(image_conftest, "_resolve_image_smoke_context", lambda: context)
    monkeypatch.setattr(
        image_conftest,
        "_capture_compose_diagnostics",
        lambda current_phase, current_context: (
            expected if (current_phase, current_context) == (phase, context) else []
        ),
    )
    item = SimpleNamespace(
        get_closest_marker=lambda name: object(),
        stash=pytest.Stash(),
    )
    report = SimpleNamespace(when=phase, failed=True, sections=[])

    result = _run_report_hook(item, report)

    assert result is report
    assert report.sections == expected


@pytest.mark.unit
def test_report_hook_captures_disposable_project_diagnostics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    primary_context = _context(tmp_path, project="sentinel-smoke-primary")
    disposable_context = primary_context.for_project(
        "sentinel-smoke-primary-isolated-abcd1234"
    )
    stack = image_conftest.IsolatedComposeStack(
        disposable_context, tmp_path / "override.yml"
    )
    stack._brought_up = True
    captured_projects: list[str] = []

    def fake_capture(phase: str, context: Any) -> list[tuple[str, str]]:
        captured_projects.append(context.project)
        return [(context.project, phase)]

    monkeypatch.setattr(
        image_conftest, "_resolve_image_smoke_context", lambda: primary_context
    )
    monkeypatch.setattr(image_conftest, "_capture_compose_diagnostics", fake_capture)
    stash = pytest.Stash()
    stash[image_conftest._DISPOSABLE_STACKS] = [stack]
    item = SimpleNamespace(get_closest_marker=lambda name: object(), stash=stash)
    report = SimpleNamespace(when="call", failed=True, sections=[])

    _run_report_hook(item, report)

    assert captured_projects == [primary_context.project, disposable_context.project]
    assert report.sections == [
        (primary_context.project, "call"),
        (disposable_context.project, "call"),
    ]


@pytest.mark.unit
def test_report_hook_reuses_cached_disposable_teardown_diagnostics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    primary_context = _context(tmp_path, project="sentinel-smoke-primary")
    disposable_context = primary_context.for_project(
        "sentinel-smoke-primary-isolated-abcd1234"
    )
    stack = image_conftest.IsolatedComposeStack(
        disposable_context, tmp_path / "override.yml"
    )
    stack._brought_up = True
    stack._teardown_failure_diagnostics = [("cached teardown", "snapshot")]

    def fake_capture(phase: str, context: Any) -> list[tuple[str, str]]:
        assert context is primary_context
        return [("primary", phase)]

    monkeypatch.setattr(
        image_conftest, "_resolve_image_smoke_context", lambda: primary_context
    )
    monkeypatch.setattr(image_conftest, "_capture_compose_diagnostics", fake_capture)
    stash = pytest.Stash()
    stash[image_conftest._DISPOSABLE_STACKS] = [stack]
    item = SimpleNamespace(get_closest_marker=lambda name: object(), stash=stash)
    report = SimpleNamespace(when="teardown", failed=True, sections=[])

    _run_report_hook(item, report)

    assert report.sections == [
        ("primary", "teardown"),
        ("cached teardown", "snapshot"),
    ]


@pytest.mark.unit
def test_report_hook_diagnostic_failure_does_not_mask_primary_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = _context(tmp_path)
    monkeypatch.setattr(image_conftest, "_resolve_image_smoke_context", lambda: context)

    def fail_capture(phase: str, current_context: Any) -> list[tuple[str, str]]:
        raise OSError("diagnostics unavailable")

    monkeypatch.setattr(image_conftest, "_capture_compose_diagnostics", fail_capture)
    item = SimpleNamespace(
        get_closest_marker=lambda name: object(), stash=pytest.Stash()
    )
    report = SimpleNamespace(when="call", failed=True, sections=[])

    result = _run_report_hook(item, report)

    assert result is report
    assert report.failed
    assert report.sections[0][0] == "image smoke diagnostics unavailable (call)"
    assert "diagnostics unavailable" in report.sections[0][1]


@pytest.mark.unit
@pytest.mark.parametrize(("failed", "has_image_marker"), [(False, True), (True, False)])
def test_report_hook_skips_diagnostics_when_not_applicable(
    monkeypatch: pytest.MonkeyPatch, failed: bool, has_image_marker: bool
) -> None:
    monkeypatch.setattr(
        image_conftest,
        "_resolve_image_smoke_context",
        lambda: pytest.fail("unexpected context resolution"),
    )
    marker = object() if has_image_marker else None
    item = SimpleNamespace(get_closest_marker=lambda name: marker, stash=pytest.Stash())
    report = SimpleNamespace(when="call", failed=failed, sections=[])

    result = _run_report_hook(item, report)

    assert result is report
    assert report.sections == []


@pytest.mark.unit
def test_register_disposable_project_appends_one_complete_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    open_calls: list[tuple[Path, int]] = []
    writes: list[tuple[int, bytes]] = []

    def fake_open(path: Path, flags: int) -> int:
        open_calls.append((path, flags))
        return 17

    def fake_write(descriptor: int, value: bytes) -> int:
        writes.append((descriptor, value))
        return len(value)

    monkeypatch.setattr(os, "open", fake_open)
    monkeypatch.setattr(os, "write", fake_write)
    monkeypatch.setattr(os, "close", lambda descriptor: None)

    image_conftest._register_disposable_project(context, "sentinel-child-123")

    assert open_calls == [
        (
            context.project_registry,
            os.O_WRONLY | os.O_APPEND,
        )
    ]
    assert writes == [(17, b"sentinel-child-123\n")]


@pytest.mark.unit
def test_register_disposable_project_fails_on_partial_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    monkeypatch.setattr(os, "open", lambda path, flags: 17)
    monkeypatch.setattr(os, "write", lambda descriptor, value: len(value) - 1)
    monkeypatch.setattr(os, "close", lambda descriptor: None)

    with pytest.raises(pytest.UsageError, match="complete disposable project record"):
        image_conftest._register_disposable_project(context, "sentinel-child-123")


@pytest.mark.unit
def test_register_disposable_project_reports_registry_write_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    monkeypatch.setattr(
        os,
        "open",
        lambda path, flags: (_ for _ in ()).throw(OSError("permission denied")),
    )

    with pytest.raises(
        pytest.UsageError,
        match="cannot append disposable project to IMAGE_SMOKE_PROJECT_REGISTRY",
    ):
        image_conftest._register_disposable_project(context, "sentinel-child-123")


@pytest.mark.unit
def test_register_disposable_project_rejects_invalid_record(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)

    with pytest.raises(pytest.UsageError, match="disposable project must start"):
        image_conftest._register_disposable_project(context, "other\nproject")

    assert context.project_registry.read_text() == ""


@pytest.mark.unit
def test_register_disposable_project_appends_records_on_real_filesystem(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)

    image_conftest._register_disposable_project(context, "sentinel-child-one")
    image_conftest._register_disposable_project(context, "sentinel-child-two")

    assert context.project_registry.read_text().splitlines() == [
        "sentinel-child-one",
        "sentinel-child-two",
    ]


@pytest.mark.unit
def test_register_disposable_project_concurrent_appends_are_complete(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    projects = [f"sentinel-child-{index}" for index in range(32)]

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(
                lambda project: image_conftest._register_disposable_project(
                    context, project
                ),
                projects,
            )
        )

    records = context.project_registry.read_text().splitlines()
    assert len(records) == len(projects)
    assert len(set(records)) == len(projects)
    assert set(records) == set(projects)


@pytest.mark.unit
def test_isolated_fixture_derives_and_registers_project_before_yield(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, project="sentinel-smoke-primary")
    request = SimpleNamespace(node=SimpleNamespace(stash=pytest.Stash()))
    fixture = cast(Any, image_conftest.isolated_compose_stack).__wrapped__(
        tmp_path, request, context
    )

    stack = next(fixture)

    assert stack.project.startswith("sentinel-smoke-primary-isolated-")
    assert len(stack.project.removeprefix("sentinel-smoke-primary-isolated-")) == 8
    assert context.project_registry.read_text() == f"{stack.project}\n"
    assert request.node.stash[image_conftest._DISPOSABLE_STACKS] == [stack]
    with pytest.raises(StopIteration):
        next(fixture)


@pytest.mark.unit
def test_disposable_stack_uses_derived_project_and_candidate_safe_up(
    tmp_path: Path,
    compose_calls: tuple[
        list[tuple[list[str], float]],
        Callable[..., subprocess.CompletedProcess[str]],
    ],
) -> None:
    calls, _ = compose_calls
    primary_context = _context(tmp_path)
    context = primary_context.for_project(
        f"{primary_context.project}-isolated-abcd1234"
    )
    stack = image_conftest.IsolatedComposeStack(context, tmp_path / "override.yml")

    stack.up("services: {}\n", "worker")

    assert stack.project.startswith(f"{primary_context.project}-isolated-")
    assert calls == [
        (
            [
                *context.common_args,
                "-f",
                str(tmp_path / "override.yml"),
                "up",
                "-d",
                "--wait",
                "--no-build",
                "--pull",
                "never",
                "worker",
            ],
            90.0,
        )
    ]


@pytest.mark.unit
def test_disposable_teardown_failure_captures_own_diagnostics_and_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    compose_calls: tuple[
        list[tuple[list[str], float]],
        Callable[..., subprocess.CompletedProcess[str]],
    ],
) -> None:
    calls, queue_result = compose_calls
    context = _context(tmp_path, project="sentinel-smoke-isolated-abcd1234")
    stack = image_conftest.IsolatedComposeStack(context, tmp_path / "override.yml")
    stack._brought_up = True
    queue_result(1, stderr="volume busy")
    captured: list[tuple[str, str]] = []

    def fake_capture(phase: str, current_context: Any) -> list[tuple[str, str]]:
        captured.append((phase, current_context.project))
        return [("disposable state", "snapshot")]

    monkeypatch.setattr(image_conftest, "_capture_compose_diagnostics", fake_capture)

    with pytest.raises(pytest.fail.Exception, match="teardown failed with exit 1"):
        stack.teardown()

    assert calls[0] == (
        [*context.common_args, "down", "-v", "--remove-orphans"],
        60.0,
    )
    assert captured == [("teardown", context.project)]
    assert stack.teardown_failure_diagnostics == [("disposable state", "snapshot")]


@pytest.mark.unit
def test_disposable_teardown_is_inert_before_up(
    tmp_path: Path,
    compose_calls: tuple[
        list[tuple[list[str], float]],
        Callable[..., subprocess.CompletedProcess[str]],
    ],
) -> None:
    calls, _ = compose_calls
    stack = image_conftest.IsolatedComposeStack(
        _context(tmp_path), tmp_path / "override.yml"
    )

    stack.teardown()

    assert calls == []
