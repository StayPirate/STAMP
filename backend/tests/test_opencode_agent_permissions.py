"""Structural tests for OpenCode reviewer command permissions."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / ".opencode" / "agents"
COMMANDS_DIR = REPO_ROOT / ".opencode" / "commands"

READ_ONLY_BASELINE = (
    ("*", "deny"),
    ("git status", "allow"),
    ("git status *", "allow"),
    ("git diff", "allow"),
    ("git diff *", "allow"),
    ("git log", "allow"),
    ("git log *", "allow"),
    ("git show", "allow"),
    ("git show *", "allow"),
    ("git grep *", "allow"),
    ("git blame *", "allow"),
    ("git rev-parse *", "allow"),
    ("git merge-base *", "allow"),
    ("git ls-files", "allow"),
    ("git ls-files *", "allow"),
    ("git ls-tree *", "allow"),
    ("git describe", "allow"),
    ("git describe *", "allow"),
    ("git cat-file *", "allow"),
    ("gh issue view *", "allow"),
    ("gh issue list", "allow"),
    ("gh issue list *", "allow"),
    ("gh pr view", "allow"),
    ("gh pr view *", "allow"),
    ("gh pr list", "allow"),
    ("gh pr list *", "allow"),
    ("gh pr diff", "allow"),
    ("gh pr diff *", "allow"),
    ("gh pr checks", "allow"),
    ("gh pr checks *", "allow"),
    ("gh repo view", "allow"),
    ("gh repo view *", "allow"),
    ("gh project view *", "allow"),
    ("gh project list", "allow"),
    ("gh project list *", "allow"),
    ("gh project item-list *", "allow"),
    ("gh run view", "allow"),
    ("gh run view *", "allow"),
    ("gh run list", "allow"),
    ("gh run list *", "allow"),
    ("glab issue view *", "allow"),
    ("glab issue list", "allow"),
    ("glab issue list *", "allow"),
    ("glab mr view", "allow"),
    ("glab mr view *", "allow"),
    ("glab mr list", "allow"),
    ("glab mr list *", "allow"),
    ("glab mr diff", "allow"),
    ("glab mr diff *", "allow"),
    ("glab repo view", "allow"),
    ("glab repo view *", "allow"),
    ("glab ci get", "allow"),
    ("glab ci get *", "allow"),
    ("glab ci list", "allow"),
    ("glab ci list *", "allow"),
    ("glab ci trace", "allow"),
    ("glab ci trace *", "allow"),
)

CLI_PARITY = (
    ("gh issue view *", "glab issue view *"),
    ("gh issue list *", "glab issue list *"),
    ("gh pr view *", "glab mr view *"),
    ("gh pr list *", "glab mr list *"),
    ("gh pr diff *", "glab mr diff *"),
    ("gh repo view *", "glab repo view *"),
    ("gh run view *", "glab ci get *"),
    ("gh run list *", "glab ci list *"),
)

MUTATING_PREFIXES = (
    "git add",
    "git commit",
    "git push",
    "gh api",
    "gh issue create",
    "gh issue edit",
    "gh pr create",
    "gh pr edit",
    "gh pr merge",
    "glab api",
    "glab issue create",
    "glab issue update",
    "glab mr create",
    "glab mr merge",
)

ROLE_EXTENSIONS = {
    "cicd-reviewer": (
        ("actionlint", "allow"),
        ("actionlint *", "allow"),
        ("shellcheck *", "allow"),
        ("shfmt -d *", "allow"),
        ("uv run pytest", "allow"),
        ("uv run pytest *", "allow"),
    ),
    "external-contract-verifier": (
        ("curl *", "allow"),
        ("secbox osc *", "allow"),
        ("git clone *", "allow"),
        ("git ls-remote *", "allow"),
    ),
    "test-reviewer": (
        ("uv run pytest", "allow"),
        ("uv run pytest *", "allow"),
    ),
}

_BASH_RULE_RE = re.compile(r'^    "([^"]+)": (allow|ask|deny)$')


def _frontmatter(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    _, frontmatter, _ = text.split("---", 2)
    return frontmatter.splitlines()


def _bash_rules(frontmatter: list[str]) -> tuple[tuple[str, str], ...]:
    start = frontmatter.index("  bash:") + 1
    rules: list[tuple[str, str]] = []
    for line in frontmatter[start:]:
        match = _BASH_RULE_RE.fullmatch(line)
        if not match:
            break
        rules.append((match.group(1), match.group(2)))
    return tuple(rules)


@pytest.mark.unit
def test_reviewer_permissions_match_shared_baseline() -> None:
    agent_paths = sorted(AGENTS_DIR.glob("*.md"))
    assert agent_paths, "No OpenCode reviewer definitions found"

    errors: list[str] = []
    baseline_patterns = {pattern for pattern, _ in READ_ONLY_BASELINE}
    for gh_pattern, glab_pattern in CLI_PARITY:
        if (gh_pattern in baseline_patterns) != (glab_pattern in baseline_patterns):
            errors.append(f"baseline CLI parity differs: {gh_pattern} / {glab_pattern}")
    allowed_patterns = baseline_patterns | {
        pattern
        for extensions in ROLE_EXTENSIONS.values()
        for pattern, action in extensions
        if action == "allow"
    }
    for pattern in allowed_patterns:
        if pattern.startswith(MUTATING_PREFIXES):
            errors.append(f"reviewer permissions permit a mutating command: {pattern}")

    for path in agent_paths:
        frontmatter = _frontmatter(path)
        if "  edit: deny" not in frontmatter:
            errors.append(f"{path.name}: edit permission must be deny")

        expected = READ_ONLY_BASELINE + ROLE_EXTENSIONS.get(path.stem, ())
        actual = _bash_rules(frontmatter)
        if actual != expected:
            errors.append(
                f"{path.name}: bash rules differ from the shared baseline "
                "and permitted role extensions"
            )

    assert not errors, "OpenCode reviewer permission drift:\n" + "\n".join(errors)


@pytest.mark.unit
def test_opencode_command_definitions_are_direct_children() -> None:
    nested_commands = sorted(
        path.relative_to(COMMANDS_DIR)
        for path in COMMANDS_DIR.glob("**/*.md")
        if path.parent != COMMANDS_DIR
    )

    assert not nested_commands, (
        "Nested Markdown files are registered as unintended OpenCode commands:\n"
        + "\n".join(str(path) for path in nested_commands)
    )
