# Python version — must match backend/.python-version (CI drift-check enforces this)
ARG PYTHON_VERSION=3.13

# Stage 1: Build dependencies
FROM python:${PYTHON_VERSION}-slim AS builder

WORKDIR /app

RUN pip install --no-cache-dir hatchling

COPY pyproject.toml .
RUN pip install --no-cache-dir --prefix=/install .

# Stage 2: Runtime
FROM python:${PYTHON_VERSION}-slim AS runtime

WORKDIR /app

# Install SUSE internal CA certificate for TLS connections to internal
# services (IBS, SMELT, AIMAAS, RabbitMQ)
COPY certs/SUSE_Trust_Root.crt /usr/local/share/ca-certificates/SUSE_Trust_Root.crt
RUN update-ca-certificates

# Copy installed dependencies from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY . .

# Create non-root user
RUN useradd --create-home --no-log-init appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
