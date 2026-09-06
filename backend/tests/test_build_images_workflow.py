"""Regression tests for the build-images workflow's build/smoke/publish
pipeline (`.github/workflows/build-images.yml`).

Covers two related invariants:

1. **Fail-fast push loop**: prior to a fix, a failed intermediate
   `docker push` inside the tag loop was masked — the step's exit code
   was that of the *last* loop iteration, so a transient failure on an
   earlier tag went undetected as long as the final iteration
   succeeded. See issue #69.
2. **Single build, tested artifact published unchanged**: the image is
   built exactly once (`push: false`, `load: true`), the blocking smoke
   gate runs against that exact local artifact, and only if it passes
   is the SAME local image re-tagged and pushed — never rebuilt. See
   issue #185 (this hardens the structural proof that the published
   digest is the tested one, rather than relying only on human review
   of the workflow comments).
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "build-images.yml"
)

STEP_NAME_MARKER = "name: Push tested image (same digest)"

# Logs both `docker tag <source> <target>` and `docker push <target>`
# invocations (space-separated, one call per line) so tests can assert
# on the tag *source* image, not just which tags were pushed.
DOCKER_STUB = """\
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "tag" ]]; then
    echo "tag ${2:-} ${3:-}" >> "${DOCKER_STUB_LOG}"
    exit 0
fi
if [[ "${1:-}" == "push" ]]; then
    tag="${2:-}"
    echo "push ${tag}" >> "${DOCKER_STUB_LOG}"
    if [[ "${tag}" == *fail* ]]; then
        exit 1
    fi
    exit 0
fi
if [[ "${1:-}" == "buildx" && "${2:-}" == "imagetools" && "${3:-}" == "inspect" ]]; then
    if [[ "${4:-}" == *malformed* ]]; then
        echo '{"digest":"not-a-digest"}'
        exit 0
    fi
    if [[ "${4:-}" == *different* ]]; then
        digest="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    else
        digest="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    fi
    printf '{"digest":"sha256:%s"}\n' "${digest}"
    exit 0
fi
exit 0
"""


def _push_loop_script() -> str:
    """Return the standalone script invoked by the image push step."""
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert STEP_NAME_MARKER in workflow
    assert "run: ./scripts/publish-image.sh" in workflow
    return (
        Path(__file__).resolve().parents[2] / "scripts" / "publish-image.sh"
    ).read_text(encoding="utf-8")


def _install_docker_stub(bin_dir: Path) -> None:
    stub_path = bin_dir / "docker"
    stub_path.write_text(DOCKER_STUB, encoding="utf-8")
    mode = stub_path.stat().st_mode
    stub_path.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run_push_loop(
    tmp_path: Path, *, image_tags: str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _install_docker_stub(bin_dir)
    log_file = tmp_path / "docker-stub.log"
    log_file.write_text("", encoding="utf-8")

    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SMOKE_IMAGE": "sentinel-backend:smoke",
        "IMAGE_TAGS": image_tags,
        "DOCKER_STUB_LOG": str(log_file),
        "GITHUB_OUTPUT": str(tmp_path / "github-output"),
    }
    result = subprocess.run(
        ["bash", "-c", _push_loop_script()],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, log_file


def _pushed_tags(log_file: Path) -> list[str]:
    entries = log_file.read_text(encoding="utf-8").splitlines()
    return [
        entry.removeprefix("push ") for entry in entries if entry.startswith("push ")
    ]


def _tag_invocations(log_file: Path) -> list[str]:
    entries = log_file.read_text(encoding="utf-8").splitlines()
    return [entry.removeprefix("tag ") for entry in entries if entry.startswith("tag ")]


@pytest.mark.unit
def test_push_loop_aborts_on_first_failing_push(tmp_path: Path) -> None:
    tags = "ghcr.io/example/sentinel:fail\nghcr.io/example/sentinel:should-not-push"

    result, log_file = _run_push_loop(tmp_path, image_tags=tags)

    assert result.returncode != 0
    assert _pushed_tags(log_file) == ["ghcr.io/example/sentinel:fail"]


@pytest.mark.unit
def test_push_loop_pushes_all_tags_when_all_succeed(tmp_path: Path) -> None:
    tags = (
        "ghcr.io/example/sentinel:master\n"
        "ghcr.io/example/sentinel:1.2.3\n"
        "ghcr.io/example/sentinel:1.2"
    )

    result, log_file = _run_push_loop(tmp_path, image_tags=tags)

    assert result.returncode == 0
    assert _pushed_tags(log_file) == [
        "ghcr.io/example/sentinel:master",
        "ghcr.io/example/sentinel:1.2.3",
        "ghcr.io/example/sentinel:1.2",
    ]


@pytest.mark.unit
def test_push_loop_rejects_tags_with_different_registry_digests(
    tmp_path: Path,
) -> None:
    tags = "ghcr.io/example/sentinel:1.2.3\nghcr.io/example/sentinel:different"

    result, _ = _run_push_loop(tmp_path, image_tags=tags)

    assert result.returncode != 0
    assert "do not resolve to one digest" in result.stderr


@pytest.mark.unit
def test_push_loop_rejects_malformed_registry_digest(tmp_path: Path) -> None:
    result, _ = _run_push_loop(
        tmp_path, image_tags="ghcr.io/example/sentinel:malformed"
    )

    assert result.returncode != 0
    assert "invalid digest" in result.stderr


@pytest.mark.unit
def test_push_loop_rejects_tag_list_without_publishable_entry(tmp_path: Path) -> None:
    result, _ = _run_push_loop(tmp_path, image_tags="\n")

    assert result.returncode != 0
    assert "did not contain a publishable tag" in result.stderr


@pytest.mark.unit
def test_push_loop_skips_blank_tag_lines(tmp_path: Path) -> None:
    tags = "ghcr.io/example/sentinel:master\n\nghcr.io/example/sentinel:1.2.3"

    result, log_file = _run_push_loop(tmp_path, image_tags=tags)

    assert result.returncode == 0
    assert _pushed_tags(log_file) == [
        "ghcr.io/example/sentinel:master",
        "ghcr.io/example/sentinel:1.2.3",
    ]


@pytest.mark.unit
def test_every_pushed_tag_is_sourced_from_the_smoke_image(tmp_path: Path) -> None:
    # Each `docker tag` call must re-tag the exact local artifact that
    # was smoke-tested (env.SMOKE_IMAGE) — never a different or rebuilt
    # image. This is what makes the published digest mechanically
    # identical to the tested one.
    tags = "ghcr.io/example/sentinel:master\nghcr.io/example/sentinel:1.2.3"

    result, log_file = _run_push_loop(tmp_path, image_tags=tags)

    assert result.returncode == 0
    assert _tag_invocations(log_file) == [
        "sentinel-backend:smoke ghcr.io/example/sentinel:master",
        "sentinel-backend:smoke ghcr.io/example/sentinel:1.2.3",
    ]


_STEP_MARKER = re.compile(r"^ {6}- ")


def _step_start_indices(lines: list[str]) -> list[int]:
    """Indices of every top-level step marker line (`      - `).

    Both jobs use flat step lists at a fixed 6-space indent. This matches
    the minimal, dependency-free scanning approach already used by
    `test_workflow_timeouts.py` rather than introducing a PyYAML parse.
    """
    return [index for index, line in enumerate(lines) if _STEP_MARKER.match(line)]


def _step_block(lines: list[str], start_index: int, boundaries: list[int]) -> list[str]:
    later_boundaries = [b for b in boundaries if b > start_index]
    end_index = later_boundaries[0] if later_boundaries else len(lines)
    return lines[start_index:end_index]


@pytest.mark.unit
def test_image_is_built_exactly_once_without_pushing() -> None:
    lines = WORKFLOW_PATH.read_text(encoding="utf-8").splitlines()
    boundaries = _step_start_indices(lines)

    build_step_starts = [
        start
        for start in boundaries
        if any(
            "uses: docker/build-push-action" in line
            for line in _step_block(lines, start, boundaries)
        )
    ]

    assert len(build_step_starts) == 1, (
        "Expected exactly one docker/build-push-action step; a second "
        "build invocation would produce a different digest than the one "
        "smoke-tested, breaking the 'published == tested' guarantee"
    )

    block_text = "\n".join(_step_block(lines, build_step_starts[0], boundaries))
    assert "push: false" in block_text
    assert "load: true" in block_text


@pytest.mark.unit
def test_smoke_test_step_precedes_push_step() -> None:
    lines = WORKFLOW_PATH.read_text(encoding="utf-8").splitlines()
    smoke_index = next(
        index
        for index, line in enumerate(lines)
        if "name: Image smoke test (blocking gate)" in line
    )
    push_index = next(
        index for index, line in enumerate(lines) if STEP_NAME_MARKER in line
    )

    assert smoke_index < push_index, (
        "The blocking smoke gate must run before the publish step so a "
        "failing smoke test prevents any push"
    )


@pytest.mark.unit
def test_sbom_gate_precedes_push_and_release_metadata_depends_on_build() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert (
        workflow.index("name: Image smoke test (blocking gate)")
        < workflow.index("name: Generate and validate release SBOM candidate")
        < workflow.index(STEP_NAME_MARKER)
    )
    assert "if: startsWith(github.ref, 'refs/tags/v')" in workflow
    assert "needs: build-backend" in workflow
    assert workflow.count("create-storage-record: false") == 2
    assert workflow.count("push-to-registry: true") == 2
    assert "gh release upload" in workflow
    assert "--clobber" in workflow


@pytest.mark.unit
def test_release_attestations_use_build_outputs_and_expected_predicates() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    attest_references = re.findall(
        r"uses: actions/attest@([0-9a-f]{40}) # v[0-9]+\.[0-9]+\.[0-9]+",
        workflow,
    )
    assert len(attest_references) == 2
    assert len(set(attest_references)) == 1
    assert (
        workflow.count(
            "subject-digest: ${{ needs.build-backend.outputs.image-digest }}"
        )
        == 2
    )
    assert (
        "sbom-path: ${{ runner.temp }}/release-sbom/"
        "${{ needs.build-backend.outputs.sbom-file-name }}" in workflow
    )
    assert "name: ${{ needs.build-backend.outputs.sbom-artifact-name }}" in workflow


@pytest.mark.unit
def test_build_job_exports_exact_release_metadata_outputs() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    outputs = workflow.split("    outputs:", 1)[1].split("    permissions:", 1)[0]

    assert "image-name: ${{ steps.push.outputs.image_name }}" in outputs
    assert "image-digest: ${{ steps.push.outputs.digest }}" in outputs
    assert (
        "sbom-artifact-name: ${{ steps.sbom-metadata.outputs.artifact_name }}"
        in outputs
    )
    assert "sbom-file-name: ${{ steps.sbom-metadata.outputs.file_name }}" in outputs


@pytest.mark.unit
def test_release_metadata_job_has_only_required_write_permissions() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    job = workflow.split("  publish-release-metadata:", 1)[1]

    permissions = job.split("    steps:", 1)[0]
    permission_lines = {
        line.strip()
        for line in permissions.splitlines()
        if line.strip().endswith(": write")
    }
    assert permission_lines == {
        "attestations: write",
        "contents: write",
        "id-token: write",
        "packages: write",
    }


@pytest.mark.unit
def test_sbom_metadata_outputs_enter_shell_via_environment() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    step = workflow.split("- name: Generate and validate release SBOM candidate", 1)[
        1
    ].split("- name: Retain validated SBOM", 1)[0]

    assert "SBOM_FILE_PATH: ${{ steps.sbom-metadata.outputs.file_path }}" in step
    assert "SBOM_VERSION: ${{ steps.sbom-metadata.outputs.version }}" in step
    run_block = step.split("run:", 1)[1]
    assert "steps.sbom-metadata.outputs" not in run_block


@pytest.mark.unit
def test_sbom_subject_name_is_normalized_for_ghcr() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert '"${REGISTRY}/${IMAGE_NAME,,}"' in workflow
