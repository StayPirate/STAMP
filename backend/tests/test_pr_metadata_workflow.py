"""Regression tests for the pull request metadata workflow."""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "pr-metadata.yml"
)


def _workflow_script() -> str:
    """Extract the shell script executed by the metadata workflow."""
    lines = WORKFLOW_PATH.read_text(encoding="utf-8").splitlines()
    run_index = next(
        index for index, line in enumerate(lines) if line.strip() == "run: |"
    )
    run_indent = len(lines[run_index]) - len(lines[run_index].lstrip())
    script_lines: list[str] = []

    for line in lines[run_index + 1 :]:
        indentation = len(line) - len(line.lstrip())
        if line.strip() and indentation <= run_indent:
            break
        script_lines.append(line)

    return textwrap.dedent("\n".join(script_lines))


def _validate_metadata(
    *,
    title: str = "ci: adopt issue-first GitHub workflow",
    body: str = "- Issue linkage: Closes #29",
    author: str = "jdoe",
    head_ref: str = "ci/issue-first-workflow",
) -> subprocess.CompletedProcess[str]:
    """Execute the workflow validator with representative event metadata."""
    env = os.environ | {
        "PR_TITLE": title,
        "PR_BODY": body,
        "PR_AUTHOR": author,
        "PR_HEAD_REF": head_ref,
    }
    return subprocess.run(
        ["bash", "-c", _workflow_script()],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.unit
def test_pr_metadata_valid_title_and_issue_linkage_pass() -> None:
    result = _validate_metadata()

    assert result.returncode == 0
    assert result.stderr == ""


@pytest.mark.unit
def test_pr_metadata_invalid_title_fails() -> None:
    result = _validate_metadata(title="Adopt issue-first GitHub workflow")

    assert result.returncode == 1
    assert "does not follow Conventional Commits format" in result.stdout


@pytest.mark.unit
@pytest.mark.parametrize(
    ("title", "expected_returncode"),
    [
        (f"ci: {'x' * 67}", 0),
        (f"ci: {'x' * 68}", 1),
    ],
)
def test_pr_metadata_title_length_boundary(
    title: str, expected_returncode: int
) -> None:
    result = _validate_metadata(title=title)

    assert result.returncode == expected_returncode


@pytest.mark.unit
def test_pr_metadata_missing_issue_linkage_fails() -> None:
    result = _validate_metadata(body="No issue linkage")

    assert result.returncode == 1
    assert "must contain exactly one issue linkage" in result.stdout


@pytest.mark.unit
def test_pr_metadata_duplicate_issue_linkage_fails() -> None:
    result = _validate_metadata(
        body="- Issue linkage: Closes #29\n- Issue linkage: Closes #30"
    )

    assert result.returncode == 1
    assert "at most one '- Issue linkage:' field" in result.stdout


@pytest.mark.unit
def test_pr_metadata_standalone_closes_line_passes() -> None:
    result = _validate_metadata(body="## Summary\nSome free-form body.\n\nCloses #42")

    assert result.returncode == 0
    assert result.stderr == ""


@pytest.mark.unit
def test_pr_metadata_standalone_na_is_rejected() -> None:
    result = _validate_metadata(body="## Summary\nN/A - cosmetic fix\n")

    assert result.returncode == 1
    assert "must contain exactly one issue linkage" in result.stdout


@pytest.mark.unit
def test_pr_metadata_duplicate_standalone_closes_fails() -> None:
    result = _validate_metadata(body="Closes #29\nCloses #30")

    assert result.returncode == 1
    assert "must contain exactly one issue linkage" in result.stdout


@pytest.mark.unit
def test_pr_metadata_inline_closes_mention_is_not_matched() -> None:
    result = _validate_metadata(
        body="This eventually closes #29 once merged. See also Closes #30 text."
    )

    assert result.returncode == 1
    assert "must contain exactly one issue linkage" in result.stdout


@pytest.mark.unit
def test_pr_metadata_template_format_takes_precedence_over_standalone() -> None:
    # A template field with an invalid value must fail even if a valid
    # standalone `Closes #N` line is also present elsewhere in the body.
    result = _validate_metadata(body="- Issue linkage: not-a-valid-value\n\nCloses #42")

    assert result.returncode == 1
    assert "Issue linkage must be 'Closes #N' or 'N/A - <specific reason>'" in (
        result.stdout
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("linkage", "expected_returncode"),
    [
        ("N/A - spelling correction", 0),
        ("N/A", 1),
        ("N/A -    ", 1),
    ],
)
def test_pr_metadata_human_exemption_requires_specific_reason(
    linkage: str, expected_returncode: int
) -> None:
    result = _validate_metadata(body=f"- Issue linkage: {linkage}")

    assert result.returncode == expected_returncode


@pytest.mark.unit
@pytest.mark.parametrize(
    ("author", "head_ref"),
    [
        ("renovate[bot]", "renovate/pip-httpx-1.x"),
        ("github-actions[bot]", "release-please--branches--master"),
    ],
)
def test_pr_metadata_approved_automation_does_not_require_issue_linkage(
    author: str, head_ref: str
) -> None:
    result = _validate_metadata(body="", author=author, head_ref=head_ref)

    assert result.returncode == 0


@pytest.mark.unit
def test_pr_metadata_automation_with_invalid_title_still_fails() -> None:
    result = _validate_metadata(
        title="Invalid automated title",
        body="",
        author="renovate[bot]",
        head_ref="renovate/pip-httpx-1.x",
    )

    assert result.returncode == 1
    assert "does not follow Conventional Commits format" in result.stdout
