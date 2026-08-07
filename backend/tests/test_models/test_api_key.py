"""Integration tests for the ApiKey model (backend/app/models/api_key.py).

See docs/data-model.md (ApiKey) and
docs/features/identity/api-key-management.md (API Key Contract) for the
full specification. These tests require real PostgreSQL because they
exercise the partial unique index that SQLite (or an in-memory backend)
would not enforce identically.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import ApiKey
from app.models.user import User


@pytest.mark.integration
class TestApiKeyCreation:
    async def test_create_api_key(
        self,
        api_key_factory: Callable[..., Awaitable[ApiKey]],
    ) -> None:
        key = await api_key_factory()

        assert key.id is not None
        assert key.created_at is not None
        assert key.last_used_at is None
        assert key.expires_at is None
        assert key.revoked_at is None
        assert key.revoked_by is None

    async def test_no_updated_at_column(self) -> None:
        """ApiKey has no `updated_at` column (docs/data-model.md, ApiKey)."""
        assert not hasattr(ApiKey, "updated_at")


@pytest.mark.integration
class TestApiKeyNotNullConstraints:
    async def test_missing_user_id_rejected(self, db_session: AsyncSession) -> None:
        db_session.add(ApiKey(key_hash="a" * 64, prefix="stl_ak_abcd", name="test-key"))
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_missing_key_hash_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory()
        db_session.add(ApiKey(user_id=user.id, prefix="stl_ak_abcd", name="test-key"))
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_missing_prefix_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory()
        db_session.add(ApiKey(user_id=user.id, key_hash="a" * 64, name="test-key"))
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_missing_name_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory()
        db_session.add(ApiKey(user_id=user.id, key_hash="a" * 64, prefix="stl_ak_abcd"))
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestApiKeyNullableColumns:
    async def test_last_used_at_nullable(
        self, api_key_factory: Callable[..., Awaitable[ApiKey]]
    ) -> None:
        key = await api_key_factory()
        assert key.last_used_at is None

    async def test_expires_at_nullable(
        self, api_key_factory: Callable[..., Awaitable[ApiKey]]
    ) -> None:
        key = await api_key_factory()
        assert key.expires_at is None

    async def test_revoked_at_nullable(
        self, api_key_factory: Callable[..., Awaitable[ApiKey]]
    ) -> None:
        key = await api_key_factory()
        assert key.revoked_at is None

    async def test_revoked_by_nullable(
        self, api_key_factory: Callable[..., Awaitable[ApiKey]]
    ) -> None:
        key = await api_key_factory()
        assert key.revoked_by is None

    async def test_expires_at_settable(
        self, api_key_factory: Callable[..., Awaitable[ApiKey]]
    ) -> None:
        expires = datetime.now(UTC) + timedelta(days=90)
        key = await api_key_factory(expires_at=expires)
        assert key.expires_at == expires

    async def test_revocation_fields_settable(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        api_key_factory: Callable[..., Awaitable[ApiKey]],
    ) -> None:
        admin = await user_factory()
        revoked_time = datetime.now(UTC)
        key = await api_key_factory(revoked_at=revoked_time, revoked_by=admin.id)
        assert key.revoked_at == revoked_time
        assert key.revoked_by == admin.id


@pytest.mark.integration
class TestApiKeyUniqueKeyHash:
    async def test_duplicate_key_hash_rejected(
        self,
        db_session: AsyncSession,
        api_key_factory: Callable[..., Awaitable[ApiKey]],
    ) -> None:
        first = await api_key_factory()

        db_session.add(
            ApiKey(
                user_id=first.user_id,
                key_hash=first.key_hash,
                prefix="stl_ak_dupex",
                name="another-key",
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestApiKeyPartialUniqueNameIndex:
    """UNIQUE (user_id, name) WHERE revoked_at IS NULL
    (docs/data-model.md, ApiKey, Indexes).
    """

    async def test_same_active_name_for_same_user_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        api_key_factory: Callable[..., Awaitable[ApiKey]],
    ) -> None:
        user = await user_factory()
        await api_key_factory(user_id=user.id, name="ci-pipeline")

        db_session.add(
            ApiKey(
                user_id=user.id,
                key_hash="b" * 64,
                prefix="stl_ak_other",
                name="ci-pipeline",
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_same_name_allowed_after_first_revoked(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        api_key_factory: Callable[..., Awaitable[ApiKey]],
    ) -> None:
        user = await user_factory()
        first = await api_key_factory(user_id=user.id, name="ci-pipeline")

        first.revoked_at = datetime.now(UTC)
        await db_session.flush()

        db_session.add(
            ApiKey(
                user_id=user.id,
                key_hash="c" * 64,
                prefix="stl_ak_newer",
                name="ci-pipeline",
            )
        )
        # No exception raised: the first key is now revoked, so it no
        # longer participates in the partial unique index.
        await db_session.flush()

    async def test_same_name_allowed_for_different_users(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        api_key_factory: Callable[..., Awaitable[ApiKey]],
    ) -> None:
        user_a = await user_factory()
        user_b = await user_factory()
        await api_key_factory(user_id=user_a.id, name="ci-pipeline")

        db_session.add(
            ApiKey(
                user_id=user_b.id,
                key_hash="d" * 64,
                prefix="stl_ak_userb",
                name="ci-pipeline",
            )
        )
        # No exception raised: the constraint is scoped per user_id.
        await db_session.flush()


@pytest.mark.integration
class TestApiKeyForeignKeys:
    async def test_nonexistent_user_id_rejected(self, db_session: AsyncSession) -> None:
        key = ApiKey(
            user_id=uuid.uuid4(),
            key_hash="e" * 64,
            prefix="stl_ak_bad1",
            name="test-key",
        )
        db_session.add(key)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_nonexistent_revoked_by_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory()
        key = ApiKey(
            user_id=user.id,
            key_hash="f" * 64,
            prefix="stl_ak_bad2",
            name="test-key",
            revoked_at=datetime.now(UTC),
            revoked_by=uuid.uuid4(),
        )
        db_session.add(key)
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestApiKeyNoCascadeOnUserDeletion:
    """`User.api_keys` deliberately has no cascade: user deletion is not
    supported (docs/features/identity/user-service.md, User Deletion).
    A hypothetical `delete(user)` must fail loudly instead of silently
    destroying API key records.
    """

    async def test_deleting_user_with_api_keys_raises(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        api_key_factory: Callable[..., Awaitable[ApiKey]],
    ) -> None:
        user = await user_factory()
        await api_key_factory(user_id=user.id)

        await db_session.delete(user)
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestApiKeyRelationships:
    async def test_api_key_user_relationship(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        api_key_factory: Callable[..., Awaitable[ApiKey]],
    ) -> None:
        user = await user_factory()
        key = await api_key_factory(user_id=user.id)
        await db_session.refresh(key, attribute_names=["user"])

        assert key.user is not None
        assert key.user.id == user.id

    async def test_user_api_keys_relationship(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        api_key_factory: Callable[..., Awaitable[ApiKey]],
    ) -> None:
        user = await user_factory()
        await api_key_factory(user_id=user.id)
        await db_session.refresh(user, attribute_names=["api_keys"])

        assert len(user.api_keys) == 1
        assert user.api_keys[0].user_id == user.id

    async def test_revoking_user_relationship(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        api_key_factory: Callable[..., Awaitable[ApiKey]],
    ) -> None:
        admin = await user_factory()
        key = await api_key_factory(revoked_at=datetime.now(UTC), revoked_by=admin.id)
        await db_session.refresh(key, attribute_names=["revoking_user"])

        assert key.revoking_user is not None
        assert key.revoking_user.id == admin.id

    async def test_revoking_user_disambiguated_from_owner(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        api_key_factory: Callable[..., Awaitable[ApiKey]],
    ) -> None:
        """Two FKs to `user.id` (`user_id`, `revoked_by`) must resolve to
        distinct relationships without ambiguity errors."""
        owner = await user_factory()
        admin = await user_factory()
        key = await api_key_factory(
            user_id=owner.id,
            revoked_at=datetime.now(UTC),
            revoked_by=admin.id,
        )
        await db_session.refresh(key, attribute_names=["user", "revoking_user"])

        assert key.user.id == owner.id
        assert key.revoking_user is not None
        assert key.revoking_user.id == admin.id
        assert key.user.id != key.revoking_user.id


@pytest.mark.integration
class TestApiKeyTimezoneAwareTimestamps:
    async def test_created_at_is_timezone_aware(
        self,
        db_session: AsyncSession,
        api_key_factory: Callable[..., Awaitable[ApiKey]],
    ) -> None:
        key = await api_key_factory()
        await db_session.refresh(key)
        assert key.created_at.tzinfo is not None


@pytest.mark.integration
class TestApiKeySchemaIndexes:
    """Verifies the indexes declared on `api_key` exist with the
    expected columns and partial predicate (docs/data-model.md, ApiKey,
    Indexes).
    """

    async def test_user_id_revoked_at_index_exists(
        self, db_session: AsyncSession
    ) -> None:
        conn = await db_session.connection()
        indexes = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_indexes("api_key")
        )
        index_names = {idx["name"]: idx for idx in indexes}
        assert "ix_api_key_user_id_revoked_at" in index_names
        assert index_names["ix_api_key_user_id_revoked_at"]["column_names"] == [
            "user_id",
            "revoked_at",
        ]

    async def test_partial_unique_name_index_exists_with_predicate(
        self, db_session: AsyncSession
    ) -> None:
        conn = await db_session.connection()
        indexes = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_indexes("api_key")
        )
        index_names = {idx["name"]: idx for idx in indexes}
        assert "uq_api_key_user_id_name_active" in index_names
        index = index_names["uq_api_key_user_id_name_active"]
        assert index["column_names"] == ["user_id", "name"]
        assert index["unique"] is True
        assert "revoked_at" in index["dialect_options"]["postgresql_where"]
