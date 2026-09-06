#!/usr/bin/env bash
# Publish one local image under every newline-delimited IMAGE_TAGS entry and
# write the common registry image name and digest to GITHUB_OUTPUT.

set -euo pipefail

main() {
    : "${SMOKE_IMAGE:?SMOKE_IMAGE must name the tested local image}"
    : "${IMAGE_TAGS:?IMAGE_TAGS must contain at least one registry tag}"
    : "${GITHUB_OUTPUT:?GITHUB_OUTPUT must identify the step output file}"

    local tag image_name="" published_digest="" current_name current_digest

    while read -r tag; do
        [[ -z "${tag}" ]] && continue

        echo "Tagging and pushing ${tag}"
        docker tag "${SMOKE_IMAGE}" "${tag}"
        docker push "${tag}"

        current_name="${tag%:*}"
        current_digest="$(
            docker buildx imagetools inspect "${tag}" \
                --format '{{json .Manifest}}' | jq -er '.digest'
        )"
        if [[ ! "${current_digest}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
            echo "Registry returned an invalid digest for ${tag}: ${current_digest}" >&2
            return 1
        fi

        if [[ -z "${published_digest}" ]]; then
            image_name="${current_name}"
            published_digest="${current_digest}"
        elif [[ "${current_name}" != "${image_name}" ]]; then
            echo "Published tags do not share one image name" >&2
            return 1
        elif [[ "${current_digest}" != "${published_digest}" ]]; then
            echo "Published tags do not resolve to one digest" >&2
            return 1
        fi
    done <<<"${IMAGE_TAGS}"

    if [[ -z "${published_digest}" ]]; then
        echo "IMAGE_TAGS did not contain a publishable tag" >&2
        return 1
    fi

    {
        echo "image_name=${image_name}"
        echo "digest=${published_digest}"
    } >>"${GITHUB_OUTPUT}"
}

main "$@"
