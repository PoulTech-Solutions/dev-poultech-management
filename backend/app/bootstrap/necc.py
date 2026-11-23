"""One-time NECC pull when the market tables are empty (fresh DB / new environment)."""

from __future__ import annotations

import asyncio

import structlog

from app.core.config import Settings
from app.db.session import SessionLocal
from app.modules.market.repository import MarketRepository
from app.modules.market.service import MarketService

log = structlog.get_logger()
_bootstrap_task: asyncio.Task[None] | None = None


async def bootstrap_necc_if_empty(settings: Settings) -> None:
    if not settings.market_sync_enabled:
        return
    try:
        async with SessionLocal() as db:
            repo = MarketRepository(db)
            if await repo.list_zone_names():
                return
            log.info('necc_bootstrap_start', reason='no_zones_in_db')
            service = MarketService(db, settings)
            rows = await service.sync_recent_months()
            log.info('necc_bootstrap_done', rows=rows)
    except Exception as exc:
        log.warning('necc_bootstrap_failed', error=str(exc))


def schedule_necc_bootstrap(settings: Settings) -> None:
    """Fire-and-forget on API startup — does not block listening on :8000."""

    async def _run() -> None:
        await bootstrap_necc_if_empty(settings)

    global _bootstrap_task
    try:
        loop = asyncio.get_running_loop()
        _bootstrap_task = loop.create_task(_run())
    except RuntimeError:
        pass
