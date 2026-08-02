"""Regression tests for the Python-version drift check in ci.yml.

The `backend-lint` job's "Verify Dockerfile and pyproject.toml Python
version match .python-version" step fails the build when
`backend/Dockerfile`'s `ARG PYTHON_VERSION` or `backend/pyproject.toml`'s
`requires-python` disagree with `backend/.python-version`
(`docs/conventions.md`, Runtime Version -> Source of Truth). See issue
#69, Acceptance Criteria: "The Python-version drift check fails when
`requires-python` and `.python-version` disagree."

This test extracts the exact shell script from the step and executes it
against fixture files in an isolated temporary directory (never against
this repository's real `backend/` files), verifying both the match and
each mismatch case.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"

STEP_NAME_MARKER = (
    "name: Verify Dockerfile and pyproject.toml Python version match .python-version"
)


def _drift_check_script() -> str:
    """Extract the shell script executed by the version drift check step."""
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


def _run_drift_check(
    tmp_path: Path,
    *,
    python_version: str = "3.13",
    dockerfile_version: str = "3.13",
    requires_python: str = "3.13",
) -> subprocess.CompletedProcess[str]:
    (tmp_path / ".python-version").write_text(f"{python_version}\n", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text(
        f"ARG PYTHON_VERSION={dockerfile_version}\n"
        "FROM python:${PYTHON_VERSION}-slim\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nrequires-python = ">={requires_python}"\n',
        encoding="utf-8",
    )

    return subprocess.run(
        ["bash", "-c", _drift_check_script()],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.unit
def test_drift_check_passes_when_all_versions_match(tmp_path: Path) -> None:
    result = _run_drift_check(tmp_path)

    assert result.returncode == 0
    assert result.stdout == ""


@pytest.mark.unit
def test_drift_check_fails_on_dockerfile_mismatch(tmp_path: Path) -> None:
    result = _run_drift_check(tmp_path, dockerfile_version="3.12")

    assert result.returncode == 1
    assert "Dockerfile ARG PYTHON_VERSION=3.12 does not match" in result.stdout


@pytest.mark.unit
def test_drift_check_fails_on_requires_python_mismatch(tmp_path: Path) -> None:
    result = _run_drift_check(tmp_path, requires_python="3.14")

    assert result.returncode == 1
    assert "pyproject.toml requires-python=>=3.14 does not match" in result.stdout


@pytest.mark.unit
def test_drift_check_reports_dockerfile_mismatch_before_requires_python(
    tmp_path: Path,
) -> None:
    # When both files disagree, the Dockerfile check runs first and
    # exits immediately — the requires-python check is never reached.
    result = _run_drift_check(
        tmp_path, dockerfile_version="3.12", requires_python="3.14"
    )

    assert result.returncode == 1
    assert "Dockerfile ARG PYTHON_VERSION=3.12 does not match" in result.stdout
    assert "requires-python" not in result.stdout
