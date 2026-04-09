# XR Invest Backend

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
- `https://api.xrinvest.uz/admin-panel/`
- Admin Home landing page: `https://api.xrinvest.uz/admin`

`/admin` now opens a lightweight Admin Home page with direct links to:

- the SQL admin panel
- learning video admin
- `/health`
- `/docs`

If you later want `https://xrinvest.uz/admin` on the root domain, point that path to this backend or add a Cloudflare reverse-proxy rule.

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

If you want to watch incoming requests live in the console while testing:

```powershell
.\scripts\start_backend_with_request_logs.ps1
```

This mode prints request lines like `GET /api/v1/... -> 200` so you can watch app traffic in real time.

3. Install Cloudflare Tunnel on Windows:

```powershell
winget install --id Cloudflare.cloudflared
```

4. Start a Quick Tunnel:

```powershell
.\scripts\start_cloudflare_quick_tunnel.ps1
```

5. If you are still testing locally, Cloudflare prints a random `https://<name>.trycloudflare.com` URL.
Use that URL as the mobile app API base URL instead of `http://localhost:8000`.

Useful notes:

- HTTP API example: `https://<name>.trycloudflare.com/api/v1/...`
- WebSocket example: `wss://<name>.trycloudflare.com/api/v1/ws?user_id=<id>`
- If R2 is not configured, media files resolve through the same public host.
- Permanent API domain example: `https://api.xrinvest.uz`
- Set `XR_PUBLIC_BASE_URL=https://api.xrinvest.uz` so backend-generated media and websocket URLs use your real domain.

## Cloudflare R2 media storage

Community images, direct-message voice notes, direct-message full images, and learning video uploads can now be stored in Cloudflare R2 instead of the backend filesystem.

Set these env vars to enable R2 storage:

- `XR_R2_ACCOUNT_ID`
- `XR_R2_ACCESS_KEY_ID`
- `XR_R2_SECRET_ACCESS_KEY`
- `XR_R2_BUCKET_NAME`
- `XR_R2_PUBLIC_BASE_URL`
- Optional: `XR_R2_ENDPOINT_URL` if you do not want it derived from `XR_R2_ACCOUNT_ID`
- Optional: `XR_R2_REGION` (defaults to `auto`)

Typical values:

- `XR_R2_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com`
- `XR_R2_PUBLIC_BASE_URL=https://<your-public-r2-domain>`

Behavior:

- When the R2 variables above are present, uploads are written to R2 and API responses return the public R2 URL.
- When they are missing, the backend keeps the legacy local `/media/...` fallback so local development does not break unexpectedly.

Firebase push default credential file is expected at:

`backend/credentials/firebase-admin.json`

## Auto start on Windows

If you want the backend and named Cloudflare tunnel to auto-start when you sign in on Windows:

```powershell
cd C:\XR HODL\backend
.\scripts\install_backend_stack_startup_task.ps1
```

If Task Scheduler is blocked by Windows policy, the installer falls back to the current user's Startup folder automatically.

Manual launcher:

```powershell
cd C:\XR HODL\backend
.\scripts\start_backend_stack.ps1
```

This launcher is idempotent:

- if backend is already listening on `127.0.0.1:8000`, it does not start a duplicate
- if `cloudflared tunnel run xrinvest-backend` is already running, it does not start a duplicate
- logs are written under `runtime-logs/`

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
