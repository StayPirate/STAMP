#!/usr/bin/env python3
"""Validate pull request title and issue-linkage metadata."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TITLE_PATTERN = re.compile(
    r"(?:feat|fix|docs|refactor|test|chore|ci)"
    r"(?:\([a-z0-9](?:[a-z0-9 _-]*[a-z0-9])?\))?!?: "
    r"\S(?:.*\S)?"
)
ISSUE_LINK_PATTERN = re.compile(r"Closes #([1-9][0-9]*)")
BRANCH_PATTERN = re.compile(
    r"(?:feature|fix|docs|refactor|chore|ci|test)/([1-9][0-9]*)-[a-z0-9][a-z0-9-]*"
)
EXEMPT_BRANCH_PATTERN = re.compile(
    r"(?:feature|fix|docs|refactor|chore|ci|test)/[a-z][a-z0-9-]*"
)
RELEASE_BRANCH_PATTERN = re.compile(
    r"release-please--branches--master--components--sentinel"
)
MAX_TITLE_LENGTH = 71
MAX_EXEMPT_FILES = 3
MAX_EXEMPT_LINES = 50
RELEASE_PLEASE_AUTHOR = "StayPirate"


@dataclass(frozen=True)
class PullRequestMetadata:
    """Pull request fields consumed by the validator."""

    title: str
    body: str
    author: str
    head_ref: str
    head_repository: str
    base_repository: str
    labels: frozenset[str]
    changed_files: int
    additions: int
    deletions: int


def _is_automated_pull_request(metadata: PullRequestMetadata) -> bool:
    """Return whether approved automation exclusively owns the pull request."""
    same_repository = metadata.head_repository == metadata.base_repository
    is_dependabot = (
        metadata.author == "dependabot[bot]"
        and metadata.head_ref.startswith("dependabot/")
        and same_repository
    )
    is_release_please = (
        metadata.author == RELEASE_PLEASE_AUTHOR
        and RELEASE_BRANCH_PATTERN.fullmatch(metadata.head_ref) is not None
        and "autorelease: pending" in metadata.labels
        and same_repository
    )
    return is_dependabot or is_release_please


def _extract_issue_linkage(body: str) -> list[str]:
    """Return Issue linkage field values outside fenced code blocks."""
    values: list[str] = []
    fence_character: str | None = None
    fence_length = 0
    in_html_comment = False
    for line in body.splitlines():
        stripped = line.strip()
        fence_match = re.fullmatch(r"(`{3,}|~{3,})(.*)", stripped)
        if fence_match is not None:
            marker, suffix = fence_match.groups()
            if fence_character is None:
                fence_character = marker[0]
                fence_length = len(marker)
            elif (
                marker[0] == fence_character
                and len(marker) >= fence_length
                and not suffix.strip()
            ):
                fence_character = None
                fence_length = 0
            continue
        if fence_character is not None:
            continue
        if in_html_comment:
            if "-->" in stripped:
                in_html_comment = False
            continue
        if "<!--" in stripped:
            if "-->" not in stripped.split("<!--", maxsplit=1)[1]:
                in_html_comment = True
            continue
        if fence_character is None and line.startswith("- Issue linkage:"):
            values.append(line.removeprefix("- Issue linkage:").strip())
    return values


def validate_metadata(metadata: PullRequestMetadata) -> list[str]:
    """Return validation errors for pull request metadata."""
    errors: list[str] = []

    if len(metadata.title) > MAX_TITLE_LENGTH:
        errors.append(
            f"PR title is {len(metadata.title)} characters; maximum is "
            f"{MAX_TITLE_LENGTH}."
        )

    if TITLE_PATTERN.fullmatch(metadata.title) is None:
        errors.append(
            "PR title must follow Conventional Commits: type[(scope)][!]: description."
        )

    if _is_automated_pull_request(metadata):
        return errors

    linkage_values = _extract_issue_linkage(metadata.body)
    if len(linkage_values) != 1:
        errors.append(
            "PR body must contain exactly one Issue linkage field outside code fences."
        )
        return errors

    linkage = linkage_values[0]
    issue_match = ISSUE_LINK_PATTERN.fullmatch(linkage)
    if issue_match is not None:
        branch_match = BRANCH_PATTERN.fullmatch(metadata.head_ref)
        if branch_match is None:
            errors.append(
                "Issue-linked PR branches must match <prefix>/<issue-number>-<slug>."
            )
        elif branch_match.group(1) != issue_match.group(1):
            errors.append("Branch and Issue linkage numbers must match.")
        return errors

    if linkage.startswith("N/A - ") and linkage.removeprefix("N/A - ").strip():
        if EXEMPT_BRANCH_PATTERN.fullmatch(metadata.head_ref) is None:
            errors.append(
                "Exempt PR branches must match <prefix>/<slug> without an "
                "issue number requirement."
            )
        changed_lines = metadata.additions + metadata.deletions
        if (
            metadata.changed_files > MAX_EXEMPT_FILES
            or changed_lines > MAX_EXEMPT_LINES
        ):
            errors.append(
                "Issue-link exemption exceeds the cosmetic size limit of "
                f"{MAX_EXEMPT_FILES} files and {MAX_EXEMPT_LINES} changed lines."
            )
        return errors

    errors.append("Issue linkage must be 'Closes #N' or 'N/A - <specific reason>'.")
    return errors


def _load_event(event_path: Path) -> PullRequestMetadata:
    """Load consumed pull request fields from a GitHub event payload."""
    event: dict[str, Any] = json.loads(event_path.read_text(encoding="utf-8"))
    pull_request = event["pull_request"]
    head_repository = pull_request["head"].get("repo") or {}
    return PullRequestMetadata(
        title=pull_request["title"],
        body=pull_request.get("body") or "",
        author=pull_request["user"]["login"],
        head_ref=pull_request["head"]["ref"],
        head_repository=head_repository.get("full_name", ""),
        base_repository=pull_request["base"]["repo"]["full_name"],
        labels=frozenset(label["name"] for label in pull_request["labels"]),
        changed_files=pull_request["changed_files"],
        additions=pull_request["additions"],
        deletions=pull_request["deletions"],
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-path", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    """Validate event metadata and emit GitHub Actions annotations."""
    metadata = _load_event(_parse_args().event_path)
    errors = validate_metadata(metadata)
    for error in errors:
        print(f"::error::{error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
