# WebSocket Load Testing

This runbook gives us a repeatable path to validate `1k`, `5k`, and `10k` websocket connections before we claim capacity in production.

## What This Covers

- staged websocket connection tests
- optional topic fanout validation with `feed:news` and `presence:*`
- connect latency tracking
- Prometheus metric sampling from `/metrics`
- JSON summaries that can be compared across runs

## Important Boundaries

- Do not use Cloudflare quick tunnels for capacity testing.
- Do not run `10k` from a laptop and treat the number as production truth.
- Use `XR_ALLOW_INSECURE_DEMO_WS_USER_ID_AUTH=true` only in staging or synthetic load-test environments.
- For real production validation, prefer Linux hosts with the same CPU, memory, Redis, and Postgres topology you plan to deploy.

## Backend Start Command

Start the backend with the production runner:

```powershell
cd c:\backend
$env:XR_BACKEND_HOST = "0.0.0.0"
$env:XR_BACKEND_PORT = "8000"
$env:XR_BACKEND_WORKERS = "4"
$env:XR_BACKEND_BACKLOG = "4096"
$env:XR_BACKEND_LIMIT_CONCURRENCY = "25000"
$env:XR_BACKEND_WS_PING_INTERVAL_SECONDS = "20"
$env:XR_BACKEND_WS_PING_TIMEOUT_SECONDS = "20"
$env:XR_WEBSOCKET_RATE_LIMIT_MAX_CONNECTS_PER_IP = "20000"
$env:XR_WEBSOCKET_RATE_LIMIT_MAX_MESSAGES_PER_IP = "40000"
$env:XR_WEBSOCKET_RATE_LIMIT_MAX_MESSAGES_PER_USER = "5000"
$env:XR_ALLOW_INSECURE_DEMO_WS_USER_ID_AUTH = "true"
C:\backend\.venv\Scripts\python.exe C:\backend\run_prod.py
```

Suggested baseline for first serious staging run:

- `4` workers minimum
- Redis available and healthy
- Postgres on a separate service, not local dev defaults
- `/metrics` enabled
- rate limits temporarily raised if the whole load is coming from a single source IP

## Quick Smoke Test

```powershell
cd c:\backend
.\scripts\run_ws_load_test.ps1 -Scenario smoke -DemoUserIdAuth
```

This verifies:

- clients can connect
- heartbeat loop works
- basic fanout paths do not immediately collapse

## Staged Runs

Run these in order. Do not jump straight to `10k`.

### 1k

```powershell
cd c:\backend
.\scripts\run_ws_load_test.ps1 -Scenario 1k -DemoUserIdAuth
```

### 5k

```powershell
cd c:\backend
.\scripts\run_ws_load_test.ps1 -Scenario 5k -DemoUserIdAuth
```

### 10k

```powershell
cd c:\backend
.\scripts\run_ws_load_test.ps1 -Scenario 10k -DemoUserIdAuth
```

Each run writes a summary JSON into `c:\backend\load-test-results`.

## Real Auth Mode

If you want to test with real bearer tokens instead of demo `user_id` auth:

```powershell
cd c:\backend
.\scripts\run_ws_load_test.ps1 -Scenario 1k -TokenFile c:\backend\tokens.txt
```

`tokens.txt` should contain one bearer token per line.

## Metrics To Watch

The load harness samples `/metrics` and includes selected values in the summary:

- `xr_backend_ws_active_connections`
- `xr_backend_ws_active_users`
- `xr_backend_ws_outbound_events_total`
- `xr_backend_ws_fanout_targets`

You should also watch these platform signals outside the harness:

- CPU per app instance
- memory growth
- Redis CPU and latency
- Postgres CPU, slow queries, and connection count
- websocket disconnect rate
- reverse proxy or load balancer saturation

## Pass/Fail Guidance

Treat a stage as healthy only if all of these hold:

- connect failure rate stays below `0.5%`
- active websocket count reaches at least `98%` of the target
- connect latency `p95` stays below `1500ms`
- no sustained memory climb after the ramp settles
- Redis and Postgres stay stable under the run
- websocket error counters do not trend upward throughout the soak

Treat a stage as failed if any of these happen:

- active connections keep falling after the ramp
- connect failures spike above `1%`
- `p95` connect latency keeps climbing instead of stabilizing
- Redis or Postgres saturates
- application workers restart, stall, or stop draining queues

## Tuning Ramp And Handshake Concurrency

If you see connect timeouts during the ramp, lower `-ConnectConcurrency` first before you assume the whole websocket stack is broken.

If you are driving a large test from one machine or one NAT IP, raise the websocket rate limits for that environment first. Otherwise you may end up testing the rate limiter instead of the transport.

Example:

```powershell
cd c:\backend
.\scripts\run_ws_load_test.ps1 -Scenario 1k -DemoUserIdAuth -ConnectConcurrency 50
```

If the handshake is just slow rather than broken, raise the connect timeout as well:

```powershell
cd c:\backend
.\scripts\run_ws_load_test.ps1 -Scenario 1k -DemoUserIdAuth -ConnectConcurrency 50 -ConnectTimeoutSeconds 30
```

This is especially important on local Windows machines, where aggressive handshake bursts can produce false-negative smoke results.

## Notes On Interpreting Results

- `1k` passing does not imply `10k` will pass.
- `10k connected` is not the same as `10k active users sending traffic`.
- presence-heavy runs and chat-heavy runs should be tested separately.
- after a successful `10k` idle or low-traffic run, add a second run with more topic subscriptions or message churn.

## Current Harness

The runner script is:

- [ws_load_test.py](c:\backend\scripts\ws_load_test.py)

The Windows wrapper is:

- [run_ws_load_test.ps1](c:\backend\scripts\run_ws_load_test.ps1)

If the team wants deeper validation later, the next step is a second harness that drives chat send/read traffic instead of mostly idle heartbeat traffic.
