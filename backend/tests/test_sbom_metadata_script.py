"""Tests for release-SBOM filename and version selection."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "select-sbom-metadata.sh"
)


def _run_selector(
    tmp_path: Path, *, is_release: str, ref_name: str = "v1.2.3"
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    output = tmp_path / "github-output"
    env = os.environ | {
        "IS_RELEASE": is_release,
        "GITHUB_REF_NAME": ref_name,
        "SOURCE_SHA": "a" * 40,
        "GITHUB_RUN_ID": "12345",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(output),
    }
    result = subprocess.run(
        [str(SCRIPT_PATH)], env=env, capture_output=True, text=True, check=False
    )
    values = (
        dict(line.split("=", 1) for line in output.read_text().splitlines())
        if output.exists()
        else {}
    )
    return result, values


@pytest.mark.unit
def test_selector_removes_v_prefix_from_release_asset_name(tmp_path: Path) -> None:
    result, values = _run_selector(tmp_path, is_release="true")

    assert result.returncode == 0
    assert values == {
        "version": "1.2.3",
        "file_name": "sentinel-1.2.3.sbom.cdx.json",
        "file_path": str(tmp_path / "sentinel-1.2.3.sbom.cdx.json"),
        "artifact_name": "sentinel-sbom-12345",
    }


@pytest.mark.unit
def test_selector_uses_master_candidate_name_for_non_release(tmp_path: Path) -> None:
    result, values = _run_selector(tmp_path, is_release="false")

    assert result.returncode == 0
    assert values["version"] == "a" * 40
    assert values["file_name"] == "sentinel-master.sbom.cdx.json"


@pytest.mark.unit
@pytest.mark.parametrize("ref_name", ["v1.2", "v1.2.3.4", 'v1.2.3";id'])
def test_selector_rejects_non_semver_release_tag(tmp_path: Path, ref_name: str) -> None:
    result, values = _run_selector(tmp_path, is_release="true", ref_name=ref_name)

    assert result.returncode != 0
    assert values == {}
    assert "Release tag must have the form" in result.stderr
