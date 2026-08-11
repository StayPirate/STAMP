"""Tests for password hashing, verification, and validation utilities
(backend/app/core/passwords.py).

See docs/features/identity/local-authentication.md (Password
Management, Hashing configuration) for the authoritative contract
exercised here.
"""

from __future__ import annotations

from unittest.mock import patch

import bcrypt
import pytest

from app.core.exceptions import ServiceError
from app.core.passwords import (
    BCRYPT_COST,
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    PasswordValidationError,
    hash_password,
    validate_password,
    verify_dummy_password,
    verify_password,
)

_VALID_PASSWORD = "a-valid-password-16"


@pytest.mark.unit
class TestValidatePassword:
    def test_minimum_length_is_accepted(self) -> None:
        validate_password("a" * MIN_PASSWORD_LENGTH)

    def test_maximum_length_is_accepted(self) -> None:
        validate_password("a" * MAX_PASSWORD_LENGTH)

    def test_below_minimum_length_raises(self) -> None:
        with pytest.raises(PasswordValidationError):
            validate_password("a" * (MIN_PASSWORD_LENGTH - 1))

    def test_above_maximum_length_raises(self) -> None:
        with pytest.raises(PasswordValidationError):
            validate_password("a" * (MAX_PASSWORD_LENGTH + 1))

    def test_empty_password_raises(self) -> None:
        with pytest.raises(PasswordValidationError):
            validate_password("")

    def test_password_validation_error_is_a_shared_service_error(self) -> None:
        """`PasswordValidationError` inherits directly from `ServiceError`
        (docs/conventions.md, Service Exception Conventions — Shared
        exceptions), since it is defined in Core and raised by any
        service that validates a candidate password (e.g.
        `user_service.create_user()`)."""
        assert issubclass(PasswordValidationError, ServiceError)


@pytest.mark.unit
class TestHashPassword:
    def test_produces_a_60_character_bcrypt_hash(self) -> None:
        hashed = hash_password(_VALID_PASSWORD)
        assert len(hashed) == 60
        assert hashed.startswith(f"$2b${BCRYPT_COST}$")

    def test_uses_exact_prehash_pipeline(self) -> None:
        """`bcrypt(base64(SHA-256(UTF-8(password))), cost=12)` — verified
        by manually reproducing the pipeline and checking the stored
        hash validates against the manually-computed pre-hash."""
        import base64
        import hashlib

        hashed = hash_password(_VALID_PASSWORD)
        prehash = base64.b64encode(
            hashlib.sha256(_VALID_PASSWORD.encode("utf-8")).digest()
        )
        assert bcrypt.checkpw(prehash, hashed.encode("ascii"))

    def test_different_calls_produce_different_hashes(self) -> None:
        """A fresh random salt is used on every call."""
        assert hash_password(_VALID_PASSWORD) != hash_password(_VALID_PASSWORD)

    def test_handles_password_longer_than_bcrypts_72_byte_limit(self) -> None:
        """The SHA-256 pre-hash normalizes any length to a fixed 44-byte
        ASCII input, so bcrypt's native 72-byte limit is never reached."""
        long_password = "x" * MAX_PASSWORD_LENGTH
        hashed = hash_password(long_password)
        assert verify_password(long_password, hashed) is True


@pytest.mark.unit
class TestVerifyPassword:
    def test_correct_password_verifies(self) -> None:
        hashed = hash_password(_VALID_PASSWORD)
        assert verify_password(_VALID_PASSWORD, hashed) is True

    def test_incorrect_password_fails(self) -> None:
        hashed = hash_password(_VALID_PASSWORD)
        assert verify_password("wrong-password-value", hashed) is False

    def test_malformed_stored_hash_returns_false_not_exception(self) -> None:
        assert verify_password(_VALID_PASSWORD, "not-a-valid-bcrypt-hash") is False

    def test_empty_stored_hash_returns_false_not_exception(self) -> None:
        assert verify_password(_VALID_PASSWORD, "") is False


@pytest.mark.unit
class TestVerifyDummyPassword:
    def test_never_raises(self) -> None:
        verify_dummy_password(_VALID_PASSWORD)

    def test_reuses_the_same_process_wide_dummy_hash(self) -> None:
        """Verified by observing the verification boundary is reached
        with a stable, process-reusable hash — not by asserting
        wall-clock timing equivalence (see
        docs/features/platform/testing-strategy.md, Anti-enumeration)."""
        with patch("app.core.passwords.bcrypt.checkpw") as mock_checkpw:
            mock_checkpw.return_value = False
            verify_dummy_password(_VALID_PASSWORD)
            verify_dummy_password("a-completely-different-password")

        first_call_hash = mock_checkpw.call_args_list[0].args[1]
        second_call_hash = mock_checkpw.call_args_list[1].args[1]
        assert first_call_hash == second_call_hash

    def test_performs_equivalent_cost_bcrypt_work(self) -> None:
        """The dummy verification calls `bcrypt.checkpw()` — the same
        underlying primitive `verify_password()` uses — proving
        equivalent-cost work is performed for unknown/ineligible users."""
        with patch(
            "app.core.passwords.bcrypt.checkpw", wraps=bcrypt.checkpw
        ) as mock_checkpw:
            verify_dummy_password(_VALID_PASSWORD)
            mock_checkpw.assert_called_once()
