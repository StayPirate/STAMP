"""Sentinel FastAPI application entry point."""

from __future__ import annotations

from importlib.metadata import version as get_version

import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import health
from app.api.v1 import api_keys, auth, identity_audit, users
from app.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.logging import configure_logging
from app.core.middleware import RequestIDMiddleware
from app.core.query_limits import enforce_query_parameter_length_limit

configure_logging(settings)
logger = structlog.get_logger(__name__)

app = FastAPI(
    title="Sentinel",
    description="Security update management platform for SUSE/openSUSE distributions",
    version=get_version("sentinel"),
    debug=settings.debug,
    # Applies the shared query-parameter length limit
    # (docs/api-spec.md, Query Parameter Length Limit) to every current
    # and future endpoint automatically — see
    # docs/conventions.md (FastAPI Conventions, "Cross-cutting query
    # parameter constraints").
    dependencies=[Depends(enforce_query_parameter_length_limit)],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID", "Retry-After"],
)
app.add_middleware(RequestIDMiddleware)


@app.exception_handler(AppError)
async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Render `AppError` as the standard `{"code": ..., "detail": ...}`
    envelope — see `docs/api-spec.md` (Response Format). Propagates
    `exc.headers` (e.g. `Retry-After` for `AUTH_ACCOUNT_LOCKED`) onto
    the response verbatim."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code.value, "detail": exc.detail},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Render Pydantic/FastAPI request validation failures as the
    standard `422 VALIDATION_ERROR` envelope — see `docs/api-spec.md`
    (Response Format, Global Responses).

    Only `loc`, `msg`, and `type` are projected from each Pydantic
    error dict. Pydantic v2's `errors()` also includes an `input` key
    with the raw offending value (and sometimes `ctx`), which is not
    part of the documented `errors` element schema and could echo
    sensitive request data (e.g. a password field) back to the client.
    """
    return JSONResponse(
        status_code=422,
        content={
            "code": ErrorCode.VALIDATION_ERROR.value,
            "detail": "Request validation failed",
            "errors": [
                {"loc": e["loc"], "msg": e["msg"], "type": e["type"]}
                for e in jsonable_encoder(exc.errors())
            ],
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
app.include_router(api_keys.router)
app.include_router(users.router)
app.include_router(identity_audit.router)
