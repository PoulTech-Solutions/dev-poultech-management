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
| PUT | `/market/admin/poultry-rates` | Bearer + `market:rates:manage` | Manual culls/broilers per hub (staff/admin) |

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

NECC does not expose push notifications. **Celery Beat** (timezone **Asia/Kolkata**) runs
`app.tasks.market.sync_necc_egg_rates` once per day — default **02:00 IST**
(`MARKET_SYNC_CRON_HOUR=2`, `MARKET_SYNC_CRON_MINUTE=0`).

Each run **upserts** the last `MARKET_SYNC_MONTHS_BACK` month sheets (default 12). Older daily prices already
in Postgres are kept; only changed/new days update. The mobile/web app **never** calls `e2necc` — graphs and
yesterday/today/tomorrow cards read the database (optional Redis cache on `/market/dashboard`), so UI stays fast.

Docker profile `app` includes **beat** + **worker** on the `scheduled` queue. After changing cron in `.env`, recreate beat:

```powershell
docker compose --profile app up -d --force-recreate beat
```

When today’s column is not on the sheet yet, APIs use the **latest published day** on the current month sheet
(`metadata.price_as_of`, `metadata.is_live_today=false`) — not hardcoded prices.

**First time / empty DB:** zones table has no rows until NECC sync runs. API startup triggers a background bootstrap when the table is empty (`MARKET_SYNC_ENABLED=true`). You can also run:

```powershell
docker compose exec api python -m scripts.sync_necc_egg_rates
```

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
| `profile_location_required` | 403 | Unused for dashboard; missing address uses default/nearest hub (`match_status`) |
| `market_sync_failed` | 502 | Reserved for admin refresh flows |

## Migrations

Run `alembic upgrade head` for `c4e8a91b2f10` (zones/daily/sync) and `e7f2b3c94d21` (month summaries + `tehsil`), then `f3a8b2c91d40` (`market_poultry_daily` for culls/broilers).

**Dev Docker DB already has tables but no `alembic_version` row** (e.g. after manual or partial upgrades): stamp the last revision that matches the schema, then upgrade — do **not** run `upgrade` from empty:

```powershell
docker compose run --rm migrate alembic stamp a9c4e1f82b30
docker compose run --rm migrate alembic upgrade head
docker compose --profile app up -d --build api
```

## Culls & broilers (manual, same market location as eggs)

Farmers do **not** pick a separate location type. Culls/broilers use the **same resolution path as eggs**:

1. Profile **`pincode`** (geo reference) + **`state`** + **`district`** → `resolve_zone_name()`  
2. Optional override **`market_preferred_zone`** (`PATCH /market/zone-preference`)  
3. Dashboard `?zone=` for explicit hub (same as eggs)

That resolves to one row in **`market_egg_zones`** (internal **market hub** / city key). Eggs attach NECC sync to that hub; culls/broilers attach manual or future API rows to the **same** `zone_id`. No per-pincode rate table.

- Stored in `market_poultry_daily` with `product` `culls` | `broilers`, calendar `rate_date`, and `source` `manual` (future API sync uses `api`).
- **`PUT /api/v1/market/admin/poultry-rates`** — requires JWT permission **`market:rates:manage`** (roles: `staff`, `admin`, `super_admin` only). Farmers stay read-only via dashboard.
- Body: `product`, `zone` (hub name — same list as `GET /market/zones`), `today`, optional `yesterday` / `tomorrow` prices.
- **`GET /api/v1/market/dashboard`** appends live `culls` and `broilers` rate cards + ticker rows when data exists for the user’s resolved hub.
- After migrate, run `python scripts/seed_identity.py` inside the API container if `market:rates:manage` is missing on staff/admin roles.