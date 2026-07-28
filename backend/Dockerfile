# syntax=docker/dockerfile:1

# Python version — must match backend/.python-version (CI drift-check enforces this)
ARG PYTHON_VERSION=3.13

# Stage 1: Install dependencies and build the project (non-editable)
FROM python:${PYTHON_VERSION}-slim AS builder

# Pin the uv version used to build this image (keep in sync with the
# `version:` input of astral-sh/setup-uv in .github/workflows/ci.yml)
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /bin/uv

# Use the Python interpreter already present in this base image instead
# of letting uv download its own managed interpreter
ENV UV_PYTHON_DOWNLOADS=0 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies only, in their own layer. This layer is
# invalidated only when pyproject.toml or uv.lock change — not on every
# application code change — which keeps rebuilds fast.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev --no-editable

# Copy application source and install the project itself (non-editable).
COPY app/ app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# Stage 2: Runtime
FROM python:${PYTHON_VERSION}-slim AS runtime

WORKDIR /app

# Install SUSE internal CA certificate for TLS connections to internal
# services (IBS, SMELT, AIMAAS, RabbitMQ)
COPY certs/SUSE_Trust_Root.crt /usr/local/share/ca-certificates/SUSE_Trust_Root.crt
RUN update-ca-certificates

# Copy the synced virtual environment (dependencies + project code,
# installed non-editable) from the builder stage. uv itself is not
# copied — it is not needed at runtime.
COPY --from=builder /app/.venv /app/.venv

# Copy application code needed at runtime (Alembic migrations run from
# this same image via a one-shot job — see docs/deployment.md,
# Database Migrations)
COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini .

# Activate the virtual environment for all subsequent commands
ENV PATH="/app/.venv/bin:$PATH"

# Create non-root user
RUN useradd --create-home --no-log-init appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
