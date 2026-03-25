from __future__ import annotations

import time
from typing import Any

from fastapi import Request

from app.presentation.api.request_state import get_optional_cache, get_settings_value


class JsonRouteCache:
    def __init__(
        self,
        *,
        namespace: str,
        ttl_setting_name: str,
        default_ttl_seconds: int,
        min_ttl_seconds: int = 1,
        max_ttl_seconds: int = 300,
    ) -> None:
        self._namespace = namespace.strip() or "route"
        self._ttl_setting_name = ttl_setting_name.strip()
        self._default_ttl_seconds = max(1, int(default_ttl_seconds))
        self._min_ttl_seconds = max(1, int(min_ttl_seconds))
        self._max_ttl_seconds = max(self._min_ttl_seconds, int(max_ttl_seconds))
        self._process_cache: dict[str, tuple[float, Any]] = {}

    def ttl_seconds(self, request: Request) -> int:
        configured = int(
            get_settings_value(
                request,
                self._ttl_setting_name,
                self._default_ttl_seconds,
            )
        )
        return max(self._min_ttl_seconds, min(configured, self._max_ttl_seconds))

    def build_key(self, *parts: object) -> str:
        return f"{self._namespace}:" + "|".join(str(part or "").strip() for part in parts)

    def _process_get(self, key: str) -> Any | None:
        cached = self._process_cache.get(key)
        if cached is None:
            return None
        expires_at, payload = cached
        if time.monotonic() >= expires_at:
            self._process_cache.pop(key, None)
            return None
        return payload

    def _process_set(self, request: Request, key: str, payload: Any) -> None:
        expires_at = time.monotonic() + self.ttl_seconds(request)
        self._process_cache[key] = (expires_at, payload)

    def _process_delete_exact(self, key: str) -> None:
        self._process_cache.pop(key, None)

    def _process_delete_prefix(self, prefix: str) -> None:
        for key in list(self._process_cache.keys()):
            if key.startswith(prefix):
                self._process_cache.pop(key, None)

    def normalize_payload(self, value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, list):
            return [self.normalize_payload(item) for item in value]
        if isinstance(value, dict):
            return {str(key): self.normalize_payload(item) for key, item in value.items()}
        return value

    async def get(self, request: Request, key: str) -> Any | None:
        process_cached = self._process_get(key)
        if process_cached is not None:
            return process_cached
        cache = get_optional_cache(request)
        if cache is None:
            return None
        cached = await cache.get_json(key)
        if cached is None:
            return None
        self._process_set(request, key, cached)
        return cached

    async def set(self, request: Request, key: str, payload: Any) -> Any:
        normalized_payload = self.normalize_payload(payload)
        self._process_set(request, key, normalized_payload)
        cache = get_optional_cache(request)
        if cache is not None and isinstance(normalized_payload, (dict, list)):
            await cache.set_json(
                key,
                normalized_payload,
                ttl_seconds=self.ttl_seconds(request),
            )
        return normalized_payload

    async def delete_exact(self, request: Request, key: str) -> None:
        self._process_delete_exact(key)
        cache = get_optional_cache(request)
        if cache is not None:
            await cache.delete_json(key)

    async def delete_prefix(self, request: Request, prefix: str) -> None:
        self._process_delete_prefix(prefix)
        cache = get_optional_cache(request)
        if cache is not None:
            await cache.delete_json_prefix(prefix)
