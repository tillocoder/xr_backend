from __future__ import annotations

from fastapi import Request, WebSocket


def get_public_base_url_for_request(request: Request) -> str:
    configured = _normalize_base_url(getattr(request.app.state.settings, "public_base_url", ""))
    if configured:
        return configured

    host = _forwarded_host(request.headers.get("x-forwarded-host")) or request.headers.get("host")
    scheme = _public_scheme(
        forwarded_proto=request.headers.get("x-forwarded-proto"),
        cf_visitor=request.headers.get("cf-visitor"),
        fallback_scheme=request.url.scheme,
    )
    if host:
        return f"{scheme}://{host.strip().rstrip('/')}/"
    return str(request.base_url)


def get_public_base_url_for_websocket(ws: WebSocket) -> str:
    configured = _normalize_base_url(getattr(ws.app.state.settings, "public_base_url", ""))
    if configured:
        return configured

    host = _forwarded_host(ws.headers.get("x-forwarded-host")) or ws.headers.get("host")
    if not host:
        host = "127.0.0.1:8000"
    scheme = _public_scheme(
        forwarded_proto=ws.headers.get("x-forwarded-proto"),
        cf_visitor=ws.headers.get("cf-visitor"),
        fallback_scheme=ws.url.scheme,
    )
    return f"{scheme}://{host.strip().rstrip('/')}/"


def _normalize_base_url(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return f"{raw.rstrip('/')}/"


def _forwarded_host(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return raw.split(",", 1)[0].strip()


def _public_scheme(
    *,
    forwarded_proto: str | None,
    cf_visitor: str | None,
    fallback_scheme: str,
) -> str:
    forwarded = str(forwarded_proto or "").split(",", 1)[0].strip().lower()
    if forwarded in {"https", "wss"}:
        return "https"
    if forwarded in {"http", "ws"}:
        return "http"

    visitor = str(cf_visitor or "").lower()
    if '"scheme":"https"' in visitor:
        return "https"
    if '"scheme":"http"' in visitor:
        return "http"

    return "https" if fallback_scheme in {"https", "wss"} else "http"
