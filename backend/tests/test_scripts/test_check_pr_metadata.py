from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "check-pr-metadata.py"
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "pr-metadata.yml"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_pr_metadata", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SCRIPT = _load_script()


def _metadata(**overrides: object) -> object:
    values: dict[str, object] = {
        "title": "ci: enforce pull request metadata",
        "body": "- Issue linkage: Closes #29",
        "author": "jdoe",
        "head_ref": "ci/29-issue-first-workflow",
        "head_repository": "StayPirate/Sentinel",
        "base_repository": "StayPirate/Sentinel",
        "labels": frozenset(),
        "changed_files": 2,
        "additions": 20,
        "deletions": 10,
    }
    values.update(overrides)
    return SCRIPT.PullRequestMetadata(**values)


@pytest.mark.unit
@pytest.mark.parametrize(
    "title",
    [
        "ci: enforce pull request metadata",
        "docs(workflow): clarify issue policy",
        "feat(auth)!: replace token format",
        "fix!: replace response envelope",
        f"ci: {'x' * 67}",
    ],
)
def test_validate_metadata_valid_human_pr_returns_no_errors(title: str) -> None:
    assert SCRIPT.validate_metadata(_metadata(title=title)) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("title", "expected_error"),
    [
        ("Update workflow", "Conventional Commits"),
        (f"ci: {'x' * 68}", "maximum is 71"),
        ("ci:    ", "Conventional Commits"),
        ("ci( ): update workflow", "Conventional Commits"),
        ("CI: update workflow", "Conventional Commits"),
        ("ci:update workflow", "Conventional Commits"),
        ("ci: update\nworkflow", "Conventional Commits"),
    ],
)
def test_validate_metadata_invalid_title_returns_exact_error(
    title: str, expected_error: str
) -> None:
    errors = SCRIPT.validate_metadata(_metadata(title=title))

    assert len(errors) == 1
    assert expected_error in errors[0]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("body", "expected_error"),
    [
        ("", "exactly one"),
        ("- Issue linkage:", "must be 'Closes #N'"),
        ("- Issue linkage: N/A", "must be 'Closes #N'"),
        ("- Issue linkage: Closes #0", "must be 'Closes #N'"),
        ("- Issue linkage: Closes #029", "must be 'Closes #N'"),
        (
            "- Issue linkage: Closes #29\n- Issue linkage: Closes #30",
            "exactly one",
        ),
        (
            "```markdown\n- Issue linkage: Closes #29\n```",
            "exactly one",
        ),
        (
            "````markdown\n```\n- Issue linkage: Closes #29\n````",
            "exactly one",
        ),
        (
            "~~~~markdown\n- Issue linkage: Closes #29\n~~~~",
            "exactly one",
        ),
        (
            "<!--\n- Issue linkage: Closes #29\n-->",
            "exactly one",
        ),
    ],
)
def test_validate_metadata_invalid_linkage_returns_error(
    body: str, expected_error: str
) -> None:
    errors = SCRIPT.validate_metadata(_metadata(body=body))

    assert any(expected_error in error for error in errors)


@pytest.mark.unit
def test_validate_metadata_issue_number_must_match_branch() -> None:
    errors = SCRIPT.validate_metadata(_metadata(body="- Issue linkage: Closes #30"))

    assert errors == ["Branch and Issue linkage numbers must match."]


@pytest.mark.unit
def test_validate_metadata_ignores_fenced_decoy_and_accepts_real_field() -> None:
    body = "```markdown\n- Issue linkage: Closes #30\n```\n- Issue linkage: Closes #29"

    assert SCRIPT.validate_metadata(_metadata(body=body)) == []


@pytest.mark.unit
def test_validate_metadata_fenced_html_comment_does_not_hide_real_field() -> None:
    body = "```markdown\n<!--\n```\n- Issue linkage: Closes #29"

    assert SCRIPT.validate_metadata(_metadata(body=body)) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "head_ref",
    ["ci/issue-first-workflow", "other/29-issue-first-workflow"],
)
def test_validate_metadata_issue_link_requires_numbered_conventional_branch(
    head_ref: str,
) -> None:
    errors = SCRIPT.validate_metadata(_metadata(head_ref=head_ref))

    assert errors == [
        "Issue-linked PR branches must match <prefix>/<issue-number>-<slug>."
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "prefix",
    ["feature", "fix", "docs", "refactor", "chore", "ci", "test"],
)
def test_validate_metadata_accepts_every_numbered_branch_prefix(prefix: str) -> None:
    assert (
        SCRIPT.validate_metadata(_metadata(head_ref=f"{prefix}/29-metadata-policy"))
        == []
    )


@pytest.mark.unit
def test_validate_metadata_small_human_exemption_returns_no_errors() -> None:
    metadata = _metadata(
        body="- Issue linkage: N/A - typo-only documentation correction",
        head_ref="docs/correct-workflow-typo",
        changed_files=3,
        additions=25,
        deletions=25,
    )

    assert SCRIPT.validate_metadata(metadata) == []


@pytest.mark.unit
def test_validate_metadata_exemption_rejects_numbered_branch() -> None:
    metadata = _metadata(
        body="- Issue linkage: N/A - cosmetic correction",
        head_ref="docs/29-cosmetic-correction",
    )

    assert SCRIPT.validate_metadata(metadata) == [
        "Exempt PR branches must match <prefix>/<slug> without an "
        "issue number requirement."
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "prefix",
    ["feature", "fix", "docs", "refactor", "chore", "ci", "test"],
)
def test_validate_metadata_accepts_every_exempt_branch_prefix(prefix: str) -> None:
    metadata = _metadata(
        body="- Issue linkage: N/A - cosmetic correction",
        head_ref=f"{prefix}/cosmetic-correction",
    )

    assert SCRIPT.validate_metadata(metadata) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("changed_files", "additions", "deletions"),
    [(4, 1, 1), (1, 51, 0), (1, 25, 26)],
)
def test_validate_metadata_large_human_exemption_returns_error(
    changed_files: int, additions: int, deletions: int
) -> None:
    metadata = _metadata(
        body="- Issue linkage: N/A - cosmetic correction",
        head_ref="docs/cosmetic-correction",
        changed_files=changed_files,
        additions=additions,
        deletions=deletions,
    )

    errors = SCRIPT.validate_metadata(metadata)

    assert any("cosmetic size limit" in error for error in errors)


@pytest.mark.unit
@pytest.mark.parametrize(
    "metadata",
    [
        _metadata(
            author="dependabot[bot]",
            head_ref="dependabot/pip/backend/httpx-1.0",
            body="",
        ),
        _metadata(
            author="StayPirate",
            head_ref="release-please--branches--master--components--sentinel",
            body="",
            labels=frozenset({"autorelease: pending"}),
        ),
    ],
)
def test_validate_metadata_approved_automation_does_not_require_link(
    metadata: object,
) -> None:
    assert SCRIPT.validate_metadata(metadata) == []


@pytest.mark.unit
def test_validate_metadata_automation_still_requires_valid_title() -> None:
    metadata = _metadata(
        title="Invalid title",
        author="dependabot[bot]",
        head_ref="dependabot/pip/backend/httpx-1.0",
        body="",
    )

    assert SCRIPT.validate_metadata(metadata) == [
        "PR title must follow Conventional Commits: type[(scope)][!]: description."
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "metadata",
    [
        _metadata(
            author="jdoe",
            head_ref="dependabot/pip/backend/httpx-1.0",
            body="",
        ),
        _metadata(
            author="dependabot[bot]",
            head_ref="ci/dependency-update",
            body="",
        ),
        _metadata(
            author="jdoe",
            head_ref="release-please--branches--master--components--sentinel",
            body="",
            labels=frozenset({"autorelease: pending"}),
        ),
        _metadata(
            author="StayPirate",
            head_ref="release-please--branches--master--components--sentinel",
            body="",
        ),
        _metadata(
            author="StayPirate",
            head_ref="ci/release-please-test",
            body="",
            labels=frozenset({"autorelease: pending"}),
        ),
        _metadata(
            author="dependabot[bot]",
            head_ref="dependabot/pip/backend/httpx-1.0",
            head_repository="fork/Sentinel",
            body="",
        ),
        _metadata(
            author="StayPirate",
            head_ref="release-please--branches--master--components--sentinel",
            head_repository="fork/Sentinel",
            body="",
            labels=frozenset({"autorelease: pending"}),
        ),
    ],
)
def test_validate_metadata_spoofed_automation_requires_link(
    metadata: object,
) -> None:
    errors = SCRIPT.validate_metadata(metadata)

    assert errors == [
        "PR body must contain exactly one Issue linkage field outside code fences."
    ]


@pytest.mark.unit
def test_main_invalid_event_emits_annotations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "title": "Invalid title",
                    "body": "",
                    "user": {"login": "jdoe"},
                    "head": {
                        "ref": "ci/invalid-title",
                        "repo": {"full_name": "StayPirate/Sentinel"},
                    },
                    "base": {"repo": {"full_name": "StayPirate/Sentinel"}},
                    "labels": [],
                    "changed_files": 1,
                    "additions": 1,
                    "deletions": 0,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["check-pr-metadata", "--event-path", str(event_path)],
    )

    assert SCRIPT.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("::error::") == 2


@pytest.mark.unit
def test_main_valid_event_emits_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "title": "ci: enforce pull request metadata",
                    "body": "- Issue linkage: Closes #29",
                    "user": {"login": "jdoe"},
                    "head": {
                        "ref": "ci/29-issue-first-workflow",
                        "repo": {"full_name": "StayPirate/Sentinel"},
                    },
                    "base": {"repo": {"full_name": "StayPirate/Sentinel"}},
                    "labels": [],
                    "changed_files": 2,
                    "additions": 20,
                    "deletions": 10,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["check-pr-metadata", "--event-path", str(event_path)],
    )

    assert SCRIPT.main() == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.unit
def test_workflow_uses_trusted_base_validator_and_revalidates_labels() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "pull_request_target:" in workflow
    assert "labeled" in workflow
    assert "unlabeled" in workflow
    assert "edited" in workflow
    assert "synchronize" in workflow
    assert "branches: [master]" in workflow
    assert "ref: ${{ github.event.pull_request.base.sha }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "python scripts/check-pr-metadata.py --event-path" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert workflow.count("actions/checkout@") == 1
    assert "pull_request.head.sha" not in workflow
