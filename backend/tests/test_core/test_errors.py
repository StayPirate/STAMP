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

    def test_authorization_error_codes_are_registered(self) -> None:
        """See docs/features/identity/rbac.md (`require_capability()`
        Dependency) and authentication.md (Session-Only Authentication
        Dependency, API Endpoints)."""
        assert ErrorCode.AUTH_INSUFFICIENT_PERMISSION == "AUTH_INSUFFICIENT_PERMISSION"
        assert ErrorCode.AUTH_SESSION_REQUIRED == "AUTH_SESSION_REQUIRED"
        assert ErrorCode.USER_NOT_FOUND == "USER_NOT_FOUND"

    def test_api_key_management_error_codes_are_registered(self) -> None:
        """See docs/features/identity/api-key-service.md (Service
        Exceptions) and docs/features/identity/api-key-management.md
        (API, Error responses)."""
        assert ErrorCode.AUTH_API_KEY_NOT_FOUND == "AUTH_API_KEY_NOT_FOUND"
        assert ErrorCode.AUTH_API_KEY_NAME_CONFLICT == "AUTH_API_KEY_NAME_CONFLICT"
        assert ErrorCode.AUTH_API_KEY_NAME_INVALID == "AUTH_API_KEY_NAME_INVALID"
        assert ErrorCode.AUTH_API_KEY_INVALID_EXPIRY == "AUTH_API_KEY_INVALID_EXPIRY"
        assert ErrorCode.USER_INACTIVE == "USER_INACTIVE"

    def test_date_range_inverted_is_registered(self) -> None:
        """See docs/api-spec.md (Date Range Interpretation, Inverted
        range validation)."""
        assert ErrorCode.DATE_RANGE_INVERTED == "DATE_RANGE_INVERTED"
