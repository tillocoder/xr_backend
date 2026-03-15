# XR Hodl Backend

FastAPI backend skeleton for scalable community/chat realtime delivery.

## What is included

- FastAPI app entrypoint
- PostgreSQL async session setup
- Redis cache and Pub/Sub bridge
- WebSocket manager for user and room channels
- Optimized community feed endpoint
- Chat REST endpoints for initial load and pagination
- WebSocket event pipeline for chat, notifications, unread count, reactions

## Run locally

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:XR_DATABASE_URL="postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/xrhodl"
$env:XR_REDIS_URL="redis://127.0.0.1:6379/0"
$env:XR_AUTO_CREATE_SCHEMA="false"
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

For quick local throwaway runs you can skip Alembic and let FastAPI create tables:

```powershell
$env:XR_AUTO_CREATE_SCHEMA="true"
uvicorn app.main:app --reload --port 8000
```

## Admin panel

This backend ships with a built-in admin panel at:

- `http://127.0.0.1:8000/admin-panel`

Credentials are controlled via env vars (defaults are dev-only):

- `XR_ADMIN_PANEL_USERNAME`
- `XR_ADMIN_PANEL_PASSWORD`
- `XR_ADMIN_PANEL_SECRET_KEY` (cookie signing key)

## AI news translation (Gemini)

The mobile app can fetch AI-translated crypto news via:

- `GET /api/v1/news/feed?limit=40&lang=uz`
- `GET /api/v1/home/overview?news_limit=10&lang=uz`

And translate arbitrary text (used for full article bodies):

- `POST /api/v1/translation/text` with JSON `{ "text": "...", "targetLang": "uz" }`

### Configure API key / model

Preferred (editable from the admin panel):

1. Run migrations: `alembic upgrade head`
2. Open `http://127.0.0.1:8000/admin-panel`
3. Add/update **AiProviderConfig** row:
   - `provider`: `gemini`
   - `api_key`: your Google Gemini API key
   - `model`: e.g. `gemini-3-flash-preview`
   - `enabled`: `true`

Fallback (env vars):

- `XR_GEMINI_API_KEY` (or `GEMINI_API_KEY`)
- `XR_GEMINI_MODEL` (or `GEMINI_MODEL`)

### Publish rules / retention

- Backend releases **max 1 new article per hour** and **max 12 per day** into the app feed.
- Released items are kept for **10 days** and the client "All news" view caps at **50 items**.

## Cloudflare tunnel for APK testing

This removes the USB or `localhost` dependency for testing on a real phone, but your PC and backend must still stay online. If you want the APK to work while the PC is off, you need to deploy the backend to a real server.

1. Start PostgreSQL and Redis.
2. Start the backend on all interfaces:

```powershell
.\scripts\start_backend_for_tunnel.ps1
```

3. Install Cloudflare Tunnel on Windows:

```powershell
winget install --id Cloudflare.cloudflared
```

4. Start a Quick Tunnel:

```powershell
.\scripts\start_cloudflare_quick_tunnel.ps1
```

5. Cloudflare prints a random `https://<name>.trycloudflare.com` URL.
Use that URL as the mobile app API base URL instead of `http://localhost:8000`.

Useful notes:

- HTTP API example: `https://<name>.trycloudflare.com/api/v1/...`
- WebSocket example: `wss://<name>.trycloudflare.com/api/v1/ws?user_id=<id>`
- Media files will also resolve through the same public host.
- If you later create a permanent Cloudflare hostname, set `XR_PUBLIC_BASE_URL` to that public `https://...` URL.

Firebase push default credential file is expected at:

`backend/credentials/firebase-admin.json`

## Demo auth

This scaffold uses `X-User-Id` for HTTP and `?user_id=` for WebSocket auth.
Replace it with JWT/session validation before production.

## Alembic

```powershell
alembic upgrade head
alembic revision --autogenerate -m "describe change"
alembic downgrade -1
```

`alembic.ini` reads the same `XR_DATABASE_URL` from `.env`.

## Recommended next steps

1. Replace demo auth with JWT verification.
2. Attach Flutter desktop client to `/api/v1/ws`.
3. Move old polling endpoints to "initial load only".
4. Add Prometheus metrics and rate limiting.
