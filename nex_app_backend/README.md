# Nex App Backend

FastAPI backend scaffold for the Flutter `nex_app` project.

This project follows the same overall shape as your existing `c:\backend`
service:

- layered `app/` structure
- `bootstrap`, `core`, `db`, `models`, `modules`, `presentation`, `schemas`,
  `services`
- Alembic migrations
- SQLAdmin panel
- `run_dev.py` and `run_prod.py`

This version is intentionally Docker-free and works locally with SQLite by
default, so you can start it quickly on Windows.

## Production hardening included

- environment-aware config validation
- request IDs on every response
- structured error payloads for HTTP, validation, and 500 errors
- safer JWT validation with issuer and audience checks
- password strength policy
- DB session rollback on unhandled errors
- connection pool settings for non-SQLite databases
- optional JSON logs for staging and production

## Included modules

- JWT auth with register, login, refresh, logout, me, change-password
- profile endpoint for signed-in user
- public preview bootstrap endpoint for the current Flutter home UI
- market, invest, and game overview endpoints for the Nex app shell
- health and readiness endpoints
- admin panel for `User` and `AuthSession`

## Project layout

```text
nex_app_backend/
  alembic/
  app/
    bootstrap/
    core/
    db/
    models/
    modules/
    presentation/
    schemas/
    services/
  credentials/
  docs/
  media/
  scripts/
  tests/
  run_dev.py
  run_prod.py
```

## Run locally

```powershell
cd nex_app_backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
alembic upgrade head
python run_dev.py
```

## Run with production settings

Use strong secrets and disable automatic schema creation before running
`run_prod.py`.

```powershell
$env:NEX_ENVIRONMENT="production"
$env:NEX_AUTO_CREATE_SCHEMA="false"
$env:NEX_API_DOCS_ENABLED="false"
$env:NEX_JWT_SECRET_KEY="replace-with-a-long-random-secret"
$env:NEX_ADMIN_PANEL_PASSWORD="replace-with-a-strong-admin-password"
$env:NEX_ADMIN_PANEL_SECRET_KEY="replace-with-a-long-admin-secret"
python run_prod.py --host 0.0.0.0 --port 8000 --workers 2
```

If insecure defaults are left in place, the app will now fail fast on startup in
production mode.

Backend URLs:

- API docs: `http://127.0.0.1:8000/docs`
- Health: `http://127.0.0.1:8000/health`
- Admin panel: `http://127.0.0.1:8000/admin-panel`
- Preview dashboard bootstrap: `http://127.0.0.1:8000/api/v1/profile/preview-bootstrap`

## Quick auth flow

1. `POST /api/v1/auth/register`
2. `POST /api/v1/auth/login`
3. Use returned `accessToken` as `Authorization: Bearer <token>`
4. `GET /api/v1/auth/me`
5. `POST /api/v1/auth/refresh` with refresh token

## Flutter preview integration

The current Flutter app opens straight into home and reads demo dashboard data
from:

`GET /api/v1/profile/preview-bootstrap`

That endpoint is public on purpose so the UI can render without showing the auth
screen first. Signed-in clients can use:

`GET /api/v1/profile/bootstrap`

## Default database

The default DB is:

`sqlite+aiosqlite:///./nex_app_backend.db`

If you want PostgreSQL later, update `NEX_DATABASE_URL` in `.env` and run
Alembic again.

## Recommended next steps

1. Connect Flutter auth screens to `/api/v1/auth/*`
2. Replace demo dashboard responses with real business data
3. Add OTP, Google sign-in token exchange, or phone auth
4. Deploy with PostgreSQL when you move beyond local development
