# Architecture Notes

`nex_app_backend` keeps the same style as your reference backend:

- `bootstrap/` wires the app together
- `core/` owns settings, security, and logging
- `db/` owns engine and session lifecycle
- `models/` owns SQLAlchemy entities
- `modules/` owns feature routers and feature-specific application logic
- `presentation/` owns HTTP middleware and top-level API aggregation
- `services/` owns reusable business services
- `schemas/` owns API contracts

Main features currently implemented:

- auth and token lifecycle
- signed-in profile bootstrap
- dashboard overview endpoints for market, invest, and game

