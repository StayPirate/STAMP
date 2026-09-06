#!/usr/bin/env bash
# Generate and validate the CycloneDX SBOM for a locally loaded image.
# Usage: scripts/sbom-gate.sh IMAGE OUTPUT_FILE SUBJECT_NAME SUBJECT_VERSION

set -euo pipefail

main() {
    if [[ $# -ne 4 ]]; then
        echo "Usage: $0 IMAGE OUTPUT_FILE SUBJECT_NAME SUBJECT_VERSION" >&2
        return 2
    fi

    local image="$1"
    local output_file="$2"
    local subject_name="$3"
    local subject_version="$4"
    local script_dir repo_root output_dir output_name image_archive image_archive_quoted

    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    repo_root="$(dirname "${script_dir}")"
    # shellcheck disable=SC1091 # path is resolved from this script's location
    source "${repo_root}/.github/sbom-tools.env"

    output_dir="$(dirname "${output_file}")"
    output_name="$(basename "${output_file}")"
    mkdir -p "${output_dir}"
    output_dir="$(cd "${output_dir}" && pwd)"
    output_file="${output_dir}/${output_name}"
    image_archive="${output_file}.image.tar"
    printf -v image_archive_quoted '%q' "${image_archive}"
    # shellcheck disable=SC2064 # freeze the escaped local path before return
    trap "rm -f -- ${image_archive_quoted}" EXIT

    docker save --output "${image_archive}" "${image}"

    docker run --rm \
        --volume "${output_dir}:/output" \
        "${SYFT_IMAGE}" "docker-archive:/output/${output_name}.image.tar" \
        --source-name "${subject_name}" \
        --source-version "${subject_version}" \
        --output "cyclonedx-json@${CYCLONEDX_SPEC_VERSION}=/output/${output_name}"

    docker run --rm \
        --volume "${output_dir}:/data:ro" \
        "${CYCLONEDX_CLI_IMAGE}" \
        validate \
        --input-file "/data/${output_name}" \
        --input-format json \
        --input-version "v${CYCLONEDX_SPEC_VERSION//./_}" \
        --fail-on-errors

    uv run --project "${repo_root}/backend" python \
        "${repo_root}/scripts/validate_release_sbom.py" \
        "${output_file}" \
        "${repo_root}/backend/pyproject.toml" \
        --expected-version "${CYCLONEDX_SPEC_VERSION}" \
        --expected-subject "${subject_name}"
}

main "$@"
