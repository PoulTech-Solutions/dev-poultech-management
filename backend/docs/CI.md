# Backend continuous integration

GitHub Actions workflow: [`.github/workflows/backend-ci.yml`](../../.github/workflows/backend-ci.yml)

## When it runs

- Every **push** that changes `backend/**` (any branch, including `feature/authentication`)
- Every **pull request** that changes `backend/**`
- **Manual**: Actions → Backend CI → Run workflow

## What it runs

| Step | Command |
|------|---------|
| Lint | `ruff check app scripts` |
| Tests | `pytest` (64 tests) |

## CI environment (no secrets)

| Variable | Value |
|----------|--------|
| `APP_ENV` | `test` |
| `REDIS_FAKE` | `1` (fakeredis + lupa) |
| `EMAIL_BACKEND` | `console` (no SMTP) |
| `RATE_LIMIT_ENABLED` | `false` |
| Postgres | Service container `poultech_test` on port 5432 |
| JWT keys | Generated per run with OpenSSL (not committed) |

## Match CI locally (Docker)

```powershell
cd backend
docker compose run --rm --no-deps -u root `
  -e APP_ENV=test -e REDIS_FAKE=1 -e RATE_LIMIT_ENABLED=false -e EMAIL_BACKEND=console `
  api sh -c "pip install -q -r requirements-dev.txt && ruff check app scripts && pytest"
```

## After push

1. Open https://github.com/PoulTech-Solutions/dev-poultech-management/actions
2. Select the **Backend CI** run for your branch
3. Green = safe to open a PR into `develop` / `main`