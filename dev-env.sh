#!/usr/bin/env bash
# STAMP Development Environment Manager
#
# Auto-detects Podman or Docker and manages development services
# (PostgreSQL, Redis) defined in docker-compose.yml.
#
# Usage: ./dev-env.sh <command>
#
# Commands:
#   up       Start development services in the background
#   down     Stop and remove development services
#   logs     Follow service logs (Ctrl+C to stop)
#   ps       Show status of development services
#   status   Show detected runtime and service status
#   help     Show this help message

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"
PROJECT_NAME="stamp"

# Colors for output (disabled if not a terminal)
if [[ -t 1 ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    BLUE='\033[0;34m'
    NC='\033[0m' # No Color
else
    RED=''
    GREEN=''
    YELLOW=''
    BLUE=''
    NC=''
fi

log_info() {
    echo -e "${BLUE}[STAMP]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[STAMP]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[STAMP]${NC} $1"
}

log_error() {
    echo -e "${RED}[STAMP]${NC} $1"
}

# --- Runtime Detection ---

RUNTIME=""
COMPOSE_CMD=""

detect_runtime() {
    # Priority 1: Podman
    if command -v podman &>/dev/null; then
        # Check if running rootless
        local rootless
        rootless=$(podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null || echo "unknown")

        if [[ "${rootless}" == "true" ]]; then
            log_info "Detected Podman (rootless)"
        elif [[ "${rootless}" == "false" ]]; then
            log_warn "Detected Podman (rootful). Rootless mode is recommended."
            log_warn "See: https://github.com/containers/podman/blob/main/docs/tutorials/rootless_tutorial.md"
        else
            log_info "Detected Podman"
        fi

        RUNTIME="podman"

        # Check for compose: native plugin first, then podman-compose
        if podman compose version &>/dev/null; then
            COMPOSE_CMD="podman compose"
            log_info "Using Podman Compose plugin"
            return 0
        elif command -v podman-compose &>/dev/null; then
            COMPOSE_CMD="podman-compose"
            log_info "Using podman-compose"
            return 0
        else
            log_error "Podman is installed but no Compose tool was found."
            echo ""
            echo "  Install one of the following:"
            echo ""
            echo "    Option A: Podman Compose plugin (if available for your Podman version)"
            echo "      Check your distribution's package manager for 'podman-plugins' or similar."
            echo ""
            echo "    Option B: podman-compose (Python package)"
            echo "      pip install podman-compose"
            echo ""
            return 1
        fi
    fi

    # Priority 2: Docker
    if command -v docker &>/dev/null; then
        log_info "Detected Docker"
        RUNTIME="docker"

        # Check for compose: plugin first, then standalone
        if docker compose version &>/dev/null; then
            COMPOSE_CMD="docker compose"
            log_info "Using Docker Compose plugin"
            return 0
        elif command -v docker-compose &>/dev/null; then
            COMPOSE_CMD="docker-compose"
            log_info "Using docker-compose (standalone)"
            return 0
        else
            log_error "Docker is installed but no Compose tool was found."
            echo ""
            echo "  Install Docker Compose:"
            echo "    https://docs.docker.com/compose/install/"
            echo ""
            return 1
        fi
    fi

    # Nothing found
    log_error "No container runtime found."
    echo ""
    echo "  To run the STAMP development environment, install one of the following:"
    echo ""
    echo "    Option 1 (recommended): Podman + Compose (rootless)"
    echo "      https://podman.io/getting-started/installation"
    echo "      pip install podman-compose"
    echo ""
    echo "    Option 2: Docker + Docker Compose"
    echo "      https://docs.docker.com/engine/install/"
    echo ""
    return 1
}

# --- Commands ---

compose_exec() {
    ${COMPOSE_CMD} -f "${COMPOSE_FILE}" -p "${PROJECT_NAME}" "$@"
}

cmd_up() {
    log_info "Starting development services..."
    compose_exec up -d
    log_success "Development services are running."
    echo ""
    echo "  PostgreSQL: localhost:5432 (user: stamp, password: stamp, db: stamp)"
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
    log_info "Runtime: ${RUNTIME}"
    log_info "Compose: ${COMPOSE_CMD}"
    echo ""
    compose_exec ps
}

cmd_help() {
    echo ""
    echo "STAMP Development Environment Manager"
    echo ""
    echo "Usage: $0 <command>"
    echo ""
    echo "Commands:"
    echo "  up       Start development services in the background"
    echo "  down     Stop and remove development services"
    echo "  logs     Follow service logs (Ctrl+C to stop)"
    echo "  ps       Show status of development services"
    echo "  status   Show detected runtime and service status"
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

    if ! detect_runtime; then
        exit 1
    fi

    case "${command}" in
        up)     cmd_up ;;
        down)   cmd_down ;;
        logs)   cmd_logs ;;
        ps)     cmd_ps ;;
        status) cmd_status ;;
        *)
            log_error "Unknown command: ${command}"
            cmd_help
            exit 1
            ;;
    esac
}

main "$@"
