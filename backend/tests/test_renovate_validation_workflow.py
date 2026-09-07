"""Regression tests for the advisory Renovate validation workflow."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "renovate-validation.yml"


@pytest.mark.unit
def test_renovate_validation_workflow_is_read_only_and_advisory() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "types: [opened, synchronize, reopened]" in workflow
    assert "workflow_dispatch:" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "continue-on-error" not in workflow
    assert "--dry-run=lookup" in workflow
    assert "--platform=local" in workflow
    assert "--onboarding=false" in workflow
    assert "--enabled=true" in workflow
    assert '-v "${GITHUB_WORKSPACE}:/workspace:ro"' in workflow
    assert "-e RENOVATE_GITHUB_COM_TOKEN" in workflow
    assert "RENOVATE_GITHUB_COM_TOKEN: ${{ github.token }}" in workflow
    assert "renovate-config-validator --strict --no-global renovate.jsonc" in workflow


@pytest.mark.unit
def test_renovate_validation_workflow_pins_official_image() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    image_match = re.search(
        r"RENOVATE_IMAGE:\s+renovate/renovate:(\d+\.\d+\.\d+)@"
        r"(sha256:[0-9a-f]{64})",
        workflow,
    )

    assert image_match is not None
    assert image_match.group(1) == "44.65.5"


@pytest.mark.unit
def test_renovate_validation_workflow_does_not_create_changes() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "git push" not in workflow
    assert "--platform=github" not in workflow
    assert "--platform=local" in workflow
    assert "--dry-run=lookup" in workflow
