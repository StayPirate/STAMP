"""Shared test-only helpers for database transaction tests.

See docs/features/platform/testing-strategy.md (Rollback Within a Test) for
the savepoint contract these helpers support.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def rollback_test_scope(session: AsyncSession) -> AsyncIterator[None]:
    """Preserve existing setup and roll back all work performed in the scope.

    The shared ``db_session`` fixture joins an external transaction using
    ``join_transaction_mode="create_savepoint"``. Committing here releases the
    current setup savepoint without committing the external transaction. Work
    in the context starts a new savepoint, which is rolled back on exit.

    Code inside the scope must not commit.
    """
    await session.commit()
    try:
        yield
    finally:
        await session.rollback()
