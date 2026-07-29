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
# Runtime-agnostic: works with Docker or Podman, reusing the same
# detection pattern as scripts/dev-env.sh (native `compose` plugin
# preferred, standalone `*-compose` as fallback).
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
# Exit code: the pytest exit code (0 = pass). Teardown always runs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_NAME="sentinel-smoke"
SMOKE_COMPOSE="${ROOT_DIR}/docker-compose.smoke.yml"

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

# --- Runtime detection (same pattern as scripts/dev-env.sh) ---

COMPOSE_CMD=""

detect_compose() {
    if command -v podman &>/dev/null; then
        if podman compose version &>/dev/null; then
            COMPOSE_CMD="podman compose"
            return 0
        elif command -v podman-compose &>/dev/null; then
            COMPOSE_CMD="podman-compose"
            return 0
        fi
    fi

    if command -v docker &>/dev/null; then
        if docker compose version &>/dev/null; then
            COMPOSE_CMD="docker compose"
            return 0
        elif command -v docker-compose &>/dev/null; then
            COMPOSE_CMD="docker-compose"
            return 0
        fi
    fi

    echo "Error: no container runtime with a Compose implementation found." >&2
    echo "Install Docker or Podman with a Compose plugin (see scripts/dev-env.sh)." >&2
    return 1
}

detect_compose

# Assemble the compose invocation once.
compose() {
    # shellcheck disable=SC2086
    ${COMPOSE_CMD} -p "${PROJECT_NAME}" -f "${SMOKE_COMPOSE}" "$@"
}

teardown() {
    echo "[image-smoke] Tearing down..."
    compose down -v --remove-orphans || true
}
trap teardown EXIT

echo "[image-smoke] Using: ${COMPOSE_CMD}"
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
    cd "${ROOT_DIR}/backend" && \
    IMAGE_SMOKE_BASE_URL="${IMAGE_SMOKE_BASE_URL}" \
    COMPOSE_CMD="${COMPOSE_CMD}" \
    COMPOSE_FILES="${SMOKE_COMPOSE}" \
    uv run pytest -m image tests/image/
)
PYTEST_EXIT=$?
set -e

echo "[image-smoke] pytest exit code: ${PYTEST_EXIT}"
exit "${PYTEST_EXIT}"
