"""Unit tests for local authentication request/response schemas
(`backend/app/schemas/auth.py`).

See `docs/features/identity/local-authentication.md` (Login Endpoint)
for the authoritative request/response contract. These tests focus on
the `repr=False` secret-protection guarantee for `LoginRequest.password`
and `LoginData.access_token` — the schema's other behavioral properties
(no length constraints, `token_type` literal) are documented in the
module docstring and require no separate test coverage per the
completeness convention (docs/conventions.md, Function Specification
Completeness).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.schemas.auth import LoginData, LoginRequest, LoginResponse


@pytest.mark.unit
class TestLoginRequest:
    def test_password_is_hidden_from_repr(self) -> None:
        """`password: str = Field(repr=False)` must keep the plaintext
        password out of `repr()` — a login failure or unexpected
        exception must never leak the credential via a debug/log
        representation of the request object."""
        secret_password = "S3cr3t-P@ssw0rd"
        request = LoginRequest(username="jdoe", password=secret_password)
        assert secret_password not in repr(request)

    def test_username_remains_visible_in_repr(self) -> None:
        """Non-secret fields must remain visible — guards against
        over-broad redaction being applied by mistake in the future."""
        request = LoginRequest(username="jdoe", password="irrelevant")
        assert "jdoe" in repr(request)


@pytest.mark.unit
class TestLoginData:
    def test_access_token_is_hidden_from_repr(self) -> None:
        """`access_token: str = Field(repr=False)` must keep the bearer
        token out of `repr()`."""
        secret_token = "eyJhbGciOiJIUzI1NiJ9.fictional-token-payload"
        data = LoginData(
            access_token=secret_token,
            expires_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        assert secret_token not in repr(data)

    def test_expires_at_remains_visible_in_repr(self) -> None:
        data = LoginData(
            access_token="irrelevant",
            expires_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        assert "2026" in repr(data)


@pytest.mark.unit
class TestLoginResponse:
    def test_access_token_is_hidden_from_nested_repr(self) -> None:
        """`repr=False` on `LoginData.access_token` must also protect
        the bearer token when the object is embedded inside the
        `{"data": ...}` response envelope, not just in isolation."""
        secret_token = "eyJhbGciOiJIUzI1NiJ9.fictional-token-payload"
        response = LoginResponse(
            data=LoginData(
                access_token=secret_token,
                expires_at=datetime(2026, 8, 1, tzinfo=UTC),
            )
        )
        assert secret_token not in repr(response)
