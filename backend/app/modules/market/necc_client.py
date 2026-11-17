from __future__ import annotations

import httpx
import structlog

from app.core.config import Settings

log = structlog.get_logger()


async def fetch_necc_sheet_html(settings: Settings, *, year: int, month: int) -> str:
    url = settings.necc_egg_price_url
    data = {
        'ddlMonth': f'{month:02d}',
        'ddlYear': str(year),
        'btnReport': 'Get Sheet',
    }
    timeout = httpx.Timeout(settings.market_http_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.post(url, data=data)
        response.raise_for_status()
        text = response.text
    if 'NECC SUGGESTED EGG PRICES' not in text:
        log.warning('necc_html_unexpected', year=year, month=month, status=response.status_code)
        raise ValueError('Unexpected NECC response body')
    return text


def parse_necc_html(html: str):
    from app.modules.market.necc_parser import parse_necc_suggested_prices

    return parse_necc_suggested_prices(html)
