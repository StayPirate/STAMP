#!/usr/bin/env python3
"""Generate OpenAPI JSON schema from the FastAPI application.

This script imports the FastAPI app and dumps its OpenAPI schema to stdout
as formatted JSON. It does not require a running server, database, or Redis
connection — FastAPI builds the schema statically from route decorators.

Usage:
    python scripts/generate_openapi.py > openapi.json
"""

from __future__ import annotations

import json
import os
import sys

# Provide required settings for schema generation (no runtime needed).
# This must precede the app import which triggers Settings() instantiation.
os.environ.setdefault(
    "JWT_SECRET_KEY", "openapi-schema-generation-only-not-for-runtime-use"
)

from app.main import app  # noqa: E402


def main() -> None:
    """Print the OpenAPI schema as formatted JSON to stdout."""
    schema = app.openapi()
    json.dump(schema, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
