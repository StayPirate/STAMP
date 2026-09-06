"""Regression tests for the shared release-SBOM gate script."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "sbom-gate.sh"

DOCKER_STUB = """\
#!/usr/bin/env bash
set -euo pipefail
echo "$*" >> "${DOCKER_STUB_LOG}"
if [[ "${1:-}" == "save" ]]; then
    if [[ "${DOCKER_FAIL_ON:-}" == "save" ]]; then exit 7; fi
    output="${3:-}"
    touch "${output}"
    exit 0
fi
if [[ "${1:-}" == "run" ]]; then
    if [[ "${*}" == *anchore/syft* && "${DOCKER_FAIL_ON:-}" == "syft" ]]; then
        exit 8
    fi
    if [[ "${*}" == *cyclonedx-cli* && "${DOCKER_FAIL_ON:-}" == "validator" ]]; then
        exit 9
    fi
    exit 0
fi
exit 1
"""

UV_STUB = """\
#!/usr/bin/env bash
set -euo pipefail
echo "$*" >> "${UV_STUB_LOG}"
exit "${UV_STUB_EXIT:-0}"
"""


def _install_stub(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run_gate(
    tmp_path: Path, *, fail_on: str = "", semantic_exit: str = "0"
) -> tuple[subprocess.CompletedProcess[str], list[str], list[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _install_stub(bin_dir / "docker", DOCKER_STUB)
    _install_stub(bin_dir / "uv", UV_STUB)
    docker_log = tmp_path / "docker.log"
    uv_log = tmp_path / "uv.log"
    output = tmp_path / "candidate.cdx.json"
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DOCKER_STUB_LOG": str(docker_log),
        "UV_STUB_LOG": str(uv_log),
        "DOCKER_FAIL_ON": fail_on,
        "UV_STUB_EXIT": semantic_exit,
    }
    result = subprocess.run(
        [
            str(SCRIPT_PATH),
            "sentinel:test",
            str(output),
            "ghcr.io/example/sentinel",
            "1.2.3",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    docker_calls = docker_log.read_text(encoding="utf-8").splitlines()
    uv_calls = (
        uv_log.read_text(encoding="utf-8").splitlines() if uv_log.exists() else []
    )
    return result, docker_calls, uv_calls, output


@pytest.mark.unit
def test_sbom_gate_runs_pinned_tools_and_semantic_validator(tmp_path: Path) -> None:
    result, docker_calls, uv_calls, output = _run_gate(tmp_path)

    assert result.returncode == 0
    assert docker_calls[0] == f"save --output {output}.image.tar sentinel:test"
    assert "anchore/syft:v1.51.1@sha256:" in docker_calls[1]
    assert "--source-name ghcr.io/example/sentinel" in docker_calls[1]
    assert "--source-version 1.2.3" in docker_calls[1]
    assert "cyclonedx-json@1.5=/output/candidate.cdx.json" in docker_calls[1]
    assert "cyclonedx/cyclonedx-cli:0.33.1@sha256:" in docker_calls[2]
    assert "--input-version v1_5 --fail-on-errors" in docker_calls[2]
    assert len(uv_calls) == 1
    assert "scripts/validate_release_sbom.py" in uv_calls[0]
    assert "--expected-subject ghcr.io/example/sentinel" in uv_calls[0]
    assert not Path(f"{output}.image.tar").exists()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("fail_on", "semantic_exit", "expected_calls"),
    [("save", "0", 1), ("syft", "0", 2), ("validator", "0", 3)],
)
def test_sbom_gate_stops_on_tool_failure(
    tmp_path: Path, fail_on: str, semantic_exit: str, expected_calls: int
) -> None:
    result, docker_calls, uv_calls, output = _run_gate(
        tmp_path, fail_on=fail_on, semantic_exit=semantic_exit
    )

    assert result.returncode != 0
    assert len(docker_calls) == expected_calls
    assert uv_calls == []
    assert not Path(f"{output}.image.tar").exists()


@pytest.mark.unit
def test_sbom_gate_propagates_semantic_validator_failure(tmp_path: Path) -> None:
    result, docker_calls, uv_calls, output = _run_gate(tmp_path, semantic_exit="12")

    assert result.returncode == 12
    assert len(docker_calls) == 3
    assert len(uv_calls) == 1
    assert not Path(f"{output}.image.tar").exists()


@pytest.mark.unit
def test_sbom_gate_rejects_wrong_argument_count() -> None:
    result = subprocess.run(
        [str(SCRIPT_PATH)], capture_output=True, text=True, check=False
    )

    assert result.returncode == 2
    assert "Usage:" in result.stderr
