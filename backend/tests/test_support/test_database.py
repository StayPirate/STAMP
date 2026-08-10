"""Tests for shared database transaction test helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from tests.support.database import rollback_test_scope


@pytest.mark.integration
class TestRollbackTestScope:
    async def test_preserves_setup_and_rolls_back_scoped_mutation(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory(full_name=None)
        user_id = user.id

        async with rollback_test_scope(db_session):
            user.full_name = "Changed Name"
            await db_session.flush()

        refreshed = await db_session.get(User, user_id, populate_existing=True)
        assert refreshed is not None
        assert refreshed.full_name is None

    async def test_rolls_back_scoped_mutation_when_exception_escapes(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory(full_name=None)
        user_id = user.id

        async def _mutate_then_fail() -> None:
            async with rollback_test_scope(db_session):
                user.full_name = "Changed Name"
                await db_session.flush()
                raise RuntimeError("simulated failure")

        with pytest.raises(RuntimeError, match="simulated failure"):
            await _mutate_then_fail()

        refreshed = await db_session.get(User, user_id, populate_existing=True)
        assert refreshed is not None
        assert refreshed.full_name is None
