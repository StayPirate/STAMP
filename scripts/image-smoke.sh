#!/usr/bin/env bash
# Sentinel image smoke-test runner.
#
# Builds (or reuses) the backend Docker image, brings up the
# self-contained smoke compose stack (docker-compose.smoke.yml), runs the
# black-box `image`-marked pytest suite against the running containers,
# then tears everything down. Used identically in local development and
# in CI (the CI gate in .github/workflows/build-images.yml invokes this
# same script).
#
# The harness requires Docker Engine or Docker Desktop with the Docker
# Compose CLI plugin version 2.7.0 or later. This runner requirement is
# separate from the OCI image's deployment-agnostic runtime support.
#
# The smoke stack (docker-compose.smoke.yml) is self-contained: it brings
# up its own postgres + redis (no host ports) plus the application
# services, so it can run even while scripts/dev-env.sh is up on the
# standard host ports 5432/6379.
#
# Usage:
#   scripts/image-smoke.sh [--no-build]
#
# Options:
#   --no-build   Skip `compose build`; use a pre-built image already
#                present locally. CI builds/loads the image once (to
#                guarantee the tested and published artifacts share a
#                digest) and passes SENTINEL_IMAGE + --no-build.
#
# Environment:
#   SENTINEL_IMAGE        Image ref used by docker-compose.smoke.yml
#                         (default: sentinel-backend:smoke).
#   IMAGE_SMOKE_PORT      Host port the api service is published on
#                         (default: 18000 — deliberately not 8000, to
#                         avoid clashing with a local uvicorn dev server).
#   IMAGE_SMOKE_BASE_URL  Base URL the pytest suite targets
#                         (default: http://localhost:${IMAGE_SMOKE_PORT}).
#
# Exit code: the pytest exit code when pytest runs; otherwise the failing
# preflight/build/start command's exit code. Teardown runs after preflight.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_NAME="sentinel-smoke"
SMOKE_COMPOSE="${ROOT_DIR}/docker-compose.smoke.yml"
MINIMUM_COMPOSE_VERSION="2.7.0"

export SENTINEL_IMAGE="${SENTINEL_IMAGE:-sentinel-backend:smoke}"
export IMAGE_SMOKE_PORT="${IMAGE_SMOKE_PORT:-18000}"
IMAGE_SMOKE_BASE_URL="${IMAGE_SMOKE_BASE_URL:-http://localhost:${IMAGE_SMOKE_PORT}}"

NO_BUILD=0
for arg in "$@"; do
    case "${arg}" in
        --no-build) NO_BUILD=1 ;;
        *)
            echo "Error: unknown argument '${arg}'" >&2
            echo "Usage: scripts/image-smoke.sh [--no-build]" >&2
            exit 1
            ;;
    esac
done

check_docker_environment() {
    if ! command -v docker &>/dev/null; then
        echo "Error: Docker CLI not found." >&2
        echo "Install Docker Engine or Docker Desktop with Docker Compose ${MINIMUM_COMPOSE_VERSION} or later." >&2
        return 1
    fi

    local server_identity
    if ! server_identity=$(docker version --format '{{.Server.Platform.Name}}|{{(index .Server.Components 0).Name}}'); then
        echo "Error: Docker Engine is unreachable." >&2
        echo "Start Docker Engine or Docker Desktop and verify the active Docker context." >&2
        return 1
    fi

    # Docker Engine may leave Platform.Name empty and identify its first
    # server component as exactly "Engine".
    case "${server_identity}" in
        Docker\ Engine* | Docker\ Desktop* | \|Engine) ;;
        *)
            echo "Error: unsupported container server '${server_identity:-unknown}'." >&2
            echo "The image-smoke harness requires Docker Engine or Docker Desktop; Podman compatibility endpoints are not supported." >&2
            return 1
            ;;
    esac

    local compose_version
    if ! compose_version=$(docker compose version --short); then
        echo "Error: Docker Compose CLI plugin not found." >&2
        echo "Install Docker Compose ${MINIMUM_COMPOSE_VERSION} or later for the 'docker compose' command." >&2
        return 1
    fi

    if [[ ! "${compose_version}" =~ ^v?([0-9]+)\.([0-9]+)\.([0-9]+)([-+._][0-9A-Za-z.+_-]+)?$ ]]; then
        echo "Error: unable to parse Docker Compose version '${compose_version}'." >&2
        echo "Install Docker Compose ${MINIMUM_COMPOSE_VERSION} or later." >&2
        return 1
    fi

    local major="${BASH_REMATCH[1]}"
    local minor="${BASH_REMATCH[2]}"
    local patch="${BASH_REMATCH[3]}"
    local minimum_major=2
    local minimum_minor=7
    local minimum_patch=0
    local unsupported=0
    if ((major < minimum_major)); then
        unsupported=1
    elif ((major == minimum_major && minor < minimum_minor)); then
        unsupported=1
    elif ((major == minimum_major && minor == minimum_minor)); then
        if ((patch < minimum_patch)); then
            unsupported=1
        fi
    fi
    if ((unsupported)); then
        echo "Error: Docker Compose ${compose_version} is unsupported; ${MINIMUM_COMPOSE_VERSION} or later is required." >&2
        return 1
    fi

    COMPOSE_VERSION="${compose_version}"
}

COMPOSE_VERSION=""
check_docker_environment

# Assemble the compose invocation once.
compose() {
    docker compose -p "${PROJECT_NAME}" -f "${SMOKE_COMPOSE}" "$@"
}

# shellcheck disable=SC2317,SC2329  # reachable + invoked indirectly via 'trap teardown EXIT'
teardown() {
    echo "[image-smoke] Tearing down..."
    compose down -v --remove-orphans || true
}
trap teardown EXIT

echo "[image-smoke] Using: docker compose ${COMPOSE_VERSION}"
echo "[image-smoke] Image:  ${SENTINEL_IMAGE}"

if [[ "${NO_BUILD}" -eq 0 ]]; then
    echo "[image-smoke] Building image..."
    compose build
else
    echo "[image-smoke] Skipping build (--no-build); using existing ${SENTINEL_IMAGE}"
fi

echo "[image-smoke] Starting stack (up --wait)..."
compose up -d --wait

echo "[image-smoke] Running image test suite..."
set +e
(
    cd "${ROOT_DIR}/backend" &&
        IMAGE_SMOKE_BASE_URL="${IMAGE_SMOKE_BASE_URL}" \
            COMPOSE_FILES="${SMOKE_COMPOSE}" \
            COMPOSE_PROJECT="${PROJECT_NAME}" \
            uv run pytest -m image tests/image/
)
PYTEST_EXIT=$?
set -e

echo "[image-smoke] pytest exit code: ${PYTEST_EXIT}"
exit "${PYTEST_EXIT}"
