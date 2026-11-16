# Frontend (Expo) integration

## URLs

| Service | Local URL |
|---------|-----------|
| Backend API | `http://localhost:8000/api/v1` |
| OpenAPI | `http://localhost:8000/docs` |
| Frontend web (Docker) | `http://localhost:8088` or `http://127.0.0.1:8088` |
| Frontend dev (Expo web) | `http://localhost:8081` or `http://localhost:19006` |

## CORS

Copy from `backend/.env.example` (must include every origin the Expo app uses):

```env
CORS_ORIGINS=http://localhost:5173,http://localhost:8088,http://127.0.0.1:8088,http://localhost:8081,http://127.0.0.1:8081,http://localhost:19006
GOOGLE_CLIENT_ID=<same as EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID>
```

Restart the `api` container after changing.

## Auth flow (implemented)

1. `POST /auth/login` → OTP challenge (`otp_session_id`)
2. `POST /auth/verify-otp` → `access_token`
3. `GET /me` → profile mapped to UI user (`user` → `customer`, `farm_owner` → `poultry_owner`)
4. `POST /auth/google` with Google **ID token** (web/native); optional account link when email matches an existing user

See `frontend/docs/BACKEND_API_MAPPING.md` for full field-level mapping.

## Run stack

```powershell
# Terminal 1 — backend
cd dev-poultech-management\backend
docker compose --profile app up -d

# Terminal 2 — frontend web (Expo)
cd frontend
npm install
npm run web

# Or frontend Docker
cd frontend\deploy
docker compose up --build
```

## Test user

Register via API/Swagger or use an existing seeded user. OTP arrives via configured SMTP (Brevo/Mailpit) or Celery worker logs when `EMAIL_BACKEND=console`.