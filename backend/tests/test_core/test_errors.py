"""Tests for the standard error envelope (backend/app/core/errors.py)."""

from __future__ import annotations

import pytest

from app.core.errors import AppError, ErrorCode


@pytest.mark.unit
class TestAppError:
    def test_carries_status_code_error_code_and_detail(self) -> None:
        error = AppError(
            status_code=400, code=ErrorCode.AUTH_LOGOUT_NOT_APPLICABLE, detail="nope"
        )
        assert error.status_code == 400
        assert error.code == ErrorCode.AUTH_LOGOUT_NOT_APPLICABLE
        assert error.detail == "nope"

    def test_is_an_exception(self) -> None:
        error = AppError(
            status_code=401, code=ErrorCode.AUTH_NOT_AUTHENTICATED, detail="nope"
        )
        assert isinstance(error, Exception)
        assert str(error) == "nope"

    def test_headers_default_to_none(self) -> None:
        error = AppError(
            status_code=401, code=ErrorCode.AUTH_INVALID_CREDENTIALS, detail="nope"
        )
        assert error.headers is None

    def test_carries_optional_headers(self) -> None:
        error = AppError(
            status_code=429,
            code=ErrorCode.AUTH_ACCOUNT_LOCKED,
            detail="locked",
            headers={"Retry-After": "60"},
        )
        assert error.headers == {"Retry-After": "60"}


@pytest.mark.unit
class TestErrorCode:
    def test_values_are_upper_snake_case_strings(self) -> None:
        for code in ErrorCode:
            assert code.value == code.value.upper()
            assert isinstance(code.value, str)
