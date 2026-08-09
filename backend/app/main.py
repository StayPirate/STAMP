"""Sentinel FastAPI application entry point."""

from __future__ import annotations

from importlib.metadata import version as get_version

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import health
from app.api.v1 import auth
from app.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.logging import configure_logging
from app.core.middleware import RequestIDMiddleware

configure_logging(settings)
logger = structlog.get_logger(__name__)

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


@app.exception_handler(AppError)
async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Render `AppError` as the standard `{"code": ..., "detail": ...}`
    envelope — see `docs/api-spec.md` (Response Format)."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code.value, "detail": exc.detail},
    )


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Render Pydantic/FastAPI request validation failures as the
    standard `422 VALIDATION_ERROR` envelope — see `docs/api-spec.md`
    (Response Format, Global Responses)."""
    return JSONResponse(
        status_code=422,
        content={
            "code": ErrorCode.VALIDATION_ERROR.value,
            "detail": "Request validation failed",
            "errors": jsonable_encoder(exc.errors()),
        },
    )


@app.exception_handler(Exception)
async def _unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Render any unhandled exception as the generic `500 INTERNAL_ERROR`
    envelope — see `docs/api-spec.md` (Global Responses). Logged at
    ERROR with the full traceback for operator diagnosis."""
    logger.error("unhandled_exception", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={
            "code": ErrorCode.INTERNAL_ERROR.value,
            "detail": "An unexpected error occurred.",
        },
    )


app.include_router(health.router)
app.include_router(auth.router)
