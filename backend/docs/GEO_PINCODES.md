# India pincode reference (geo)

## Why

Egg rates need **pincode + state + district** on the profile. Free-text pincodes are easy to fake; the app should **pick from a validated list** backed by India Post data.

## Architecture (fast, no full list in the app)

| Piece | Role |
|-------|------|
| `geo_pincodes` table | One row per pincode → canonical `state`, `district`, optional `tehsil` |
| `GET /geo/pincodes/search?q=411` | Prefix search, max 15 rows (JWT + rate limit) — typeahead only |
| `GET /geo/pincodes/{pincode}` | Single lookup (Redis cache, 24h default) |
| `PATCH /me/profile` | If `pincode` set and `GEO_PINCODE_STRICT=true`, pincode **must** exist; state/district **auto-filled** from master |

**Do not** ship the full ~165k pincode list to the mobile/web bundle. The client calls search after **3+ digits** (debounced).

## Data load

1. Migration `a9c4e1f82b30` creates the table and seeds **12 metros** for dev/tests.
2. Production: download the public India pincode CSV (e.g. data.gov.in *All India Pincode Directory*), then:

```powershell
cd dev-poultech-management\backend
docker compose exec api python -m scripts.import_india_pincodes --csv /path/in/container/pincodes.csv
```

Or on host with venv: `python -m scripts.import_india_pincodes --csv .\data\pincodes.csv`

Re-import yearly or when India Post updates; no daily cron.

## Environment

| Variable | Default | Meaning |
|----------|---------|---------|
| `GEO_PINCODE_STRICT` | `true` | Reject unknown pincodes on profile update |
| `GEO_PINCODE_SEARCH_MIN_DIGITS` | `3` | Min prefix length for search |
| `GEO_PINCODE_SEARCH_LIMIT` | `15` | Max autocomplete results |
| `GEO_PINCODE_CACHE_SECONDS` | `86400` | Redis TTL for `GET /geo/pincodes/{pincode}` |

If the table is **empty** and strict mode is on, profile pincode validation is **skipped** (logged warning) so local dev is not blocked until import runs.

## Frontend (next)

- Debounced search → show `label` (`411001 · Pune, Maharashtra`)
- On select → PATCH profile with `pincode` only; backend sets state/district/tehsil