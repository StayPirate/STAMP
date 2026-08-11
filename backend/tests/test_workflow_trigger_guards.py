"""Regression tests for the complete workflow_run privileged-trigger guard.

`workflow_run.branches` matches the *head* branch of the triggering CI
run, not the base branch it targeted (see the GitHub Actions
`workflow_run` event documentation, "Limiting your workflow to run based
on branches"). A CI run produced by a `pull_request` event whose head
branch happens to be named "master" (e.g. a fork's default branch) would
otherwise pass the `branches: [master]` filter declared in
`build-images.yml` and `release-please.yml`, letting an unreviewed commit
be built and published with `packages: write` (`build-images.yml`) or
processed with `contents: write` (`release-please.yml`). See issue #96.

Both workflows require the COMPLETE guard in their job-level `if:`
condition:

1. `github.event.workflow_run.event == 'push'` — the triggering CI run
   was itself caused by a `push` (never a `pull_request`).
2. `github.event.workflow_run.conclusion == 'success'` — the triggering
   CI run actually passed; a failed or cancelled CI run on master must
   not trigger an image build or a release.

Either condition alone is insufficient: a `push`-triggered CI run that
failed must not publish an image or cut a release, and a successful
`pull_request`-triggered run must not either. A plain substring check
for both guard fragments would still pass if they were combined with
`||` instead of `&&` (a real regression that weakens the guard while
keeping both fragments present in the text). This module instead
extracts the `if:` condition and evaluates it as a boolean expression
against synthetic GitHub context values, proving the actual AND
relationship — a push-triggered, successful run is allowed, but every
other combination (wrong event, failed conclusion, or both) is
rejected. See issue #185.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


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


def _evaluate_condition(
    condition: str,
    *,
    event_name: str,
    workflow_run_event: str,
    workflow_run_conclusion: str,
) -> bool:
    """Evaluate a GitHub Actions job `if:` expression as a Python boolean
    expression, substituting the three context values this module cares
    about with literal strings.

    Only handles the operators actually used by these two workflows'
    guard conditions (`==`, `!=`, `&&`, `||`, parentheses) — not a
    general GitHub Actions expression evaluator. The substituted values
    come exclusively from this test module's own parametrization (never
    from the workflow file's dynamic content), so building a Python
    expression string and evaluating it is safe. The expression's
    *structure* (operators, parentheses) does come from the workflow
    file's text, so `__builtins__` is stripped from the eval globals as
    a defense-in-depth measure — this expression never needs to call
    any builtin.
    """
    expr = condition.strip()
    if expr.startswith("if:"):
        expr = expr[len("if:") :].strip()
    if expr.startswith(">"):
        expr = expr[1:].strip()
    expr = expr.replace("github.event_name", repr(event_name))
    expr = expr.replace("github.event.workflow_run.event", repr(workflow_run_event))
    expr = expr.replace(
        "github.event.workflow_run.conclusion", repr(workflow_run_conclusion)
    )
    expr = expr.replace("&&", " and ").replace("||", " or ")
    return bool(eval(expr, {"__builtins__": {}}))


@pytest.mark.unit
class TestBuildImagesGuard:
    """`build-images.yml` accepts a `push` trigger unconditionally (tag
    push path) and a `workflow_run` trigger only when it was itself
    caused by a `push` event that concluded successfully."""

    def _condition(self) -> str:
        workflow_path = REPO_ROOT / ".github" / "workflows" / "build-images.yml"
        return _job_if_condition(workflow_path, "name: Build Backend Image")

    def test_tag_push_always_allowed(self) -> None:
        # event_name='push' takes the `github.event_name != 'workflow_run'`
        # branch — workflow_run fields are irrelevant for this path.
        assert _evaluate_condition(
            self._condition(),
            event_name="push",
            workflow_run_event="push",
            workflow_run_conclusion="success",
        )

    def test_push_triggered_successful_ci_run_allowed(self) -> None:
        assert _evaluate_condition(
            self._condition(),
            event_name="workflow_run",
            workflow_run_event="push",
            workflow_run_conclusion="success",
        )

    def test_pull_request_triggered_ci_run_rejected(self) -> None:
        assert not _evaluate_condition(
            self._condition(),
            event_name="workflow_run",
            workflow_run_event="pull_request",
            workflow_run_conclusion="success",
        )

    def test_failed_ci_run_rejected(self) -> None:
        assert not _evaluate_condition(
            self._condition(),
            event_name="workflow_run",
            workflow_run_event="push",
            workflow_run_conclusion="failure",
        )

    def test_pull_request_and_failed_ci_run_rejected(self) -> None:
        assert not _evaluate_condition(
            self._condition(),
            event_name="workflow_run",
            workflow_run_event="pull_request",
            workflow_run_conclusion="failure",
        )


@pytest.mark.unit
class TestReleasePleaseGuard:
    """`release-please.yml` has no tag-push path — a `workflow_run`
    trigger is accepted only when it was itself caused by a `push` event
    that concluded successfully."""

    def _condition(self) -> str:
        workflow_path = REPO_ROOT / ".github" / "workflows" / "release-please.yml"
        return _job_if_condition(workflow_path, "  release-please:")

    def test_push_triggered_successful_ci_run_allowed(self) -> None:
        assert _evaluate_condition(
            self._condition(),
            event_name="workflow_run",
            workflow_run_event="push",
            workflow_run_conclusion="success",
        )

    def test_pull_request_triggered_ci_run_rejected(self) -> None:
        assert not _evaluate_condition(
            self._condition(),
            event_name="workflow_run",
            workflow_run_event="pull_request",
            workflow_run_conclusion="success",
        )

    def test_failed_ci_run_rejected(self) -> None:
        assert not _evaluate_condition(
            self._condition(),
            event_name="workflow_run",
            workflow_run_event="push",
            workflow_run_conclusion="failure",
        )

    def test_pull_request_and_failed_ci_run_rejected(self) -> None:
        assert not _evaluate_condition(
            self._condition(),
            event_name="workflow_run",
            workflow_run_event="pull_request",
            workflow_run_conclusion="failure",
        )
