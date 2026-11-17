#!/usr/bin/env python3
"""Pull NECC month sheets into Postgres (same job as Celery).

Usage (from backend/ with .env loaded):
  python scripts/sync_necc_egg_rates.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.modules.market.service import MarketService


async def _main() -> int:
    settings = get_settings()
    if not settings.market_sync_enabled:
        print('MARKET_SYNC_ENABLED=false — nothing to do', file=sys.stderr)
        return 1
    async with SessionLocal() as db:
        service = MarketService(db, settings)
        rows = await service.sync_recent_months()
    print(f'OK — upserted {rows} daily price cells across recent months')
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(_main()))
