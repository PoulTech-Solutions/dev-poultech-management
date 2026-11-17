from __future__ import annotations

import asyncio

import structlog
from celery import Task

from app.core.celery_app import celery_app
from app.core.config import get_settings

log = structlog.get_logger()


@celery_app.task(
    name='app.tasks.market.sync_necc_egg_rates',
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={'max_retries': 2},
    soft_time_limit=120,
    time_limit=180,
)
def sync_necc_egg_rates(_self: Task) -> dict:
    settings = get_settings()
    if not settings.market_sync_enabled:
        log.info('necc_sync_skipped', reason='disabled')
        return {'status': 'skipped'}

    async def _run() -> int:
        from app.db.session import SessionLocal
        from app.modules.market.service import MarketService

        async with SessionLocal() as db:
            service = MarketService(db, settings)
            return await service.sync_recent_months()

    try:
        rows = asyncio.run(_run())
        return {'status': 'ok', 'rows': rows}
    except Exception as exc:
        log.warning('necc_sync_task_failed', error=str(exc))
        raise
