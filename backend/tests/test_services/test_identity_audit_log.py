"""Tests for the identity audit trail service
(backend/app/services/identity_audit_log.py).

See docs/features/identity/identity-audit-log.md for the contract
under test: the `IdentityAuditEventType` field contract, the `detail`
JSONB Schema Contract, the deterministic validation order, and the
512-code-point / 4096-byte limits.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import IdentityAuditEventType
from app.models.identity_audit_event import IdentityAuditEvent
from app.models.user import User
from app.services import base_audit_log
from app.services.identity_audit_log import (
    IdentityAuditLog,
    _measure_and_check_detail_size,
)

pytestmark = pytest.mark.integration


@pytest.mark.unit
class TestRegistration:
    def test_registered_under_identity_name(self) -> None:
        assert base_audit_log.AUDIT_LOG_REGISTRY["identity"] is IdentityAuditLog

    def test_binds_to_identity_audit_event_model(self) -> None:
        assert IdentityAuditLog.model_class is IdentityAuditEvent

    def test_description(self) -> None:
        assert IdentityAuditLog.description == (
            "User lifecycle, roles, API keys, and role mappings"
        )


# Each entry: (event_type, kwargs_builder) where kwargs_builder receives
# (actor_user, target_user) and returns the full log_event() kwargs for
# a minimal, valid invocation of that event type.
_HappyPathCase = tuple[IdentityAuditEventType, Callable[[User, User], dict[str, Any]]]


def _happy_path_cases() -> list[_HappyPathCase]:
    key_id = str(uuid.uuid4())
    return [
        (
            IdentityAuditEventType.USER_CREATED,
            lambda actor, target: {
                "user_id": None,
                "target_user_id": target.id,
                "new_value": "jdoe",
            },
        ),
        (
            IdentityAuditEventType.USER_DEACTIVATED,
            lambda actor, target: {
                "user_id": actor.id,
                "target_user_id": target.id,
                "old_value": "active",
                "new_value": "inactive",
                "detail": {"reason": "manual_deactivation"},
            },
        ),
        (
            IdentityAuditEventType.USER_REACTIVATED,
            lambda actor, target: {
                "user_id": actor.id,
                "target_user_id": target.id,
                "old_value": "inactive",
                "new_value": "active",
            },
        ),
        (
            IdentityAuditEventType.PASSWORD_RESET,
            lambda actor, target: {
                "user_id": actor.id,
                "target_user_id": target.id,
            },
        ),
        (
            IdentityAuditEventType.ROLE_ADDED,
            lambda actor, target: {
                "user_id": actor.id,
                "target_user_id": target.id,
                "new_value": "admin",
            },
        ),
        (
            IdentityAuditEventType.ROLE_REMOVED,
            lambda actor, target: {
                "user_id": actor.id,
                "target_user_id": target.id,
                "old_value": "admin",
            },
        ),
        (
            IdentityAuditEventType.ROLE_MAPPING_CREATED,
            lambda actor, target: {
                "user_id": actor.id,
                "target_user_id": None,
                "new_value": "SecurityTeam -> admin",
                "detail": {
                    "group_name": "SecurityTeam",
                    "role": "admin",
                    "affected_users": 5,
                },
            },
        ),
        (
            IdentityAuditEventType.ROLE_MAPPING_DELETED,
            lambda actor, target: {
                "user_id": actor.id,
                "target_user_id": None,
                "old_value": "SecurityTeam -> admin",
                "detail": {
                    "group_name": "SecurityTeam",
                    "role": "admin",
                    "affected_users": 3,
                },
            },
        ),
        (
            IdentityAuditEventType.USERNAME_CHANGED,
            lambda actor, target: {
                "user_id": actor.id,
                "target_user_id": target.id,
                "old_value": "jdoe",
                "new_value": "jdoe2",
            },
        ),
        (
            IdentityAuditEventType.API_KEY_CREATED,
            lambda actor, target: {
                "user_id": target.id,
                "target_user_id": target.id,
                "new_value": "ci-pipeline",
                "detail": {"key_id": key_id},
            },
        ),
        (
            IdentityAuditEventType.API_KEY_REVOKED,
            lambda actor, target: {
                "user_id": actor.id,
                "target_user_id": target.id,
                "old_value": "ci-pipeline",
                "detail": {"key_id": key_id},
            },
        ),
        (
            IdentityAuditEventType.EMAIL_CHANGED,
            lambda actor, target: {
                "user_id": actor.id,
                "target_user_id": target.id,
                "old_value": "old@example.com",
                "new_value": "new@example.com",
            },
        ),
        (
            IdentityAuditEventType.FULL_NAME_CHANGED,
            lambda actor, target: {
                "user_id": actor.id,
                "target_user_id": target.id,
                "old_value": None,
                "new_value": "Alice Smith",
            },
        ),
        (
            IdentityAuditEventType.MANAGER_CHANGED,
            lambda actor, target: {
                "user_id": None,
                "target_user_id": target.id,
                "old_value": None,
                "new_value": "bwilson",
            },
        ),
    ]


@pytest.mark.parametrize(
    ("event_type", "kwargs_builder"),
    _happy_path_cases(),
    ids=[case[0].value for case in _happy_path_cases()],
)
class TestHappyPathAllEventTypes:
    async def test_creates_exactly_one_event_with_correct_fields(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        event_type: IdentityAuditEventType,
        kwargs_builder: Callable[[User, User], dict[str, Any]],
    ) -> None:
        actor = await user_factory()
        target = await user_factory()
        kwargs = kwargs_builder(actor, target)

        await IdentityAuditLog.log_event(db_session, event_type=event_type, **kwargs)

        rows = (
            (
                await db_session.execute(
                    select(IdentityAuditEvent).where(
                        IdentityAuditEvent.event_type == event_type.value
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        event = rows[0]
        assert event.user_id == kwargs["user_id"]
        assert event.target_user_id == kwargs["target_user_id"]
        assert event.old_value == kwargs.get("old_value")
        assert event.new_value == kwargs.get("new_value")
        assert event.detail == kwargs.get("detail")


@pytest.mark.unit
class TestEventTypeValidation:
    async def test_raw_string_event_type_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        with pytest.raises(ValueError, match="IdentityAuditEventType"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type="user_created",  # type: ignore[arg-type]
                user_id=None,
                target_user_id=target.id,
                new_value="jdoe",
            )


@pytest.mark.integration
class TestFieldPresenceValidation:
    async def test_user_created_missing_target_user_id_rejected(
        self, db_session: AsyncSession
    ) -> None:
        with pytest.raises(ValueError, match="target_user_id"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.USER_CREATED,
                user_id=None,
                target_user_id=None,
                new_value="jdoe",
            )

    async def test_user_created_missing_new_value_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        with pytest.raises(ValueError, match="new_value"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.USER_CREATED,
                user_id=None,
                target_user_id=target.id,
            )

    async def test_user_created_old_value_present_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        with pytest.raises(ValueError, match="old_value"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.USER_CREATED,
                user_id=None,
                target_user_id=target.id,
                old_value="unexpected",
                new_value="jdoe",
            )

    async def test_password_reset_old_value_present_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        with pytest.raises(ValueError, match="old_value"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.PASSWORD_RESET,
                user_id=None,
                target_user_id=target.id,
                old_value="unexpected",
            )

    async def test_password_reset_new_value_present_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        with pytest.raises(ValueError, match="new_value"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.PASSWORD_RESET,
                user_id=None,
                target_user_id=target.id,
                new_value="unexpected",
            )

    async def test_role_mapping_created_missing_actor_rejected(
        self, db_session: AsyncSession
    ) -> None:
        """role_mapping_created is intrinsically administrator-only:
        user_id (the actor) is required."""
        with pytest.raises(ValueError, match="user_id"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.ROLE_MAPPING_CREATED,
                user_id=None,
                target_user_id=None,
                new_value="SecurityTeam -> admin",
                detail={
                    "group_name": "SecurityTeam",
                    "role": "admin",
                    "affected_users": 5,
                },
            )

    async def test_role_mapping_created_target_user_id_present_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        admin = await user_factory()
        target = await user_factory()
        with pytest.raises(ValueError, match="target_user_id"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.ROLE_MAPPING_CREATED,
                user_id=admin.id,
                target_user_id=target.id,
                new_value="SecurityTeam -> admin",
                detail={
                    "group_name": "SecurityTeam",
                    "role": "admin",
                    "affected_users": 5,
                },
            )

    async def test_role_mapping_deleted_missing_actor_rejected(
        self, db_session: AsyncSession
    ) -> None:
        with pytest.raises(ValueError, match="user_id"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.ROLE_MAPPING_DELETED,
                user_id=None,
                target_user_id=None,
                old_value="SecurityTeam -> admin",
                detail={
                    "group_name": "SecurityTeam",
                    "role": "admin",
                    "affected_users": 3,
                },
            )

    async def test_api_key_created_missing_actor_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        with pytest.raises(ValueError, match="user_id"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.API_KEY_CREATED,
                user_id=None,
                target_user_id=target.id,
                new_value="ci-pipeline",
                detail={"key_id": str(uuid.uuid4())},
            )

    async def test_api_key_created_actor_and_target_may_differ(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        """log_event() only requires both user_id and target_user_id to
        be non-NULL for api_key_created; it does not enforce actor ==
        target. That coherence (actor = owner = target) is guaranteed
        by `api_key_service.create_key()` deriving both from the same
        parameter — a caller obligation, not a log_event() invariant."""
        actor = await user_factory()
        target = await user_factory()

        await IdentityAuditLog.log_event(
            db_session,
            event_type=IdentityAuditEventType.API_KEY_CREATED,
            user_id=actor.id,
            target_user_id=target.id,
            new_value="ci-pipeline",
            detail={"key_id": str(uuid.uuid4())},
        )

        row = (
            await db_session.execute(
                select(IdentityAuditEvent).where(
                    IdentityAuditEvent.event_type == "api_key_created"
                )
            )
        ).scalar_one()
        assert row.user_id == actor.id
        assert row.target_user_id == target.id

    async def test_manager_changed_actor_present_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        """manager_changed is intrinsically system-only: user_id (the
        actor) must be NULL."""
        actor = await user_factory()
        target = await user_factory()
        with pytest.raises(ValueError, match="user_id"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.MANAGER_CHANGED,
                user_id=actor.id,
                target_user_id=target.id,
            )


@pytest.mark.integration
class TestDetailValidation:
    async def test_event_type_without_detail_support_rejects_non_null(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        with pytest.raises(ValueError, match="does not support a detail payload"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.PASSWORD_RESET,
                user_id=None,
                target_user_id=target.id,
                detail={"source": "external_sync"},
            )

    async def test_event_type_requiring_detail_rejects_null(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        with pytest.raises(ValueError, match="requires a detail payload"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.USER_DEACTIVATED,
                user_id=None,
                target_user_id=target.id,
                old_value="active",
                new_value="inactive",
                detail=None,
            )

    async def test_non_mapping_detail_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        with pytest.raises(ValueError, match="JSON object"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.USER_CREATED,
                user_id=None,
                target_user_id=target.id,
                new_value="jdoe",
                detail=["not", "a", "mapping"],  # type: ignore[arg-type]
            )

    async def test_empty_detail_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        with pytest.raises(ValueError, match="empty"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.USER_CREATED,
                user_id=None,
                target_user_id=target.id,
                new_value="jdoe",
                detail={},
            )

    async def test_unknown_key_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        with pytest.raises(ValueError, match="unsupported keys"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.USER_CREATED,
                user_id=None,
                target_user_id=target.id,
                new_value="jdoe",
                detail={"unexpected_key": "value"},
            )

    async def test_missing_required_key_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        admin = await user_factory()
        with pytest.raises(ValueError, match="missing required keys"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.ROLE_MAPPING_CREATED,
                user_id=admin.id,
                target_user_id=None,
                new_value="SecurityTeam -> admin",
                detail={"group_name": "SecurityTeam", "role": "admin"},
            )

    async def test_source_wrong_literal_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        with pytest.raises(ValueError, match="external_sync"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.USER_CREATED,
                user_id=None,
                target_user_id=target.id,
                new_value="jdoe",
                detail={"source": "manual"},
            )

    async def test_role_added_mapping_without_source_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        with pytest.raises(ValueError, match="must be provided together"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.ROLE_ADDED,
                user_id=None,
                target_user_id=target.id,
                new_value="admin",
                detail={"mapping": "SecurityTeam"},
            )

    async def test_role_added_source_without_mapping_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        with pytest.raises(ValueError, match="must be provided together"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.ROLE_ADDED,
                user_id=None,
                target_user_id=target.id,
                new_value="admin",
                detail={"source": "external_sync"},
            )

    async def test_role_added_source_and_mapping_together_accepted(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        await IdentityAuditLog.log_event(
            db_session,
            event_type=IdentityAuditEventType.ROLE_ADDED,
            user_id=None,
            target_user_id=target.id,
            new_value="admin",
            detail={"source": "external_sync", "mapping": "SecurityTeam"},
        )
        row = (
            await db_session.execute(
                select(IdentityAuditEvent).where(
                    IdentityAuditEvent.event_type == "role_added"
                )
            )
        ).scalar_one()
        assert row.detail == {"source": "external_sync", "mapping": "SecurityTeam"}

    async def test_affected_users_wrong_type_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        admin = await user_factory()
        with pytest.raises(ValueError, match="affected_users"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.ROLE_MAPPING_CREATED,
                user_id=admin.id,
                target_user_id=None,
                new_value="SecurityTeam -> admin",
                detail={
                    "group_name": "SecurityTeam",
                    "role": "admin",
                    "affected_users": "5",
                },
            )

    async def test_affected_users_boolean_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        """A boolean is not an integer for this contract (detail JSONB
        Schema Contract, Notes) — bool is a subclass of int in Python."""
        admin = await user_factory()
        with pytest.raises(ValueError, match="affected_users"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.ROLE_MAPPING_CREATED,
                user_id=admin.id,
                target_user_id=None,
                new_value="SecurityTeam -> admin",
                detail={
                    "group_name": "SecurityTeam",
                    "role": "admin",
                    "affected_users": True,
                },
            )

    async def test_affected_users_negative_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        admin = await user_factory()
        with pytest.raises(ValueError, match="affected_users"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.ROLE_MAPPING_CREATED,
                user_id=admin.id,
                target_user_id=None,
                new_value="SecurityTeam -> admin",
                detail={
                    "group_name": "SecurityTeam",
                    "role": "admin",
                    "affected_users": -1,
                },
            )

    async def test_key_id_invalid_uuid_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        with pytest.raises(ValueError, match="key_id"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.API_KEY_CREATED,
                user_id=target.id,
                target_user_id=target.id,
                new_value="ci-pipeline",
                detail={"key_id": "not-a-uuid"},
            )

    async def test_key_id_non_canonical_uppercase_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        non_canonical = str(uuid.uuid4()).upper()
        with pytest.raises(ValueError, match="key_id"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.API_KEY_CREATED,
                user_id=target.id,
                target_user_id=target.id,
                new_value="ci-pipeline",
                detail={"key_id": non_canonical},
            )

    async def test_key_id_non_string_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        with pytest.raises(ValueError, match="key_id"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.API_KEY_CREATED,
                user_id=target.id,
                target_user_id=target.id,
                new_value="ci-pipeline",
                detail={"key_id": 12345},
            )

    async def test_mapping_wrong_type_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        with pytest.raises(ValueError, match="mapping"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.ROLE_ADDED,
                user_id=None,
                target_user_id=target.id,
                new_value="admin",
                detail={"source": "external_sync", "mapping": 123},
            )


@pytest.mark.integration
class TestOldNewValueTruncation:
    async def test_value_within_limit_is_not_truncated(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        value = "a" * 512
        await IdentityAuditLog.log_event(
            db_session,
            event_type=IdentityAuditEventType.USER_CREATED,
            user_id=None,
            target_user_id=target.id,
            new_value=value,
        )
        row = (
            await db_session.execute(
                select(IdentityAuditEvent).where(
                    IdentityAuditEvent.event_type == "user_created"
                )
            )
        ).scalar_one()
        assert row.new_value == value
        assert len(row.new_value) == 512

    async def test_value_over_limit_is_truncated_to_512_codepoints(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        value = "a" * 600
        await IdentityAuditLog.log_event(
            db_session,
            event_type=IdentityAuditEventType.USER_CREATED,
            user_id=None,
            target_user_id=target.id,
            new_value=value,
        )
        row = (
            await db_session.execute(
                select(IdentityAuditEvent).where(
                    IdentityAuditEvent.event_type == "user_created"
                )
            )
        ).scalar_one()
        assert row.new_value == "a" * 512
        assert len(row.new_value) == 512

    async def test_multibyte_astral_characters_truncated_by_codepoint(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        """Each emoji below is a single Unicode code point outside the
        BMP (astral plane), encoded as a UTF-8 4-byte sequence and a
        UTF-16 surrogate pair. Truncation must count code points, not
        UTF-16 code units or bytes."""
        target = await user_factory()
        value = "\U0001f600" * 600  # 😀 repeated 600 times
        await IdentityAuditLog.log_event(
            db_session,
            event_type=IdentityAuditEventType.USER_CREATED,
            user_id=None,
            target_user_id=target.id,
            new_value=value,
        )
        row = (
            await db_session.execute(
                select(IdentityAuditEvent).where(
                    IdentityAuditEvent.event_type == "user_created"
                )
            )
        ).scalar_one()
        assert row.new_value == "\U0001f600" * 512
        assert len(row.new_value) == 512

    async def test_none_remains_none(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        await IdentityAuditLog.log_event(
            db_session,
            event_type=IdentityAuditEventType.PASSWORD_RESET,
            user_id=None,
            target_user_id=target.id,
        )
        row = (
            await db_session.execute(
                select(IdentityAuditEvent).where(
                    IdentityAuditEvent.event_type == "password_reset"
                )
            )
        ).scalar_one()
        assert row.old_value is None
        assert row.new_value is None


def _serialized_detail_size(detail: dict[str, Any]) -> int:
    """Mirror the production serialization exactly, for constructing
    precise byte-boundary test fixtures (see
    `IdentityAuditLog.log_event()`, `_measure_and_check_detail_size`)."""
    serialized = json.dumps(
        detail,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return len(serialized.encode("utf-8"))


@pytest.mark.integration
class TestDetailSizeBoundary:
    async def test_exactly_4096_bytes_accepted(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        key_id = str(uuid.uuid4())
        overhead = _serialized_detail_size({"key_id": key_id, "reason": ""})
        padding = "a" * (4096 - overhead)
        detail = {"key_id": key_id, "reason": padding}
        assert _serialized_detail_size(detail) == 4096

        await IdentityAuditLog.log_event(
            db_session,
            event_type=IdentityAuditEventType.API_KEY_REVOKED,
            user_id=None,
            target_user_id=target.id,
            old_value="ci-pipeline",
            detail=detail,
        )
        row = (
            await db_session.execute(
                select(IdentityAuditEvent).where(
                    IdentityAuditEvent.event_type == "api_key_revoked"
                )
            )
        ).scalar_one()
        assert row.detail == detail

    async def test_4097_bytes_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        key_id = str(uuid.uuid4())
        overhead = _serialized_detail_size({"key_id": key_id, "reason": ""})
        padding = "a" * (4097 - overhead)
        detail = {"key_id": key_id, "reason": padding}
        assert _serialized_detail_size(detail) == 4097

        with pytest.raises(ValueError, match="4096"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.API_KEY_REVOKED,
                user_id=None,
                target_user_id=target.id,
                old_value="ci-pipeline",
                detail=detail,
            )

        rows = (
            (
                await db_session.execute(
                    select(IdentityAuditEvent).where(
                        IdentityAuditEvent.event_type == "api_key_revoked"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows == []

    async def test_multibyte_utf8_boundary_measured_in_bytes(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        """Each '€' character encodes to 3 UTF-8 bytes but is a single
        Python `str` character — the size limit must be measured in
        encoded bytes, not characters."""
        target = await user_factory()
        key_id = str(uuid.uuid4())
        overhead = _serialized_detail_size({"key_id": key_id, "reason": ""})
        remaining = 4096 - overhead
        char_count = remaining // 3
        padding = "\u20ac" * char_count  # '€', 3 bytes each in UTF-8
        detail = {"key_id": key_id, "reason": padding}
        size = _serialized_detail_size(detail)
        assert size <= 4096

        # Accepted at or under the boundary.
        await IdentityAuditLog.log_event(
            db_session,
            event_type=IdentityAuditEventType.API_KEY_REVOKED,
            user_id=None,
            target_user_id=target.id,
            old_value="ci-pipeline",
            detail=detail,
        )

        # One more '€' pushes at least 3 bytes past any remaining slack,
        # guaranteeing the limit is exceeded.
        over_detail = {"key_id": key_id, "reason": padding + "\u20ac" * 10}
        with pytest.raises(ValueError, match="4096"):
            await IdentityAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.API_KEY_REVOKED,
                user_id=None,
                target_user_id=target.id,
                old_value="ci-pipeline-2",
                detail=over_detail,
            )


@pytest.mark.integration
class TestNoCommit:
    async def test_does_not_commit(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        commit_spy = AsyncMock(wraps=db_session.commit)
        monkeypatch.setattr(db_session, "commit", commit_spy)

        await IdentityAuditLog.log_event(
            db_session,
            event_type=IdentityAuditEventType.USER_CREATED,
            user_id=None,
            target_user_id=target.id,
            new_value="jdoe",
        )

        commit_spy.assert_not_called()

    async def test_flushes_within_the_caller_transaction(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()
        flush_spy = AsyncMock(wraps=db_session.flush)
        monkeypatch.setattr(db_session, "flush", flush_spy)

        await IdentityAuditLog.log_event(
            db_session,
            event_type=IdentityAuditEventType.USER_CREATED,
            user_id=None,
            target_user_id=target.id,
            new_value="jdoe",
        )

        flush_spy.assert_called_once()


@pytest.mark.integration
class TestRollbackAtomicity:
    async def test_rollback_removes_the_flushed_audit_event(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory()

        await IdentityAuditLog.log_event(
            db_session,
            event_type=IdentityAuditEventType.USER_CREATED,
            user_id=None,
            target_user_id=target.id,
            new_value="jdoe",
        )
        rows = (await db_session.execute(select(IdentityAuditEvent))).scalars().all()
        assert len(rows) == 1

        await db_session.rollback()

        rows_after = (
            (await db_session.execute(select(IdentityAuditEvent))).scalars().all()
        )
        assert rows_after == []


@pytest.mark.unit
class TestMeasureAndCheckDetailSizeInternal:
    """Direct unit tests for the private
    `_measure_and_check_detail_size()` helper, covering the defensive
    JSON-encoding-failure branch that `log_event()`'s own schema
    validation makes unreachable through the public API (every
    documented `detail` value is a `str` or non-boolean `int`, both
    always JSON-serializable). The identity-audit-log.md contract
    explicitly lists "JSON encoding failure" among the conditions that
    raise `ValueError`, so this branch is exercised directly."""

    def test_unserializable_value_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="failed to serialize"):
            _measure_and_check_detail_size({"key_id": {1, 2, 3}})  # type: ignore[dict-item]

    def test_within_limit_does_not_raise(self) -> None:
        _measure_and_check_detail_size({"key_id": "a" * 10})
