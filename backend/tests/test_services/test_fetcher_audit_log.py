"""Tests for the fetcher audit trail service
(backend/app/services/fetcher_audit_log.py).

See docs/features/platform/fetcher-infrastructure.md (Audit Trail,
FetcherAuditLog Service, Event Field Values) for the contract under
test: the `FetcherAuditEventType` field contract and the deterministic
validation order implemented by `FetcherAuditLog.log_event()`.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import FetcherAuditEventType, IdentityAuditEventType
from app.models.fetcher_audit_event import FetcherAuditEvent
from app.models.fetcher_config import FetcherConfig
from app.models.user import User
from app.services import base_audit_log
from app.services.fetcher_audit_log import FetcherAuditLog
from tests.support.database import rollback_test_scope

# No module-level `pytestmark`: pytest marks accumulate rather than
# override, so every class below carries its own explicit marker
# (mirrors tests/test_services/test_identity_audit_log.py).


@pytest.mark.unit
class TestRegistration:
    def test_registered_under_fetcher_name(self) -> None:
        assert base_audit_log.AUDIT_LOG_REGISTRY["fetcher"] is FetcherAuditLog

    def test_binds_to_fetcher_audit_event_model(self) -> None:
        assert FetcherAuditLog.model_class is FetcherAuditEvent

    def test_description(self) -> None:
        assert FetcherAuditLog.description == "Administrative actions on fetchers"


@pytest.mark.integration
class TestFetcherAuditLogNoPayloadEventTypes:
    """`disabled`, `enabled`, and `triggered` require `old_value`,
    `new_value`, and `detail` to all be `None`."""

    @pytest.mark.parametrize(
        "event_type",
        [
            FetcherAuditEventType.DISABLED,
            FetcherAuditEventType.ENABLED,
            FetcherAuditEventType.TRIGGERED,
        ],
    )
    async def test_creates_expected_row(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
        event_type: FetcherAuditEventType,
    ) -> None:
        admin = await user_factory()
        config = await fetcher_config_factory()

        await FetcherAuditLog.log_event(
            db_session,
            event_type=event_type,
            fetcher_name=config.fetcher_name,
            user_id=admin.id,
        )

        rows = (await db_session.execute(select(FetcherAuditEvent))).scalars().all()
        assert len(rows) == 1
        assert rows[0].event_type == event_type.value
        assert rows[0].fetcher_name == config.fetcher_name
        assert rows[0].user_id == admin.id
        assert rows[0].old_value is None
        assert rows[0].new_value is None
        assert rows[0].detail is None

    @pytest.mark.parametrize(
        "event_type",
        [
            FetcherAuditEventType.DISABLED,
            FetcherAuditEventType.ENABLED,
            FetcherAuditEventType.TRIGGERED,
        ],
    )
    async def test_rejects_old_value(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
        event_type: FetcherAuditEventType,
    ) -> None:
        admin = await user_factory()
        config = await fetcher_config_factory()

        with pytest.raises(ValueError, match="old_value must be None"):
            await FetcherAuditLog.log_event(
                db_session,
                event_type=event_type,
                fetcher_name=config.fetcher_name,
                user_id=admin.id,
                old_value="0 */6 * * *",
            )

    @pytest.mark.parametrize(
        "event_type",
        [
            FetcherAuditEventType.DISABLED,
            FetcherAuditEventType.ENABLED,
            FetcherAuditEventType.TRIGGERED,
        ],
    )
    async def test_rejects_new_value(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
        event_type: FetcherAuditEventType,
    ) -> None:
        admin = await user_factory()
        config = await fetcher_config_factory()

        with pytest.raises(ValueError, match="new_value must be None"):
            await FetcherAuditLog.log_event(
                db_session,
                event_type=event_type,
                fetcher_name=config.fetcher_name,
                user_id=admin.id,
                new_value="0 */4 * * *",
            )

    @pytest.mark.parametrize(
        "event_type",
        [
            FetcherAuditEventType.DISABLED,
            FetcherAuditEventType.ENABLED,
            FetcherAuditEventType.TRIGGERED,
        ],
    )
    async def test_rejects_detail(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
        event_type: FetcherAuditEventType,
    ) -> None:
        admin = await user_factory()
        config = await fetcher_config_factory()

        with pytest.raises(ValueError, match="detail must be None"):
            await FetcherAuditLog.log_event(
                db_session,
                event_type=event_type,
                fetcher_name=config.fetcher_name,
                user_id=admin.id,
                detail={"field": "schedule_override"},
            )


@pytest.mark.integration
class TestFetcherAuditLogConfigChangedStandardFields:
    @pytest.mark.parametrize(
        "field", ["schedule_override", "run_timeout", "request_delay"]
    )
    async def test_creates_expected_row(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
        field: str,
    ) -> None:
        admin = await user_factory()
        config = await fetcher_config_factory()

        await FetcherAuditLog.log_event(
            db_session,
            event_type=FetcherAuditEventType.CONFIG_CHANGED,
            fetcher_name=config.fetcher_name,
            user_id=admin.id,
            old_value="3600",
            new_value="1800",
            detail={"field": field},
        )

        rows = (await db_session.execute(select(FetcherAuditEvent))).scalars().all()
        assert len(rows) == 1
        assert rows[0].event_type == "config_changed"
        assert rows[0].old_value == "3600"
        assert rows[0].new_value == "1800"
        assert rows[0].detail == {"field": field}

    async def test_allows_null_old_value_when_previously_unset(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        admin = await user_factory()
        config = await fetcher_config_factory()

        await FetcherAuditLog.log_event(
            db_session,
            event_type=FetcherAuditEventType.CONFIG_CHANGED,
            fetcher_name=config.fetcher_name,
            user_id=admin.id,
            old_value=None,
            new_value="0 */4 * * *",
            detail={"field": "schedule_override"},
        )

        rows = (await db_session.execute(select(FetcherAuditEvent))).scalars().all()
        assert rows[0].old_value is None
        assert rows[0].new_value == "0 */4 * * *"

    async def test_allows_null_new_value_when_reset_to_default(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        admin = await user_factory()
        config = await fetcher_config_factory()

        await FetcherAuditLog.log_event(
            db_session,
            event_type=FetcherAuditEventType.CONFIG_CHANGED,
            fetcher_name=config.fetcher_name,
            user_id=admin.id,
            old_value="0 */4 * * *",
            new_value=None,
            detail={"field": "schedule_override"},
        )

        rows = (await db_session.execute(select(FetcherAuditEvent))).scalars().all()
        assert rows[0].new_value is None

    async def test_rejects_key_present_for_standard_field(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        admin = await user_factory()
        config = await fetcher_config_factory()

        with pytest.raises(ValueError, match="key must not be provided"):
            await FetcherAuditLog.log_event(
                db_session,
                event_type=FetcherAuditEventType.CONFIG_CHANGED,
                fetcher_name=config.fetcher_name,
                user_id=admin.id,
                old_value="3600",
                new_value="1800",
                detail={"field": "run_timeout", "key": "unexpected"},
            )


@pytest.mark.integration
class TestFetcherAuditLogConfigChangedCustomSettings:
    async def test_creates_expected_row(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        admin = await user_factory()
        config = await fetcher_config_factory()

        await FetcherAuditLog.log_event(
            db_session,
            event_type=FetcherAuditEventType.CONFIG_CHANGED,
            fetcher_name=config.fetcher_name,
            user_id=admin.id,
            old_value="100",
            new_value="500",
            detail={"field": "custom_settings", "key": "results_per_page"},
        )

        rows = (await db_session.execute(select(FetcherAuditEvent))).scalars().all()
        assert len(rows) == 1
        assert rows[0].detail == {
            "field": "custom_settings",
            "key": "results_per_page",
        }

    async def test_rejects_missing_key(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        admin = await user_factory()
        config = await fetcher_config_factory()

        with pytest.raises(ValueError, match="detail\\.key is required"):
            await FetcherAuditLog.log_event(
                db_session,
                event_type=FetcherAuditEventType.CONFIG_CHANGED,
                fetcher_name=config.fetcher_name,
                user_id=admin.id,
                old_value="100",
                new_value="500",
                detail={"field": "custom_settings"},
            )


@pytest.mark.integration
class TestFetcherAuditLogConfigChangedDetailValidation:
    async def test_rejects_missing_detail(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        admin = await user_factory()
        config = await fetcher_config_factory()

        with pytest.raises(ValueError, match="detail is required"):
            await FetcherAuditLog.log_event(
                db_session,
                event_type=FetcherAuditEventType.CONFIG_CHANGED,
                fetcher_name=config.fetcher_name,
                user_id=admin.id,
                old_value="3600",
                new_value="1800",
            )

    async def test_rejects_non_mapping_detail(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        admin = await user_factory()
        config = await fetcher_config_factory()

        with pytest.raises(ValueError, match="detail must be a JSON object"):
            await FetcherAuditLog.log_event(
                db_session,
                event_type=FetcherAuditEventType.CONFIG_CHANGED,
                fetcher_name=config.fetcher_name,
                user_id=admin.id,
                old_value="3600",
                new_value="1800",
                detail=["not", "a", "mapping"],  # type: ignore[arg-type]
            )

    async def test_rejects_unknown_key(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        admin = await user_factory()
        config = await fetcher_config_factory()

        with pytest.raises(ValueError, match="unsupported keys"):
            await FetcherAuditLog.log_event(
                db_session,
                event_type=FetcherAuditEventType.CONFIG_CHANGED,
                fetcher_name=config.fetcher_name,
                user_id=admin.id,
                old_value="3600",
                new_value="1800",
                detail={"field": "run_timeout", "extra": "unexpected"},
            )

    async def test_rejects_missing_field_key(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        admin = await user_factory()
        config = await fetcher_config_factory()

        with pytest.raises(ValueError, match="detail\\.field is required"):
            await FetcherAuditLog.log_event(
                db_session,
                event_type=FetcherAuditEventType.CONFIG_CHANGED,
                fetcher_name=config.fetcher_name,
                user_id=admin.id,
                old_value="3600",
                new_value="1800",
                detail={"key": "results_per_page"},
            )

    async def test_rejects_unrecognized_field_value(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        admin = await user_factory()
        config = await fetcher_config_factory()

        with pytest.raises(ValueError, match="unrecognized value"):
            await FetcherAuditLog.log_event(
                db_session,
                event_type=FetcherAuditEventType.CONFIG_CHANGED,
                fetcher_name=config.fetcher_name,
                user_id=admin.id,
                old_value="3600",
                new_value="1800",
                detail={"field": "not_a_real_field"},
            )


@pytest.mark.integration
class TestFetcherAuditLogEventTypeValidation:
    async def test_rejects_non_enum_string(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        admin = await user_factory()
        config = await fetcher_config_factory()

        with pytest.raises(ValueError, match="FetcherAuditEventType"):
            await FetcherAuditLog.log_event(
                db_session,
                event_type="disabled",  # type: ignore[arg-type]
                fetcher_name=config.fetcher_name,
                user_id=admin.id,
            )

    async def test_rejects_a_different_str_enum_with_the_same_value(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        """A member of a different `StrEnum` must be rejected —
        `isinstance` is checked, not value equality."""
        admin = await user_factory()
        config = await fetcher_config_factory()

        with pytest.raises(ValueError, match="FetcherAuditEventType"):
            await FetcherAuditLog.log_event(
                db_session,
                event_type=IdentityAuditEventType.USER_CREATED,  # type: ignore[arg-type]
                fetcher_name=config.fetcher_name,
                user_id=admin.id,
            )


@pytest.mark.integration
class TestFetcherAuditLogHumanActorValidation:
    async def test_rejects_missing_user_id(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        config = await fetcher_config_factory()

        with pytest.raises(ValueError, match="user_id is required"):
            await FetcherAuditLog.log_event(
                db_session,
                event_type=FetcherAuditEventType.DISABLED,
                fetcher_name=config.fetcher_name,
                user_id=None,
            )

        rows = (await db_session.execute(select(FetcherAuditEvent))).scalars().all()
        assert rows == []


@pytest.mark.integration
class TestFetcherAuditLogForeignKeyPropagation:
    async def test_nonexistent_fetcher_name_propagates_integrity_error(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        admin = await user_factory()

        with pytest.raises(IntegrityError):
            await FetcherAuditLog.log_event(
                db_session,
                event_type=FetcherAuditEventType.DISABLED,
                fetcher_name="nonexistent_fetcher",
                user_id=admin.id,
            )

    async def test_nonexistent_user_id_propagates_integrity_error(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        config = await fetcher_config_factory()

        with pytest.raises(IntegrityError):
            await FetcherAuditLog.log_event(
                db_session,
                event_type=FetcherAuditEventType.DISABLED,
                fetcher_name=config.fetcher_name,
                user_id=uuid.uuid4(),
            )


@pytest.mark.integration
class TestFetcherAuditLogTransactionContract:
    async def test_does_not_commit(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        admin = await user_factory()
        config = await fetcher_config_factory()
        commit_spy = AsyncMock(wraps=db_session.commit)
        monkeypatch.setattr(db_session, "commit", commit_spy)

        await FetcherAuditLog.log_event(
            db_session,
            event_type=FetcherAuditEventType.DISABLED,
            fetcher_name=config.fetcher_name,
            user_id=admin.id,
        )

        commit_spy.assert_not_called()

    async def test_flushes_within_the_caller_transaction(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        admin = await user_factory()
        config = await fetcher_config_factory()
        flush_spy = AsyncMock(wraps=db_session.flush)
        monkeypatch.setattr(db_session, "flush", flush_spy)

        await FetcherAuditLog.log_event(
            db_session,
            event_type=FetcherAuditEventType.DISABLED,
            fetcher_name=config.fetcher_name,
            user_id=admin.id,
        )

        flush_spy.assert_called_once()


@pytest.mark.integration
class TestFetcherAuditLogAtomicity:
    async def test_rollback_removes_the_flushed_audit_event(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        """If the caller's transaction rolls back (e.g. because the
        config update itself failed later in the same business
        operation), the audit event created via `log_event()` does not
        survive — it shares the same transaction as the mutation it
        records."""
        admin = await user_factory()
        config = await fetcher_config_factory()

        await FetcherAuditLog.log_event(
            db_session,
            event_type=FetcherAuditEventType.DISABLED,
            fetcher_name=config.fetcher_name,
            user_id=admin.id,
        )
        rows = (await db_session.execute(select(FetcherAuditEvent))).scalars().all()
        assert len(rows) == 1

        await db_session.rollback()

        rows_after = (
            (await db_session.execute(select(FetcherAuditEvent))).scalars().all()
        )
        assert rows_after == []

    async def test_mutation_and_audit_event_share_one_transaction(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        """The `FetcherConfig.enabled` mutation and its `DISABLED`
        audit event are visible together within the same transaction,
        and both disappear together on rollback — demonstrating the
        atomicity guarantee a real service-layer mutation would rely
        on (docs/features/platform/audit-trail-infrastructure.md,
        Atomicity)."""
        admin = await user_factory()
        config = await fetcher_config_factory(enabled=True)
        config_name = config.fetcher_name

        async with rollback_test_scope(db_session):
            config.enabled = False
            await FetcherAuditLog.log_event(
                db_session,
                event_type=FetcherAuditEventType.DISABLED,
                fetcher_name=config_name,
                user_id=admin.id,
            )
            await db_session.flush()

            refreshed = await db_session.get(
                FetcherConfig, config_name, populate_existing=True
            )
            assert refreshed is not None
            assert refreshed.enabled is False
            rows = (await db_session.execute(select(FetcherAuditEvent))).scalars().all()
            assert len(rows) == 1

        reverted = await db_session.get(
            FetcherConfig, config_name, populate_existing=True
        )
        assert reverted is not None
        assert reverted.enabled is True
        rows_after = (
            (await db_session.execute(select(FetcherAuditEvent))).scalars().all()
        )
        assert rows_after == []
