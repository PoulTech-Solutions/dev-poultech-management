# NECC egg rates (backend)

## Architecture (optimized — DB, not per-user NECC calls)

```text
NECC website (month/year HTML sheets)
        ↓  Celery Beat + worker (sync_necc_egg_rates)
Postgres: zones · daily prices · month averages
        ↓  optional Redis cache (dashboard TTL)
FastAPI /api/v1/market/*  (JWT only — app never calls e2necc)
```

- **Day / month / year** charts read **stored** rows (same data as NECC’s table).
- Sync pulls the last `MARKET_SYNC_MONTHS_BACK` months (default **12**) so **year** graphs work.
- Users get **milliseconds** responses; NECC is hit only from the **worker**.

JWT-only market APIs; NECC HTML is fetched **server-side** (never from the mobile/web client).

## Environment

See `backend/.env.example`:

- `NECC_EGG_PRICE_URL` — official NECC sheet POST target
- `MARKET_DEFAULT_ZONE` — fallback when profile city is unmapped (default `Pune`)
- `MARKET_SYNC_ENABLED` — Celery sync on/off
- `MARKET_HTTP_TIMEOUT_SECONDS` — upstream timeout
- `MARKET_DASHBOARD_CACHE_SECONDS` — Redis cache for `/market/dashboard`

See **NECC_DATA_MAPPING.md** for NECC vs API field mapping.

## Endpoints (prefix `/api/v1`)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/market/zones?include_today=true` | Bearer | All NECC cities; optional today price + change |
| GET | `/market/eggs/cities-today` | Bearer | Today rate for **every** synced city |
| GET | `/market/eggs/trend?zone=&window=days\|month\|year&year=&month=` | Bearer | Charts: 7-day, one month, or 12 month averages |
| GET | `/market/eggs/sheet?zone=&year=&month=` | Bearer | Full NECC-like month grid for one city |
| GET | `/market/dashboard?zone=` | Bearer | Today / yesterday / tomorrow + month/year arrays + ticker |
| PATCH | `/market/zone-preference` | Bearer | Save `market_preferred_zone` on profile |
| GET | `/market/eggs/compare?zones=a,b` | Bearer | Today + delta for multiple cities |

Prices are **NECC suggested INR per 100 eggs** (`display_unit: per_100_eggs`). Disclaimer is in `metadata.disclaimer`.

## See APIs on :8000

Market routes exist only after the **market branch image** is running:

```powershell
cd dev-poultech-management\backend
docker compose --profile app up -d --build
alembic upgrade head
```

Open **http://localhost:8000/docs** → tag **market** (use **Authorize** with a JWT).  
If `/market/*` is missing, the API container is still on an older build — rebuild `api` and `worker`.

## Sync (scheduler — no NECC webhook)

NECC does not expose push notifications or a fixed daily publish time. **Celery Beat** runs
`app.tasks.market.sync_necc_egg_rates` on a cron (`MARKET_SYNC_CRON_MINUTE` / `MARKET_SYNC_CRON_HOUR`, UTC).
Default: **every 3 hours**. Docker profile `app` includes a **beat** service plus **worker** on the `scheduled` queue.

When today’s column is not on the sheet yet, APIs use the **latest published day** on the current month sheet
(`metadata.price_as_of`, `metadata.is_live_today=false`) — not hardcoded prices.

Manual trigger (dev):

```python
from app.tasks.market import sync_necc_egg_rates
sync_necc_egg_rates.delay()
```

## Errors

| Code | HTTP | When |
|------|------|------|
| `market_zone_not_found` | 404 | Unknown `zone` query |
| `market_data_unavailable` | 503 | No sync data or missing today price |
| `profile_location_required` | 403 | Dashboard without `?zone=` needs profile pincode+state+district |
| `market_sync_failed` | 502 | Reserved for admin refresh flows |

## Migrations

Run `alembic upgrade head` for `c4e8a91b2f10` (zones/daily/sync) and `e7f2b3c94d21` (month summaries + `tehsil`).