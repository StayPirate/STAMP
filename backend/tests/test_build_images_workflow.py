"""Regression tests for the image push step in the build-images workflow.

Prior to this fix, a failed intermediate `docker push` inside the tag
loop was masked: the step's exit code was that of the *last* loop
iteration, so a transient failure on an earlier tag went undetected as
long as the final iteration succeeded. See issue #69.

This test extracts the exact shell script from the "Push tested image
(same digest)" step and executes it against a stub `docker` binary that
can be made to fail on a specific tag, verifying the step now aborts
immediately (fail-fast) instead of continuing past the failure.
"""

from __future__ import annotations

import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "build-images.yml"
)

STEP_NAME_MARKER = "name: Push tested image (same digest)"

DOCKER_STUB = """\
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "tag" ]]; then
    exit 0
fi
if [[ "${1:-}" == "push" ]]; then
    tag="${2:-}"
    echo "push ${tag}" >> "${DOCKER_STUB_LOG}"
    if [[ "${tag}" == *fail* ]]; then
        exit 1
    fi
    exit 0
fi
exit 0
"""


def _push_loop_script() -> str:
    """Extract the shell script executed by the image push step."""
    lines = WORKFLOW_PATH.read_text(encoding="utf-8").splitlines()
    marker_index = next(
        index for index, line in enumerate(lines) if STEP_NAME_MARKER in line
    )
    run_index = next(
        index
        for index in range(marker_index, len(lines))
        if lines[index].strip() == "run: |"
    )
    run_indent = len(lines[run_index]) - len(lines[run_index].lstrip())
    script_lines: list[str] = []

    for line in lines[run_index + 1 :]:
        indentation = len(line) - len(line.lstrip())
        if line.strip() and indentation <= run_indent:
            break
        script_lines.append(line)

    return textwrap.dedent("\n".join(script_lines))


def _install_docker_stub(bin_dir: Path) -> None:
    stub_path = bin_dir / "docker"
    stub_path.write_text(DOCKER_STUB, encoding="utf-8")
    mode = stub_path.stat().st_mode
    stub_path.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run_push_loop(
    tmp_path: Path, *, image_tags: str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _install_docker_stub(bin_dir)
    log_file = tmp_path / "docker-stub.log"
    log_file.write_text("", encoding="utf-8")

    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SMOKE_IMAGE": "sentinel-backend:smoke",
        "IMAGE_TAGS": image_tags,
        "DOCKER_STUB_LOG": str(log_file),
    }
    result = subprocess.run(
        ["bash", "-c", _push_loop_script()],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, log_file


@pytest.mark.unit
def test_push_loop_aborts_on_first_failing_push(tmp_path: Path) -> None:
    tags = "ghcr.io/example/sentinel:fail\nghcr.io/example/sentinel:should-not-push"

    result, log_file = _run_push_loop(tmp_path, image_tags=tags)

    assert result.returncode != 0
    pushed = log_file.read_text(encoding="utf-8").splitlines()
    assert pushed == ["push ghcr.io/example/sentinel:fail"]


@pytest.mark.unit
def test_push_loop_pushes_all_tags_when_all_succeed(tmp_path: Path) -> None:
    tags = (
        "ghcr.io/example/sentinel:master\n"
        "ghcr.io/example/sentinel:1.2.3\n"
        "ghcr.io/example/sentinel:1.2"
    )

    result, log_file = _run_push_loop(tmp_path, image_tags=tags)

    assert result.returncode == 0
    pushed = log_file.read_text(encoding="utf-8").splitlines()
    assert pushed == [
        "push ghcr.io/example/sentinel:master",
        "push ghcr.io/example/sentinel:1.2.3",
        "push ghcr.io/example/sentinel:1.2",
    ]


@pytest.mark.unit
def test_push_loop_skips_blank_tag_lines(tmp_path: Path) -> None:
    tags = "ghcr.io/example/sentinel:master\n\nghcr.io/example/sentinel:1.2.3"

    result, log_file = _run_push_loop(tmp_path, image_tags=tags)

    assert result.returncode == 0
    pushed = log_file.read_text(encoding="utf-8").splitlines()
    assert pushed == [
        "push ghcr.io/example/sentinel:master",
        "push ghcr.io/example/sentinel:1.2.3",
    ]
