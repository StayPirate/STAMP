"""Tests for JWT issuance, validation, and sliding refresh
(backend/app/core/jwt.py).

See docs/features/identity/authentication.md (Token Format, JWT
validation contract, Token lifecycle, Token refresh) and
docs/features/platform/testing-strategy.md (Authentication and Session)
for the mandatory boundary scenarios exercised here. Every temporal
check uses an explicit `now`/`issued_at` parameter — no wall-clock
sleeps are used anywhere in this module.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest

from app.core.jwt import (
    ALGORITHM,
    ISSUER,
    REQUIRED_CLAIMS,
    InvalidTokenError,
    JWTClaims,
    decode_and_validate,
    decode_for_logout,
    issue_token,
    refresh_token,
)

_SECRET = "a" * 32
_OTHER_SECRET = "b" * 32


def _payload(**overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    base: dict[str, object] = {
        "sub": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
        "session_deadline": int((now + timedelta(days=30)).timestamp()),
        "iss": ISSUER,
    }
    base.update(overrides)
    return base


def _encode(
    payload: dict[str, object], *, secret: str = _SECRET, alg: str = ALGORITHM
) -> str:
    return pyjwt.encode(payload, secret, algorithm=alg)


# ---------------------------------------------------------------------------
# issue_token()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIssueToken:
    def test_contains_exactly_six_required_claims(self) -> None:
        now = datetime.now(UTC)
        issued = issue_token(
            user_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            issued_at=now,
            session_deadline=now + timedelta(days=30),
            jwt_expiry_hours=72,
            secret_key=_SECRET,
        )
        payload = pyjwt.decode(issued.token, _SECRET, algorithms=[ALGORITHM])
        assert set(payload) == REQUIRED_CLAIMS

    def test_identifier_claims_are_uuid_strings(self) -> None:
        now = datetime.now(UTC)
        user_id = uuid.uuid4()
        session_id = uuid.uuid4()
        issued = issue_token(
            user_id=user_id,
            session_id=session_id,
            issued_at=now,
            session_deadline=now + timedelta(days=30),
            jwt_expiry_hours=72,
            secret_key=_SECRET,
        )
        payload = pyjwt.decode(issued.token, _SECRET, algorithms=[ALGORITHM])
        assert payload["sub"] == str(user_id)
        assert payload["session_id"] == str(session_id)
        assert isinstance(payload["sub"], str)

    def test_timing_claims_are_integers_not_booleans(self) -> None:
        now = datetime.now(UTC)
        issued = issue_token(
            user_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            issued_at=now,
            session_deadline=now + timedelta(days=30),
            jwt_expiry_hours=72,
            secret_key=_SECRET,
        )
        payload = pyjwt.decode(issued.token, _SECRET, algorithms=[ALGORITHM])
        for claim in ("iat", "exp", "session_deadline"):
            assert isinstance(payload[claim], int)
            assert not isinstance(payload[claim], bool)

    def test_exp_capped_at_session_deadline(self) -> None:
        now = datetime.now(UTC)
        deadline = now + timedelta(hours=2)
        issued = issue_token(
            user_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            issued_at=now,
            session_deadline=deadline,
            jwt_expiry_hours=72,  # would exceed the deadline
            secret_key=_SECRET,
        )
        assert issued.token_expires_at == deadline.replace(microsecond=0)

    def test_exp_uses_jwt_expiry_hours_when_earlier_than_deadline(self) -> None:
        now = datetime.now(UTC)
        deadline = now + timedelta(days=30)
        issued = issue_token(
            user_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            issued_at=now,
            session_deadline=deadline,
            jwt_expiry_hours=1,
            secret_key=_SECRET,
        )
        expected = (now + timedelta(hours=1)).replace(microsecond=0)
        assert issued.token_expires_at == expected


# ---------------------------------------------------------------------------
# decode_and_validate() — normal validation contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDecodeAndValidateSuccess:
    def test_valid_token_decodes(self) -> None:
        now = datetime.now(UTC)
        user_id = uuid.uuid4()
        session_id = uuid.uuid4()
        issued = issue_token(
            user_id=user_id,
            session_id=session_id,
            issued_at=now,
            session_deadline=now + timedelta(days=30),
            jwt_expiry_hours=72,
            secret_key=_SECRET,
        )
        claims = decode_and_validate(issued.token, secret_key=_SECRET, now=now)
        assert claims.user_id == user_id
        assert claims.session_id == session_id


@pytest.mark.unit
class TestDecodeAndValidateTemporalBoundaries:
    """Equality-is-expired boundaries, no clock-skew leeway."""

    def _token(self, **overrides: object) -> str:
        return _encode(_payload(**overrides))

    def test_exp_one_second_after_now_is_valid(self) -> None:
        now = datetime.now(UTC)
        payload = _payload(
            iat=int((now - timedelta(hours=1)).timestamp()),
            exp=int(now.timestamp()) + 1,
            session_deadline=int((now + timedelta(days=1)).timestamp()),
        )
        claims = decode_and_validate(_encode(payload), secret_key=_SECRET, now=now)
        assert claims is not None

    def test_exp_exactly_at_now_is_expired(self) -> None:
        now = datetime.now(UTC)
        payload = _payload(
            iat=int((now - timedelta(hours=1)).timestamp()),
            exp=int(now.timestamp()),
            session_deadline=int((now + timedelta(days=1)).timestamp()),
        )
        with pytest.raises(InvalidTokenError):
            decode_and_validate(_encode(payload), secret_key=_SECRET, now=now)

    def test_exp_one_second_before_now_is_expired(self) -> None:
        now = datetime.now(UTC)
        payload = _payload(
            iat=int((now - timedelta(hours=1)).timestamp()),
            exp=int(now.timestamp()) - 1,
            session_deadline=int((now + timedelta(days=1)).timestamp()),
        )
        with pytest.raises(InvalidTokenError):
            decode_and_validate(_encode(payload), secret_key=_SECRET, now=now)

    def test_session_deadline_exactly_at_now_is_expired(self) -> None:
        now = datetime.now(UTC)
        payload = _payload(
            iat=int((now - timedelta(hours=1)).timestamp()),
            exp=int((now + timedelta(hours=1)).timestamp()),
            session_deadline=int(now.timestamp()),
        )
        with pytest.raises(InvalidTokenError):
            decode_and_validate(_encode(payload), secret_key=_SECRET, now=now)

    def test_session_deadline_one_second_after_now_is_valid_if_exp_ok(self) -> None:
        now = datetime.now(UTC)
        payload = _payload(
            iat=int((now - timedelta(hours=1)).timestamp()),
            exp=int(now.timestamp()) + 1,
            session_deadline=int(now.timestamp()) + 1,
        )
        claims = decode_and_validate(_encode(payload), secret_key=_SECRET, now=now)
        assert claims is not None

    def test_iat_in_future_is_rejected(self) -> None:
        now = datetime.now(UTC)
        payload = _payload(
            iat=int(now.timestamp()) + 10,
            exp=int((now + timedelta(hours=1)).timestamp()),
            session_deadline=int((now + timedelta(days=1)).timestamp()),
        )
        with pytest.raises(InvalidTokenError):
            decode_and_validate(_encode(payload), secret_key=_SECRET, now=now)

    def test_exp_after_session_deadline_is_rejected(self) -> None:
        now = datetime.now(UTC)
        payload = _payload(
            iat=int((now - timedelta(hours=1)).timestamp()),
            exp=int((now + timedelta(days=2)).timestamp()),
            session_deadline=int((now + timedelta(days=1)).timestamp()),
        )
        with pytest.raises(InvalidTokenError):
            decode_and_validate(_encode(payload), secret_key=_SECRET, now=now)


@pytest.mark.unit
class TestDecodeAndValidateClaimShape:
    def test_missing_claim_is_rejected(self) -> None:
        now = datetime.now(UTC)
        payload = _payload()
        del payload["session_deadline"]
        with pytest.raises(InvalidTokenError):
            decode_and_validate(_encode(payload), secret_key=_SECRET, now=now)

    def test_extra_claim_is_rejected(self) -> None:
        now = datetime.now(UTC)
        payload = _payload(extra="unexpected")
        with pytest.raises(InvalidTokenError):
            decode_and_validate(_encode(payload), secret_key=_SECRET, now=now)

    def test_wrong_issuer_is_rejected(self) -> None:
        now = datetime.now(UTC)
        payload = _payload(iss="not-sentinel")
        with pytest.raises(InvalidTokenError):
            decode_and_validate(_encode(payload), secret_key=_SECRET, now=now)

    def test_non_uuid_sub_is_rejected(self) -> None:
        now = datetime.now(UTC)
        payload = _payload(sub="not-a-uuid")
        with pytest.raises(InvalidTokenError):
            decode_and_validate(_encode(payload), secret_key=_SECRET, now=now)

    def test_non_canonical_uuid_sub_is_rejected(self) -> None:
        now = datetime.now(UTC)
        payload = _payload(sub=str(uuid.uuid4()).upper())
        with pytest.raises(InvalidTokenError):
            decode_and_validate(_encode(payload), secret_key=_SECRET, now=now)

    def test_non_uuid_session_id_is_rejected(self) -> None:
        now = datetime.now(UTC)
        payload = _payload(session_id="not-a-uuid")
        with pytest.raises(InvalidTokenError):
            decode_and_validate(_encode(payload), secret_key=_SECRET, now=now)

    def test_boolean_iat_is_rejected(self) -> None:
        """JSON booleans must not be accepted as integer timing claims."""
        now = datetime.now(UTC)
        payload = _payload(iat=True)
        with pytest.raises(InvalidTokenError):
            decode_and_validate(_encode(payload), secret_key=_SECRET, now=now)

    def test_string_exp_is_rejected(self) -> None:
        now = datetime.now(UTC)
        payload = _payload(exp="not-an-int")
        with pytest.raises(InvalidTokenError):
            decode_and_validate(_encode(payload), secret_key=_SECRET, now=now)


@pytest.mark.unit
class TestDecodeAndValidateSignatureAndAlgorithm:
    def test_invalid_signature_is_rejected(self) -> None:
        now = datetime.now(UTC)
        token = _encode(_payload(), secret=_OTHER_SECRET)
        with pytest.raises(InvalidTokenError):
            decode_and_validate(token, secret_key=_SECRET, now=now)

    def test_malformed_token_is_rejected(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(InvalidTokenError):
            decode_and_validate("not-a-jwt-at-all", secret_key=_SECRET, now=now)

    def test_algorithm_substitution_is_rejected(self) -> None:
        """A token signed with a different algorithm (HS512) must be
        rejected — only HS256 is accepted."""
        now = datetime.now(UTC)
        # A 64-byte secret avoids PyJWT's InsecureKeyLengthWarning for
        # HS512 (irrelevant to what this test verifies: that a
        # differently-algorithm-signed token is rejected regardless of
        # its own key strength).
        token = _encode(_payload(), secret="c" * 64, alg="HS512")
        with pytest.raises(InvalidTokenError):
            decode_and_validate(token, secret_key=_SECRET, now=now)

    def test_none_algorithm_is_rejected(self) -> None:
        now = datetime.now(UTC)
        token = pyjwt.encode(_payload(), "", algorithm="none")
        with pytest.raises(InvalidTokenError):
            decode_and_validate(token, secret_key=_SECRET, now=now)


# ---------------------------------------------------------------------------
# decode_for_logout() — restricted decode contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDecodeForLogout:
    def test_accepts_a_temporally_expired_but_validly_signed_token(self) -> None:
        now = datetime.now(UTC)
        payload = _payload(
            iat=int((now - timedelta(days=10)).timestamp()),
            exp=int((now - timedelta(days=5)).timestamp()),
            session_deadline=int((now - timedelta(days=1)).timestamp()),
        )
        claims = decode_for_logout(_encode(payload), secret_key=_SECRET)
        assert claims.session_id is not None

    def test_still_rejects_invalid_temporal_ordering(self) -> None:
        now = datetime.now(UTC)
        payload = _payload(
            iat=int(now.timestamp()),
            exp=int((now + timedelta(days=2)).timestamp()),
            session_deadline=int((now + timedelta(days=1)).timestamp()),
        )
        with pytest.raises(InvalidTokenError):
            decode_for_logout(_encode(payload), secret_key=_SECRET)

    def test_still_rejects_invalid_signature(self) -> None:
        token = _encode(_payload(), secret=_OTHER_SECRET)
        with pytest.raises(InvalidTokenError):
            decode_for_logout(token, secret_key=_SECRET)

    def test_still_rejects_missing_claims(self) -> None:
        payload = _payload()
        del payload["iss"]
        with pytest.raises(InvalidTokenError):
            decode_for_logout(_encode(payload), secret_key=_SECRET)

    def test_still_rejects_non_uuid_session_id(self) -> None:
        payload = _payload(session_id="not-a-uuid")
        with pytest.raises(InvalidTokenError):
            decode_for_logout(_encode(payload), secret_key=_SECRET)


# ---------------------------------------------------------------------------
# refresh_token() — sliding refresh decision
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRefreshToken:
    def _claims_at(
        self, now: datetime, *, jwt_expiry_hours: int = 72, deadline_days: int = 30
    ) -> tuple[JWTClaims, datetime]:
        issued_at = now
        deadline = now + timedelta(days=deadline_days)
        issued = issue_token(
            user_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            issued_at=issued_at,
            session_deadline=deadline,
            jwt_expiry_hours=jwt_expiry_hours,
            secret_key=_SECRET,
        )
        claims = decode_and_validate(issued.token, secret_key=_SECRET, now=now)
        return claims, deadline

    def test_below_threshold_is_a_noop(self) -> None:
        now = datetime.now(UTC)
        claims, _ = self._claims_at(now, jwt_expiry_hours=72)
        later = now + timedelta(hours=35, seconds=-1)  # just under 50% of 72h
        result = refresh_token(
            claims, now=later, jwt_expiry_hours=72, secret_key=_SECRET
        )
        assert result is None

    def test_exactly_at_threshold_refreshes(self) -> None:
        now = datetime.now(UTC)
        claims, _ = self._claims_at(now, jwt_expiry_hours=72)
        exactly_at_threshold = now + timedelta(hours=36)  # 50% of 72h
        result = refresh_token(
            claims,
            now=exactly_at_threshold,
            jwt_expiry_hours=72,
            secret_key=_SECRET,
        )
        assert result is not None

    def test_just_after_threshold_refreshes(self) -> None:
        now = datetime.now(UTC)
        claims, _ = self._claims_at(now, jwt_expiry_hours=72)
        after = now + timedelta(hours=36, seconds=1)
        result = refresh_token(
            claims, now=after, jwt_expiry_hours=72, secret_key=_SECRET
        )
        assert result is not None

    def test_refreshed_token_preserves_immutable_claims(self) -> None:
        now = datetime.now(UTC)
        claims, _deadline = self._claims_at(now, jwt_expiry_hours=2)
        later = now + timedelta(hours=1)
        result = refresh_token(
            claims, now=later, jwt_expiry_hours=2, secret_key=_SECRET
        )
        assert result is not None
        refreshed_claims = decode_and_validate(
            result.token, secret_key=_SECRET, now=later
        )
        assert refreshed_claims.user_id == claims.user_id
        assert refreshed_claims.session_id == claims.session_id
        assert refreshed_claims.session_deadline == claims.session_deadline
        assert refreshed_claims.issued_at == later.replace(microsecond=0)

    def test_expiration_capped_at_session_deadline(self) -> None:
        now = datetime.now(UTC)
        deadline_days = 2
        claims, deadline = self._claims_at(
            now, jwt_expiry_hours=72, deadline_days=deadline_days
        )
        # Move past the refresh threshold but close to the deadline.
        later = deadline - timedelta(hours=1)
        result = refresh_token(
            claims, now=later, jwt_expiry_hours=72, secret_key=_SECRET
        )
        assert result is not None
        assert result.token_expires_at == deadline.replace(microsecond=0)

    def test_no_refresh_when_deadline_already_reached(self) -> None:
        now = datetime.now(UTC)
        claims, deadline = self._claims_at(now, jwt_expiry_hours=1, deadline_days=1)
        at_or_after_deadline = deadline
        result = refresh_token(
            claims,
            now=at_or_after_deadline,
            jwt_expiry_hours=1,
            secret_key=_SECRET,
        )
        assert result is None

    def test_no_refresh_when_capped_expiration_not_later_than_now(self) -> None:
        """Even when the deadline has not yet been reached, a refresh
        must not issue a token whose capped expiration would not be
        later than `now`. With config-enforced `jwt_expiry_hours >= 1`
        this combination cannot arise from real login/refresh traffic,
        but `refresh_token()` must still guard it defensively when
        invoked directly and independently — see authentication.md,
        Token refresh step 3b, and the explicit call-out that "the
        explicit guard also prevents a restricted or independently
        tested refresh call from issuing a token with no remaining
        lifetime.\""""
        now = datetime.now(UTC)
        claims = JWTClaims(
            user_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            issued_at=now - timedelta(hours=1),
            expires_at=now + timedelta(minutes=1),
            session_deadline=now + timedelta(days=1),
        )
        result = refresh_token(claims, now=now, jwt_expiry_hours=0, secret_key=_SECRET)
        assert result is None

    def test_no_database_write_side_effect(self) -> None:
        """refresh_token() is a pure function — no database session
        parameter exists, so there is nothing to assert beyond the
        signature itself: it accepts no `db` argument."""
        import inspect

        params = inspect.signature(refresh_token).parameters
        assert "db" not in params
