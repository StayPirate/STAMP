# Stage 1: Build dependencies
FROM python:3.12-slim AS builder

WORKDIR /app

RUN pip install --no-cache-dir hatchling

COPY pyproject.toml .
RUN pip install --no-cache-dir --prefix=/install .

# Stage 2: Runtime
FROM python:3.12-slim AS runtime

WORKDIR /app

# Install SUSE internal CA certificate for LDAPS and other internal TLS
# connections. The source cert lives at certs/SUSE_Trust_Root.crt in the
# repository root. It must be copied into the backend build context before
# building (e.g., cp ../certs/SUSE_Trust_Root.crt certs/) or the build
# context must be set to the repository root with -f backend/Dockerfile.
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
