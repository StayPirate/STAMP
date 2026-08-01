"""Structural tests verifying relative Markdown link integrity across
tracked documentation files.

See `docs/features/platform/testing-strategy.md` (Structural Tests)
for scope and governing principle. This test only detects and reports
broken links — resolving a broken link (fixing the path, creating the
missing file, or removing the reference) is a judgement call left to
whoever introduces or reviews the change; the test does not attempt to
guess the correct resolution.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Matches `[text](target)` but not a literal code-quoted example of link
# syntax such as `` `[spec-name](path/to/spec.md#anchor)` `` (see
# AGENTS.md, Endpoint Permission Map maintenance) — those illustrate the
# link format itself and are not meant to resolve to a real file.
_LINK_RE = re.compile(r"(?<!`)\[[^\]]*\]\(([^)]+)\)(?!`)")


def _tracked_markdown_files() -> list[Path]:
    """Every `.md` file tracked by git, repository-wide."""
    result = subprocess.run(
        ["git", "ls-files", "*.md"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [REPO_ROOT / line for line in result.stdout.splitlines() if line]


def _is_out_of_scope(target: str) -> bool:
    """`http(s)://` and `mailto:` links and anchor-only links (`#section`)
    are out of scope — see the Structural Tests section of the testing
    strategy.
    """
    return not target or target.startswith(("http://", "https://", "mailto:", "#"))


def _iter_broken_links() -> list[str]:
    broken: list[str] = []
    for md_file in _tracked_markdown_files():
        content = md_file.read_text(encoding="utf-8")
        for match in _LINK_RE.finditer(content):
            target = match.group(1)
            if _is_out_of_scope(target):
                continue
            path_part = target.split("#", 1)[0]
            resolved = (md_file.parent / path_part).resolve()
            if not resolved.exists():
                line_number = content.count("\n", 0, match.start()) + 1
                broken.append(
                    f"{md_file.relative_to(REPO_ROOT)}:{line_number}: "
                    f"link target '{target}' does not resolve to an "
                    f"existing file or directory (resolved: {resolved})"
                )
    return broken


@pytest.mark.unit
class TestDocumentationLinkIntegrity:
    """Every relative Markdown link in a tracked `.md` file must resolve
    to an existing file or directory.
    """

    def test_no_broken_relative_links(self) -> None:
        broken = _iter_broken_links()
        assert not broken, "Broken documentation links found:\n" + "\n".join(broken)
