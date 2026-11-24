"""Per-route IP limits. Identity-scoped limits live in the service, where the
request body (and therefore the email) is already parsed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request

from app.core.config import get_settings
from app.core.exceptions import RateLimitError
from app.core.store import rate_limit

# A limit name is a stable identifier: it becomes the Redis key, so renaming one
# resets every bucket. Keep the strings here, not at the call site.
LIMITS: dict[str, tuple[int, int]] = {
    'auth:signup': (5, 3600),
    'auth:login': (10, 3600),
    'auth:verify': (15, 600),
    'auth:forgot': (5, 3600),
    'auth:restore': (5, 3600),
    'auth:google': (10, 3600),
    'profile:patch': (30, 60),
    'profile:email': (5, 3600),
    'account:delete': (3, 3600),
    'sessions:list': (60, 60),
    'market:read': (120, 60),
    'market:write': (30, 60),
    'market:admin': (60, 60),
    'geo:read': (90, 60),
    'admin:read': (60, 60),
    'admin:write': (30, 60),
    'mitra:read': (120, 60),
    'mitra:write': (60, 60),
    'mitra:export': (20, 3600),
}


def client_ip(request: Request) -> str:
    """Only trust X-Forwarded-For when TLS terminates on your own proxy."""
    if get_settings().trust_forwarded_for:
        forwarded = request.headers.get('x-forwarded-for')
        if forwarded:
            return forwarded.split(',')[0].strip()
    return request.client.host if request.client else 'unknown'


def limit(name: str) -> Callable[..., Awaitable[None]]:
    max_count, window = LIMITS[name]

    async def dependency(request: Request) -> None:
        allowed, retry_after = await rate_limit(
            name=name,
            identity=client_ip(request),
            limit=max_count,
            window_seconds=window,
        )
        if not allowed:
            raise RateLimitError(
                'Too many requests from this network, try again later',
                headers={'Retry-After': str(retry_after)},
            )

    return dependency


__all__ = ['LIMITS', 'client_ip', 'limit']
