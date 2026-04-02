from __future__ import annotations

import argparse
import asyncio
import os

import uvicorn

from app.core.config import get_settings


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default)).strip()))
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Nex App Backend with production-like defaults."
    )
    parser.add_argument("--host", type=str, default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--log-level", type=str, default=None)
    parser.add_argument("--access-log", action="store_true", default=None)
    parser.add_argument("--no-access-log", action="store_false", dest="access_log")
    return parser.parse_args()


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    args = _parse_args()
    settings = get_settings()
    host = (args.host or os.getenv("NEX_BACKEND_HOST", settings.backend_host)).strip() or settings.backend_host
    port = (
        max(1, int(args.port))
        if args.port is not None
        else _env_int("NEX_BACKEND_PORT", settings.backend_port)
    )
    workers_default = min(4, max(1, os.cpu_count() or 1))
    workers = (
        max(1, int(args.workers))
        if args.workers is not None
        else _env_int("NEX_BACKEND_WORKERS", workers_default)
    )
    access_log = (
        args.access_log
        if args.access_log is not None
        else _env_bool("NEX_BACKEND_ACCESS_LOG", True)
    )
    log_level = (
        args.log_level or os.getenv("NEX_BACKEND_LOG_LEVEL", settings.request_log_level)
    ).strip().lower() or "info"

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        workers=workers,
        proxy_headers=True,
        forwarded_allow_ips=",".join(settings.forwarded_allow_ips_list),
        log_level=log_level,
        access_log=access_log,
    )
