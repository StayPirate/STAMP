"""HTTP-level tests for the application entry point (`app/main.py`).

Covers the production generic exception handler
(`_unhandled_exception_handler`, registered via
`@app.exception_handler(Exception)`): any exception that escapes route
or dependency code must render as the standard `500 INTERNAL_ERROR`
envelope (`docs/api-spec.md`, Global Responses), never leak the
exception's own message to the client, and be logged at ERROR with the
traceback for operator diagnosis. See issue #185.

A minimal standalone FastAPI app registers the real, imported handler
function (not a re-implementation) plus one route that deliberately
raises — mirroring the established pattern in
`tests/test_api/test_dependencies.py` (`_build_test_app()`) and
`tests/test_database.py` (`_build_app()`): exercising real
application code through an actual ASGI/HTTP round trip without
mutating the production `app`'s shared route table.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.errors import ErrorCode
from app.main import _unhandled_exception_handler

_SENSITIVE_DETAIL = "sensitive: password=hunter2 host=db.internal.example"


def _build_test_app() -> FastAPI:
    test_app = FastAPI()
    test_app.add_exception_handler(Exception, _unhandled_exception_handler)

    @test_app.get("/boom")
    async def boom() -> None:
        raise RuntimeError(_SENSITIVE_DETAIL)

    return test_app


@pytest.fixture
async def error_client() -> AsyncGenerator[AsyncClient]:
    # `raise_app_exceptions=False`: Starlette's `ServerErrorMiddleware`
    # always re-raises the original exception after sending the custom
    # handler's response ("This allows servers to log the error, or
    # allows test clients to optionally raise the error within the test
    # case" — see `starlette.middleware.errors`). This test wants the
    # transmitted response, not the propagated exception — exactly the
    # "optional" case that flag controls.
    async with AsyncClient(
        transport=ASGITransport(app=_build_test_app(), raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        yield client


@pytest.mark.unit
class TestUnhandledExceptionHandler:
    """HTTP-level contract for the production generic exception handler."""

    async def test_returns_500_with_the_generic_envelope(
        self, error_client: AsyncClient
    ) -> None:
        response = await error_client.get("/boom")

        assert response.status_code == 500
        assert response.json() == {
            "code": ErrorCode.INTERNAL_ERROR.value,
            "detail": "An unexpected error occurred.",
        }

    async def test_does_not_leak_the_exception_message_to_the_client(
        self, error_client: AsyncClient
    ) -> None:
        response = await error_client.get("/boom")

        assert _SENSITIVE_DETAIL not in response.text

    async def test_logs_the_failure_at_error_level(
        self,
        error_client: AsyncClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level("ERROR"):
            response = await error_client.get("/boom")

        assert response.status_code == 500
        assert "unhandled_exception" in caplog.text

    async def test_logged_record_carries_the_original_traceback(
        self,
        error_client: AsyncClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # `logger.error(..., exc_info=exc)` must attach the real
        # traceback so operators can diagnose the failure — verified by
        # asserting the raised exception's own type and message appear
        # in the captured record (structlog's `format_exc_info`
        # processor renders `exc_info` into the record's text).
        with caplog.at_level("ERROR"):
            await error_client.get("/boom")

        assert "RuntimeError" in caplog.text
        assert _SENSITIVE_DETAIL in caplog.text
