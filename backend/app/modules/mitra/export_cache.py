"""Short-lived Redis cache for repeat unit exports (same user, same unit).

Celery is not used: exports are bounded (<=5000 rows/section) and complete in-process.
Redis only avoids duplicate DB work when a farmer re-downloads within a few minutes.
"""

from __future__ import annotations

import uuid

from app.core.redis_conn import get_redis

MITRA_EXPORT_CACHE_TTL_SECONDS = 120
_MITRA_EXPORT_MAX_BYTES = 2 * 1024 * 1024


def _export_cache_key(
    user_id: uuid.UUID,
    unit_id: uuid.UUID,
    export_format: str,
    include_daily: bool,
) -> str:
    daily = '1' if include_daily else '0'
    return f'mitra:export:{user_id}:{unit_id}:{export_format}:{daily}'


async def read_export_cache(
    user_id: uuid.UUID,
    unit_id: uuid.UUID,
    export_format: str,
    include_daily: bool,
) -> tuple[bytes, str] | None:
    key = _export_cache_key(user_id, unit_id, export_format, include_daily)
    raw = await get_redis().get(key)
    if not raw:
        return None
    if isinstance(raw, str):
        raw = raw.encode('latin1')
    filename_key = f'{key}:fn'
    filename_raw = await get_redis().get(filename_key)
    if isinstance(filename_raw, bytes):
        filename = filename_raw.decode('utf-8')
    else:
        filename = str(filename_raw or 'export.csv')
    return raw, filename


async def write_export_cache(
    user_id: uuid.UUID,
    unit_id: uuid.UUID,
    export_format: str,
    include_daily: bool,
    body: bytes,
    filename: str,
) -> None:
    if len(body) > _MITRA_EXPORT_MAX_BYTES:
        return
    key = _export_cache_key(user_id, unit_id, export_format, include_daily)
    redis = get_redis()
    await redis.set(key, body, ex=MITRA_EXPORT_CACHE_TTL_SECONDS)
    await redis.set(f'{key}:fn', filename, ex=MITRA_EXPORT_CACHE_TTL_SECONDS)


async def drop_export_cache(user_id: uuid.UUID, unit_id: uuid.UUID) -> None:
    redis = get_redis()
    keys: list[str] = []
    for export_format in ('csv', 'json'):
        for include_daily in (True, False):
            key = _export_cache_key(user_id, unit_id, export_format, include_daily)
            keys.extend([key, f'{key}:fn'])
    if keys:
        await redis.delete(*keys)
