"""Tests for Sentinel's release-SBOM semantic validator."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from typing import Any, cast

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "validate_release_sbom.py"
)
SCRIPT = runpy.run_path(str(SCRIPT_PATH), run_name="validate_release_sbom")
SbomValidationError = cast(type[ValueError], SCRIPT["SbomValidationError"])
main = cast(Any, SCRIPT["main"])
validate_release_sbom = cast(Any, SCRIPT["validate_release_sbom"])


def _pyproject() -> dict[str, Any]:
    return {
        "project": {"dependencies": ["FastAPI>=1", "PyJWT[crypto]>=2"]},
        "dependency-groups": {"dev": ["pytest>=8", "httpx>=1", "fastapi>=1"]},
    }


def _sbom() -> dict[str, Any]:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "serialNumber": "urn:uuid:12345678-1234-1234-1234-123456789abc",
        "metadata": {"component": {"type": "container", "name": "sentinel"}},
        "components": [
            {"type": "library", "name": "fastapi", "purl": "pkg:pypi/fastapi@1"},
            {"type": "library", "name": "PyJWT", "purl": "pkg:pypi/pyjwt@2"},
            {
                "type": "library",
                "name": "base-files",
                "purl": "pkg:deb/debian/base-files@1",
            },
        ],
        "dependencies": [{"ref": "sentinel", "dependsOn": ["fastapi"]}],
    }


def _validate(sbom: dict[str, Any], pyproject: dict[str, Any] | None = None) -> None:
    validate_release_sbom(
        sbom,
        pyproject or _pyproject(),
        expected_version="1.7",
        expected_subject="sentinel",
    )


@pytest.mark.unit
def test_validate_release_sbom_accepts_runtime_inventory() -> None:
    _validate(_sbom())


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("bomFormat", "SPDX", "format must be CycloneDX"),
        ("specVersion", "1.5", "specVersion must be 1.7"),
        ("serialNumber", None, "serialNumber must be"),
        ("serialNumber", "not-a-uuid", "serialNumber must be"),
    ],
)
def test_validate_release_sbom_rejects_invalid_attestation_fields(
    field: str, value: object, message: str
) -> None:
    sbom = _sbom()
    sbom[field] = value

    with pytest.raises(SbomValidationError, match=message):
        _validate(sbom)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("metadata", {}, "metadata must describe a container subject"),
        ("components", [], "components must be a non-empty list"),
        ("dependencies", [], "dependencies must be a non-empty list"),
    ],
)
def test_validate_release_sbom_rejects_missing_inventory_structure(
    field: str, value: object, message: str
) -> None:
    sbom = _sbom()
    sbom[field] = value

    with pytest.raises(SbomValidationError, match=message):
        _validate(sbom)


@pytest.mark.unit
def test_validate_release_sbom_rejects_unparseable_dependency() -> None:
    pyproject = _pyproject()
    pyproject["project"]["dependencies"] = ["@invalid"]

    with pytest.raises(SbomValidationError, match="cannot parse dependency name"):
        _validate(_sbom(), pyproject)


@pytest.mark.unit
def test_validate_release_sbom_rejects_wrong_subject_name() -> None:
    sbom = _sbom()
    sbom["metadata"]["component"]["name"] = "another-image"

    with pytest.raises(SbomValidationError, match="SBOM subject must be"):
        _validate(sbom)


@pytest.mark.unit
def test_validate_release_sbom_rejects_non_container_subject() -> None:
    sbom = _sbom()
    sbom["metadata"]["component"]["type"] = "application"

    with pytest.raises(SbomValidationError, match="describe a container subject"):
        _validate(sbom)


@pytest.mark.unit
def test_validate_release_sbom_rejects_missing_runtime_dependency() -> None:
    sbom = _sbom()
    sbom["components"] = [
        component
        for component in sbom["components"]
        if not component["purl"].startswith("pkg:pypi/pyjwt@")
    ]

    with pytest.raises(SbomValidationError, match="pyjwt"):
        _validate(sbom)


@pytest.mark.unit
def test_validate_release_sbom_rejects_development_dependency() -> None:
    sbom = _sbom()
    sbom["components"].append(
        {"type": "library", "name": "pytest", "purl": "pkg:pypi/pytest@8"}
    )

    with pytest.raises(SbomValidationError, match=r"development-only.*pytest"):
        _validate(sbom)


@pytest.mark.unit
def test_validate_release_sbom_rejects_missing_debian_inventory() -> None:
    sbom = _sbom()
    sbom["components"] = [
        component
        for component in sbom["components"]
        if not component["purl"].startswith("pkg:deb/debian/")
    ]

    with pytest.raises(SbomValidationError, match="no Debian package"):
        _validate(sbom)


@pytest.mark.unit
def test_main_reads_files_and_reports_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sbom_path = tmp_path / "sbom.json"
    pyproject_path = tmp_path / "pyproject.toml"
    sbom_path.write_text(json.dumps(_sbom()), encoding="utf-8")
    pyproject_path.write_text(
        '[project]\ndependencies = ["FastAPI>=1", "PyJWT[crypto]>=2"]\n'
        '[dependency-groups]\ndev = ["pytest>=8", "httpx>=1"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_release_sbom",
            str(sbom_path),
            str(pyproject_path),
            "--expected-version",
            "1.7",
            "--expected-subject",
            "sentinel",
        ],
    )

    assert main() == 0
    assert "Validated release SBOM" in capsys.readouterr().out


@pytest.mark.unit
def test_main_rejects_non_object_json_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sbom_path = tmp_path / "sbom.json"
    pyproject_path = tmp_path / "pyproject.toml"
    sbom_path.write_text("[]", encoding="utf-8")
    pyproject_path.write_text(
        "[project]\ndependencies = []\n[dependency-groups]\ndev = []\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_release_sbom",
            str(sbom_path),
            str(pyproject_path),
            "--expected-version",
            "1.7",
            "--expected-subject",
            "sentinel",
        ],
    )

    with pytest.raises(SbomValidationError, match="root must be a JSON object"):
        main()
