#!/usr/bin/env bash
# Sentinel Development Environment Manager
#
# Uses Docker Engine or Docker Desktop with the Docker Compose CLI plugin to
# manage development services (PostgreSQL, Redis) defined in docker-compose.yml.
#
# Usage: ./scripts/dev-env.sh <command>
#
# Commands:
#   up       Start development services in the background
#   down     Stop and remove development services
#   logs     Follow service logs (Ctrl+C to stop)
#   ps       Show status of development services
#   status   Show Docker versions and service status
#   help     Show this help message

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker-compose.yml"
PROJECT_NAME="sentinel"
MINIMUM_COMPOSE_VERSION="2.7.0"

# Colors for output (disabled if not a terminal)
if [[ -t 1 ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    BLUE='\033[0;34m'
    NC='\033[0m' # No Color
else
    RED=''
    GREEN=''
    BLUE=''
    NC=''
fi

log_info() {
    echo -e "${BLUE}[Sentinel]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[Sentinel]${NC} $1"
}

log_error() {
    echo -e "${RED}[Sentinel]${NC} $1" >&2
}

# --- Docker preflight ---

DOCKER_VERSION=""
COMPOSE_VERSION=""

version_is_at_least() {
    local version="$1"
    local minimum_major="$2"
    local minimum_minor="$3"
    local minimum_patch="$4"

    if [[ ! "${version}" =~ ^v?([0-9]+)\.([0-9]+)\.([0-9]+)([-+._][0-9A-Za-z.+_-]+)?$ ]]; then
        return 2
    fi

    local major="${BASH_REMATCH[1]}"
    local minor="${BASH_REMATCH[2]}"
    local patch="${BASH_REMATCH[3]}"

    ((major > minimum_major)) && return 0
    ((major < minimum_major)) && return 1
    ((minor > minimum_minor)) && return 0
    ((minor < minimum_minor)) && return 1
    ((patch >= minimum_patch))
}

check_docker_environment() {
    if ! command -v docker &>/dev/null; then
        log_error "Docker CLI not found."
        log_error "Install Docker Engine or Docker Desktop with Docker Compose ${MINIMUM_COMPOSE_VERSION} or later."
        return 1
    fi

    local server_identity
    if ! server_identity=$(docker version --format '{{.Server.Platform.Name}}|{{(index .Server.Components 0).Name}}'); then
        log_error "Docker Engine is unreachable."
        log_error "Start Docker Engine or Docker Desktop and verify the active Docker context."
        return 1
    fi

    # Docker Engine may leave Platform.Name empty and identify its first
    # server component as exactly "Engine".
    case "${server_identity}" in
        Docker\ Engine* | Docker\ Desktop* | \|Engine) ;;
        *)
            log_error "Unsupported container server '${server_identity:-unknown}'."
            log_error "Repository development tooling requires Docker Engine or Docker Desktop."
            return 1
            ;;
    esac

    if ! DOCKER_VERSION=$(docker version --format '{{.Server.Version}}'); then
        log_error "Unable to determine the Docker Engine version."
        return 1
    fi
    if [[ -z "${DOCKER_VERSION}" ]]; then
        log_error "Docker Engine returned an empty version."
        return 1
    fi

    if ! COMPOSE_VERSION=$(docker compose version --short); then
        log_error "Docker Compose CLI plugin not found."
        log_error "Install Docker Compose ${MINIMUM_COMPOSE_VERSION} or later for the 'docker compose' command."
        return 1
    fi
    if version_is_at_least "${COMPOSE_VERSION}" 2 7 0; then
        :
    else
        local version_status=$?
        if ((version_status == 2)); then
            log_error "Unable to parse Docker Compose version '${COMPOSE_VERSION}'."
        else
            log_error "Docker Compose ${COMPOSE_VERSION} is unsupported; ${MINIMUM_COMPOSE_VERSION} or later is required."
        fi
        return 1
    fi
}

# --- Commands ---

compose_exec() {
    docker compose -f "${COMPOSE_FILE}" -p "${PROJECT_NAME}" "$@"
}

cmd_up() {
    # Ensure backend/.env exists for local development
    local env_file="${ROOT_DIR}/backend/.env"
    local env_example="${ROOT_DIR}/backend/.env.example"
    if [[ ! -f "${env_file}" ]] && [[ -f "${env_example}" ]]; then
        cp "${env_example}" "${env_file}"
        log_info "Created backend/.env from .env.example (customize as needed)"
    fi

    log_info "Starting development services..."
    compose_exec up -d
    log_success "Development services are running."
    echo ""
    echo "  PostgreSQL: localhost:5432 (user: sentinel, password: sentinel, db: sentinel)"
    echo "  Redis:      localhost:6379"
    echo ""
}

cmd_down() {
    log_info "Stopping development services..."
    compose_exec down
    log_success "Development services stopped."
}

cmd_logs() {
    compose_exec logs -f
}

cmd_ps() {
    compose_exec ps
}

cmd_status() {
    echo ""
    log_info "Docker Engine: ${DOCKER_VERSION}"
    log_info "Docker Compose: ${COMPOSE_VERSION}"
    echo ""
    compose_exec ps
}

cmd_help() {
    echo ""
    echo "Sentinel Development Environment Manager"
    echo ""
    echo "Usage: $0 <command>"
    echo ""
    echo "Commands:"
    echo "  up       Start development services in the background"
    echo "  down     Stop and remove development services"
    echo "  logs     Follow service logs (Ctrl+C to stop)"
    echo "  ps       Show status of development services"
    echo "  status   Show Docker versions and service status"
    echo "  help     Show this help message"
    echo ""
}

# --- Main ---

main() {
    local command="${1:-help}"

    if [[ "${command}" == "help" || "${command}" == "--help" || "${command}" == "-h" ]]; then
        cmd_help
        exit 0
    fi

    if ! check_docker_environment; then
        exit 1
    fi

    case "${command}" in
        up) cmd_up ;;
        down) cmd_down ;;
        logs) cmd_logs ;;
        ps) cmd_ps ;;
        status) cmd_status ;;
        *)
            log_error "Unknown command: ${command}"
            cmd_help
            exit 1
            ;;
    esac
}

main "$@"
