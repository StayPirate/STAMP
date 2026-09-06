#!/usr/bin/env bash
# Select the SBOM version, filename, and workflow-artifact name for a build.

set -euo pipefail

main() {
    : "${IS_RELEASE:?IS_RELEASE must be true or false}"
    : "${SOURCE_SHA:?SOURCE_SHA must identify the source commit}"
    : "${GITHUB_RUN_ID:?GITHUB_RUN_ID must identify the workflow run}"
    : "${RUNNER_TEMP:?RUNNER_TEMP must identify temporary storage}"
    : "${GITHUB_OUTPUT:?GITHUB_OUTPUT must identify the step output file}"

    local version file_name

    case "${IS_RELEASE}" in
        true)
            : "${GITHUB_REF_NAME:?GITHUB_REF_NAME must identify the release tag}"
            version="${GITHUB_REF_NAME#v}"
            if [[ ! "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
                echo "Release tag must have the form v<major>.<minor>.<patch>" >&2
                return 1
            fi
            file_name="sentinel-${version}.sbom.cdx.json"
            ;;
        false)
            version="${SOURCE_SHA}"
            file_name="sentinel-master.sbom.cdx.json"
            ;;
        *)
            echo "IS_RELEASE must be true or false" >&2
            return 1
            ;;
    esac

    {
        echo "version=${version}"
        echo "file_name=${file_name}"
        echo "file_path=${RUNNER_TEMP}/${file_name}"
        echo "artifact_name=sentinel-sbom-${GITHUB_RUN_ID}"
    } >>"${GITHUB_OUTPUT}"
}

main "$@"
