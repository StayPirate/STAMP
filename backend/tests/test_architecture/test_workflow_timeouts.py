"""Structural test verifying every GitHub Actions job declares a timeout.

GitHub's default job timeout is 360 minutes. Without an explicit
`timeout-minutes`, a hung step (e.g. a stuck `docker build` or a
`compose up --wait` that never becomes healthy) can occupy a runner —
and its `concurrency` group — for up to six hours before GitHub kills
it. See issue #69.

This test parses workflow YAML with a minimal indentation-based scanner
rather than a full YAML parser, to avoid depending on PyYAML, which is
only a transitive dependency of `uvicorn[standard]` and is not declared
directly in `pyproject.toml`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

WORKFLOWS_DIR = Path(__file__).resolve().parents[3] / ".github" / "workflows"


def _job_bodies(workflow_text: str) -> dict[str, list[str]]:
    """Split a workflow file's `jobs:` block into per-job line groups.

    Returns a mapping of job id -> every line belonging to that job's
    body (excluding the job id line itself), up to the next sibling job
    or the end of the `jobs:` block.
    """
    lines = workflow_text.splitlines()
    try:
        jobs_index = next(
            index for index, line in enumerate(lines) if line.rstrip() == "jobs:"
        )
    except StopIteration:
        return {}

    jobs: dict[str, list[str]] = {}
    current_job: str | None = None
    for line in lines[jobs_index + 1 :]:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            # Dedent past the jobs: block entirely (a new top-level key).
            break
        if indent == 2 and line.rstrip().endswith(":"):
            current_job = line.strip().rstrip(":")
            jobs[current_job] = []
            continue
        if current_job is not None:
            jobs[current_job].append(line)

    return jobs


def _workflow_files() -> list[Path]:
    return sorted(WORKFLOWS_DIR.glob("*.yml"))


@pytest.mark.unit
@pytest.mark.parametrize("workflow_path", _workflow_files(), ids=lambda p: p.name)
def test_every_job_declares_timeout_minutes(workflow_path: Path) -> None:
    text = workflow_path.read_text(encoding="utf-8")
    jobs = _job_bodies(text)

    assert jobs, f"{workflow_path.name}: no jobs found (parser or file issue)"

    missing = [
        job_name
        for job_name, body_lines in jobs.items()
        if not any(line.strip().startswith("timeout-minutes:") for line in body_lines)
    ]

    assert not missing, (
        f"{workflow_path.name}: job(s) missing 'timeout-minutes': {missing}"
    )
