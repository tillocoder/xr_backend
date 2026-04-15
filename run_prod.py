from __future__ import annotations

import asyncio
import argparse
import os

import uvicorn


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default)).strip()))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(1.0, float(os.getenv(name, str(default)).strip()))
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run XR HODL backend with production defaults.")
    parser.add_argument("--host", type=str, default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--expected-peak-ws", type=int, default=None)
    parser.add_argument("--target-ws-per-worker", type=int, default=None)
    parser.add_argument("--backlog", type=int, default=None)
    parser.add_argument("--limit-concurrency", type=int, default=None)
    parser.add_argument("--limit-max-requests", type=int, default=None)
    parser.add_argument("--ws-ping-interval", type=float, default=None)
    parser.add_argument("--ws-ping-timeout", type=float, default=None)
    parser.add_argument("--timeout-graceful-shutdown", type=int, default=None)
    parser.add_argument("--ws-max-queue", type=int, default=None)
    parser.add_argument("--ws-max-size", type=int, default=None)
    parser.add_argument("--log-level", type=str, default=None)
    parser.add_argument("--access-log", action="store_true", default=None)
    parser.add_argument("--no-access-log", action="store_false", dest="access_log")
    return parser.parse_args()


if __name__ == "__main__":
    if os.name == "nt" and _env_bool("XR_BACKEND_FORCE_WINDOWS_SELECTOR_EVENT_LOOP", True):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    args = _parse_args()
    os.environ.setdefault("XR_PRODUCTION_MODE", "true")
    os.environ.setdefault("XR_API_DOCS_ENABLED", "false")
    redis_required_for_runtime = _env_bool("XR_REDIS_REQUIRED_FOR_RUNTIME", True)
    os.environ["XR_REDIS_REQUIRED_FOR_RUNTIME"] = "true" if redis_required_for_runtime else "false"
    os.environ.setdefault("XR_WEBSOCKET_RATE_LIMIT_MAX_CONNECTS_PER_IP", "120")
    os.environ.setdefault("XR_WEBSOCKET_RATE_LIMIT_MAX_MESSAGES_PER_IP", "1200")
    os.environ.setdefault("XR_WEBSOCKET_RATE_LIMIT_MAX_MESSAGES_PER_USER", "600")
    host = (args.host or os.getenv("XR_BACKEND_HOST", "0.0.0.0")).strip() or "0.0.0.0"
    port = max(1, int(args.port)) if args.port is not None else _env_int("XR_BACKEND_PORT", 8000)
    expected_peak_ws = (
        max(1, int(args.expected_peak_ws))
        if args.expected_peak_ws is not None
        else _env_int("XR_BACKEND_EXPECTED_PEAK_WS_CONNECTIONS", 10000)
    )
    target_ws_per_worker = (
        max(250, int(args.target_ws_per_worker))
        if args.target_ws_per_worker is not None
        else _env_int("XR_BACKEND_TARGET_WS_PER_WORKER", 2500)
    )
    cpu_workers_default = min(8, max(2, os.cpu_count() or 2))
    ws_workers_default = max(1, (expected_peak_ws + target_ws_per_worker - 1) // target_ws_per_worker)
    workers_default = min(8, max(cpu_workers_default, ws_workers_default))
    workers = max(1, int(args.workers)) if args.workers is not None else _env_int("XR_BACKEND_WORKERS", workers_default)
    os.environ["XR_PROCESS_WORKER_COUNT"] = str(workers)
    if workers > 1 and not redis_required_for_runtime:
        raise SystemExit(
            "XR_REDIS_REQUIRED_FOR_RUNTIME=false is not supported with multiple workers in run_prod.py. "
            "Enable Redis coordination or run a single worker."
        )
    backlog = max(1, int(args.backlog)) if args.backlog is not None else _env_int("XR_BACKEND_BACKLOG", 8192)
    limit_concurrency = (
        max(1, int(args.limit_concurrency))
        if args.limit_concurrency is not None
        else _env_int(
            "XR_BACKEND_LIMIT_CONCURRENCY",
            max(20000, expected_peak_ws + max(2000, workers * 500)),
        )
    )
    limit_max_requests = (
        max(1, int(args.limit_max_requests))
        if args.limit_max_requests is not None
        else _env_int("XR_BACKEND_LIMIT_MAX_REQUESTS", 200000)
    )
    ws_ping_interval = (
        max(1.0, float(args.ws_ping_interval))
        if args.ws_ping_interval is not None
        else _env_float("XR_BACKEND_WS_PING_INTERVAL_SECONDS", 20.0)
    )
    ws_ping_timeout = (
        max(1.0, float(args.ws_ping_timeout))
        if args.ws_ping_timeout is not None
        else _env_float("XR_BACKEND_WS_PING_TIMEOUT_SECONDS", 20.0)
    )
    timeout_graceful_shutdown = (
        max(1, int(args.timeout_graceful_shutdown))
        if args.timeout_graceful_shutdown is not None
        else _env_int("XR_BACKEND_TIMEOUT_GRACEFUL_SHUTDOWN_SECONDS", 30)
    )
    ws_max_queue = max(1, int(args.ws_max_queue)) if args.ws_max_queue is not None else _env_int("XR_BACKEND_WS_MAX_QUEUE", 64)
    ws_max_size = max(1024, int(args.ws_max_size)) if args.ws_max_size is not None else _env_int("XR_BACKEND_WS_MAX_SIZE_BYTES", 1048576)
    ws_per_message_deflate = _env_bool("XR_BACKEND_WS_PER_MESSAGE_DEFLATE", False)
    access_log = args.access_log if args.access_log is not None else _env_bool("XR_BACKEND_ACCESS_LOG", True)
    forwarded_allow_ips = os.getenv(
        "XR_BACKEND_FORWARDED_ALLOW_IPS",
        "127.0.0.1,::1",
    ).strip() or "127.0.0.1,::1"
    log_level = (args.log_level or os.getenv("XR_BACKEND_LOG_LEVEL", "info")).strip().lower() or "info"

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        workers=workers,
        backlog=backlog,
        limit_concurrency=limit_concurrency,
        limit_max_requests=limit_max_requests,
        proxy_headers=True,
        forwarded_allow_ips=forwarded_allow_ips,
        timeout_keep_alive=10,
        timeout_graceful_shutdown=timeout_graceful_shutdown,
        ws_ping_interval=ws_ping_interval,
        ws_ping_timeout=ws_ping_timeout,
        ws_max_queue=ws_max_queue,
        ws_max_size=ws_max_size,
        ws_per_message_deflate=ws_per_message_deflate,
        log_level=log_level,
        access_log=access_log,
    )
