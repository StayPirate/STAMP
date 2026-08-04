"""Regression tests for the workflow_run head-branch guard.

`workflow_run.branches` matches the *head* branch of the triggering CI
run, not the base branch it targeted (see the GitHub Actions
`workflow_run` event documentation, "Limiting your workflow to run based
on branches"). A CI run produced by a `pull_request` event whose head
branch happens to be named "master" (e.g. a fork's default branch) would
otherwise pass the `branches: [master]` filter declared in
`build-images.yml` and `release-please.yml`, letting an unreviewed commit
be built and published with `packages: write` (`build-images.yml`) or
processed with `contents: write` (`release-please.yml`). See issue #96.

Both workflows now additionally require
`github.event.workflow_run.event == 'push'` in their job-level `if:`
condition. This test extracts that condition as plain text and asserts
the guard is present, so that removing it is caught immediately rather
than silently regressing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_GUARD = "github.event.workflow_run.event == 'push'"


def _job_if_condition(workflow_path: Path, job_name_marker: str) -> str:
    """Extract the job-level `if:` condition block as a single string."""
    lines = workflow_path.read_text(encoding="utf-8").splitlines()
    marker_index = next(
        index for index, line in enumerate(lines) if job_name_marker in line
    )
    if_index = next(
        index
        for index in range(marker_index, len(lines))
        if lines[index].strip().startswith("if:")
    )
    if_indent = len(lines[if_index]) - len(lines[if_index].lstrip())
    condition_lines = [lines[if_index].strip()]

    for line in lines[if_index + 1 :]:
        if not line.strip():
            break
        indentation = len(line) - len(line.lstrip())
        if indentation <= if_indent:
            break
        condition_lines.append(line.strip())

    return " ".join(condition_lines)


@pytest.mark.unit
def test_build_images_guards_against_non_push_workflow_run() -> None:
    workflow_path = REPO_ROOT / ".github" / "workflows" / "build-images.yml"
    condition = _job_if_condition(workflow_path, "name: Build Backend Image")

    assert REQUIRED_GUARD in condition


@pytest.mark.unit
def test_release_please_guards_against_non_push_workflow_run() -> None:
    workflow_path = REPO_ROOT / ".github" / "workflows" / "release-please.yml"
    condition = _job_if_condition(workflow_path, "  release-please:")

    assert REQUIRED_GUARD in condition
