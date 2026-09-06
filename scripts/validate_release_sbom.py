"""Validate Sentinel-specific invariants in a CycloneDX release SBOM."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import unquote

SERIAL_NUMBER_PATTERN = re.compile(
    r"^urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}$"
)
PACKAGE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


class SbomValidationError(ValueError):
    """Raised when a release SBOM violates a Sentinel invariant."""


def _normalize_python_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared_dependency_names(pyproject: dict[str, Any]) -> tuple[set[str], set[str]]:
    runtime_entries = pyproject.get("project", {}).get("dependencies", [])
    dev_entries = pyproject.get("dependency-groups", {}).get("dev", [])

    def names(entries: object, group: str) -> set[str]:
        if not isinstance(entries, list) or not all(
            isinstance(entry, str) for entry in entries
        ):
            raise SbomValidationError(
                f"pyproject {group} dependencies must be a list of strings"
            )

        result: set[str] = set()
        for entry in entries:
            match = PACKAGE_NAME_PATTERN.match(entry)
            if match is None:
                raise SbomValidationError(
                    f"cannot parse dependency name from {group} entry: {entry!r}"
                )
            result.add(_normalize_python_name(match.group(0)))
        return result

    runtime = names(runtime_entries, "runtime")
    dev_only = names(dev_entries, "development") - runtime
    return runtime, dev_only


def _pypi_component_names(components: list[object]) -> set[str]:
    names: set[str] = set()
    for component in components:
        if not isinstance(component, dict):
            continue
        purl = component.get("purl")
        if not isinstance(purl, str) or not purl.startswith("pkg:pypi/"):
            continue
        encoded_name = purl.removeprefix("pkg:pypi/").split("@", 1)[0]
        names.add(_normalize_python_name(unquote(encoded_name)))
    return names


def validate_release_sbom(
    sbom: dict[str, Any],
    pyproject: dict[str, Any],
    *,
    expected_version: str,
    expected_subject: str,
) -> None:
    """Validate format, subject, dependency coverage, and runtime scope."""
    if sbom.get("bomFormat") != "CycloneDX":
        raise SbomValidationError("SBOM format must be CycloneDX")
    if sbom.get("specVersion") != expected_version:
        raise SbomValidationError(
            f"SBOM specVersion must be {expected_version}, got "
            f"{sbom.get('specVersion')!r}"
        )

    serial_number = sbom.get("serialNumber")
    if not isinstance(serial_number, str) or not SERIAL_NUMBER_PATTERN.fullmatch(
        serial_number
    ):
        raise SbomValidationError("SBOM serialNumber must be a lowercase RFC 4122 URN")

    metadata = sbom.get("metadata")
    subject = metadata.get("component") if isinstance(metadata, dict) else None
    if not isinstance(subject, dict) or subject.get("type") != "container":
        raise SbomValidationError("SBOM metadata must describe a container subject")
    if subject.get("name") != expected_subject:
        raise SbomValidationError(
            f"SBOM subject must be {expected_subject!r}, got {subject.get('name')!r}"
        )

    components = sbom.get("components")
    if not isinstance(components, list) or not components:
        raise SbomValidationError("SBOM components must be a non-empty list")
    dependencies = sbom.get("dependencies")
    if not isinstance(dependencies, list) or not dependencies:
        raise SbomValidationError("SBOM dependencies must be a non-empty list")

    runtime_names, dev_only_names = _declared_dependency_names(pyproject)
    pypi_names = _pypi_component_names(components)

    missing_runtime = sorted(runtime_names - pypi_names)
    if missing_runtime:
        raise SbomValidationError(
            "SBOM is missing direct runtime Python dependencies: "
            + ", ".join(missing_runtime)
        )

    included_dev = sorted(dev_only_names & pypi_names)
    if included_dev:
        raise SbomValidationError(
            "SBOM includes development-only Python dependencies: "
            + ", ".join(included_dev)
        )

    has_debian_package = any(
        isinstance(component, dict)
        and isinstance(component.get("purl"), str)
        and component["purl"].startswith("pkg:deb/debian/")
        for component in components
    )
    if not has_debian_package:
        raise SbomValidationError("SBOM contains no Debian package components")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sbom", type=Path)
    parser.add_argument("pyproject", type=Path)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-subject", required=True)
    args = parser.parse_args()

    with args.sbom.open(encoding="utf-8") as stream:
        sbom = json.load(stream)
    with args.pyproject.open("rb") as stream:
        pyproject = tomllib.load(stream)

    if not isinstance(sbom, dict):
        raise SbomValidationError("SBOM root must be a JSON object")
    validate_release_sbom(
        sbom,
        pyproject,
        expected_version=args.expected_version,
        expected_subject=args.expected_subject,
    )
    print(f"Validated release SBOM: {args.sbom}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
