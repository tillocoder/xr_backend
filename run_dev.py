import asyncio
import os
import socket
import sys
from urllib.error import URLError
from urllib.request import urlopen

import uvicorn


def _port_in_use(host: str, port: int) -> bool:
    probe_host = _probe_host(host)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((probe_host, port)) == 0


def _backend_already_running(host: str, port: int) -> bool:
    probe_host = _probe_host(host)
    try:
        with urlopen(f"http://{probe_host}:{port}/health", timeout=1.0) as response:
            return response.status == 200
    except URLError:
        return False


def _probe_host(host: str) -> str:
    normalized = host.strip().lower()
    if normalized in {"0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return host


if __name__ == "__main__":
    host = os.getenv("XR_BACKEND_HOST", "0.0.0.0")
    port = int(os.getenv("XR_BACKEND_PORT", "8000"))
    if _port_in_use(host, port):
        if _backend_already_running(host, port):
            print(f"XR backend is already running at http://{_probe_host(host)}:{port}")
            raise SystemExit(0)
        print(
            f"Port {port} is already in use by another process. "
            f"Stop that process or set XR_BACKEND_PORT to a free port.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    config = uvicorn.Config(
        "app.main:app",
        host=host,
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1,::1",
    )
    server = uvicorn.Server(config)
    if sys.platform.startswith("win"):
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(server.serve())
    else:
        uvicorn.run(
            "app.main:app",
            host=host,
            port=port,
            proxy_headers=True,
            forwarded_allow_ips="127.0.0.1,::1",
        )
