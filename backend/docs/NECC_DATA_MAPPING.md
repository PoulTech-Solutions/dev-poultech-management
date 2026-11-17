# NECC website vs PoulTech API

## What NECC provides ([e2necc.com/home/eggprice](https://www.e2necc.com/home/eggprice))

| NECC UI | Data |
|--------|------|
| Month + Year dropdown | One **HTML table per calendar month** (POST `ddlMonth`, `ddlYear`) |
| Rows | City / zone name (35+ centres) |
| Columns | Day **1–30/31** daily suggested price (INR **per 100 eggs**) |
| Last column | **Average** for that month (NECC calculated) |
| Custom arbitrary date | **No** — only month/year sheets; pick another month/year for history |

There is **no JSON API** and **no webhook**. “Tomorrow” appears only when NECC has published that day’s column in the **current month** sheet (otherwise `-`).

## What we store (Postgres)

| Table | Content |
|-------|---------|
| `market_egg_zones` | NECC city names |
| `market_egg_daily` | One row per zone + calendar date + price |
| `market_egg_month_summaries` | NECC **Average** + our computed daily mean |
| `market_sync_runs` | Sync audit |

Celery `sync_necc_egg_rates` pulls the last **`MARKET_SYNC_MONTHS_BACK`** months (default **12**) — same as choosing month/year on NECC **12 times**.

## API mapping (JWT `/api/v1/market`)

| Need | Endpoint |
|------|----------|
| User’s city (profile address) | `GET /market/dashboard` — zone from pincode/state/district + optional `?zone=` |
| Today / yesterday / tomorrow | `dashboard.rates[0].value / previousValue / nextValue` |
| Month graph | `rates[0].month` or `GET /market/eggs/trend?window=month&year=&month=` |
| Year graph (12 months) | `rates[0].year` or `GET /market/eggs/trend?window=year` |
| NECC month average | `necc_sheet_average`, `month_average` on rate card |
| All cities today | `GET /market/eggs/cities-today` |
| City list + today price | `GET /market/zones?include_today=true` |
| Custom month sheet (like NECC) | `GET /market/eggs/sheet?zone=Pune&year=2026&month=11` |
| Compare cities | `GET /market/eggs/compare?zones=Pune,Mumbai (CC)` |

## Profile location (no GPS for now)

Egg rates for the **default** dashboard require profile **pincode + state + district** (or saved `market_preferred_zone`). Browsing another city: `?zone=` or compare endpoints — no address change.