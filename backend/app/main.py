"""Sentinel FastAPI application entry point."""

from __future__ import annotations

from importlib.metadata import version as get_version

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.core.logging import configure_logging
from app.core.middleware import RequestIDMiddleware

configure_logging(settings)

app = FastAPI(
    title="Sentinel",
    description="Security update management platform for SUSE/openSUSE distributions",
    version=get_version("sentinel"),
    debug=settings.debug,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(RequestIDMiddleware)
