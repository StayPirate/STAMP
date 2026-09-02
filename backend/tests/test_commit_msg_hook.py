"""Regression tests for the repository commit-message hook."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

HOOK_PATH = Path(__file__).resolve().parents[2] / ".githooks" / "commit-msg"


def _validate_commit_message(
    tmp_path: Path, message: str
) -> subprocess.CompletedProcess[str]:
    """Execute the real commit-msg hook against a temporary message file."""
    message_path = tmp_path / "COMMIT_EDITMSG"
    message_path.write_text(message, encoding="utf-8")
    return subprocess.run(
        ["bash", str(HOOK_PATH), str(message_path)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "commit_type", ["feat", "fix", "docs", "refactor", "test", "chore", "ci"]
)
def test_commit_msg_hook_accepts_ordinary_approved_types(
    tmp_path: Path, commit_type: str
) -> None:
    result = _validate_commit_message(
        tmp_path, f"{commit_type}: update policy fixture\n"
    )

    assert result.returncode == 0


@pytest.mark.unit
@pytest.mark.parametrize(
    "subject",
    [
        "feat!: replace public contract",
        "feat(api)!: replace public contract",
        "fix!: correct incompatible behavior",
        "fix(api)!: correct incompatible behavior",
    ],
)
def test_commit_msg_hook_accepts_feat_and_fix_breaking_titles(
    tmp_path: Path, subject: str
) -> None:
    result = _validate_commit_message(tmp_path, f"{subject}\n")

    assert result.returncode == 0


@pytest.mark.unit
@pytest.mark.parametrize("commit_type", ["docs", "refactor", "test", "chore", "ci"])
def test_commit_msg_hook_rejects_non_release_breaking_titles(
    tmp_path: Path, commit_type: str
) -> None:
    result = _validate_commit_message(
        tmp_path, f"{commit_type}!: replace public contract\n"
    )

    assert result.returncode == 1
    assert "does not follow Conventional Commits format" in result.stderr
