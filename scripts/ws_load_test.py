from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

import httpx
import websockets
from websockets.exceptions import ConnectionClosed


@dataclass(slots=True)
class LoadTestConfig:
    url: str
    metrics_url: str | None
    summary_file: Path | None
    connections: int
    duration_seconds: int
    ramp_seconds: int
    connect_concurrency: int
    ping_interval_seconds: int
    connect_timeout_seconds: float
    close_timeout_seconds: float
    progress_interval_seconds: int
    demo_user_id_auth: bool
    user_id_prefix: str
    user_id_start: int
    token_file: Path | None
    feed_subscribe: bool
    presence_ring: int
    insecure_ssl: bool


@dataclass
class LoadTestStats:
    attempted: int = 0
    connected: int = 0
    connect_failed: int = 0
    active: int = 0
    closed: int = 0
    sent_messages: int = 0
    received_messages: int = 0
    last_backend_metrics: dict[str, float] = field(default_factory=dict)
    connect_latencies_ms: list[float] = field(default_factory=list)
    errors: Counter[str] = field(default_factory=Counter)


class WsLoadTestRunner:
    def __init__(self, config: LoadTestConfig) -> None:
        self._config = config
        self._stats = LoadTestStats()
        self._stop_event = asyncio.Event()
        self._connect_semaphore = asyncio.Semaphore(
            max(1, config.connect_concurrency)
        )
        self._tokens = self._load_tokens(config.token_file)

    async def run(self) -> int:
        started_at = time.perf_counter()
        metrics_task = asyncio.create_task(self._metrics_loop())
        progress_task = asyncio.create_task(self._progress_loop())
        client_tasks = [
            asyncio.create_task(self._run_client(index))
            for index in range(self._config.connections)
        ]

        try:
            await asyncio.sleep(max(1, self._config.duration_seconds))
            self._stop_event.set()
            await asyncio.gather(*client_tasks, return_exceptions=True)
        finally:
            self._stop_event.set()
            metrics_task.cancel()
            progress_task.cancel()
            await asyncio.gather(metrics_task, progress_task, return_exceptions=True)

        total_seconds = max(0.001, time.perf_counter() - started_at)
        self._print_summary(total_seconds=total_seconds)
        return 0 if self._stats.connect_failed == 0 else 1

    def _load_tokens(self, token_file: Path | None) -> list[str]:
        if token_file is None:
            return []
        raw = token_file.read_text(encoding="utf-8")
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _user_id_for_index(self, index: int) -> str:
        value = self._config.user_id_start + index
        return f"{self._config.user_id_prefix}{value:06d}"

    def _url_for_index(self, index: int) -> str:
        if not self._config.demo_user_id_auth:
            return self._config.url
        parsed = urlparse(self._config.url)
        query = parsed.query
        user_id = self._user_id_for_index(index)
        extra_query = urlencode({"user_id": user_id})
        merged_query = f"{query}&{extra_query}" if query else extra_query
        return urlunparse(parsed._replace(query=merged_query))

    def _headers_for_index(self, index: int) -> dict[str, str]:
        if not self._tokens:
            return {}
        token = self._tokens[index % len(self._tokens)]
        return {"Authorization": f"Bearer {token}"}

    def _subscription_topics_for_index(self, index: int) -> list[str]:
        topics: list[str] = []
        if self._config.feed_subscribe:
            topics.append("feed:news")
        if self._config.presence_ring > 0:
            for step in range(1, self._config.presence_ring + 1):
                peer_index = (index + step) % max(1, self._config.connections)
                topics.append(f"presence:{self._user_id_for_index(peer_index)}")
        return topics

    async def _run_client(self, index: int) -> None:
        if self._config.ramp_seconds > 0 and self._config.connections > 1:
            delay = (self._config.ramp_seconds * index) / self._config.connections
            await asyncio.sleep(delay)
        if self._stop_event.is_set():
            return

        self._stats.attempted += 1
        url = self._url_for_index(index)
        headers = self._headers_for_index(index)
        ssl_context = None if not self._config.insecure_ssl else False

        try:
            async with self._connect_semaphore:
                if self._stop_event.is_set():
                    return
                connect_started = time.perf_counter()
                websocket = await websockets.connect(
                    url,
                    additional_headers=headers,
                    open_timeout=self._config.connect_timeout_seconds,
                    close_timeout=self._config.close_timeout_seconds,
                    ping_interval=None,
                    max_queue=16,
                    compression=None,
                    ssl=ssl_context,
                )
            connect_ms = (time.perf_counter() - connect_started) * 1000
            self._stats.connect_latencies_ms.append(connect_ms)
            self._stats.connected += 1
            self._stats.active += 1
        except Exception as exc:
            self._stats.connect_failed += 1
            self._stats.errors[type(exc).__name__] += 1
            return

        async with websocket:
            receiver_task = asyncio.create_task(self._receiver_loop(websocket))
            try:
                await self._subscribe_initial_topics(websocket, index)
                await self._heartbeat_loop(websocket)
            finally:
                receiver_task.cancel()
                await asyncio.gather(receiver_task, return_exceptions=True)
                self._stats.active = max(0, self._stats.active - 1)
                self._stats.closed += 1

    async def _subscribe_initial_topics(
        self,
        websocket: websockets.ClientConnection,
        index: int,
    ) -> None:
        for topic in self._subscription_topics_for_index(index):
            await websocket.send(
                json.dumps({"action": "subscribe_topic", "topic": topic})
            )
            self._stats.sent_messages += 1

    async def _heartbeat_loop(
        self,
        websocket: websockets.ClientConnection,
    ) -> None:
        interval = max(0, self._config.ping_interval_seconds)
        if interval <= 0:
            await self._stop_event.wait()
            return
        while not self._stop_event.is_set():
            sleep_for = interval + random.uniform(-0.2 * interval, 0.2 * interval)
            await asyncio.sleep(max(1.0, sleep_for))
            if self._stop_event.is_set():
                return
            try:
                await websocket.send(json.dumps({"action": "ping"}))
                self._stats.sent_messages += 1
            except ConnectionClosed:
                return
            except Exception as exc:
                self._stats.errors[type(exc).__name__] += 1
                return

    async def _receiver_loop(
        self,
        websocket: websockets.ClientConnection,
    ) -> None:
        try:
            async for _message in websocket:
                self._stats.received_messages += 1
        except ConnectionClosed:
            return
        except Exception as exc:
            self._stats.errors[type(exc).__name__] += 1

    async def _metrics_loop(self) -> None:
        if not self._config.metrics_url:
            return
        timeout = httpx.Timeout(5.0)
        async with httpx.AsyncClient(timeout=timeout, verify=not self._config.insecure_ssl) as client:
            while not self._stop_event.is_set():
                try:
                    response = await client.get(self._config.metrics_url)
                    if response.is_success:
                        self._stats.last_backend_metrics = self._parse_backend_metrics(
                            response.text
                        )
                except Exception:
                    pass
                await asyncio.sleep(max(3, self._config.progress_interval_seconds))

    def _parse_backend_metrics(self, payload: str) -> dict[str, float]:
        metrics: dict[str, float] = {}
        interesting = {
            "xr_backend_ws_active_connections",
            "xr_backend_ws_active_users",
            "xr_backend_ws_outbound_events_total",
            "xr_backend_ws_fanout_targets",
        }
        for line in payload.splitlines():
            normalized = line.strip()
            if not normalized or normalized.startswith("#"):
                continue
            for metric in interesting:
                if normalized.startswith(f"{metric} "):
                    try:
                        metrics[metric] = float(normalized.split(" ", 1)[1])
                    except Exception:
                        pass
        return metrics

    async def _progress_loop(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(max(2, self._config.progress_interval_seconds))
            self._print_progress()

    def _print_progress(self) -> None:
        backend = self._stats.last_backend_metrics
        backend_active = backend.get("xr_backend_ws_active_connections")
        backend_users = backend.get("xr_backend_ws_active_users")
        line = (
            f"[progress] attempted={self._stats.attempted} connected={self._stats.connected} "
            f"active={self._stats.active} failed={self._stats.connect_failed} "
            f"sent={self._stats.sent_messages} recv={self._stats.received_messages}"
        )
        if backend_active is not None:
            line += f" backend_ws={int(backend_active)}"
        if backend_users is not None:
            line += f" backend_users={int(backend_users)}"
        print(line, flush=True)

    def _print_summary(self, *, total_seconds: float) -> None:
        summary = self._build_summary(total_seconds=total_seconds)
        print("\n=== WS Load Test Summary ===", flush=True)
        print(json.dumps(summary, indent=2), flush=True)
        if self._config.summary_file is not None:
            self._config.summary_file.parent.mkdir(parents=True, exist_ok=True)
            self._config.summary_file.write_text(
                json.dumps(summary, indent=2),
                encoding="utf-8",
            )
            print(
                f"Summary written to {self._config.summary_file}",
                flush=True,
            )

    def _build_summary(self, *, total_seconds: float) -> dict[str, Any]:
        latencies = self._stats.connect_latencies_ms
        p50 = _percentile(latencies, 50)
        p95 = _percentile(latencies, 95)
        p99 = _percentile(latencies, 99)
        return {
            "targetConnections": self._config.connections,
            "attempted": self._stats.attempted,
            "connected": self._stats.connected,
            "connectFailed": self._stats.connect_failed,
            "closed": self._stats.closed,
            "activeAtEnd": self._stats.active,
            "sentMessages": self._stats.sent_messages,
            "receivedMessages": self._stats.received_messages,
            "durationSeconds": round(total_seconds, 2),
            "connectLatencyMs": {
                "p50": p50,
                "p95": p95,
                "p99": p99,
                "max": round(max(latencies), 2) if latencies else None,
            },
            "backendMetrics": self._stats.last_backend_metrics,
            "errors": dict(self._stats.errors),
        }


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 2)
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((percentile / 100) * (len(ordered) - 1))))
    return round(ordered[index], 2)


def _build_config(args: argparse.Namespace) -> LoadTestConfig:
    token_file = Path(args.token_file).resolve() if args.token_file else None
    summary_file = Path(args.summary_file).resolve() if args.summary_file else None
    if not args.demo_user_id_auth and token_file is None:
        raise SystemExit(
            "Use --demo-user-id-auth for local load tests or provide --token-file for real auth."
        )
    return LoadTestConfig(
        url=args.url,
        metrics_url=args.metrics_url.strip() or None,
        summary_file=summary_file,
        connections=max(1, args.connections),
        duration_seconds=max(10, args.duration_seconds),
        ramp_seconds=max(0, args.ramp_seconds),
        connect_concurrency=max(1, args.connect_concurrency),
        ping_interval_seconds=max(0, args.ping_interval_seconds),
        connect_timeout_seconds=max(1.0, args.connect_timeout_seconds),
        close_timeout_seconds=max(1.0, args.close_timeout_seconds),
        progress_interval_seconds=max(2, args.progress_interval_seconds),
        demo_user_id_auth=bool(args.demo_user_id_auth),
        user_id_prefix=args.user_id_prefix,
        user_id_start=max(0, args.user_id_start),
        token_file=token_file,
        feed_subscribe=bool(args.feed_subscribe),
        presence_ring=max(0, args.presence_ring),
        insecure_ssl=bool(args.insecure_ssl),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Websocket load test harness for XR backend realtime validation.",
    )
    parser.add_argument(
        "--url",
        default="ws://127.0.0.1:8000/api/v1/ws",
        help="Websocket endpoint URL.",
    )
    parser.add_argument(
        "--metrics-url",
        default="http://127.0.0.1:8000/metrics",
        help="Optional Prometheus metrics endpoint.",
    )
    parser.add_argument(
        "--summary-file",
        default="",
        help="Optional JSON file path to store the final summary.",
    )
    parser.add_argument("--connections", type=int, default=1000)
    parser.add_argument("--duration-seconds", type=int, default=120)
    parser.add_argument("--ramp-seconds", type=int, default=30)
    parser.add_argument("--connect-concurrency", type=int, default=200)
    parser.add_argument("--ping-interval-seconds", type=int, default=20)
    parser.add_argument("--connect-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--close-timeout-seconds", type=float, default=5.0)
    parser.add_argument("--progress-interval-seconds", type=int, default=5)
    parser.add_argument("--demo-user-id-auth", action="store_true")
    parser.add_argument("--user-id-prefix", default="load-user-")
    parser.add_argument("--user-id-start", type=int, default=0)
    parser.add_argument("--token-file", default="")
    parser.add_argument("--feed-subscribe", action="store_true")
    parser.add_argument("--presence-ring", type=int, default=0)
    parser.add_argument("--insecure-ssl", action="store_true")
    return parser


async def _main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    config = _build_config(args)
    runner = WsLoadTestRunner(config)
    return await runner.run()


if __name__ == "__main__":
    with asyncio.Runner() as runner:
        sys.exit(runner.run(_main()))
