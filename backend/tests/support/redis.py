"""Shared test-only helpers for Redis-dependent tests.

See docs/features/platform/testing-strategy.md (Redis Strategy) for the
fixture contract these helpers support.
"""

from __future__ import annotations

import redis.asyncio as redis_asyncio


def redis_url_from_client(client: redis_asyncio.Redis) -> str:
    """Reconstruct the connection URL for an existing async Redis client.

    Used to obtain a raw URL (e.g. for `_check_redis()`, which takes a
    URL rather than a client) from the shared `redis_client` fixture.
    """
    kwargs = client.connection_pool.connection_kwargs
    return f"redis://{kwargs['host']}:{kwargs['port']}/{kwargs.get('db', 0)}"
