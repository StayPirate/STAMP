#!/usr/bin/env bash
# Synchronizes the backend Python environment (backend/.venv) with
# backend/uv.lock when it has drifted. Invoked by the post-checkout,
# post-merge, and post-rewrite git hooks (see .githooks/) after
# operations that can change which uv.lock is checked out.
#
# Never mutates uv.lock and never fails the calling git operation:
# any problem (uv missing, sync failure) is reported as a warning on
# stderr. See docs/features/platform/testing-strategy.md
# (Pre-Commit Hooks (Local Automation)) for the behavioral contract.
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
backend_dir="${repo_root}/backend"

# Nothing to do if this checkout doesn't have a backend project
# (e.g. a very old branch predating the current layout).
[[ -f "${backend_dir}/pyproject.toml" ]] || exit 0

if ! command -v uv >/dev/null 2>&1; then
    echo "Warning: uv not installed; backend environment may be out of sync with uv.lock." >&2
    echo "Warning: run 'cd backend && uv sync' manually once uv is available." >&2
    exit 0
fi

# --locked never rewrites uv.lock; --check performs a dry run so the
# common case (already synchronized) does no work and prints nothing.
if uv sync --project "${backend_dir}" --locked --check --no-progress >/dev/null 2>&1; then
    exit 0
fi

echo "Backend environment out of sync with uv.lock; synchronizing..." >&2
if ! uv sync --project "${backend_dir}" --locked --no-progress; then
    echo "Warning: failed to synchronize the backend environment." >&2
    echo "Warning: run 'cd backend && uv sync' manually to resolve." >&2
    exit 0
fi
