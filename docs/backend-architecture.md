# Backend Architecture

## Goal

This backend now follows a feature-oriented package structure on top of the existing clean layers:

- `presentation`: HTTP/WebSocket delivery, router composition, middleware
- `application`: use-cases and orchestration
- `domain`: core business models and rules
- `infrastructure`: adapters for Redis, rate limits, health probes, external integrations

The migration strategy is incremental. Legacy modules remain operational behind stable compatibility wrappers while new work moves into feature packages.

## Package Layout

### Composition layer

- `app/bootstrap`
  - app creation
  - dependency container
  - startup/lifespan
- `app/presentation/api/v1`
  - single API v1 composition router
- `app/presentation/api/admin`
  - admin router composition

### Feature modules

- `app/modules/account`
- `app/modules/community`
- `app/modules/learning`
- `app/modules/signals`
- `app/modules/realtime`
- `app/modules/system`
- `app/modules/admin`
- `app/modules/legacy`

Each feature package should converge toward:

- `presentation`
  - FastAPI routers
  - request/response mapping
- `application`
  - use-cases
  - orchestration services
- `domain`
  - feature rules and value objects
- `infrastructure`
  - repositories, gateways, caches, third-party adapters

## Current Migration State

### Fully moved slice

- `me` endpoints now live in:
  - `app/modules/account/presentation/router.py`
  - `app/modules/account/application/me_service.py`

Compatibility shims remain in:

- `app/api/me.py`
- `app/services/me_service.py`

### Wrapped legacy slices

These are now routed through feature packages but still use legacy implementations internally:

- `community`
- `learning`
- `signals`
- `realtime`
- `admin`
- `compat`

## Rules For New Code

1. Add new HTTP routes only inside `app/modules/<feature>/presentation`.
2. Put orchestration logic in `app/modules/<feature>/application`.
3. Keep `app/api/*` only as compatibility wrappers during migration.
4. Keep `app/services/*` only as compatibility wrappers when a feature has moved.
5. Prefer feature-local dependencies over cross-feature imports.
6. Put shared technical building blocks in:
   - `app/bootstrap`
   - `app/core`
   - `app/db`
   - `app/infrastructure`

## Next Recommended Migrations

1. Split `community_service.py` into:
   - `application/post_service.py`
   - `application/profile_service.py`
   - `application/comment_service.py`
   - `application/follow_service.py`
2. Move `signals` into:
   - `modules/signals/application`
   - `modules/signals/infrastructure`
3. Move `news` from `app/services/*news*` into a dedicated `modules/news` package.
4. Move websocket endpoint orchestration into `modules/realtime/application`.

## Why This Structure

- easier onboarding for new developers
- clearer ownership by feature
- smaller mental load than a single shared `services` bucket
- safer incremental refactors
- cleaner path to testing by feature slice
