"""Owns the Redis connection so tests can swap in a fake without monkeypatching."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis


@lru_cache
def get_redis() -> Redis:
    from app.core.config import get_settings

    settings = get_settings()
    if os.getenv('REDIS_FAKE') == '1':
        from fakeredis.aioredis import FakeRedis

        return FakeRedis(decode_responses=True)

    from redis.asyncio import Redis

    # protocol=2 (RESP2): Windows Redis 3.x rejects HELLO (RESP3). Redis 7 in Docker
    # still accepts RESP2, so this is safe for local + compose.
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        protocol=2,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


async def close_redis() -> None:
    """Close the pool and forget it, so a later startup builds connections on its
    own event loop instead of reusing sockets from a loop that no longer exists.
    """
    if get_redis.cache_info().currsize:
        await get_redis().aclose()
        get_redis.cache_clear()
